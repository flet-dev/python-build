"""Shared helpers for android/tests/.

The python-build CI invokes `unittest discover -s android/tests` twice
per matrix shard (3.12.12, 3.13.12, 3.14.3) — once before `build-all.sh`
and once after — passing different env vars each time:

    Pre-build step:   PYTHON_VERSION_SHORT=<ver>  MOBILE_FORGE_TEST_PHASE=pre_build
    Post-build step:  PYTHON_VERSION_SHORT=<ver>  MOBILE_FORGE_TEST_PHASE=post_build
                                                  MOBILE_FORGE_INSTALL_TREE=<workspace>/android/install

Tests opt INTO a phase via `@pre_build` or `@post_build`; tests opt into a specific
Python version via `@requires_python`. Decorators are stdlib `unittest.skipUnless` wrappers.

Example:

    from _testlib import pre_build, post_build, requires_python

    @pre_build
    class RelocatorRegressionTests(unittest.TestCase):
        # Source-only logic; runs in the pre-build phase.
        ...

    @post_build
    class BuiltInstallTreeShape(unittest.TestCase):
        # Inspects MOBILE_FORGE_INSTALL_TREE; runs in the post-build phase.
        ...

    @post_build
    @requires_python("3.12")
    class TestLibpython312SonameInBuiltTree(unittest.TestCase):
        # Composable; runs only on the 3.12 shard's post-build phase.
        ...
"""

import os
import unittest

PYTHON_VERSION_SHORT: str = os.environ.get("PYTHON_VERSION_SHORT", "")

# Whether `pre_build` or `post_build`
PHASE: str = os.environ.get("MOBILE_FORGE_TEST_PHASE", "")

# Set by the workflow's post-build test step to the freshly-built install
# tree, so `@post_build` tests can inspect actual generated artifacts.
INSTALL_TREE: str = os.environ.get("MOBILE_FORGE_INSTALL_TREE", "")


def requires_python(*versions: str):
    """Skip unless PYTHON_VERSION_SHORT is one of `versions`."""
    return unittest.skipUnless(
        PYTHON_VERSION_SHORT in versions,
        f"requires PYTHON_VERSION_SHORT in {versions} "
        f"(got PYTHON_VERSION_SHORT={PYTHON_VERSION_SHORT or '<unset>'!r})",
    )


def pre_build(cls_or_func):
    """Run only during the pre-build test phase.

    Pre-build tests exercise source-only logic and don't need a built install tree.
    The workflow invokes this phase before the build step.
    """
    return unittest.skipUnless(
        PHASE in ("", "pre_build"),
        f"requires test phase 'pre_build' "
        f"(got MOBILE_FORGE_TEST_PHASE={PHASE or '<unset>'!r})",
    )(cls_or_func)


def post_build(cls_or_func):
    """Run only during the post-build test phase.

    Post-build tests inspect the actual generated `install/` tree — sysconfigdata shape,
    dart-tarball ELF structure, etc. The workflow invokes this phase after build step
    succeeds, with `MOBILE_FORGE_INSTALL_TREE` pointing at the install dir.
    """
    return unittest.skipUnless(
        PHASE in ("", "post_build"),
        f"requires test phase 'post_build' "
        f"(got MOBILE_FORGE_TEST_PHASE={PHASE or '<unset>'!r})",
    )(cls_or_func)
