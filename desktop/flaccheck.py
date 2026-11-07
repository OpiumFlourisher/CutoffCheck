#!/usr/bin/env python3
"""
flaccheck — check whether FLAC (or any lossless) files are genuine.

Verdicts:
  GENUINE LOSSLESS            full bandwidth, no lossy cutoff
  GENUINE HI-RES              real content above 24 kHz
  FAKE HI-RES                 88.2/96/192 kHz file whose content stops at 22-24 kHz
                              (upsampled from 44.1/48 kHz; still lossless)
  FAKE 24-BIT                 24-bit file whose lowest 8 bits are always zero
                              (16-bit audio padded to 24-bit)
  LOSSY TRANSCODE             brick-wall cutoff typical of MP3/AAC/Opus —
                              a "fake FLAC" made from a lossy file
  SUSPICIOUS                  cutoff in the grey zone; check the spectrogram

Requirements: ffmpeg + ffprobe on PATH, Python 3.9+, numpy, (matplotlib for --png)

Usage:
  python3 flaccheck.py ~/Music/song.flac
  python3 flaccheck.py ~/Downloads            # scans folder recursively
  python3 flaccheck.py ~/Downloads --png out/ # also saves spectrograms
  python3 flaccheck.py ~/Downloads --csv report.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import os
import subprocess
import sys
from dataclasses import dataclass, field

import numpy as np

import codecprint

AUDIO_EXT = {".flac", ".wav", ".aiff", ".aif", ".alac", ".m4a", ".ape", ".wv"}
NFFT = 4096


# ----------------------------------------------------------------------------
# Decoding
# ----------------------------------------------------------------------------
def probe(path: str) -> dict:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=codec_name,sample_rate,channels,bits_per_raw_sample,"
         "bits_per_sample,sample_fmt,duration:format=duration,bit_rate",
         "-of", "json", path],
        capture_output=True, text=True, check=True).stdout
    j = json.loads(out)
    s = j["streams"][0]
    bits = int(s.get("bits_per_raw_sample") or s.get("bits_per_sample") or 0)
    if not bits:
        bits = {"s16": 16, "s16p": 16, "s32": 32, "s32p": 32}.get(s.get("sample_fmt"), 0)
    dur = float(s.get("duration") or j.get("format", {}).get("duration") or 0)
    return {
        "codec": s.get("codec_name"),
        "sr": int(s["sample_rate"]),
        "channels": int(s.get("channels", 2)),
        "bits": bits,
        "duration": dur,
        "bitrate": int(j.get("format", {}).get("bit_rate") or 0),
    }


def decode(path: str, channels: int) -> np.ndarray:
    """Decode to int32 interleaved, shape (n, channels). 16/24-bit audio lands in
    the top bits of the int32, so trailing-zero analysis works directly."""
    raw = subprocess.run(
        ["ffmpeg", "-v", "error", "-i", path, "-map", "0:a:0",
         "-f", "s32le", "-acodec", "pcm_s32le", "-"],
        capture_output=True, check=True).stdout
    a = np.frombuffer(raw, dtype="<i4")
    return a.reshape(-1, channels) if channels > 1 else a.reshape(-1, 1)


# ----------------------------------------------------------------------------
# Analysis
# ----------------------------------------------------------------------------
def effective_bits(pcm: np.ndarray) -> int:
    """Number of bits actually used (32 - common trailing zero bits)."""
    acc = np.bitwise_or.reduce(pcm.ravel().view(np.uint32))
    if acc == 0:
        return 0
    tz = (int(acc) & -int(acc)).bit_length() - 1
    return 32 - tz


def spectrum_frames(mono: np.ndarray) -> np.ndarray:
    """Power spectrum in dBFS per frame, shape (frames, bins)."""
    hop = NFFT
    n = (len(mono) - NFFT) // hop
    if n < 8:
        raise ValueError("file too short")
    # cap analysed frames for speed on very long files
    idx = np.linspace(0, n - 1, min(n, 3000)).astype(int) * hop
    win = np.hanning(NFFT).astype(np.float32)
    frames = np.stack([mono[i:i + NFFT] for i in idx]) * win
    spec = np.abs(np.fft.rfft(frames, axis=1)) / (win.sum() / 2)
    return 20 * np.log10(spec + 1e-12)


def smooth(x: np.ndarray, k: int) -> np.ndarray:
    if k <= 1:
        return x
    return np.convolve(x, np.ones(k) / k, mode="same")


@dataclass
class Result:
    path: str
    sr: int = 0
    bits: int = 0
    eff_bits: int = 0
    duration: float = 0.0
    cutoff_khz: float = 0.0
    wall_db: float = 0.0
    lossy_frames_pct: float = 0.0
    hires_energy: bool = False
    codec: str = ""             # codec whose encoder fingerprint was found
    codec_conf: str = ""        # "certain" / "likely"
    codec_scores: dict = field(default_factory=dict)
    edge_khz: float = 0.0       # steepest large drop in the spectrum
    verdict: str = ""
    lossy_guess: str = ""
    notes: list[str] = field(default_factory=list)
    error: str = ""


def find_cutoff(profile: np.ndarray, freqs: np.ndarray, ref: float, drop_db: float = 65.0):
    """Highest frequency where the profile is *sustainedly* within drop_db of ref.
    Ignores the last ~250 Hz below Nyquist (decoder/dither artefacts) and isolated
    spikes: at least 80% of the 500 Hz band below a point must be above threshold."""
    df = freqs[1] - freqs[0]
    valid = freqs < freqs[-1] - 250
    mask = ((profile > ref - drop_db) & valid).astype(float)
    k = max(1, int(500 / df))
    sustained = np.convolve(mask, np.ones(k) / k, mode="full")[:len(mask)]  # trailing window
    idx = np.where(sustained >= 0.8)[0]
    return float(freqs[idx[-1]]) if len(idx) else 0.0


def wall_steepness(profile: np.ndarray, freqs: np.ndarray, fc: float, nyq: float) -> float:
    """dB drop across the cutoff: mean level just below minus mean level just above."""
    below = (freqs > fc - 1500) & (freqs < fc - 300)
    above = (freqs > fc + 300) & (freqs < min(fc + 1500, nyq))
    if below.sum() < 3 or above.sum() < 3:
        return 0.0
    return float(profile[below].mean() - profile[above].mean())


def find_edge(profile, freqs, ref, nyq):
    """Steepest large drop above 12 kHz: (frequency, dB drop over ~1 kHz)."""
    df = freqs[1] - freqs[0]
    k1, k2 = max(1, int(300 / df)), max(2, int(1200 / df))
    best = (0.0, 0.0)
    for i in range(int(12000 / df), len(freqs) - k2):
        if freqs[i] > nyq - 700:
            break
        below = profile[i - k2: i - k1].mean()
        if below < ref - 45:
            continue
        drop = below - profile[i + k1: i + k2].mean()
        if drop > best[1]:
            best = (float(freqs[i]), float(drop))
    return best


def guess_lossy(fc_khz: float) -> str:
    # lowpass points of LAME 3.100 / common encoders
    if fc_khz < 11.5:
        return "very low bitrate lossy (≤64 kbps)"
    if fc_khz < 15.0:
        return "MP3 ~96 kbps / low-bitrate AAC"
    if fc_khz < 16.3:
        return "MP3 112–128 kbps (older encoder)"
    if fc_khz < 17.5:
        return "MP3 128 kbps"
    if fc_khz < 18.6:
        return "MP3 160 kbps"
    if fc_khz < 19.4:
        return "MP3 192–224 kbps"
    if fc_khz < 20.2:
        return "MP3 256 kbps / V0"
    return "MP3 320 kbps, Opus or high-bitrate AAC"


def analyse(path: str) -> Result:
    r = Result(path=path)
    info = probe(path)
    r.sr, r.bits, r.duration = info["sr"], info["bits"], info["duration"]
    nyq = r.sr / 2

    pcm = decode(path, info["channels"])
    r.eff_bits = effective_bits(pcm)
    mono = (pcm.astype(np.float64).mean(axis=1) / 2**31).astype(np.float32)

    db = spectrum_frames(mono)
    freqs = np.fft.rfftfreq(NFFT, 1 / r.sr)

    # keep the louder half of frames (ignore silence / fades)
    energy = db.max(axis=1)
    loud = db[energy >= np.percentile(energy, 50)]

    smooth_k = max(1, int(150 / (r.sr / NFFT)))          # ~150 Hz smoothing
    profile = smooth(np.percentile(loud, 95, axis=0), smooth_k)
    mid = (freqs > 2000) & (freqs < 8000)
    ref = float(np.median(profile[mid]))

    fc = find_cutoff(profile, freqs, ref)
    r.cutoff_khz = round(fc / 1000, 2)
    r.wall_db = round(wall_steepness(profile, freqs, fc, nyq), 1)

    # per-frame cutoffs -> how much of the track has a low brick wall
    frame_cuts = []
    for row in loud[:: max(1, len(loud) // 400)]:
        p = smooth(row, smooth_k)
        frame_cuts.append(find_cutoff(p, freqs, float(np.median(p[mid]))))
    frame_cuts = np.array(frame_cuts)
    r.lossy_frames_pct = round(100 * np.mean(frame_cuts < min(19500, nyq - 1500)), 1)

    # hi-res content check
    if r.sr >= 88200:
        hi = freqs > 25000
        # real hi-res: sustained content above 25 kHz, not just a few spikes
        hi_level = np.percentile(loud[:, hi], 75, axis=0)
        r.hires_energy = bool(np.median(hi_level) > ref - 70 and fc > 25000)

    # ------------------- encoder fingerprint (MP3 / AAC / Vorbis) -------------
    edge_f, edge_drop = find_edge(profile, freqs, ref, nyq)
    r.edge_khz = round(edge_f / 1000, 2)
    if r.sr <= 48000:
        scale = 2.0 ** 31
        left = pcm[:, 0] / scale
        right = pcm[:, 1] / scale if pcm.shape[1] > 1 else None
        fp = codecprint.detect(left, right, bits=r.eff_bits or r.bits or 16)
        r.codec, r.codec_conf = fp["codec"] or "", fp.get("confidence", "")
        r.codec_scores = {k: v["z"] for k, v in fp["stats"].items()}

    # ---------------------------- verdicts ----------------------------------
    flags = []
    brick = r.wall_db >= 25
    near_nyq = fc >= min(nyq - 1200, 21000)
    hard_wall = r.wall_db >= 60 and fc < 21200 and r.sr <= 48000
    opus_edge = 19600 <= edge_f <= 20800 and edge_drop >= 40 and r.sr <= 48000

    if r.codec:
        flags.append("LOSSY TRANSCODE")
        r.lossy_guess = f"{r.codec} (encoder fingerprint found, {r.codec_conf})"
        if fc < 20200 and brick and r.codec == "MP3":
            r.notes.append(f"cutoff at {r.cutoff_khz} kHz suggests {guess_lossy(r.cutoff_khz)}")
        elif fc >= 20200:
            r.notes.append("no frequency cutoff: a high-bitrate encode that a spectrogram alone would miss")
    elif (fc < 20200 and brick) or hard_wall:
        flags.append("LOSSY TRANSCODE")
        r.lossy_guess = guess_lossy(r.cutoff_khz) + " (from cutoff)"
        if r.sr <= 48000:
            r.notes.append("no MP3/AAC/Vorbis fingerprint: probably resampled after decoding, or another codec")
    elif opus_edge:
        flags.append("LOSSY TRANSCODE")
        r.lossy_guess = "Opus or another lossy codec (hard 20 kHz edge)"
        r.notes.append(f"content drops {edge_drop:.0f} dB right at {r.edge_khz} kHz — Opus never goes above 20 kHz")
    elif fc < 20200:
        flags.append("SUSPICIOUS")
        r.notes.append("low cutoff but gradual roll-off and no encoder fingerprint — likely a dark master")
    elif fc < 21000 and brick and r.sr <= 48000:
        flags.append("SUSPICIOUS")
        r.notes.append("sharp cutoff at ~20–21 kHz but no encoder fingerprint — probably a mastering filter")

    if r.sr >= 88200 and "LOSSY TRANSCODE" not in flags:
        if r.hires_energy:
            flags.append("GENUINE HI-RES")
        else:
            # where does the content fall off? first point 20 dB below the midrange
            band = freqs > 15000
            under = np.where(profile[band] < ref - 20)[0]
            edge = freqs[band][under[0]] if len(under) else fc
            src = "44.1 kHz" if edge < 23000 else "48 kHz"
            r.notes.append(f"content falls off at ~{edge/1000:.1f} kHz")
            flags.append(f"FAKE HI-RES (upsampled from {src})")

    if r.bits >= 24 and 0 < r.eff_bits <= 16:
        flags.append("FAKE 24-BIT (padded 16-bit)")

    if not any(f.startswith(("LOSSY", "SUSPICIOUS", "FAKE HI-RES", "GENUINE HI-RES")) for f in flags):
        flags.insert(0, "GENUINE LOSSLESS")
        if not near_nyq and r.sr <= 48000:
            r.notes.append(f"content ends at {r.cutoff_khz} kHz (Nyquist {nyq/1000:.1f}) — "
                           "resampling/mastering filter, not lossy")

    if "LOSSY TRANSCODE" not in flags and r.lossy_frames_pct >= 5 and fc >= 20200:
        r.notes.append(f"{r.lossy_frames_pct}% of loud frames show a low cutoff — "
                       "likely a lossy sample inside the original production")

    r.verdict = " + ".join(flags)
    r._profile = (freqs, profile, ref)  # for plotting
    # keep a small copy for display only (≈ 800 frames × 512 bins, float32)
    disp = db[:: max(1, len(db) // 800)]
    nb = (disp.shape[1] - 1) // 4 * 4
    r._db = disp[:, :nb].reshape(len(disp), -1, 4).max(axis=2).astype(np.float32)
    return r


# ----------------------------------------------------------------------------
# Output
# ----------------------------------------------------------------------------
def draw_figure(r: Result, fig):
    """Draw spectrogram + frequency profile onto a matplotlib Figure."""
    fig.clear()
    freqs, profile, ref = r._profile
    ax = fig.subplots(1, 2, gridspec_kw={"width_ratios": [3, 1]})
    ax[0].imshow(r._db.T, origin="lower", aspect="auto", cmap="inferno",
                 vmin=-120, vmax=0, extent=[0, r.duration or r._db.shape[0], 0, r.sr / 2000])
    ax[0].axhline(r.cutoff_khz, color="cyan", ls="--", lw=1)
    ax[0].set_xlabel("time (s)"); ax[0].set_ylabel("kHz")
    ax[0].set_title(os.path.basename(r.path), fontsize=10)
    ax[1].plot(profile, freqs / 1000, color="orange")
    ax[1].axhline(r.cutoff_khz, color="cyan", ls="--", lw=1)
    ax[1].set_xlabel("dBFS (95th pct)"); ax[1].set_ylim(0, r.sr / 2000)
    ax[1].set_title(r.verdict, fontsize=8, wrap=True)
    fig.tight_layout()


def save_png(r: Result, outdir: str):
    from matplotlib.figure import Figure

    os.makedirs(outdir, exist_ok=True)
    fig = Figure(figsize=(16, 6))
    draw_figure(r, fig)
    name = os.path.splitext(os.path.basename(r.path))[0] + ".png"
    fig.savefig(os.path.join(outdir, name), dpi=90)


COLORS = {"GENUINE": "\033[32m", "FAKE HI": "\033[33m", "FAKE 24": "\033[33m",
          "SUSPIC": "\033[33m", "LOSSY": "\033[31m", "ERROR": "\033[31m"}


def colour(v: str) -> str:
    if not sys.stdout.isatty():
        return v
    for k, c in COLORS.items():
        if v.startswith(k):
            return f"{c}{v}\033[0m"
    return v


def collect(paths: list[str]) -> list[str]:
    files = []
    for p in paths:
        p = os.path.expanduser(p)
        if os.path.isdir(p):
            for root, _, names in os.walk(p):
                files += [os.path.join(root, n) for n in sorted(names)
                          if os.path.splitext(n)[1].lower() in AUDIO_EXT]
        elif os.path.isfile(p):
            files.append(p)
    return files


def write_csv(results: list[Result], path: str):
    with open(os.path.expanduser(path), "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["file", "sample_rate", "bits", "effective_bits", "cutoff_khz",
                    "wall_db", "lossy_frames_pct", "verdict", "lossy_guess", "notes"])
        for r in results:
            w.writerow([r.path, r.sr, r.bits, r.eff_bits, r.cutoff_khz, r.wall_db,
                        r.lossy_frames_pct, r.verdict, r.lossy_guess,
                        "; ".join(r.notes + ([r.error] if r.error else []))])


def ensure_ffmpeg_on_path():
    """Apps launched from Finder don't get Homebrew's PATH; add the usual spots."""
    extra = ["/opt/homebrew/bin", "/usr/local/bin", "/opt/local/bin"]
    here = os.path.dirname(os.path.abspath(sys.argv[0]))
    extra.insert(0, here)  # a bundled ffmpeg next to the app also works
    os.environ["PATH"] = os.pathsep.join(extra + [os.environ.get("PATH", "")])


def main():
    ap = argparse.ArgumentParser(description="Check FLAC authenticity")
    ap.add_argument("paths", nargs="+", help="files or folders")
    ap.add_argument("--png", metavar="DIR", help="save spectrogram PNGs to DIR")
    ap.add_argument("--csv", metavar="FILE", help="write a CSV report")
    args = ap.parse_args()
    ensure_ffmpeg_on_path()

    files = collect(args.paths)
    if not files:
        sys.exit("No audio files found.")

    results = []
    for i, f in enumerate(files, 1):
        print(f"[{i}/{len(files)}] {os.path.basename(f)}", flush=True)
        try:
            r = analyse(f)
            if args.png:
                save_png(r, args.png)
        except Exception as e:  # keep going on bad files
            r = Result(path=f, verdict="ERROR", error=str(e))
        results.append(r)
        fmt = f"{r.sr/1000:g} kHz / {r.bits}-bit" if r.sr else ""
        print(f"    {fmt}   cutoff {r.cutoff_khz} kHz   wall {r.wall_db} dB")
        print(f"    → {colour(r.verdict)}" + (f"  [{r.lossy_guess}]" if r.lossy_guess else ""))
        for n in r.notes:
            print(f"      · {n}")
        if r.error:
            print(f"      · {r.error}")

    # summary
    print("\nSUMMARY")
    for r in results:
        print(f"  {colour(r.verdict.split(' + ')[0]):<40}  {os.path.basename(r.path)}")

    if args.csv:
        write_csv(results, args.csv)
        print(f"\nCSV written to {args.csv}")


if __name__ == "__main__":
    main()
