# CutoffCheck

Checks whether FLAC files are genuine. It flags lossy transcodes (fake FLACs made from MP3/AAC/Opus), fake hi-res (upsampled 44.1/48 kHz audio), and fake 24-bit (padded 16-bit).

## Setup (macOS)

```bash
brew install ffmpeg
pip3 install numpy matplotlib
```

## Mac app (double-click, no terminal)

Put `flaccheck.py`, `codecprint.py`, `flaccheck_app.py` and `build_mac.sh` in one folder, then once:

```bash
cd path/to/flaccheck
bash build_mac.sh
mv "dist/CutoffCheck.app" /Applications/
```

Open **CutoffCheck** from Applications or Launchpad. Drag files or folders onto the window (or use Add Files / Add Folder), then click a row to see its spectrogram. The colours mean green = genuine, orange = fake hi-res, fake 24-bit or suspicious, red = lossy.

If macOS warns that the app is from an unidentified developer, right-click it → **Open** once.

To run the window without building: `python3 flaccheck_app.py`

## Command-line usage

```bash
python3 flaccheck.py ~/Downloads/song.flac               # one file
python3 flaccheck.py ~/Downloads                         # whole folder (recursive)
python3 flaccheck.py ~/Downloads --png ~/Desktop/specs   # also save spectrogram + profile PNGs
python3 flaccheck.py ~/Downloads --csv report.csv        # CSV report
```

## Verdicts

| Verdict | Meaning |
|---|---|
| GENUINE LOSSLESS | Full bandwidth, no lossy cutoff |
| GENUINE HI-RES | Real, sustained content above 25 kHz |
| FAKE HI-RES (upsampled from 44.1/48 kHz) | Still lossless, just inflated sample rate |
| FAKE 24-BIT (padded 16-bit) | Lowest 8 bits are always zero |
| LOSSY TRANSCODE [MP3 / AAC / Vorbis fingerprint, or cutoff] | Went through a lossy encoder, even at 320 kbps |
| SUSPICIOUS | Grey zone: open the PNG and judge by eye |

## How it works

1. **Decode** with ffmpeg to 32-bit integer PCM.
2. **Bit depth:** if a 24-bit file always has its lowest 8 bits at zero, it's padded 16-bit.
3. **Frequency cutoff:** a 95th-percentile spectrum of the loud passages shows where content ends and how sharp the edge is. A flat brick wall below ~20 kHz is a lossy encoder's lowpass. A hard 40+ dB drop at exactly 20 kHz is Opus.
4. **Encoder fingerprint** (`codecprint.py`), which catches 320 kbps / V0 / AAC 256 files with *no* cutoff. MP3, AAC and Vorbis store audio as quantised transform coefficients in fixed blocks, and many coefficients are exactly zero. Re-running the codec's own transform (MP3's hybrid polyphase+MDCT filterbank; AAC's 1024-point MDCT with sine/KBD windows; Vorbis's MDCT) at every possible block alignment, those zeros reappear at exactly one alignment. Genuine audio has no such alignment. A file is flagged only if one alignment stands out strongly overall **and** wins in at least half of the song's sections.
5. **Hi-res:** for 88.2 kHz and above, it checks for real content above 25 kHz and estimates the true source rate.

## Accuracy on the test set

| Material | Result |
|---|---|
| Genuine files (normal, volume-changed, resampled, 24-bit, quiet piano, mono speech, sparse electronic, clipped master, noise) | 14 / 14 correct, no false alarms |
| MP3 128 → 320 kbps and V0, including with the lowpass disabled (normal music) | all caught, codec identified |
| AAC 256 / 320 kbps with no lowpass, at 44.1 and 48 kHz, volume-changed (normal music) | all caught, codec identified |
| Vorbis q10, Opus 256 kbps | caught |
| Fake hi-res (upsampled 44.1/48 → 96 kHz) | 2 / 2 |

## Known limits

- **Resampled after decoding** (e.g. MP3 → upsampled to 96 kHz): the block pattern is destroyed, so only the cutoff check can catch it.
- **Very quiet, very sparse or constantly clipping recordings** saved as 16-bit: the codec's zeros get buried in rounding noise or destroyed by clipping. In testing, lossy versions of quiet piano, sparse electronic, mono speech and a heavily clipped master were missed.
- **Codecs without a fingerprint detector** (WMA, AC-3, ATRAC…) are only caught by their cutoff.
