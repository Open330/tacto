"""What a test needs that a fresh checkout does not have, as explicit skips.

A test that drives the signed launcher or helper, or a real headless browser,
cannot run before those exist. Skipping only when they are absent keeps a
present-but-broken build a failure.
"""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SIGNED_BUILD = ('launcher/tacto', 'helper/Tacto Helper.app/Contents/MacOS/TactoHelper', 'helper/tacto-helper')


def missing_signed_build():
    return [path for path in SIGNED_BUILD if not (ROOT / path).exists()]


requires_signed_build = unittest.skipIf(
    bool(missing_signed_build()),
    'signed build not present (' + ', '.join(missing_signed_build()) + '): run helper/build.sh, '
    'helper/bundle.sh and the launcher build')

requires_playwright = unittest.skipUnless(
    importlib.util.find_spec('playwright') is not None,
    'playwright not installed: uv sync --all-extras')
