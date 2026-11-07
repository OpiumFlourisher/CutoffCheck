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

## How users get updates (v1.3.0 and later)

When the app opens it asks GitHub for your latest release (at most every 6 hours). If that release's version is higher than the installed one, it shows an **Update available** banner with a button that downloads the new APK. So shipping an update is just: publish a new release with a higher tag.

For this to work:
- **The repo must be public**, or GitHub won't tell the app about releases.
- **Every APK must be signed with the same key** (the four secrets). Otherwise Android refuses to install it over the old version.
- **Tags must go up**: `v1.3.0` → `v1.3.1` → `v1.4.0`. Only releases are announced; pre-releases and drafts are ignored.
- Builds from the *Actions* tab (not releases) are labelled "dev build". Uninstall a dev build before installing a release, because a dev build can have a higher internal version number than the release.

People on v1.2.0 don't have the update banner yet, so they need to install v1.3.0 by hand once.

The version shown in the app comes from the release tag automatically. The web version's label is written in `web/template.html` (`v1.4`), so bump that if you publish the web page too.

## If the build fails

Open *Actions*, click the failed run and read the red step. The usual causes are a mistyped secret, or a missing secret (in which case the APK is still built, but signed with a throwaway key; the log shows a warning).

## Changing the app

Edit `web/template.html` (the page) or `web/analysis.js` / `web/codecprint.template.js` (the checks), then run `python3 web/build.py`. The build workflow runs that script too, so the app is always built from the latest page.
