import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from tacto.preferences import load_preferences, DEFAULTS
from tacto.access_log import AccessLog, recent_logs


class PreferencesTests(unittest.TestCase):
    def test_defaults_do_not_create_files_and_only_supported_types_are_applied(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'Tacto'
            self.assertEqual(DEFAULTS, load_preferences(root))
            self.assertFalse(root.exists())
            root.mkdir()
            for value in [{'logging_enabled':'false','retention_days':True,'show_control_banner':0},
                          {'schema':2,'logging_enabled':False}, ['invalid']]:
                (root/'preferences.json').write_text(json.dumps(value))
                self.assertEqual(DEFAULTS, load_preferences(root))
            (root/'preferences.json').write_text(json.dumps(dict(schema=1,logging_enabled=False,retention_days=7,present_first_result=False,show_control_banner=False)))
            self.assertFalse(load_preferences(root)['logging_enabled'])
            self.assertNotIn('present_first_result',load_preferences(root))
            self.assertFalse(load_preferences(root)['show_control_banner'])
            self.assertEqual(7, load_preferences(root)['retention_days'])

    def test_disabled_logging_creates_no_receipt_or_log_directory(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)/'Tacto'; root.mkdir()
            (root/'preferences.json').write_text('{"schema":1,"logging_enabled":false}')
            log = AccessLog('observe', root=root/'Logs')
            log.event('access_start', operation='observe'); log.finish(0); log.close()
            self.assertIsNone(log.path)
            self.assertFalse((root/'Logs').exists())

    def test_retention_setting_prunes_old_receipts_but_preserves_unrelated_files(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'Tacto'; logs=root/'Logs'; logs.mkdir(parents=True)
            old=logs/('tacto-20260101T000000000000Z-'+'1'*32+'.jsonl'); old.write_text('{}\n')
            stamp=time.time()-10*86400; os.utime(old,(stamp,stamp))
            sentinel=logs/'notes.json'; sentinel.write_text('KEEP')
            (root/'preferences.json').write_text('{"schema":1,"retention_days":90}')
            log=AccessLog('observe',root=logs); log.finish(0); log.close()
            self.assertTrue(old.exists())
            (root/'preferences.json').write_text('{"schema":1,"retention_days":7}')
            log=AccessLog('observe',root=logs); log.finish(0); log.close()
            self.assertFalse(old.exists()); self.assertEqual('KEEP',sentinel.read_text())

    def test_symlink_and_oversized_settings_are_ignored_without_reading_target(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)/'Tacto'; root.mkdir(); outside=Path(folder)/'outside'
            outside.write_text('{"logging_enabled":false}')
            (root/'preferences.json').symlink_to(outside)
            self.assertEqual(DEFAULTS,load_preferences(root))
            (root/'preferences.json').unlink(); (root/'preferences.json').write_text(' '*17000)
            self.assertEqual(DEFAULTS,load_preferences(root))
