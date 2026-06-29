# Python for Android

Scripts and CI jobs for building Python for Android.

* Can be run on both Linux and macOS.
* Python 3.12 uses the legacy patched cross-build flow.
* Python 3.13+ uses CPython's official `Android/android.py` build flow.
* Creates Python installation with a structure suitable for https://github.com/flet-dev/mobile-forge.

## Usage

To build Python for a specific ABI:

```
./build.sh 3.13.12 arm64-v8a
```

To build all ABIs:

```
./build-all.sh 3.13.12
```

ABI support (all minors): `arm64-v8a`, `x86_64`, `armeabi-v7a`. The exact set
built per minor is driven by `android_abis` in the top-level `manifest.json`.

## Credits

Build process depends on:
* https://github.com/beeware/cpython-android-source-deps

Based on the work from:
* https://github.com/chaquo/chaquopy/tree/master/target
* https://github.com/beeware/Python-Android-support
* https://github.com/beeware/cpython-android-source-deps
* https://github.com/GRRedWings/python3-android
