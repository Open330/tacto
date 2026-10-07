"""Private image capture and explicit, read-only image-provider boundary."""
import json
from pathlib import Path
import stat
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from nlc.visual import VisualCapture, VisualError, capture_browser, query_visual

try:
    import playwright.sync_api
    WEB = True
except ImportError:
    WEB = False


class VisualProvider(unittest.TestCase):
    def setUp(self):
        self.capture = VisualCapture('v1', 'b1', 'page', 'digest', 'state',
                                     {'width': 20, 'height': 20}, 1, b'\x89PNG\r\n\x1a\nprivate pixels')

    @staticmethod
    def events(text):
        return '\n'.join(json.dumps(event) for event in [
            {'type':'thread.started','thread_id':'test'}, {'type':'turn.started'},
            {'type':'item.completed','item':{'id':'final','type':'agent_message','text':text}},
            {'type':'turn.completed','usage':{'input_tokens':10,'output_tokens':2}}])

    def test_metadata_and_private_explicit_artifact(self):
        self.assertNotIn('png', self.capture.to_dict())
        self.assertNotIn('private pixels', repr(self.capture))
        with tempfile.TemporaryDirectory() as directory:
            path = self.capture.save(Path(directory) / 'image.png')
            self.assertEqual(0o600, stat.S_IMODE(path.stat().st_mode))
            self.assertEqual(b'\x89PNG\r\n\x1a\nprivate pixels', path.read_bytes())
            with self.assertRaises(FileExistsError):
                self.capture.save(path)

    def test_pinned_image_command_and_no_authority(self):
        roots = []
        def run(argv, prompt, timeout):
            self.assertEqual(['codex', 'exec'], argv[:2])
            self.assertIn('--ignore-user-config', argv)
            self.assertEqual('read-only', argv[argv.index('--sandbox') + 1])
            self.assertEqual('chosen-model', argv[argv.index('--model') + 1])
            self.assertEqual('-', argv[-1])
            image = Path(argv[argv.index('--image') + 1])
            roots.append(image.parent)
            self.assertEqual(b'\x89PNG\r\n\x1a\nprivate pixels', image.read_bytes())
            Path(argv[argv.index('--output-last-message') + 1]).write_text('{"answer":"blue square"}')
            return subprocess.CompletedProcess(argv, 0, self.events(Path(argv[argv.index('--output-last-message') + 1]).read_text()), '')
        with patch('nlc.codex_result._run_codex', side_effect=run):
            result = query_visual(self.capture, 'What color?', model='chosen-model')
        self.assertEqual('blue square', result['answer'])
        self.assertEqual('visual_read', result['evidence_kind'])
        self.assertFalse(result['commit_witness'])
        self.assertFalse(result['authorizes_action'])
        self.assertFalse(roots[0].exists())

    def test_unsupported_and_failed_provider_never_fallback(self):
        with patch('nlc.codex_result._run_codex') as run:
            with self.assertRaisesRegex(VisualError, 'unsupported_visual_provider'):
                query_visual(self.capture, 'read', provider='unsupported-provider')
            run.assert_not_called()
            run.return_value = subprocess.CompletedProcess([], 1, '', '')
            with self.assertRaisesRegex(VisualError, 'visual_provider_failed'):
                query_visual(self.capture, 'read')
            self.assertEqual(1, run.call_count)

    def test_local_timeout_kills_process_group(self):
        from nlc.visual import _run_visual
        from unittest.mock import MagicMock
        process = MagicMock()
        process.pid = 12345
        process.communicate.side_effect = subprocess.TimeoutExpired('codex', 1)
        with patch('nlc.visual.subprocess.Popen') as popen, \
             patch('nlc.visual.os.killpg') as killpg, \
             patch('nlc.visual.os.name', 'posix'):
            popen.return_value.__enter__.return_value = process
            with self.assertRaises(subprocess.TimeoutExpired):
                _run_visual(['codex'], input='read', timeout=1, cwd='/tmp')
        self.assertTrue(popen.call_args.kwargs['start_new_session'])
        killpg.assert_called_once()
        self.assertEqual(12345, killpg.call_args.args[0])
        process.wait.assert_called_once()
        self.assertEqual(1, process.communicate.call_count)

    def test_invalid_provider_answer_is_not_accepted(self):
        def run(argv, prompt, timeout):
            Path(argv[argv.index('--output-last-message') + 1]).write_text('{"answer":"ok","authorize":true}')
            return subprocess.CompletedProcess(argv, 0, self.events(Path(argv[argv.index('--output-last-message') + 1]).read_text()), '')
        with patch('nlc.codex_result._run_codex', side_effect=run):
            with self.assertRaisesRegex(VisualError, 'invalid_visual_response'):
                query_visual(self.capture, 'read')


