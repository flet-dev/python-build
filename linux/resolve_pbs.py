#!/usr/bin/env python3
"""Resolve a python-build-standalone (PBS) download URL.

PBS publishes one GitHub release per build date (tag = YYYYMMDD), each containing
assets for many CPython versions/arches. Rather than hand-pinning a release date,
this finds the newest release that actually ships the exact micro version + arch we
want and prints its download URL.

Usage:
    resolve_pbs.py --version 3.13.13 --arch x86_64 --arch-ver _v2
    resolve_pbs.py --version 3.13.13 --arch aarch64            # arch-ver defaults to ""
    resolve_pbs.py --version 3.13.13 --arch x86_64 --arch-ver _v2 --release 20260510

When --release is given it is treated as an explicit override: the canonical URL for
that release is returned without hitting the API. Otherwise the GitHub releases API is
queried (authenticated with GITHUB_TOKEN/GH_TOKEN when present, to dodge rate limits).
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request

REPO = "astral-sh/python-build-standalone"
API = f"https://api.github.com/repos/{REPO}/releases"
DL = f"https://github.com/{REPO}/releases/download"

# PBS has a very long release history. A target micro that we'd ship is always recent,
# so bound the newest-first fallback scan rather than crawling every page (which both
# burns rate limit and risks gateway timeouts when the asset genuinely doesn't exist).
MAX_FALLBACK_PAGES = 5  # 5 * 100 = 500 most-recent releases


def asset_name(version: str, arch: str, arch_ver: str, release: str) -> str:
    # NOTE: the "install_only_stripped" (not "freethreaded") flavour is the one we ship.
    return (
        f"cpython-{version}+{release}-{arch}{arch_ver}"
        f"-unknown-linux-gnu-install_only_stripped.tar.gz"
    )


def _get(url: str) -> bytes:
    req = urllib.request.Request(url)
    req.add_header("Accept", "application/vnd.github+json")
    req.add_header("X-GitHub-Api-Version", "2022-11-28")
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    with urllib.request.urlopen(req, timeout=60) as resp:
        return resp.read()


def _find_in_release(release: dict, version: str, arch: str, arch_ver: str) -> str | None:
    want = asset_name(version, arch, arch_ver, release["tag_name"])
    for asset in release.get("assets", []):
        if asset.get("name") == want:
            return asset["browser_download_url"]
    return None


def resolve(version: str, arch: str, arch_ver: str) -> str:
    # Try the latest release first (the common, fast path).
    try:
        latest = json.loads(_get(f"{API}/latest"))
        url = _find_in_release(latest, version, arch, arch_ver)
        if url:
            return url
    except urllib.error.HTTPError as exc:  # pragma: no cover - network dependent
        if exc.code not in (403, 404):
            raise

    # Fall back to paging newest-first (bounded) until we find a release with the asset.
    for page in range(1, MAX_FALLBACK_PAGES + 1):
        releases = json.loads(_get(f"{API}?per_page=100&page={page}"))
        if not releases:
            break
        for release in releases:  # API returns newest-first
            url = _find_in_release(release, version, arch, arch_ver)
            if url:
                return url

    raise SystemExit(
        f"No python-build-standalone asset found for "
        f"{asset_name(version, arch, arch_ver, '<release>')} "
        f"in the {MAX_FALLBACK_PAGES * 100} most-recent releases."
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--version", required=True, help="full CPython version, e.g. 3.13.13")
    parser.add_argument("--arch", required=True, help="e.g. x86_64 or aarch64")
    parser.add_argument("--arch-ver", default="", help="microarch suffix, e.g. _v2 (default: none)")
    parser.add_argument(
        "--release",
        default="",
        help="explicit PBS release tag (YYYYMMDD); overrides auto-resolution",
    )
    args = parser.parse_args()

    if args.release:
        name = asset_name(args.version, args.arch, args.arch_ver, args.release)
        print(f"{DL}/{args.release}/{name}")
        return

    print(resolve(args.version, args.arch, args.arch_ver))


if __name__ == "__main__":
    main()
