"""Cross-process digest reuse, invalidation and private cache failure handling."""
import hashlib
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin' and shutil.which('swiftc'), 'macOS Swift runtime')
class BuildKeyCacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.build = tempfile.TemporaryDirectory(prefix='tacto-key-cache-test-')
        root = Path(cls.build.name)
        source = root/'main.swift'
        source.write_text('''import Foundation
let binary=URL(fileURLWithPath:CommandLine.arguments[1])
let value = CommandLine.arguments[3] == "plain" ? TactoMode.buildKey(binary)
    : TactoMode.cachedBuildKey(binary,cacheRoot:CommandLine.arguments[2])
print(value ?? "unavailable")
''')
        cls.probe = root/'probe'
        result = subprocess.run(['swiftc', str(source), str(ROOT/'HelperModeProtocol.swift'),
                                 str(ROOT/'launcher/BuildKeyCache.swift'), '-o', str(cls.probe)],
                                capture_output=True, text=True, timeout=60)
        if result.returncode:
            cls.build.cleanup()
            raise AssertionError(result.stderr)

    @classmethod
    def tearDownClass(cls):
        cls.build.cleanup()

    def key(self, binary, cache, *, plain=False):
        return subprocess.check_output([str(self.probe),str(binary),str(cache),
                                        'plain' if plain else 'cached'],text=True,timeout=10).strip()

    def test_cross_process_hit_is_not_used_by_the_helpers_uncached_hash(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); binary=root/'binary'; cache=root/'cache'
            binary.write_bytes(b'owned-test-executable')
            expected=hashlib.sha256(binary.read_bytes()).hexdigest()
            self.assertEqual(expected,self.key(binary,cache))
            entry,=cache.glob('*.sha256')
            self.assertEqual(0o700,cache.stat().st_mode & 0o777)
            self.assertEqual(0o600,entry.stat().st_mode & 0o777)
            # A valid private routing hint proves a separate process reads the
            # cache, while the helper's uncached build key remains independent.
            entry.write_text('0'*64)
            self.assertEqual('0'*64,self.key(binary,cache))
            self.assertEqual(expected,self.key(binary,cache,plain=True))
            entry.write_text('truncated')
            self.assertEqual(expected,self.key(binary,cache))
            self.assertEqual(expected,entry.read_text())

    def test_same_size_mtime_restoration_and_atomic_replacement_invalidate(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); binary=root/'binary'; cache=root/'cache'
            binary.write_bytes(b'old-build')
            self.assertEqual(hashlib.sha256(b'old-build').hexdigest(),self.key(binary,cache))
            before=binary.stat()
            binary.write_bytes(b'new-build')
            os.utime(binary,ns=(before.st_atime_ns,before.st_mtime_ns))
            after=binary.stat()
            self.assertEqual(before.st_ino,after.st_ino)
            self.assertEqual(before.st_mtime_ns,after.st_mtime_ns)
            self.assertEqual(hashlib.sha256(b'new-build').hexdigest(),self.key(binary,cache))
            replacement=root/'replacement';replacement.write_bytes(b'end-build')
            os.utime(replacement,ns=(before.st_atime_ns,before.st_mtime_ns));replacement.replace(binary)
            self.assertNotEqual(before.st_ino,binary.stat().st_ino)
            self.assertEqual(hashlib.sha256(b'end-build').hexdigest(),self.key(binary,cache))

    def test_symlink_hardlink_and_unsafe_directory_fall_back_without_touching_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder); binary=root/'binary'; cache=root/'cache'
            binary.write_bytes(b'owned-build'); expected=hashlib.sha256(binary.read_bytes()).hexdigest()
            self.assertEqual(expected,self.key(binary,cache));entry,=cache.glob('*.sha256')
            outside=root/'outside';outside.write_text('0'*64);outside.chmod(0o600)
            for make in (lambda:entry.symlink_to(outside),lambda:os.link(outside,entry)):
                entry.unlink();make()
                self.assertEqual(expected,self.key(binary,cache))
                self.assertEqual('0'*64,outside.read_text())
                self.assertFalse(entry.is_symlink())
            cache.chmod(0o755)
            self.assertEqual(expected,self.key(binary,cache))
            cache.chmod(0o700)
            link=root/'cache-link';link.symlink_to(cache,target_is_directory=True)
            self.assertEqual(expected,self.key(binary,link))
            self.assertFalse((cache/'binary').exists())

    def test_old_entries_are_bounded_without_removing_unrelated_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder);cache=root/'cache';cache.mkdir(mode=0o700)
            sentinel=cache/'notes.txt';sentinel.write_text('KEEP')
            outside=root/'outside';outside.write_text('KEEP')
            link=cache/('a'*64+'.sha256');link.symlink_to(outside)
            for index in range(67):
                binary=root/f'binary-{index}';binary.write_bytes(str(index).encode())
                self.assertEqual(hashlib.sha256(binary.read_bytes()).hexdigest(),self.key(binary,cache))
            regular=[p for p in cache.glob('*.sha256') if not p.is_symlink()]
            self.assertLessEqual(len(regular),64)
            self.assertEqual('KEEP',sentinel.read_text());self.assertEqual('KEEP',outside.read_text())
            self.assertTrue(link.is_symlink())
