# Tronic Mini Pocket Printer - Quick Android "driver"

This is a minimal Android app that prints to the Tronic Mini Pocket Printer in two ways:

1. **System Print Service** — appears in Android’s native *Print* dialog  
2. **Share target** — appears in the system *Share / Send* sheet with a **print preview**

## What it does

- Print target name: `Tronic Mini Pocket Printer` (Print Service)
- Share target label: `Tronic Pocket Printer` (images, PDF, plain text)
- Renders pages/images to **384 px** width (48 mm @ 203 dpi)
- Optional manual MAC for experimental pairing-free mode
- Sends data over Bluetooth Classic SPP (BLE fallback) with the verified A2Y sequence:
  - `10 FF F1 03`
  - `00 x 12`
  - `1D 76 30 ... raster`
  - `1B 4A 50`
  - `10 FF F1 45`

## Build

1. Open `android-driver` in Android Studio.
2. Let Gradle sync.
3. Build and install (debug or release) on your phone.

## Build without Android Studio (Windows)

From the parent `Pocket printer` folder run:

```bat
build_android_apk_no_studio.bat
```

This downloads portable build tools into `.android-build-tools` and creates a signed APK under:

`android-driver\app\build\outputs\apk\`

## Build on GitHub Actions (no local tool install)

Workflow: `.github/workflows/build-apk.yml`

1. Push this project to GitHub.
2. **Actions** → **Build Android APK**.
3. Download artifact and install the APK.

Debug and release builds use the same project keystore under `signing/`, so APKs from CI and local builds share one signature and can upgrade each other.

### Signature / “App not installed” conflicts

Android refuses to upgrade an app signed with a **different** key. That used to happen when CI used a fresh debug key each runner.

From **0.1.6** onward, installs share one stable key. **Once**, uninstall any older build (0.1.5 and below), then install 0.1.6+. After that, later releases should update in place.

## Setup on phone

1. Pair `Mini Pocket Printer` in Android Bluetooth settings (recommended).
2. Open **Tronic Pocket Print Service**.
3. **Choose paired printer**, or enter MAC → **Save manual MAC**.
4. Enable the print service (for the system Print dialog):
   - **Settings → Connected devices → Printing** (wording varies by OEM)
   - Turn on **Tronic Pocket Print Service**

## Print

### A) Share sheet (with preview) — easiest for photos/files

1. In Gallery / Files / Chrome / … tap **Share / Send**
2. Choose **Tronic Pocket Printer**
3. Check the **preview** (already scaled to print width)
4. Tap **Print**

Supports: `image/*`, `application/pdf`, `text/plain` (and multiple images).

**Web pages (Chrome):** Share often sends only the URL. Use a **screenshot**, share a **PDF**, or system **Print** → Tronic Mini Pocket Printer — not “Share page link”.

### B) System Print dialog

From any app that supports Android print:

1. Choose **Print** (not only Share)
2. Select **Tronic Mini Pocket Printer**
3. Print

## Notes

- MVP uses monochrome thresholding (no advanced dithering yet).
- Pairing-free mode is best-effort (depends on the phone’s Bluetooth stack).
- Android 12+ will prompt for Bluetooth permission when you print / pick a device.
- Classic SPP is the reliable path; only one client should use the printer at a time
  (don’t keep a PC gateway connected if the phone should print).
