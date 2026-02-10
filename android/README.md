# Python for Android

Scripts and CI jobs for building Python 3 for Android.

* Can be run on both Linux and macOS.
* Build Python 3.x - specific or the last minor version.
* Installs NDK r29 or use pre-installed one with path configured by `NDK_HOME` variable.
* Creates Python installation with a structure suitable for https://github.com/flet-dev/mobile-forge
* Python 3.13+ uses CPython's official `Android/android.py` build flow.

## Usage

To build Python for a specific ABI:

```
./build.sh 3.13.12 arm64-v8a
```

To build all ABIs:

```
./build-all.sh 3.13.12
```

For Python 3.13+, official CPython Android tooling currently supports `arm64-v8a` and `x86_64`.

## Credits

Build process depends on:
* https://github.com/beeware/cpython-android-source-deps

Based on the work from:
* https://github.com/chaquo/chaquopy/tree/master/target
* https://github.com/beeware/Python-Android-support
* https://github.com/beeware/cpython-android-source-deps
* https://github.com/GRRedWings/python3-android
