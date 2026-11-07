"""
codecprint — find the encoder "fingerprint" that lossy codecs leave in audio.

A lossy decoder rebuilds sound from quantised transform coefficients, and many
of them are exactly zero. If you run the codec's own transform again, at the
codec's own frame alignment, those zeros come back. At any other alignment, or
for audio that never went through a lossy codec, they don't.

So for each codec we try every possible alignment in several sections of the
song. If one alignment wins in most sections, the file went through that codec,
even at 320 kbps with no frequency cutoff.

Detects: MP3 (MPEG-1 Layer III hybrid filterbank), AAC (KBD and sine windows),
Vorbis. Opus is caught separately by its hard 20 kHz bandwidth edge.
"""
from __future__ import annotations

import numpy as np
from numpy.lib.stride_tricks import sliding_window_view as swv

# MPEG-1 audio analysis window, first half (ISO/IEC 11172-3 table C.1), x 65536.
# Values as tabulated in FFmpeg (libavcodec/mpegaudiodsp_data.c, LGPL-2.1+).
_ENWINDOW_HALF = [
    0, -1, -1, -1, -1, -1, -1, -2, -2, -2, -2, -3,
    -3, -4, -4, -5, -5, -6, -7, -7, -8, -9, -10, -11,
    -13, -14, -16, -17, -19, -21, -24, -26, -29, -31, -35, -38,
    -41, -45, -49, -53, -58, -63, -68, -73, -79, -85, -91, -97,
    -104, -111, -117, -125, -132, -139, -147, -154, -161, -169, -176, -183,
    -190, -196, -202, -208, 213, 218, 222, 225, 227, 228, 228, 227,
    224, 221, 215, 208, 200, 189, 177, 163, 146, 127, 106, 83,
    57, 29, -2, -36, -72, -111, -153, -197, -244, -294, -347, -401,
    -459, -519, -581, -645, -711, -779, -848, -919, -991, -1064, -1137, -1210,
    -1283, -1356, -1428, -1498, -1567, -1634, -1698, -1759, -1817, -1870, -1919, -1962,
    -2001, -2032, -2057, -2075, -2085, -2087, -2080, -2063, 2037, 2000, 1952, 1893,
    1822, 1739, 1644, 1535, 1414, 1280, 1131, 970, 794, 605, 402, 185,
    -45, -288, -545, -814, -1095, -1388, -1692, -2006, -2330, -2663, -3004, -3351,
    -3705, -4063, -4425, -4788, -5153, -5517, -5879, -6237, -6589, -6935, -7271, -7597,
    -7910, -8209, -8491, -8755, -8998, -9219, -9416, -9585, -9727, -9838, -9916, -9959,
    -9966, -9935, -9863, -9750, -9592, -9389, -9139, -8840, -8492, -8092, -7640, -7134,
    6574, 5959, 5288, 4561, 3776, 2935, 2037, 1082, 70, -998, -2122, -3300,
    -4533, -5818, -7154, -8540, -9975, -11455, -12980, -14548, -16155, -17799, -19478, -21189,
    -22929, -24694, -26482, -28289, -30112, -31947, -33791, -35640, -37489, -39336, -41176, -43006,
    -44821, -46617, -48390, -50137, -51853, -53534, -55178, -56778, -58333, -59838, -61289, -62684,
    -64019, -65290, -66494, -67629, -68692, -69679, -70590, -71420, -72169, -72835, -73415, -73908,
    -74313, -74630, -74856, -74992, 75038,
]

REL = 1e-3            # "zero" = more than 60 dB below the frame's RMS
SECTIONS = 6
FRAMES = 2            # MDCT frames per section and alignment
GRANULES = 6          # MP3 granules per section and alignment
RULES = {             # grid, fold, sections that must agree
    "MP3": (576, None, 3),
    "AAC": (1024, None, 3),
    "Vorbis": (1024, 128, 4),
}


def _mpeg_window():
    C = np.zeros(512)
    for i, v in enumerate(_ENWINDOW_HALF):
        C[i] = v
        if i:
            C[512 - i] = v if i % 64 == 0 else -v
    return C / 65536.0


