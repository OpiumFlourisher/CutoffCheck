/* FLAC authenticity analysis — JS port of flaccheck.py (same thresholds). */
(function (root) {
  "use strict";
  const NFFT = 4096;
  const MAX_FRAMES = 2000;

  // ---------- FFT (radix-2, complex, in-place) ----------
  const fftCache = {};
  function fftTables(n) {
    if (fftCache[n]) return fftCache[n];
    const levels = Math.log2(n);
    const rev = new Uint32Array(n);
    for (let i = 0; i < n; i++) {
      let r = 0, x = i;
      for (let b = 0; b < levels; b++) { r = (r << 1) | (x & 1); x >>= 1; }
      rev[i] = r;
    }
    const cos = new Float64Array(n / 2), sin = new Float64Array(n / 2);
    for (let i = 0; i < n / 2; i++) { cos[i] = Math.cos(2 * Math.PI * i / n); sin[i] = Math.sin(2 * Math.PI * i / n); }
    return (fftCache[n] = { rev, cos, sin });
  }
  function fft(re, im) {
    const n = re.length, { rev, cos, sin } = fftTables(n);
    for (let i = 0; i < n; i++) {
      const j = rev[i];
      if (j > i) { let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; }
    }
    for (let size = 2; size <= n; size <<= 1) {
      const half = size >> 1, step = n / size;
      for (let i = 0; i < n; i += size) {
        for (let j = 0, k = 0; j < half; j++, k += step) {
          const a = i + j, b = a + half;
          const tr = re[b] * cos[k] + im[b] * sin[k];
          const ti = -re[b] * sin[k] + im[b] * cos[k];
          re[b] = re[a] - tr; im[b] = im[a] - ti;
          re[a] += tr; im[a] += ti;
        }
      }
    }
  }

  // ---------- small numeric helpers (numpy-equivalent) ----------
  function percentile(arr, p) {               // linear interpolation, like numpy
    const a = Float64Array.from(arr).sort();
    const pos = (a.length - 1) * p / 100, lo = Math.floor(pos), hi = Math.ceil(pos);
    return a[lo] + (a[hi] - a[lo]) * (pos - lo);
  }
  const median = (a) => percentile(a, 50);
  function smooth(x, k) {                    // np.convolve(x, ones(k)/k, 'same')
    if (k <= 1) return Float64Array.from(x);
    const n = x.length, out = new Float64Array(n), off = Math.floor((k - 1) / 2);
    const pre = new Float64Array(n + 1);
    for (let i = 0; i < n; i++) pre[i + 1] = pre[i] + x[i];
    for (let i = 0; i < n; i++) {
      const c = i + off;                      // index into the 'full' output
      const lo = Math.max(0, c - k + 1), hi = Math.min(n - 1, c);
      out[i] = (pre[hi + 1] - pre[lo]) / k;
    }
    return out;
  }
  function meanWhere(x, freqs, lo, hi) {
    let s = 0, c = 0;
    for (let i = 0; i < x.length; i++) if (freqs[i] > lo && freqs[i] < hi) { s += x[i]; c++; }
    return c ? s / c : NaN;
  }
  function medianWhere(x, freqs, lo, hi) {
    const v = [];
    for (let i = 0; i < x.length; i++) if (freqs[i] > lo && freqs[i] < hi) v.push(x[i]);
    return median(v);
  }

  function findCutoff(profile, freqs, ref, dropDb = 65) {
    const df = freqs[1] - freqs[0], n = profile.length, top = freqs[n - 1] - 250;
    const k = Math.max(1, Math.floor(500 / df));
    let run = 0; const mask = new Uint8Array(n);
    let last = -1;
    for (let i = 0; i < n; i++) {
      mask[i] = profile[i] > ref - dropDb && freqs[i] < top ? 1 : 0;
      run += mask[i];
      if (i >= k) run -= mask[i - k];
      if (run / k >= 0.8) last = i;
    }
    return last >= 0 ? freqs[last] : 0;
  }
  function wallSteepness(profile, freqs, fc, nyq) {
    let sb = 0, cb = 0, sa = 0, ca = 0;
    for (let i = 0; i < freqs.length; i++) {
      const f = freqs[i];
      if (f > fc - 1500 && f < fc - 300) { sb += profile[i]; cb++; }
      if (f > fc + 300 && f < Math.min(fc + 1500, nyq)) { sa += profile[i]; ca++; }
    }
    return cb < 3 || ca < 3 ? 0 : sb / cb - sa / ca;
  }

  function guessLossy(k) {
    if (k < 11.5) return "very low bitrate lossy (≤64 kbps)";
    if (k < 15.0) return "MP3 ~96 kbps / low-bitrate AAC";
    if (k < 16.3) return "MP3 112–128 kbps (older encoder)";
    if (k < 17.5) return "MP3 128 kbps";
    if (k < 18.6) return "MP3 160 kbps";
    if (k < 19.4) return "MP3 192–224 kbps";
    if (k < 20.2) return "MP3 256 kbps / V0";
    return "MP3 320 kbps, Opus or high-bitrate AAC";
  }

  /** Bits actually used. channels: Float32Array[] in [-1,1); declaredBits from the header. */
  function effectiveBits(channels, declaredBits) {
    const scale = Math.pow(2, declaredBits - 1);
    let acc = 0;
    for (const ch of channels) {
      for (let i = 0; i < ch.length; i += 1) {
        acc |= Math.round(ch[i] * scale);
        if (acc & 1) return declaredBits;     // lowest bit used: nothing to find
      }
    }
    if (acc === 0) return 0;
    let tz = 0; while (((acc >> tz) & 1) === 0 && tz < 32) tz++;
    return declaredBits - tz;
  }

  /**
   * analyse({ sr, bits, channels: Float32Array[], onProgress? })
   * returns a result object (same fields/verdicts as flaccheck.py)
   */
  function analyse({ sr, bits, channels, onProgress }) {
    const nyq = sr / 2, len = channels[0].length, nch = channels.length;
    const r = { sr, bits, duration: len / sr, notes: [], lossyGuess: "" };
    r.effBits = bits >= 24 ? effectiveBits(channels, bits) : bits;

    // ---- spectrogram frames (dBFS) ----
    const n = Math.floor((len - NFFT) / NFFT);
    if (n < 8) throw new Error("File is too short to analyse.");
    const count = Math.min(n, MAX_FRAMES), bins = NFFT / 2 + 1;
    const win = new Float64Array(NFFT); let wsum = 0;
    for (let i = 0; i < NFFT; i++) { win[i] = 0.5 - 0.5 * Math.cos(2 * Math.PI * i / (NFFT - 1)); wsum += win[i]; }
    const norm = wsum / 2;
    const db = new Array(count), energy = new Float64Array(count);
    const re = new Float64Array(NFFT), im = new Float64Array(NFFT);
    for (let f = 0; f < count; f++) {
      const start = Math.floor(count > 1 ? f * (n - 1) / (count - 1) : 0) * NFFT;
      for (let i = 0; i < NFFT; i++) {
        let s = 0; for (let c = 0; c < nch; c++) s += channels[c][start + i];
        re[i] = (s / nch) * win[i]; im[i] = 0;
      }
      fft(re, im);
      const row = new Float32Array(bins); let mx = -Infinity;
      for (let b = 0; b < bins; b++) {
        const v = 20 * Math.log10(Math.hypot(re[b], im[b]) / norm + 1e-12);
        row[b] = v; if (v > mx) mx = v;
      }
      db[f] = row; energy[f] = mx;
      if (onProgress && f % 100 === 0) onProgress(0.35 * f / count);
    }
    const freqs = new Float64Array(bins);
    for (let b = 0; b < bins; b++) freqs[b] = b * sr / NFFT;

    // ---- louder half of frames ----
    const thr = median(energy);
    const loud = db.filter((_, i) => energy[i] >= thr);

    const smoothK = Math.max(1, Math.floor(150 / (sr / NFFT)));
    const col = new Float64Array(loud.length);
    const p95 = new Float64Array(bins), p75 = new Float64Array(bins);
    for (let b = 0; b < bins; b++) {
      for (let i = 0; i < loud.length; i++) col[i] = loud[i][b];
      const a = Float64Array.from(col).sort();
      const pick = (p) => { const pos = (a.length - 1) * p, lo = Math.floor(pos), hi = Math.ceil(pos); return a[lo] + (a[hi] - a[lo]) * (pos - lo); };
      p95[b] = pick(0.95); p75[b] = pick(0.75);
    }
    const profile = smooth(p95, smoothK);
    const ref = medianWhere(profile, freqs, 2000, 8000);

    const fc = findCutoff(profile, freqs, ref);
    r.cutoffKhz = Math.round(fc / 10) / 100;
    r.wallDb = Math.round(wallSteepness(profile, freqs, fc, nyq) * 10) / 10;

    // ---- per-frame cutoffs ----
    const step = Math.max(1, Math.floor(loud.length / 400));
    let low = 0, tot = 0;
    const lowLimit = Math.min(19500, nyq - 1500);
    for (let i = 0; i < loud.length; i += step) {
      const p = smooth(loud[i], smoothK);
      if (findCutoff(p, freqs, medianWhere(p, freqs, 2000, 8000)) < lowLimit) low++;
      tot++;
    }
    r.lossyFramesPct = Math.round(1000 * low / tot) / 10;

    // ---- hi-res ----
    r.hiresEnergy = false;
    if (sr >= 88200) {
      const hi = [];
      for (let b = 0; b < bins; b++) if (freqs[b] > 25000) hi.push(p75[b]);
      r.hiresEnergy = median(hi) > ref - 70 && fc > 25000;
    }

    // ---- steepest large drop (Opus edge) ----
    let edgeF = 0, edgeDrop = 0;
    {
      const df = freqs[1] - freqs[0], k1 = Math.max(1, Math.floor(300 / df)), k2 = Math.max(2, Math.floor(1200 / df));
      for (let i = Math.floor(12000 / df); i < bins - k2; i++) {
        if (freqs[i] > nyq - 700) break;
        let a = 0; for (let j = i - k2; j < i - k1; j++) a += profile[j]; a /= (k2 - k1);
        if (a < ref - 45) continue;
        let b = 0; for (let j = i + k1; j < i + k2; j++) b += profile[j]; b /= (k2 - k1);
        if (a - b > edgeDrop) { edgeDrop = a - b; edgeF = freqs[i]; }
      }
    }
    r.edgeKhz = Math.round(edgeF / 10) / 100;

    // ---- encoder fingerprint (MP3 / AAC / Vorbis) ----
    r.codec = ""; r.codecConf = ""; r.codecScores = {};
    if (sr <= 48000 && root.CodecPrint) {
      const fp = root.CodecPrint.detect(channels[0], nch > 1 ? channels[1] : null, r.effBits || bits || 16,
        (p) => { if (onProgress) onProgress(0.35 + 0.65 * p); });
      r.codec = fp.codec || ""; r.codecConf = fp.confidence || "";
      for (const k in fp.stats) r.codecScores[k] = fp.stats[k].z;
    }

    // ---- verdicts ----
    const flags = [];
    const brick = r.wallDb >= 25;
    const nearNyq = fc >= Math.min(nyq - 1200, 21000);
    const hardWall = r.wallDb >= 60 && fc < 21200 && sr <= 48000;
    const opusEdge = edgeF >= 19600 && edgeF <= 20800 && edgeDrop >= 40 && sr <= 48000;

    if (r.codec) {
      flags.push("LOSSY TRANSCODE");
      r.lossyGuess = `${r.codec} (encoder fingerprint found, ${r.codecConf})`;
      if (fc < 20200 && brick && r.codec === "MP3") r.notes.push(`Cutoff at ${r.cutoffKhz} kHz suggests ${guessLossy(r.cutoffKhz)}.`);
      else if (fc >= 20200) r.notes.push("No frequency cutoff: a high-bitrate encode that a spectrogram alone would miss.");
    } else if ((fc < 20200 && brick) || hardWall) {
      flags.push("LOSSY TRANSCODE"); r.lossyGuess = guessLossy(r.cutoffKhz) + " (from cutoff)";
      if (sr <= 48000) r.notes.push("No MP3/AAC/Vorbis fingerprint: probably resampled after decoding, or another codec.");
    } else if (opusEdge) {
      flags.push("LOSSY TRANSCODE"); r.lossyGuess = "Opus or another lossy codec (hard 20 kHz edge)";
      r.notes.push(`Content drops ${Math.round(edgeDrop)} dB right at ${r.edgeKhz} kHz. Opus never goes above 20 kHz.`);
    } else if (fc < 20200) {
      flags.push("SUSPICIOUS");
      r.notes.push("Low cutoff but a gradual roll-off and no encoder fingerprint. Likely a dark master; check the spectrogram.");
    } else if (fc < 21000 && brick && sr <= 48000) {
      flags.push("SUSPICIOUS");
      r.notes.push("Sharp cutoff at about 20–21 kHz but no encoder fingerprint. Probably a mastering filter.");
    }

    if (sr >= 88200 && !flags.includes("LOSSY TRANSCODE")) {
      if (r.hiresEnergy) flags.push("GENUINE HI-RES");
      else {
        let edge = fc;
        for (let b = 0; b < bins; b++) if (freqs[b] > 15000 && profile[b] < ref - 20) { edge = freqs[b]; break; }
        const src = edge < 23000 ? "44.1 kHz" : "48 kHz";
        flags.push(`FAKE HI-RES (upsampled from ${src})`);
        r.notes.push(`Content falls off at about ${(edge / 1000).toFixed(1)} kHz.`);
      }
    }
    if (bits >= 24 && r.effBits > 0 && r.effBits <= 16) flags.push("FAKE 24-BIT (padded 16-bit)");

    if (!flags.some((f) => /^(LOSSY|SUSPICIOUS|FAKE HI-RES|GENUINE HI-RES)/.test(f))) {
      flags.unshift("GENUINE LOSSLESS");
      if (!nearNyq && sr <= 48000)
        r.notes.push(`Content ends at ${r.cutoffKhz} kHz (limit ${(nyq / 1000).toFixed(1)}). That's a resampling or mastering filter, not lossy compression.`);
    }
    if (!flags.includes("LOSSY TRANSCODE") && r.lossyFramesPct >= 5 && fc >= 20200)
      r.notes.push(`${r.lossyFramesPct}% of loud moments show a low cutoff, likely a lossy sample inside the original production.`);

    r.flags = flags;
    r.verdict = flags.join(" + ");

    // ---- display data: ≈ 600 frames × 512 bins ----
    const dstep = Math.max(1, Math.floor(db.length / 600)), dbins = Math.floor((bins - 1) / 4);
    r.display = [];
    for (let f = 0; f < db.length; f += dstep) {
      const row = new Float32Array(dbins);
      for (let b = 0; b < dbins; b++) row[b] = Math.max(db[f][4 * b], db[f][4 * b + 1], db[f][4 * b + 2], db[f][4 * b + 3]);
      r.display.push(row);
    }
    r.profile = profile; r.freqs = freqs; r.ref = ref;
    if (onProgress) onProgress(1);
    return r;
  }

  /** Read STREAMINFO from a FLAC file (ArrayBuffer). Returns {sr, bits, channels, samples}. */
  function readFlacInfo(buf) {
    const u8 = new Uint8Array(buf);
    let p = 0;
    // skip ID3v2 tag if present
    if (u8[0] === 0x49 && u8[1] === 0x44 && u8[2] === 0x33) {
      const size = (u8[6] << 21) | (u8[7] << 14) | (u8[8] << 7) | u8[9];
      p = 10 + size;
    }
    if (String.fromCharCode(u8[p], u8[p + 1], u8[p + 2], u8[p + 3]) !== "fLaC") return null;
    const s = p + 8;                           // STREAMINFO body
    const sr = (u8[s + 10] << 12) | (u8[s + 11] << 4) | (u8[s + 12] >> 4);
    const channels = ((u8[s + 12] >> 1) & 7) + 1;
    const bits = (((u8[s + 12] & 1) << 4) | (u8[s + 13] >> 4)) + 1;
    const samples = (u8[s + 13] & 15) * 2 ** 32 + ((u8[s + 14] << 24) >>> 0) + (u8[s + 15] << 16) + (u8[s + 16] << 8) + u8[s + 17];
    return { sr, bits, channels, samples };
  }

  root.FlacAnalysis = { analyse, readFlacInfo, effectiveBits };
  if (typeof module !== "undefined") module.exports = root.FlacAnalysis;
})(typeof window !== "undefined" ? window : globalThis);
