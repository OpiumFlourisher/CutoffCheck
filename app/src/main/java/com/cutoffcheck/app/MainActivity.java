package com.cutoffcheck.app;

import android.app.Activity;
import android.content.ActivityNotFoundException;
import android.content.ClipData;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Bundle;
import android.webkit.JavascriptInterface;
import android.webkit.ValueCallback;
import android.webkit.WebChromeClient;
import android.webkit.WebResourceRequest;
import android.webkit.WebResourceResponse;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.io.ByteArrayInputStream;
import java.io.ByteArrayOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.nio.charset.StandardCharsets;
import java.util.Collections;

/**
 * CutoffCheck shell: shows the bundled checker page (app/src/main/assets/index.html)
 * in a WebView. All analysis runs in the page's JavaScript, on the phone.
 *
 * The page is served from a fixed https address that never touches the network, so
 * workers, blob URLs and audio decoding behave exactly like in Chrome. Anything not
 * in the assets folder gets a 404: the page that reads your songs can't go online.
 *
 * The only network access is the update check below, done in Java: it downloads the
 * latest-release info from the GitHub API and passes it to the page, which shows a
 * banner if there's a newer version. It can be turned off in App settings.
 */
public class MainActivity extends Activity {

    private static final String HOST = "appassets.androidplatform.net";
    private static final String START_URL = "https://" + HOST + "/index.html";
    private static final int PICK_FILES = 1;
    private static final long CHECK_EVERY_MS = 6L * 60 * 60 * 1000;   // reuse a result for 6 hours
    private static final String PREF_CHECK = "update_check";
    private static final String PREF_LAST = "update_last_check";
    private static final String PREF_JSON = "update_last_json";

