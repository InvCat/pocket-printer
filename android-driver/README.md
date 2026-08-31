# Tronic Mini Pocket Printer - Quick Android "driver"

This is a minimal Android Print Service app that makes the printer available in Android's native print menu.

## What it does

- Appears as `Tronic Mini Pocket Printer` in Android print targets.
- Accepts system print jobs (PDF from Android print spooler).
- Renders pages to 384 px width.
- Supports experimental no-pre-pair mode by manual MAC input.
- Sends data over Bluetooth Classic SPP with the verified A2Y sequence:
  - `10 FF F1 03`
  - `00 x 12`
  - `1D 76 30 ... raster`
  - `1B 4A 50`
  - `10 FF F1 45`

## Build

1. Open `android-driver` in Android Studio.
2. Let Gradle sync.
3. Build and install debug APK on your Android phone.

## Build without Android Studio (Windows)

From the parent `Pocket printer` folder run:

```bat
build_android_apk_no_studio.bat
```

This downloads portable build tools into `.android-build-tools` and creates:

`android-driver\app\build\outputs\apk\debug\app-debug.apk`

## Build on GitHub Actions (no local tool install)

This repository includes workflow:

`Pocket printer/.github/workflows/build-apk.yml`

How to use:
1. Push the `Pocket printer` project to a GitHub repository.
2. Open the repository's `Actions` tab.
3. Run workflow: `Build Android APK` (or push changes under `android-driver`).
4. After success, download artifact:
   `tronic-pocket-print-service-debug-apk`
5. Extract and install `app-debug.apk` on Android.

## Setup on phone

1. Recommended: pair `Mini Pocket Printer` in Android Bluetooth settings.
2. Open app `Tronic Pocket Print Service`.
3. Either:
   - `Choose paired printer`, or
   - enter manual MAC and tap `Save manual MAC (experimental)` for pairing-free attempt.
4. Enable the print service in:
   - Settings -> Connected devices -> Printing
   - Turn on `Tronic Pocket Print Service`

## Print

From any app with Android share/print:
- choose `Print`
- select `Tronic Mini Pocket Printer`
- print

## Notes

- This is a quick MVP and uses monochrome thresholding (no advanced dithering yet).
- Pairing-free mode is best-effort only (depends on phone Bluetooth stack).
- Android 12+ requires Bluetooth permission prompt in the setup app.