_C = _mpeg_window()
_POLY = np.cos((2 * np.arange(32)[:, None] + 1) * (np.arange(64)[None, :] - 16) * np.pi / 64)
_CI = np.array([-0.6, -0.535, -0.33, -0.185, -0.095, -0.041, -0.0142, -0.0037])
_CS, _CA = 1 / np.sqrt(1 + _CI ** 2), _CI / np.sqrt(1 + _CI ** 2)
_MDCT18 = (np.cos(np.pi / 72 * (2 * np.arange(36)[:, None] + 1 + 18) * (2 * np.arange(18)[None, :] + 1))
           * np.sin(np.pi / 36 * (np.arange(36) + 0.5))[:, None])
_A = np.array([18 * sb - 1 - i for sb in range(1, 32) for i in range(8)])
_B = np.array([18 * sb + i for sb in range(1, 32) for i in range(8)])
_CS_T, _CA_T = np.tile(_CS, 31), np.tile(_CA, 31)
_ODD = np.zeros(576, bool)
for _sb in range(1, 32, 2):
    _ODD[18 * _sb: 18 * _sb + 18] = True


# ------------------------------------------------------------------ windows
def kbd_window(N, alpha=4.0):
    w = np.kaiser(N + 1, np.pi * alpha)
    c = np.sqrt(np.cumsum(w[:N]) / w.sum())
    return np.concatenate([c, c[::-1]])


def sine_window(N):
    return np.sin(np.pi * (np.arange(2 * N) + 0.5) / (2 * N))


def vorbis_window(N):
    n = np.arange(2 * N)
    return np.sin(np.pi / 2 * np.sin(np.pi * (n + 0.5) / (2 * N)) ** 2)


# ------------------------------------------------------------------ transforms
def mdct_rows(frames, win):
    """MDCT of each row (2N samples -> N coefficients) via an N/2-point FFT."""
    z = frames * win
    N = z.shape[1] // 2
    h = N // 2
    n = np.arange(h)
    r = np.empty((z.shape[0], N))
    r[:, :h] = -z[:, 3 * h + n] - z[:, 3 * h - 1 - n]
    r[:, h:] = z[:, n] - z[:, N - 1 - n]
    v = (r[:, 2 * n] + 1j * r[:, N - 1 - 2 * n]) * np.exp(-1j * np.pi * (n + 0.25) / N)
    V = np.fft.fft(v, axis=1) * np.exp(-1j * np.pi * n / N)
    X = np.empty((z.shape[0], N))
    X[:, 2 * n] = V.real
    X[:, N - 1 - 2 * n] = -V.imag
    return X


def sparsity(X, lo, hi, floor=0.0):
    """Fraction of coefficients that are 'zero': >60 dB below the frame RMS, or
    inside the rounding-noise floor of the file's bit depth."""
    B = np.abs(X[:, lo:hi])
    ref = np.sqrt(np.mean(B * B, axis=1, keepdims=True)) + 1e-20
    return np.mean(B < np.maximum(ref * REL, floor), axis=1)


def noise_floor(bits, gain):
    """3 x the std of a transform coefficient caused by integer rounding."""
    return 3.0 * (2.0 ** -(bits - 1)) / np.sqrt(12.0) * gain


# ------------------------------------------------------------------ scans
def mdct_curves(chans, starts, N, win, floor=0.0):
    """(sections, N) sparsity per absolute grid position."""
    curves = np.zeros((len(starts), N))
    lo, hi = int(N * 0.02), int(N * 0.75)
    for c in chans:
        for si, s in enumerate(starts):
            seg = c[s: s + (FRAMES + 2) * N]
            if len(seg) < (FRAMES + 2) * N - 1:
                continue
            V = swv(seg, 2 * N)[: FRAMES * N]
            per = sparsity(mdct_rows(V, win), lo, hi, floor).reshape(FRAMES, N).mean(axis=0)
            curves[si] += np.roll(per, s % N)
    return curves


