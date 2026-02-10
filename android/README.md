# Python for Android

Scripts and CI jobs for building Python 3.13 for Android.

* Can be run on both Linux and macOS.
* Builds Python 3.13.x only.
* Creates Python installation with a structure suitable for https://github.com/flet-dev/mobile-forge
* Uses CPython's official `Android/android.py` build flow.

## Usage

To build Python for a specific ABI (`arm64-v8a` or `x86_64`):

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
