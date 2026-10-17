"""Real browser dispatch and disk readback through the reusable host adapter."""
from pathlib import Path
import tempfile
import unittest
from urllib.parse import quote

try:
    import playwright.sync_api
    WEB = True
except ImportError:
    WEB = False


@unittest.skipUnless(WEB, 'optional playwright dependency not installed')
class ArtifactBrowser(unittest.TestCase):
    def test_save_actual_bytes_noop_and_corruption_have_distinct_outcomes(self):
        from tacto.artifact_receipt import FreshFileReceipt
        from tacto.browser import BrowserDriver
        from tacto.contract import Resource, SafetyContract, ContextBinding, issue_approval
        from tacto.flow import Flow, Selector, Step, replay
        from tacto.trace import TracingDriver

        driver = BrowserDriver()
        self.addCleanup(driver.close)
        current = {}
        def save():
            current['calls'] += 1
            if current['mode'] != 'noop':
                current['path'].write_bytes(b'hello' if current['mode'] == 'normal' else b'wrong')
        driver._page.expose_function('saveFixture', save)
        html = '<button onclick="saveFixture()">Save</button>'
        for mode in ('normal', 'noop', 'corrupt'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory(prefix='tacto-file-proof-') as folder:
                directory = Path(folder).resolve()
                current.update(mode=mode, calls=0, path=directory / 'note.txt')
                receipt = FreshFileReceipt(directory, 'note.txt', 'hello', timeout_seconds=0.2)
                # Expose binding callbacks on the real driver and verify the
                # live tracing wrapper preserves them without serializing proof.
                driver.prepare_commit = receipt.prepare_commit
                driver.after_commit = receipt.after_commit
                driver.commit_expected = receipt.commit_expected
                driver.goto('data:text/html,' + quote(html))
                flow = Flow('save-fixture', 'page', [Step('click', Selector(role='Button', title='Save'),
                    safety=SafetyContract(effects=['write_persistent'],
                        resource=Resource(object_type='file', object_identity='note.txt', scope='item'),
                        reversibility='recoverable'),
                    binding=ContextBinding(bundle_id=driver.identity('page'), operation='click'))])
                traced = TracingDriver(driver)
                approved = issue_approval(flow.steps[0].binding, flow.steps[0].safety)
                result = replay(traced, flow, approvals={0: approved})
                self.assertEqual(1, current['calls'], result.log)
                self.assertEqual(mode == 'normal', result.ok, result.log)
                if mode == 'normal':
                    self.assertEqual(b'hello', current['path'].read_bytes())
                    self.assertEqual(1, len(result.commit_evidence))
                else:
                    self.assertEqual('unverified', result.log[-1]['status'])
                    self.assertFalse(result.commit_evidence)
                self.assertEqual(1, sum(e['kind'] == 'act' for e in traced.trace.events))


if __name__ == '__main__':
    unittest.main()