def _alias(xr):
    A, B = xr[:, _A].copy(), xr[:, _B].copy()
    xr[:, _A] = A * _CS_T + B * _CA_T
    xr[:, _B] = B * _CS_T - A * _CA_T
    return xr


def mp3_curves(chans, starts, floor=0.0):
    """(sections, 576) sparsity per absolute MP3 granule grid position."""
    curves = np.zeros((len(starts), 576))
    nsteps = 18 * (GRANULES + 2)
    inv = np.where(np.arange(36) % 2 == 1, -1.0, 1.0)
    for c in chans:
        win512 = swv(c, 512)
        for si, s in enumerate(starts):
            if s < 600 or s + 32 * nsteps + 64 > len(c):
                continue
            for phase in range(32):
                idx = s + phase + 32 * np.arange(nsteps)
                X = win512[idx - 511][:, ::-1] * _C
                S = X.reshape(-1, 8, 64).sum(axis=1) @ _POLY.T
                for bphase in range(18):
                    blocks = swv(S[bphase: bphase + 18 * (GRANULES + 1)], 36, axis=0)[::18][:GRANULES].copy()
                    blocks[:, 1::2, :] *= inv          # frequency inversion: odd subbands, odd samples
                    xr = (blocks @ _MDCT18).reshape(GRANULES, 576)
                    pos = (s + phase + 32 * bphase) % 576
                    curves[si, pos] += sparsity(_alias(xr), 8, 540, floor).mean()
    return curves


def grid_stats(curves, fold=None):
    if fold:
        curves = curves.reshape(curves.shape[0], -1, fold).max(axis=1)
    active = (curves.max(axis=1) - curves.min(axis=1)) > 1e-6
    winners = curves.argmax(axis=1)[active]
    if len(winners) == 0:
        return {"agree": 0, "sections": 0, "z": 0.0, "excess": 0.0, "lift": 0.0}
    vals, counts = np.unique(winners, return_counts=True)
    pos = int(vals[np.argmax(counts)])
    total = curves[active].sum(axis=0)
    n = int(active.sum())
    med = float(np.median(total))
    mad = float(np.median(np.abs(total - med))) * 1.4826
    peak = int(np.argmax(total))
    excess = float(total[peak] - med) / n          # extra 'zero' fraction at the best alignment
    z = (total[peak] - med) / max(mad, 1e-3 * n)   # how far it stands out from all other alignments
    return {"agree": int(counts.max()), "sections": n, "z": round(float(z), 1),
            "excess": round(excess, 4), "lift": float(total[pos] / (med + 1e-4))}


