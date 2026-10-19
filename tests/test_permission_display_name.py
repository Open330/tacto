"""Permission instructions must name the bundle the person actually installed."""
import plistlib
import tempfile
import unittest
from pathlib import Path

from tacto.helper import helper_display_name
from tacto.onboard import Check, _screen_recording_for


class PermissionDisplayNameTests(unittest.TestCase):
    def test_the_installed_bundle_name_is_reported_as_is(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'Tacto Helper.app/Contents/MacOS/TactoHelper'
            binary.parent.mkdir(parents=True)
            for name in ['Tacto Helper', 'Renamed Helper']:
                with self.subTest(name=name):
                    (binary.parent.parent / 'Info.plist').write_bytes(plistlib.dumps({
                        'CFBundleDisplayName': name, 'CFBundleIdentifier': 'ai.callabo.nlc.helper'}))
                    actual = helper_display_name(binary)
                    self.assertEqual(name, actual)
                    check = _screen_recording_for(Check('screen_recording', False, '', '', ''),
                        {'built': True, 'name': actual, 'screen_capture': False})
                    self.assertIn(name, check.title)
                    self.assertIn(name, check.fix_hint)
                    self.assertFalse(check.ok)

    def test_localized_product_name_and_outer_screen_holder(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'Tacto Helper.app/Contents/MacOS/TactoHelper'
            binary.parent.mkdir(parents=True)
            contents = binary.parent.parent
            (contents / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleDisplayName':'Base Name'}))
            localized = contents / 'Resources/en.lproj'
            localized.mkdir(parents=True)
            (localized / 'InfoPlist.strings').write_bytes(plistlib.dumps({'CFBundleDisplayName':'Tacto Helper'}))
            self.assertEqual('Tacto Helper', helper_display_name(binary))
            check = _screen_recording_for(Check('screen_recording', False, '', '', ''),
                {'built':True, 'name':'Tacto Helper', 'screen_name':'Tacto', 'screen_capture':True})
            self.assertIn("'Tacto'", check.fix_hint)
            self.assertNotIn('Helper', check.fix_hint)
            self.assertTrue(check.ok)

    def test_doctor_resolves_source_and_standalone_capture_holders(self):
        from unittest.mock import patch
        from tacto.onboard import helper_status
        for standalone in (False, True):
            with self.subTest(standalone=standalone), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                runtime = root / 'Tacto.app/Contents/Resources/runtime' if standalone else root
                binary = runtime / 'helper/Tacto Helper.app/Contents/MacOS/TactoHelper'
                binary.parent.mkdir(parents=True)
                (binary.parent.parent / 'Info.plist').write_bytes(plistlib.dumps({'CFBundleDisplayName':'Tacto Helper'}))
                if standalone:
                    (root / 'Tacto.app/Contents/Info.plist').write_bytes(plistlib.dumps({'CFBundleDisplayName':'Tacto'}))
                # No binary is created, so resolving names cannot launch a helper.
                with patch('tacto.helper.HELPER', str(binary)):
                    status = helper_status()
                self.assertFalse(status['built'])
                self.assertEqual('Tacto Helper', status['name'])
                self.assertEqual('Tacto' if standalone else None, status.get('screen_name'))

    def test_bare_or_invalid_bundle_metadata_has_a_product_name(self):
        with tempfile.TemporaryDirectory() as directory:
            binary = Path(directory) / 'Tacto Helper.app/Contents/MacOS/TactoHelper'
            binary.parent.mkdir(parents=True)
            for content in [b'not a plist', plistlib.dumps([]), plistlib.dumps({'CFBundleDisplayName': 42})]:
                (binary.parent.parent / 'Info.plist').write_bytes(content)
                self.assertEqual('Tacto Helper', helper_display_name(binary))
            self.assertEqual('Tacto Helper', helper_display_name(Path(directory) / 'tacto-helper'))
