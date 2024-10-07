# Python for Android

Scripts and CI jobs for building Python 3 for Android.

* Can be run on both Linux and macOS.
* Build Python 3.12 - specific or the last minor version.
* Installs NDK r26d or use pre-installed one with path configured by `NDK_HOME` variable.
* Creates Python installation with a structure suitable for https://github.com/flet-dev/mobile-forge

## Usage

To build the latest minor version of Python 3.12 for selected Android API:

```
./build.sh 3.12 arm64-v8a
```

To build all ABIs:

```
./build-all.sh 3.12
```

## Credits

Build process depends on:
* https://github.com/beeware/cpython-android-source-deps

Based on the work from:
* https://github.com/chaquo/chaquopy/tree/master/target
* https://github.com/beeware/Python-Android-support
* https://github.com/beeware/cpython-android-source-deps
* https://github.com/GRRedWings/python3-android