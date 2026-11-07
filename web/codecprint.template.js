/* codecprint — JS port of codecprint.py: finds MP3 / AAC / Vorbis encoder grids
   in decoded audio. Same constants and decision rules as the Python version. */
(function (root) {
  "use strict";
  const ENWINDOW_HALF = /*__ENWINDOW__*/;
  const MP3_GAIN = /*__MP3GAIN__*/;
  const REL = 1e-3, FRAMES = 2, GRANULES = 6, Z_MIN = 10, EXCESS_MIN = 0.01;

  // ---------------- tables ----------------
  const C = new Float64Array(512);
  ENWINDOW_HALF.forEach((v, i) => { C[i] = v; if (i) C[512 - i] = i % 64 === 0 ? v : -v; });
  for (let i = 0; i < 512; i++) C[i] /= 65536;
  const POLY = new Float64Array(32 * 64);
  for (let sb = 0; sb < 32; sb++) for (let j = 0; j < 64; j++) POLY[sb * 64 + j] = Math.cos((2 * sb + 1) * (j - 16) * Math.PI / 64);
  const MDCT18 = new Float64Array(36 * 18);
  for (let k = 0; k < 36; k++) for (let m = 0; m < 18; m++)
    MDCT18[k * 18 + m] = Math.cos(Math.PI / 72 * (2 * k + 1 + 18) * (2 * m + 1)) * Math.sin(Math.PI / 36 * (k + 0.5));
  const CI = [-0.6, -0.535, -0.33, -0.185, -0.095, -0.041, -0.0142, -0.0037];
  const CS = CI.map((c) => 1 / Math.sqrt(1 + c * c)), CA = CI.map((c) => c / Math.sqrt(1 + c * c));

  // ---------------- windows ----------------
  function besselI0(x) { let s = 1, t = 1; for (let k = 1; k < 60; k++) { t *= (x / (2 * k)) * (x / (2 * k)); s += t; if (t < 1e-17 * s) break; } return s; }
  function kbdWindow(N, alpha = 4) {
    const M = N + 1, beta = Math.PI * alpha, w = new Float64Array(M);
    let total = 0;
    for (let n = 0; n < M; n++) { const r = 2 * n / (M - 1) - 1; w[n] = besselI0(beta * Math.sqrt(Math.max(0, 1 - r * r))); total += w[n]; }
    const out = new Float64Array(2 * N); let c = 0;
    for (let n = 0; n < N; n++) { c += w[n]; out[n] = Math.sqrt(c / total); out[2 * N - 1 - n] = out[n]; }
    return out;
  }
  const sineWindow = (N) => Float64Array.from({ length: 2 * N }, (_, n) => Math.sin(Math.PI * (n + 0.5) / (2 * N)));
  const vorbisWindow = (N) => Float64Array.from({ length: 2 * N }, (_, n) => Math.sin(Math.PI / 2 * Math.sin(Math.PI * (n + 0.5) / (2 * N)) ** 2));

  // ---------------- FFT (radix-2 complex, in place) ----------------
  const fftT = {};
  function fftTables(n) {
    if (fftT[n]) return fftT[n];
    const lv = Math.log2(n), rev = new Uint32Array(n);
    for (let i = 0; i < n; i++) { let r = 0, x = i; for (let b = 0; b < lv; b++) { r = (r << 1) | (x & 1); x >>= 1; } rev[i] = r; }
    const cos = new Float64Array(n / 2), sin = new Float64Array(n / 2);
    for (let i = 0; i < n / 2; i++) { cos[i] = Math.cos(2 * Math.PI * i / n); sin[i] = Math.sin(2 * Math.PI * i / n); }
    return (fftT[n] = { rev, cos, sin });
  }
  function fft(re, im) {
    const n = re.length, { rev, cos, sin } = fftTables(n);
    for (let i = 0; i < n; i++) { const j = rev[i]; if (j > i) { let t = re[i]; re[i] = re[j]; re[j] = t; t = im[i]; im[i] = im[j]; im[j] = t; } }
    for (let size = 2; size <= n; size <<= 1) {
      const half = size >> 1, step = n / size;
      for (let i = 0; i < n; i += size) for (let j = 0, k = 0; j < half; j++, k += step) {
        const a = i + j, b = a + half, tr = re[b] * cos[k] + im[b] * sin[k], ti = -re[b] * sin[k] + im[b] * cos[k];
        re[b] = re[a] - tr; im[b] = im[a] - ti; re[a] += tr; im[a] += ti;
      }
    }
  }

  // ---------------- MDCT via N/2-point FFT (same algorithm as mdct_rows) ----------------
  function makeMdct(N, win) {
    const h = N / 2, re = new Float64Array(h), im = new Float64Array(h), z = new Float64Array(2 * N), r = new Float64Array(N);
    const pc = new Float64Array(h), ps = new Float64Array(h), qc = new Float64Array(h), qs = new Float64Array(h);
    for (let n = 0; n < h; n++) {
      pc[n] = Math.cos(Math.PI * (n + 0.25) / N); ps[n] = -Math.sin(Math.PI * (n + 0.25) / N);
      qc[n] = Math.cos(Math.PI * n / N); qs[n] = -Math.sin(Math.PI * n / N);
    }
    const X = new Float64Array(N);
    return function (sig, start) {            // MDCT of sig[start .. start+2N)
      for (let i = 0; i < 2 * N; i++) z[i] = sig[start + i] * win[i];
      for (let n = 0; n < h; n++) { r[n] = -z[3 * h + n] - z[3 * h - 1 - n]; r[h + n] = z[n] - z[N - 1 - n]; }
      for (let n = 0; n < h; n++) {
        const a = r[2 * n], b = r[N - 1 - 2 * n];
        re[n] = a * pc[n] - b * ps[n]; im[n] = a * ps[n] + b * pc[n];
      }
      fft(re, im);
      for (let n = 0; n < h; n++) {
        const vr = re[n] * qc[n] - im[n] * qs[n], vi = re[n] * qs[n] + im[n] * qc[n];
        X[2 * n] = vr; X[N - 1 - 2 * n] = -vi;
      }
      return X;
    };
  }

  function sparsity(X, off, lo, hi, floor) {
    let ss = 0;
    for (let i = lo; i < hi; i++) { const v = X[off + i]; ss += v * v; }
    const thr = Math.max((Math.sqrt(ss / (hi - lo)) + 1e-20) * REL, floor);
    let c = 0;
    for (let i = lo; i < hi; i++) if (Math.abs(X[off + i]) < thr) c++;
    return c / (hi - lo);
  }
  const noiseFloor = (bits, gain) => 3 * Math.pow(2, -(bits - 1)) / Math.sqrt(12) * gain;

  // ---------------- scans ----------------
  function mdctCurves(chans, starts, N, win, floor) {
    const curves = starts.map(() => new Float64Array(N));
    const lo = Math.floor(N * 0.02), hi = Math.floor(N * 0.75), mdct = makeMdct(N, win);
    for (const c of chans) starts.forEach((s, si) => {
      if (s + (FRAMES + 2) * N - 1 > c.length) return;
      const per = new Float64Array(N);
      for (let f = 0; f < FRAMES; f++) for (let o = 0; o < N; o++)
        per[o] += sparsity(mdct(c, s + f * N + o), 0, lo, hi, floor) / FRAMES;
      const sh = s % N;
      for (let o = 0; o < N; o++) curves[si][(o + sh) % N] += per[o];
    });
    return curves;
  }

  function mp3Curves(chans, starts, floor) {
    const curves = starts.map(() => new Float64Array(576));
    const nsteps = 18 * (GRANULES + 2), S = new Float64Array(nsteps * 32), Y = new Float64Array(64);
    const xr = new Float64Array(GRANULES * 576);
    for (const c of chans) starts.forEach((s, si) => {
      if (s < 600 || s + 32 * nsteps + 64 > c.length) return;
      for (let phase = 0; phase < 32; phase++) {
        // polyphase analysis: S[t][sb]
        for (let t = 0; t < nsteps; t++) {
          const idx = s + phase + 32 * t;
          for (let j = 0; j < 64; j++) {
            let y = 0;
            for (let m = 0; m < 8; m++) { const k = j + 64 * m; y += c[idx - k] * C[k]; }
            Y[j] = y;
          }
          for (let sb = 0; sb < 32; sb++) {
            let v = 0; const pr = sb * 64;
            for (let j = 0; j < 64; j++) v += POLY[pr + j] * Y[j];
            S[t * 32 + sb] = v;
          }
        }
        for (let bphase = 0; bphase < 18; bphase++) {
          for (let g = 0; g < GRANULES; g++) {
            const t0 = bphase + 18 * g;
            for (let sb = 0; sb < 32; sb++) {
              const odd = sb & 1, base = g * 576 + sb * 18;
              for (let m = 0; m < 18; m++) xr[base + m] = 0;
              for (let k = 0; k < 36; k++) {
                let v = S[(t0 + k) * 32 + sb];
                if (odd && (k & 1)) v = -v;               // frequency inversion
                if (v === 0) continue;
                const mr = k * 18;
                for (let m = 0; m < 18; m++) xr[base + m] += v * MDCT18[mr + m];
              }
            }
            // alias-reduction butterflies
            for (let sb = 1; sb < 32; sb++) for (let i = 0; i < 8; i++) {
              const ia = g * 576 + 18 * sb - 1 - i, ib = g * 576 + 18 * sb + i, A = xr[ia], B = xr[ib];
              xr[ia] = A * CS[i] + B * CA[i]; xr[ib] = B * CS[i] - A * CA[i];
            }
          }
          let sp = 0;
          for (let g = 0; g < GRANULES; g++) sp += sparsity(xr, g * 576, 8, 540, floor);
          curves[si][(s + phase + 32 * bphase) % 576] += sp / GRANULES;
        }
      }
    });
    return curves;
  }

  // ---------------- statistics ----------------
  function median(a) {
    const b = Float64Array.from(a).sort(), n = b.length;
    return n % 2 ? b[(n - 1) / 2] : (b[n / 2 - 1] + b[n / 2]) / 2;
  }
  function gridStats(curves, fold) {
    let rows = curves;
    if (fold) rows = curves.map((c) => { const f = new Float64Array(fold).fill(-Infinity); c.forEach((v, p) => { if (v > f[p % fold]) f[p % fold] = v; }); return f; });
    const active = rows.filter((r) => { let mx = -Infinity, mn = Infinity; for (const v of r) { if (v > mx) mx = v; if (v < mn) mn = v; } return mx - mn > 1e-6; });
    if (!active.length) return { agree: 0, sections: 0, z: 0, excess: 0, lift: 0 };
    const argmax = (r) => { let b = 0; for (let i = 1; i < r.length; i++) if (r[i] > r[b]) b = i; return b; };
    const counts = new Map();
    active.forEach((r) => { const w = argmax(r); counts.set(w, (counts.get(w) || 0) + 1); });
    let pos = Infinity, agree = 0;
    for (const [v, c] of counts) if (c > agree || (c === agree && v < pos)) { agree = c; pos = v; }
    const total = new Float64Array(active[0].length);
    active.forEach((r) => r.forEach((v, i) => { total[i] += v; }));
    const n = active.length, med = median(total);
    const mad = median(total.map((v) => Math.abs(v - med))) * 1.4826;
    const peak = argmax(total);
    const excess = (total[peak] - med) / n, z = (total[peak] - med) / Math.max(mad, 1e-3 * n);
    return { agree, sections: n, z: Math.round(z * 10) / 10, excess: Math.round(excess * 1e4) / 1e4, lift: total[pos] / (med + 1e-4) };
  }

  function sectionStarts(mono, count, peakSrc) {
    const blk = 4096, nb = Math.floor(mono.length / blk);
    if (nb < 8) return [];
    const rms = new Float64Array(nb), clipped = new Uint8Array(nb);
    let peak = 0;
    for (let b = 0; b < nb; b++) for (let i = b * blk; i < (b + 1) * blk; i++) { const p = Math.abs(peakSrc[i]); if (p > peak) peak = p; }
    let nclip = 0;
    for (let b = 0; b < nb; b++) {
      let ss = 0, cl = 0;
      for (let i = b * blk; i < (b + 1) * blk; i++) { ss += mono[i] * mono[i]; if (peak > 0.5 && Math.abs(peakSrc[i]) >= 0.999 * peak) cl++; }
      rms[b] = Math.sqrt(ss / blk); clipped[b] = cl > 2 ? 1 : 0; nclip += clipped[b];
    }
    const gate = Math.max(1e-4, 0.1 * median(rms));
    if (nclip / nb < 0.8) for (let b = 0; b < nb; b++) if (clipped[b]) rms[b] = 0;
    const starts = [];
    for (let i = 0; i < count; i++) {
      const f = 0.06 + (0.94 - 0.06) * i / (count - 1);
      let b = Math.floor(nb * f);
      const end = Math.min(nb - 4, b + Math.max(1, Math.floor(nb / count)));
      while (b < end && rms[b] < gate) b++;
      if (b < nb - 4 && rms[b] >= gate) starts.push(b * blk + (i * 1009 + 97) % blk);
    }
    return starts;
  }

  function decide(stats) {
    let best = null;
    for (const name of ["MP3", "AAC", "Vorbis"]) {
      const st = stats[name];
      if (!st || st.sections < 3) continue;
      const n = st.sections;
      const agreeOk = st.agree >= Math.max(3, Math.ceil(n / 2));
      const strong = st.z >= Z_MIN && st.excess >= EXCESS_MIN;
      const veryConsistent = st.agree >= Math.max(4, Math.ceil(3 * n / 4)) && st.z >= 0.6 * Z_MIN;
      if ((strong && agreeOk) || veryConsistent) if (best === null || st.z > stats[best].z) best = name;
    }
    return best;
  }
  function confidence(stats, codec) {
    if (!codec) return "";
    const st = stats[codec];
    return st.z >= 2 * Z_MIN && st.agree >= 0.6 * st.sections ? "certain" : "likely";
  }

  /** detect(left, right|null, bits, onProgress?) -> {codec, confidence, stats, sections} */
  function detect(left, right, bits, onProgress) {
    const n = left.length, R = right || left;
    const mid = new Float64Array(n), side = new Float64Array(n), pk = new Float64Array(n);
    let sideMax = 0;
    for (let i = 0; i < n; i++) {
      mid[i] = (left[i] + R[i]) / 2; side[i] = (left[i] - R[i]) / 2;
      pk[i] = Math.max(Math.abs(left[i]), Math.abs(R[i]));
      if (Math.abs(side[i]) > sideMax) sideMax = Math.abs(side[i]);
    }
    const chans = sideMax < 1e-9 ? [mid] : [mid, side];
    const all = sectionStarts(mid, 12, pk);
    const first = all.filter((_, i) => i % 2 === 0), second = all.filter((_, i) => i % 2 === 1);
    bits = Math.max(8, Math.min(32, Math.floor(bits || 16)));
    const fM = noiseFloor(bits, Math.sqrt(512)), f3 = noiseFloor(bits, MP3_GAIN);
    const W = { kbd: kbdWindow(1024), sine: sineWindow(1024), vorbis: vorbisWindow(1024) };
    const prog = (p) => { if (onProgress) onProgress(p); };

    function scan(starts, p0, p1) {
      const k = mdctCurves(chans, starts, 1024, W.kbd, fM); prog(p0 + (p1 - p0) * 0.15);
      const s = mdctCurves(chans, starts, 1024, W.sine, fM); prog(p0 + (p1 - p0) * 0.3);
      const aac = k.map((row, i) => row.map((v, j) => Math.max(v, s[i][j])));
      const vorbis = mdctCurves(chans, starts, 1024, W.vorbis, fM); prog(p0 + (p1 - p0) * 0.45);
      const mp3 = mp3Curves(chans, starts, f3); prog(p1);
      return { AAC: aac, Vorbis: vorbis, MP3: mp3 };
    }
    const statsOf = (cv) => ({ AAC: gridStats(cv.AAC), Vorbis: gridStats(cv.Vorbis, 128), MP3: gridStats(cv.MP3) });

    if (first.length < 3) return { codec: null, confidence: "", stats: {}, sections: 0 };
    let curves = scan(first, 0, 0.6);
    let stats = statsOf(curves), found = decide(stats), used = first.length;
    const borderline = Object.values(stats).some((v) => v.z >= Z_MIN / 2 || v.agree >= 2);
    if (found === null && borderline && second.length >= 3) {
      const more = scan(second, 0.6, 1);
      curves = { AAC: curves.AAC.concat(more.AAC), Vorbis: curves.Vorbis.concat(more.Vorbis), MP3: curves.MP3.concat(more.MP3) };
      stats = statsOf(curves); found = decide(stats); used += second.length;
    }
    prog(1);
    return { codec: found, confidence: confidence(stats, found), stats, sections: used };
  }

  root.CodecPrint = { detect };
  if (typeof module !== "undefined") module.exports = root.CodecPrint;
})(typeof window !== "undefined" ? window : globalThis);