@unittest.skipUnless(WEB, 'optional playwright dependency not installed')
class BrowserVisual(unittest.TestCase):
    def setUp(self):
        from nlc.browser import BrowserDriver
        self.driver = BrowserDriver()
        self.addCleanup(self.driver.close)
        self.driver.goto("data:text/html,<canvas id='c' width='80' height='80'></canvas><script>c.getContext('2d').fillRect(0,0,80,80)</script>")
        self.obs = self.driver.observe()['obs_id']

    def test_canvas_image_capture(self):
        capture = capture_browser(self.driver, self.obs)
        self.assertTrue(capture.png.startswith(b'\x89PNG\r\n\x1a\n'))
        self.assertGreater(len(capture.png), 100)
        self.assertEqual(self.obs, capture.obs_id)
        self.assertEqual('best_effort_dom_scroll', capture.to_dict()['consistency'])
        json.dumps(capture.to_dict())

    def test_stale_and_navigated_observations_refused(self):
        with self.assertRaisesRegex(VisualError, 'stale_observation'):
            capture_browser(self.driver, 'wrong')
        self.driver._page.goto('data:text/html,new document')
        with self.assertRaisesRegex(VisualError, 'stale_document'):
            capture_browser(self.driver, self.obs)

    def test_dom_scroll_and_observation_changes_during_capture_refused(self):
        page = self.driver._page
        original = page.screenshot
        def changed(**kwargs):
            data = original(**kwargs)
            page.evaluate("document.body.setAttribute('data-changed','yes')")
            return data
        with patch.object(page, 'screenshot', side_effect=changed):
            with self.assertRaisesRegex(VisualError, 'page_changed_during_capture'):
                capture_browser(self.driver, self.obs)
        def reobserve(**kwargs):
            data = original(**kwargs)
            self.driver.observe()
            return data
        with patch.object(page, 'screenshot', side_effect=reobserve):
            with self.assertRaisesRegex(VisualError, 'stale_observation'):
                capture_browser(self.driver, self.obs)

    def test_scroll_change_during_capture_refused(self):
        page = self.driver._page
        page.evaluate("document.body.style.height='10000px'")
        original = page.screenshot
        def scroll(**kwargs):
            data = original(**kwargs)
            page.evaluate('scrollTo(0,100)')
            return data
        with patch.object(page, 'screenshot', side_effect=scroll):
            with self.assertRaisesRegex(VisualError, 'page_changed_during_capture'):
                capture_browser(self.driver, self.obs)


class VisualAnswerShapeTests(unittest.TestCase):
    """An invalid visual answer says how it was invalid — never what it said."""
    def test_each_malformation_has_its_own_closed_code(self):
        from nlc.visual import VisualError, _visual_answer
        cases = {'```json\n{"answer":"x"}\n```': 'markdown_fence', 'not json': 'json_decode',
                 '[1]': 'root_not_object', '{"action":"stop"}': 'bare_action_object',
                 '{"answer":"x","why":"y"}': 'unexpected_keys',
                 '{"answer":{"action":"stop"}}': 'answer_is_object', '{"answer":5}': 'answer_not_string'}
        for raw, shape in cases.items():
            with self.assertRaises(VisualError) as caught:
                _visual_answer(raw)
            self.assertEqual('invalid_visual_response', str(caught.exception))
            self.assertEqual(shape, caught.exception.shape, raw)
            self.assertNotIn('stop', shape)

    def test_the_prompt_shows_the_exact_envelope(self):
        import inspect
        from nlc import visual
        self.assertIn('Do not use Markdown fences', inspect.getsource(visual))
        envelope = json.dumps({'answer': json.dumps({'action': 'stop'})})
        self.assertEqual({'action': 'stop'}, json.loads(visual._visual_answer(envelope)))
