# CutoffCheck

Find out if your FLAC files are really lossless.

A lot of "lossless" music online is an MP3 or AAC file that someone converted to FLAC. It looks the same, takes up lossless-sized space, and sounds like the MP3 it came from. CutoffCheck finds those, along with fake hi-res and fake 24-bit files, right on your Android phone.

## Download

Get the latest **CutoffCheck-x.y.z.apk** from the [Releases](../../releases) page.

To install it:
1. Open the downloaded APK on your phone.
2. If Android asks, allow your browser or file manager to **install unknown apps** (it only needs this once).
3. Tap **Install**.

Needs Android 8.0 or newer.

## What it checks

| Result | Meaning |
|---|---|
| **Genuine** | Real lossless audio |
| **Lossy · MP3 / AAC / Vorbis** | A converted lossy file. This is a fake FLAC, even at 320 kbps |
| **Fake hi-res** | Labelled 88.2/96 kHz, but really 44.1/48 kHz audio. Still lossless, just bigger |
| **Fake 24-bit** | 16-bit audio padded to 24-bit. Still lossless |
| **Suspicious** | Borderline. Have a look at the spectrogram |

You also get a spectrogram for every song, and you can play the song with a line that follows the music.

## How it works

1. **Frequency cutoff.** Lossy encoders throw away high frequencies, which leaves a flat "wall" in the spectrogram. Opus always stops at exactly 20 kHz.
2. **Encoder fingerprint.** Even high-bitrate files without a cutoff can be caught. MP3, AAC and Vorbis save audio in fixed-size blocks, and many values in those blocks end up exactly zero. CutoffCheck redoes each encoder's maths at every possible block position. If one position shows far more zeros than all the others, all through the song, the file went through that encoder. Real lossless audio never has a position like that.
3. **Hi-res and bit depth.** It checks for real content above 24 kHz, and whether the lowest 8 bits of a 24-bit file are ever used.

## Privacy

Your songs never leave your phone. The screen that reads and analyses them is built into the app and has no network access at all.

The only thing the app ever connects to is GitHub, once when it opens, to ask for the version number of the latest release. If there's a newer one, you'll see an **Update available** banner. You can turn this off in **App settings** at the bottom of the app.

## Updates

When a new version is out, tap **Download update** on the banner, open the downloaded APK and tap **Update**. Your settings are kept.

## Known issues

- If a lossy file was resampled after converting (for example an MP3 turned into fake 96 kHz), only the cutoff check can catch it.
- Very quiet or very sparse recordings, and songs that clip constantly, can hide the encoder pattern.
- WMA, AC-3 and other rare codecs are only caught if they leave a cutoff.
- Big 96 kHz files use a lot of memory on older phones. Check them a few at a time.

## Other versions

- `web/`: the same checker as a single web page (`python3 web/build.py` builds it into `web/dist/`).
- `desktop/`: a Python command-line tool and desktop window for Mac and PC. See `desktop/README.md`.

## Building it yourself

```bash
python3 -m pip install numpy
python3 web/build.py            # builds the page the app shows
gradle :app:assembleRelease     # needs JDK 17, Gradle 8.7 and the Android SDK
```

Every push and every `v*` tag is built automatically by GitHub Actions (`.github/workflows/build-apk.yml`).

## Licence

MIT, see [LICENSE](LICENSE). The bundled fonts (Grenze Gotisch, Cinzel, EB Garamond) are under the SIL Open Font License.
