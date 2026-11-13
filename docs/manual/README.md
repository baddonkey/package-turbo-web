# Illustrated user documentation

- Open `user-manual.html` in a browser for the illustrated guide.
- Open `gallery.html` to browse all 30 screenshots at full resolution.
- Edit `user-manual.md` to update the text.

Keep the HTML files beside the `screenshots` directory when copying or sharing them. Text is English, with the German interface labels preserved.

## Regenerate

From the repository root on Windows, with the Python web dependencies and built Angular app already available:

```powershell
npm.cmd install --prefix .package-turbo-web/manual-tools --no-save --package-lock=false playwright
.\.package-turbo-web\manual-tools\node_modules\.bin\playwright.cmd install chromium
node tools/capture-manual.cjs
node tools/build-manual.cjs
```

Capture requires `samples/SIP_20260421_KOST_eCH1.3F_1a-3d-IO.zip`, which is a local sample and is not tracked by Git. The capture script creates an isolated workspace under `.package-turbo-web/manual-demo-*`, generates temporary credentials and runs a temporary server on port 7011. Keep that port free and run only one capture at a time. It leaves the live server on port 7000 and existing package workspaces alone. The demo server and browser stop at the end of the run. Demo workspaces are retained locally for inspection.

The build script generates the HTML guide, gallery and preview, and checks that every embedded screenshot exists and decodes. Playwright is a local documentation tool; it is not added to the application's dependencies. Regeneration does not publish the documentation.
