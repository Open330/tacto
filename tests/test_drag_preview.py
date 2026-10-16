"""Drag image packaging boundary tests; no signing, disk image or GUI calls."""
import importlib.util
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('drag_builder',ROOT/'setup/build_drag_preview.py')
builder=importlib.util.module_from_spec(spec);spec.loader.exec_module(builder)

class DragPreviewTests(unittest.TestCase):
    def test_existing_or_symlink_image_refuses_before_source_or_tools(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();image=root/'preview.dmg';image.write_bytes(b'KEEP')
            link=root/'link.dmg';link.symlink_to(root/'missing')
            with patch.object(builder,'app_manifest',side_effect=AssertionError('must not read app')),patch.object(builder,'run') as tool:
                for path in (image,link):
                    with self.assertRaises(FileExistsError):builder.build(root/'absent.app',path)
                tool.assert_not_called()
            self.assertEqual(b'KEEP',image.read_bytes());self.assertTrue(link.is_symlink())

    def test_nonprivate_or_noncanonical_output_parent_refuses(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();public=root/'public';public.mkdir(mode=0o755)
            link=root/'alias';link.symlink_to(root,target_is_directory=True)
            for output in (public/'preview.dmg',link/'preview.dmg'):
                with self.assertRaises(ValueError):builder.build(root/'absent.app',output)
                self.assertFalse(output.exists())

    def test_source_symlink_escape_and_broken_links_are_refused(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();app=root/'owned.app';app.mkdir()
            outside=root/'PRIVATE';outside.write_text('outside')
            link=app/'escape';link.symlink_to(outside)
            with self.assertRaises(ValueError):builder.app_manifest(app)
            link.unlink();link.symlink_to(root/'missing')
            with self.assertRaises(ValueError):builder.app_manifest(app)
            link.unlink();(app/'file').write_text('owned');link.symlink_to('file')
            self.assertEqual({'symlink':'file'},builder.app_manifest(app)['escape'])

    def test_failure_preserves_private_reserved_output_without_tool_execution(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve();output=root/'preview.dmg'
            with patch.object(builder.platform,'system',return_value='Darwin'),patch.object(builder,'run') as tool:
                with self.assertRaises(FileNotFoundError):builder.build(root/'absent.app',output)
                tool.assert_not_called()
            self.assertEqual(b'',output.read_bytes());self.assertEqual(0o600,stat.S_IMODE(output.stat().st_mode))