def section_starts(mono, count, peak_source=None):
    """Evenly spaced section starts, nudged past quiet passages, then offset by an
    uneven amount each so looped genuine music can't line up with a codec grid."""
    n = len(mono)
    blk = 4096
    nb = n // blk
    if nb < 8:
        return []
    blocks = mono[: nb * blk].reshape(nb, blk)
    rms = np.sqrt(np.mean(blocks ** 2, axis=1))
    gate = max(1e-4, 0.1 * np.median(rms))
    pk = np.abs(peak_source if peak_source is not None else mono)[: nb * blk].reshape(nb, blk)
    peak = float(pk.max())
    clipped = np.sum(pk >= 0.999 * peak, axis=1) > 2 if peak > 0.5 else np.zeros(nb, bool)
    if np.mean(clipped) < 0.8:                     # avoid clipped passages unless the whole song clips
        rms = np.where(clipped, 0.0, rms)
    starts = []
    for i, f in enumerate(np.linspace(0.06, 0.94, count)):
        b = int(nb * f)
        end = min(nb - 4, b + max(1, nb // count))
        while b < end and rms[b] < gate:
            b += 1
        if b < nb - 4 and rms[b] >= gate:
            starts.append(b * blk + (i * 1009 + 97) % blk)
    return starts


Z_MIN = 10.0        # genuine audio scores ~2-4; lossy transcodes score 20-200
EXCESS_MIN = 0.01   # at least 1% more 'zero' coefficients at the winning alignment


def _decide(stats):
    """A codec grid must (a) stand far above every other alignment overall and
    (b) win in at least half of the sections on its own. Genuine audio never
    gets more than 2 sections agreeing; codec grids agree in most sections."""
    best = None
    for name in ("MP3", "AAC", "Vorbis"):
        st = stats.get(name)
        if not st or st["sections"] < 3:
            continue
        n = st["sections"]
        agree_ok = st["agree"] >= max(3, -(-n // 2))
        strong = st["z"] >= Z_MIN and st["excess"] >= EXCESS_MIN
        very_consistent = st["agree"] >= max(4, -(-3 * n // 4)) and st["z"] >= 0.6 * Z_MIN
        if (strong and agree_ok) or very_consistent:
            if best is None or st["z"] > stats[best]["z"]:
                best = name
    return best


def confidence(stats, codec):
    if not codec:
        return ""
    st = stats[codec]
    return "certain" if st["z"] >= 2 * Z_MIN and st["agree"] >= 0.6 * st["sections"] else "likely"


# noise gain of the MP3 analysis chain (unit-variance white noise in -> coefficient std out)
def _mp3_gain():
    rng = np.random.default_rng(0)
    w = rng.standard_normal(32 * 18 * (GRANULES + 2) + 1200)
    idx = 600 + 32 * np.arange(18 * (GRANULES + 2))
    S = (swv(w, 512)[idx - 511][:, ::-1] * _C).reshape(-1, 8, 64).sum(axis=1) @ _POLY.T
    b = swv(S[: 18 * (GRANULES + 1)], 36, axis=0)[::18][:GRANULES]
    return float(np.std(b @ _MDCT18))


_MP3_GAIN = _mp3_gain()


def detect(left, right=None, bits=16, on_progress=None):
    """Find an MP3/AAC/Vorbis encoder grid in a 32/44.1/48 kHz track.

    left/right: float arrays scaled to [-1, 1). bits: bits actually used.
    Returns {"codec": "MP3"|"AAC"|"Vorbis"|None, "confidence": str, "stats": {...}}"""
    L = np.asarray(left, float)
    R = np.asarray(right, float) if right is not None else L
    mid, side = (L + R) / 2, (L - R) / 2
    chans = [mid] if np.max(np.abs(side)) < 1e-9 else [mid, side]
    all_starts = section_starts(mid, 12, np.maximum(np.abs(L), np.abs(R)))
    first, second = all_starts[0::2], all_starts[1::2]
    bits = max(8, min(32, int(bits or 16)))
    f_mdct = noise_floor(bits, np.sqrt(1024 / 2))
    f_mp3 = noise_floor(bits, _MP3_GAIN)
    windows = {"kbd": kbd_window(1024), "sine": sine_window(1024), "vorbis": vorbis_window(1024)}

    def scan(starts):
        aac = np.maximum(mdct_curves(chans, starts, 1024, windows["kbd"], f_mdct),
                         mdct_curves(chans, starts, 1024, windows["sine"], f_mdct))
        if on_progress: on_progress(0.4)
        vorbis = mdct_curves(chans, starts, 1024, windows["vorbis"], f_mdct)
        if on_progress: on_progress(0.55)
        mp3 = mp3_curves(chans, starts, f_mp3)
        if on_progress: on_progress(0.95)
        return {"AAC": aac, "Vorbis": vorbis, "MP3": mp3}

    def stats_of(curves):
        return {"AAC": grid_stats(curves["AAC"]), "Vorbis": grid_stats(curves["Vorbis"], fold=128),
                "MP3": grid_stats(curves["MP3"])}

    if len(first) < 3:
        return {"codec": None, "confidence": "", "stats": {}, "sections": 0}
    curves = scan(first)
    stats = stats_of(curves)
    found = _decide(stats)
    borderline = any(Z_MIN / 2 <= v["z"] or v["agree"] >= 2 for v in stats.values())
    used = len(first)
    if found is None and borderline and len(second) >= 3:
        more = scan(second)
        curves = {k: np.vstack([curves[k], more[k]]) for k in curves}
        stats = stats_of(curves)
        found = _decide(stats)
        used += len(second)
    return {"codec": found, "confidence": confidence(stats, found), "stats": stats, "sections": used}
