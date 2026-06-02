# macOS support assets

Vendored helper assets for the from-source macOS framework build (`darwin/build_macos.py`).

## `app-store-compliance.patch`

Sourced from [beeware/Python-Apple-support](https://github.com/beeware/Python-Apple-support)
(`patch/Python/app-store-compliance.patch`). It removes the `itms-services` URL scheme from the
stdlib's `urllib.parse`, which Apple's App Store review flags. It is **optional** and applied only
when `build_macos.py --app-store-compliance` is passed (off by default — plain desktop macOS apps
don't need it).

## Not vendored (handled elsewhere)

- **module map** — the dart packagers overlay `darwin/Modules/module.modulemap`, so beeware's
  `module.modulemap.prefix` is unnecessary here.
- **framework/stdlib pruning** — handled by `darwin/python-darwin-framework.exclude` and
  `darwin/python-darwin-stdlib.exclude` in the dart packagers.
- **relocation** — beeware's `patch/make-relocatable.sh` logic is ported directly into
  `build_macos.py` (`install_name_tool` driven by `otool -L`).