    private WebView webView;
    private ValueCallback<Uri[]> pendingPick;
    private SharedPreferences prefs;
    private volatile String updateJson = "";
    private volatile String updateState = "idle";   // idle | pending | ok | error | off

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);
        prefs = getSharedPreferences("cutoffcheck", MODE_PRIVATE);
        webView = new WebView(this);
        setContentView(webView);

        WebSettings s = webView.getSettings();
        s.setJavaScriptEnabled(true);
        s.setDomStorageEnabled(true);
        s.setAllowFileAccess(false);
        s.setAllowContentAccess(true);           // files picked by the user arrive as content:// URIs
        s.setMediaPlaybackRequiresUserGesture(true);
        s.setBuiltInZoomControls(false);

        webView.setWebViewClient(new WebViewClient() {
            @Override
            public WebResourceResponse shouldInterceptRequest(WebView view, WebResourceRequest request) {
                return serveAsset(request.getUrl());
            }

            @Override
            public boolean shouldOverrideUrlLoading(WebView view, WebResourceRequest request) {
                return !HOST.equals(request.getUrl().getHost());   // never navigate away
            }
        });

        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onShowFileChooser(WebView view, ValueCallback<Uri[]> callback,
                                             FileChooserParams params) {
                if (pendingPick != null) pendingPick.onReceiveValue(null);
                pendingPick = callback;
                Intent pick = new Intent(Intent.ACTION_OPEN_DOCUMENT);
                pick.addCategory(Intent.CATEGORY_OPENABLE);
                pick.setType("*/*");
                pick.putExtra(Intent.EXTRA_MIME_TYPES, new String[]{
                        "audio/*", "application/x-flac", "application/octet-stream"});
                pick.putExtra(Intent.EXTRA_ALLOW_MULTIPLE,
                        params.getMode() == FileChooserParams.MODE_OPEN_MULTIPLE);
                try {
                    startActivityForResult(pick, PICK_FILES);
                } catch (ActivityNotFoundException e) {
                    pendingPick = null;
                    callback.onReceiveValue(null);
                    return false;
                }
                return true;
            }
        });

        webView.addJavascriptInterface(new AppBridge(), "CutoffApp");

        if (savedInstanceState != null) {
            webView.restoreState(savedInstanceState);
        } else {
            webView.loadUrl(START_URL);
        }

        if (prefs.getBoolean(PREF_CHECK, true)) checkForUpdate(false);
        else updateState = "off";
    }

    // ------------------------------------------------------------------ updates
    private void checkForUpdate(boolean force) {
        String cached = prefs.getString(PREF_JSON, "");
        long last = prefs.getLong(PREF_LAST, 0);
        if (!force && !cached.isEmpty() && System.currentTimeMillis() - last < CHECK_EVERY_MS) {
            updateJson = cached;
            updateState = "ok";
            notifyPage();
            return;
        }
        updateState = "pending";
        new Thread(() -> {
            String json = fetchLatestRelease();
            if (json != null) {
                updateJson = json;
                updateState = "ok";
                prefs.edit().putLong(PREF_LAST, System.currentTimeMillis()).putString(PREF_JSON, json).apply();
            } else {
                updateState = "error";
            }
            runOnUiThread(this::notifyPage);
        }, "update-check").start();
    }

    private static String fetchLatestRelease() {
        HttpURLConnection c = null;
        try {
            URL url = new URL("https://api.github.com/repos/" + BuildConfig.UPDATE_REPO + "/releases/latest");
            c = (HttpURLConnection) url.openConnection();
            c.setConnectTimeout(8000);
            c.setReadTimeout(8000);
            c.setRequestProperty("Accept", "application/vnd.github+json");
            c.setRequestProperty("User-Agent", "CutoffCheck-Android");
            if (c.getResponseCode() != 200) return null;
            try (InputStream in = c.getInputStream()) {
                ByteArrayOutputStream out = new ByteArrayOutputStream();
                byte[] buf = new byte[8192];
                int n;
                while ((n = in.read(buf)) != -1) {
                    out.write(buf, 0, n);
                    if (out.size() > 2_000_000) return null;   // release info is a few KB
                }
                return new String(out.toByteArray(), StandardCharsets.UTF_8);
            }
        } catch (IOException | RuntimeException e) {
            return null;
        } finally {
            if (c != null) c.disconnect();
        }
    }

    private void notifyPage() {
        if (webView != null) {
            webView.evaluateJavascript("window.cutoffUpdateReady && window.cutoffUpdateReady()", null);
        }
    }

    private static boolean isGithubLink(Uri u) {
        String host = u.getHost();
        return "https".equals(u.getScheme()) && host != null
                && (host.equals("github.com") || host.endsWith(".github.com")
                    || host.endsWith(".githubusercontent.com"));
    }

    /** Methods the page can call as window.CutoffApp.*(). They run on a background thread. */
    public class AppBridge {
        @JavascriptInterface
        public String version() { return BuildConfig.VERSION_NAME; }

        @JavascriptInterface
        public String updateState() { return updateState; }

        @JavascriptInterface
        public String updateJson() { return updateJson; }

        @JavascriptInterface
        public boolean isUpdateCheckOn() { return prefs.getBoolean(PREF_CHECK, true); }

        @JavascriptInterface
        public void setUpdateCheck(boolean on) {
            prefs.edit().putBoolean(PREF_CHECK, on).apply();
            if (!on) {
                updateJson = "";
                updateState = "off";
            }
        }

        @JavascriptInterface
        public void checkNow() { runOnUiThread(() -> checkForUpdate(true)); }

        /** Opens a GitHub link (the new APK or the release page) in the browser. */
        @JavascriptInterface
        public void openDownload(String link) {
            final Uri u = Uri.parse(link);
            if (!isGithubLink(u)) return;
            runOnUiThread(() -> {
                try {
                    startActivity(new Intent(Intent.ACTION_VIEW, u));
                } catch (ActivityNotFoundException ignored) {
                    // no browser installed: nothing sensible to do
                }
            });
        }
    }

    private WebResourceResponse serveAsset(Uri url) {
        if (!HOST.equals(url.getHost())) return notFound();
        String path = url.getPath();
        if (path == null || path.equals("/") || path.isEmpty()) path = "/index.html";
        path = path.substring(1);
        if (path.contains("..")) return notFound();
        try {
            InputStream in = getAssets().open(path);
            return new WebResourceResponse(mimeOf(path), "utf-8", in);
        } catch (IOException e) {
            return notFound();
        }
    }

    private static WebResourceResponse notFound() {
        return new WebResourceResponse("text/plain", "utf-8", 404, "Not Found",
                Collections.<String, String>emptyMap(), new ByteArrayInputStream(new byte[0]));
    }

    private static String mimeOf(String path) {
        if (path.endsWith(".html")) return "text/html";
        if (path.endsWith(".js")) return "text/javascript";
        if (path.endsWith(".css")) return "text/css";
        if (path.endsWith(".ttf")) return "font/ttf";
        if (path.endsWith(".png")) return "image/png";
        if (path.endsWith(".svg")) return "image/svg+xml";
        return "application/octet-stream";
    }

    @Override
    protected void onActivityResult(int requestCode, int resultCode, Intent data) {
        if (requestCode != PICK_FILES) {
            super.onActivityResult(requestCode, resultCode, data);
            return;
        }
        if (pendingPick == null) return;
        Uri[] picked = null;
        if (resultCode == RESULT_OK && data != null) {
            ClipData clip = data.getClipData();
            if (clip != null && clip.getItemCount() > 0) {
                picked = new Uri[clip.getItemCount()];
                for (int i = 0; i < clip.getItemCount(); i++) picked[i] = clip.getItemAt(i).getUri();
            } else if (data.getData() != null) {
                picked = new Uri[]{data.getData()};
            }
        }
        pendingPick.onReceiveValue(picked);
        pendingPick = null;
    }

    @Override
    protected void onSaveInstanceState(Bundle outState) {
        super.onSaveInstanceState(outState);
        webView.saveState(outState);
    }

    @Override
    public void onBackPressed() {
        if (webView.canGoBack()) webView.goBack();
        else super.onBackPressed();
    }

    @Override
    protected void onDestroy() {
        if (webView != null) webView.destroy();
        super.onDestroy();
    }
}
