"""Build the CutoffCheck pages from template.html.

  web/dist/cutoffcheck.html            single-file web version
  app/src/main/assets/index.html       offline page inside the Android app

Run from the repo root:  python3 web/build.py
"""
import json, os, sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, os.path.join(ROOT, "desktop"))
import codecprint  # noqa: E402  (source of the MP3 window table)


def read(name):
    with open(os.path.join(HERE, name), encoding="utf-8") as f:
        return f.read()


def main():
    cp = read("codecprint.template.js")
    cp = cp.replace("/*__ENWINDOW__*/", json.dumps(codecprint._ENWINDOW_HALF))
    cp = cp.replace("/*__MP3GAIN__*/", repr(codecprint._MP3_GAIN))
    scripts = cp + "\n" + read("analysis.js")
    page = read("template.html").replace("/*__ANALYSIS__*/", scripts)

    os.makedirs(os.path.join(HERE, "dist"), exist_ok=True)
    with open(os.path.join(HERE, "dist", "cutoffcheck.html"), "w", encoding="utf-8") as f:
        f.write(page)

    # ---- Android: full document, bundled font, no network, app wording ----
    app = page
    font_links = app[app.index('<link rel="preconnect" href="https://fonts.googleapis.com">'):
                     app.index('<style>')]
    app = app.replace(font_links, "")
    app = app.replace('<meta name="claude:viewport-fit-sides" content="cover">\n', "")
    app = app.replace("<style>", """<style>
@font-face { font-family: "Grenze Gotisch"; font-weight: 100 900; src: url("fonts/GrenzeGotisch.ttf") format("truetype"); font-display: swap; }
@font-face { font-family: "Cinzel"; font-weight: 400 900; src: url("fonts/Cinzel.ttf") format("truetype"); font-display: swap; }
@font-face { font-family: "EB Garamond"; font-weight: 400 800; src: url("fonts/EBGaramond.ttf") format("truetype"); font-display: swap; }
[hidden] { display: none !important; }
html, body { margin: 0; }""", 1)
    swaps = [
        ('<div class="tip"><span class="tip-label">Tip:</span> in Chrome tap &#8942; &rarr; <b>Add to Home screen</b> and it opens like a real app.</div>',
         '<div class="tip"><span class="tip-label">Tip:</span> tap anywhere on a <b>spectrogram</b> to jump to that part of the song.</div>'),
        ("<p>Once the page has loaded, yes. Your songs never leave the phone either way.</p>",
         "<p>Yes. The only time the app goes online is to ask GitHub whether a newer version exists, and you can turn that off in App settings. The part that reads your songs has no network access at all.</p>"),
        ("works best in Chrome on Android", "Android app"),
    ]
    for a, b in swaps:
        if a not in app:
            sys.exit(f"build.py: expected text not found: {a[:60]}")
        app = app.replace(a, b)
    doc = ('<!doctype html>\n<html lang="en">\n<head>\n<meta charset="utf-8">\n'
           '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
           + app.replace("\n<main>", "\n</head>\n<body>\n<main>", 1) + "\n</body>\n</html>\n")
    assets = os.path.join(ROOT, "app", "src", "main", "assets")
    os.makedirs(assets, exist_ok=True)
    with open(os.path.join(assets, "index.html"), "w", encoding="utf-8") as f:
        f.write(doc)
    print("built web/dist/cutoffcheck.html and app/src/main/assets/index.html")


if __name__ == "__main__":
    main()
