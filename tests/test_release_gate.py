"""Release must not turn missing, stale or broken evidence into approval."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch
import subprocess

ROOT = Path(__file__).resolve().parents[1]
with patch.object(sys, 'path', [str(ROOT/'setup'), *sys.path]):
    spec = importlib.util.spec_from_file_location('release_gate', ROOT/'setup/check_release.py')
    gate = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(gate)


class ReleaseGateTests(unittest.TestCase):
    def test_missing_wrong_candidate_or_failed_evidence_blocks(self):
        for value in (None, [], {}, {'image_sha256': 'old'},
                      {'image_sha256': 'current', 'gates': []}):
            result = gate.evidence_gates(value, 'current')
            self.assertEqual(set(gate.GATES), set(result))
            self.assertNotIn('passed', result.values())

    def test_artifact_is_bound_to_content_and_requires_review(self):
        with tempfile.TemporaryDirectory() as folder:
            artifact = Path(folder)/'result.json'; artifact.write_text('measured')
            row = {'status': 'passed', 'reviewer': 'reviewer', 'summary': 'checked result',
                   'artifacts': [{'path': str(artifact), 'sha256': gate.digest(artifact)}]}
            evidence = {'image_sha256': 'current', 'gates': {k: dict(row) for k in gate.GATES}}
            self.assertEqual({'passed'}, set(gate.evidence_gates(evidence, 'current').values()))
            evidence['gates']['fresh_install']['reviewer'] = ''
            self.assertEqual('review_details_required', gate.evidence_gates(evidence, 'current')['fresh_install'])
            artifact.write_text('changed')
            self.assertEqual('artifact_missing_or_changed', gate.evidence_gates(evidence, 'current')['real_app_tasks'])
            artifact.unlink()
            self.assertNotIn('passed', gate.evidence_gates(evidence, 'current').values())

    def test_packaged_app_mismatch_blocks_and_detaches(self):
        self._inspect(mismatch=True)

    def test_verification_failure_still_detaches(self):
        self._inspect(verify_failure=True)

    def test_detach_retries_without_hiding_original_failure(self):
        self._inspect(verify_failure=True, detach_failure=True)

    def test_assessment_is_for_both_packaged_app_and_image(self):
        self._inspect(mismatch=True)

    def _inspect(self, mismatch=False, verify_failure=False, detach_failure=False):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder).resolve(); app = root/'Tacto.app'; app.mkdir()
            image = root/'candidate.dmg'; image.write_bytes(b'image')
            mount_root = root/'mount'; mount_root.mkdir()
            volume = mount_root/'volume'
            calls = []
            def run(argv):
                calls.append(argv)
                if 'attach' in argv:
                    (volume/'Applications').symlink_to('/Applications')
                    import unicodedata
                    (volume/unicodedata.normalize('NFD', '먼저 읽어주세요.txt')).write_text('guide')
                failed = '--assess' in argv or 'stapler' in argv or ('detach' in argv and detach_failure)
                return subprocess.CompletedProcess(argv, int(failed), b'', b'')
            def verify(path):
                if verify_failure and path == volume/'Tacto.app':
                    raise ValueError('invalid signature')
            def manifest(path):
                return {'file': 'changed' if mismatch and path == volume/'Tacto.app' else 'same'}
            with patch.object(gate, 'command', side_effect=run), \
                 patch.object(gate, 'verify_app', side_effect=verify), \
                 patch.object(gate, 'app_manifest', side_effect=manifest), \
                 patch.object(gate.tempfile, 'mkdtemp', return_value=str(mount_root)), \
                 patch.object(gate.os.path, 'ismount', return_value=detach_failure):
                result = gate.inspect(app, image)
                if verify_failure:
                    self.assertEqual('check_failed', result['status'])
                    self.assertEqual({'stage': 'verify_packaged', 'error_class': 'ValueError'}, result['inspection_error'])
                else:
                    self.assertEqual('blocked', result['status'])
                    self.assertIn('packaged_app_matches', result['blockers'])
                    for key in ('packaged_gatekeeper_accepted', 'packaged_notarization_ticket',
                                'image_gatekeeper_accepted', 'image_notarization_ticket'):
                        self.assertIn(key, result['blockers'])
                    self.assertTrue(any(str(volume/'Tacto.app') in c and '--assess' in c for c in calls))
                    self.assertTrue(any(str(image.resolve()) in c and '--assess' in c for c in calls))
                self.assertIn(['/usr/bin/hdiutil', 'detach', str(volume)], calls)
                if detach_failure:
                    self.assertIn(['/usr/bin/hdiutil', 'detach', '-force', str(volume)], calls)
                    self.assertFalse(result['automated_checks']['image_detached'])
                    self.assertEqual(str(volume), result['retained_mountpoint'])
