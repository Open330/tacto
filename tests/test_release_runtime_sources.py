"""Installed-runtime evidence must observe implementation and legacy shim changes."""
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from examples import benchmark_runtime_latency, release_identity


class RuntimeSourceTests(unittest.TestCase):
    def package(self, root, name='tacto', version='python3.11', *, checkout=False):
        package = root/name if checkout else root/'.venv/lib'/version/'site-packages'/name
        package.mkdir(parents=True)
        (package/'__init__.py').write_bytes(b'init')
        (package/'driver.py').write_bytes(b'driver')
        return package

    def test_installed_evidence_tracks_both_packages_and_nested_modules(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            implementation = self.package(root)
            legacy = self.package(root, 'nlc')
            nested = implementation/'nested/module.py'
            nested.parent.mkdir(); nested.write_bytes(b'nested')
            launcher = root/'launcher/tacto'
            launcher.parent.mkdir(); launcher.write_bytes(b'launcher')
            with patch.object(release_identity.subprocess, 'run') as codesign, \
                 patch.object(benchmark_runtime_latency, 'code_hash', return_value='test-codehash'):
                codesign.return_value.stderr = 'CDHash=test-codehash\n'
                def snapshots():
                    return (release_identity.identity(launcher)['module_sha256'],
                            benchmark_runtime_latency.manifest(launcher)['python_sources'])
                before = snapshots()
                self.assertEqual(before[0], before[1])
                self.assertIn('nested/module.py', before[0])
                for path, key in ((implementation/'driver.py', 'driver.py'),
                                  (legacy/'__init__.py', 'nlc/__init__.py'),
                                  (nested, 'nested/module.py')):
                    path.write_bytes(b'changed')
                    after = snapshots()
                    for old, new in zip(before, after):
                        self.assertNotEqual(old[key], new[key])
                    before = after

    def test_checkout_and_legacy_only_runtime_remain_observable(self):
        for checkout in (False, True):
            for name in ('tacto', 'nlc'):
                with self.subTest(checkout=checkout, name=name), tempfile.TemporaryDirectory() as directory:
                    root = Path(directory)
                    self.package(root, name, checkout=checkout)
                    self.assertIn('driver.py', release_identity.python_source_hashes(root))
                    if checkout:
                        with self.assertRaises(ValueError):
                            release_identity.python_source_hashes(root, bundled_only=True)

    def test_missing_ambiguous_or_malformed_implementation_is_not_a_valid_shim(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaises(ValueError):
                release_identity.python_source_hashes(root)
            implementation = self.package(root)
            self.package(root, 'nlc')
            (implementation/'__init__.py').unlink()
            with self.assertRaises(ValueError):
                release_identity.python_source_hashes(root)
            (implementation/'__init__.py').write_bytes(b'init')
            self.package(root, version='python3.12')
            with self.assertRaises(ValueError):
                release_identity.python_source_hashes(root)
