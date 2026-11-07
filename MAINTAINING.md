# Publishing CutoffCheck on GitHub

## One-time setup

1. **Create the repository** on GitHub (for example `cutoffcheck`, public).
2. **Upload everything in this folder** to it. On the repo page: *Add file → Upload files*, drag the contents in, then *Commit changes*. Or with git:
   ```bash
   git init && git add . && git commit -m "CutoffCheck v1.2.0"
   git branch -M main
   git remote add origin https://github.com/YOUR-NAME/cutoffcheck.git
   git push -u origin main
   ```
3. **Add the four signing secrets.** On the repo: *Settings → Secrets and variables → Actions → New repository secret*. The names and values are in `SECRETS.txt` from the separate signing-key download:
   - `KEYSTORE_BASE64`
   - `KEYSTORE_PASSWORD`
   - `KEY_ALIAS`
   - `KEY_PASSWORD`

   Keep the signing key private and backed up. Every future update must be signed with the same key, or phones will refuse to install it over the old version. Anyone who has the key can publish updates that look like they're from you.

## Every release

1. On GitHub: *Releases → Draft a new release*.
2. *Choose a tag* → type a new version like `v1.2.0` → *Create new tag*.
3. Give it a title and click *Publish release*.
4. Open the *Actions* tab. After about 3–5 minutes the build finishes and **CutoffCheck-1.2.0.apk** appears on the release page.

Use a higher number for each release (`v1.2.1`, `v1.3.0`...). Every build (including pushes to `main`) also uploads the APK under *Actions → the run → Artifacts*, which is handy for testing before you publish.

## If the build fails

Open *Actions*, click the failed run and read the red step. The usual causes are a mistyped secret, or a missing secret (in which case the APK is still built, but signed with a throwaway key; the log shows a warning).

## Changing the app

Edit `web/template.html` (the page) or `web/analysis.js` / `web/codecprint.template.js` (the checks), then run `python3 web/build.py`. The build workflow runs that script too, so the app is always built from the latest page.
