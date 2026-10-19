"""A text question to Codex carries no tool schemas; an image question is unchanged."""
import json
from pathlib import Path
import stat
import subprocess
import unittest
from unittest.mock import patch

from tacto.codex_result import codex_result, _TEXT_ONLY_DISABLED


def stream():
    return '\n'.join(json.dumps(e) for e in [
        {'type': 'thread.started', 'thread_id': 't'}, {'type': 'turn.started'},
        {'type': 'item.completed', 'item': {'id': 'a', 'type': 'agent_message', 'text': 'OK'}},
        {'type': 'turn.completed', 'usage': {'input_tokens': 10, 'output_tokens': 1}}])


class TextOnlyCodexTests(unittest.TestCase):
    def launch(self, seen):
        def run(argv, prompt, timeout, *, cwd=None):
            seen.append(list(argv))
            if '-c' in argv:
                spec = next(a for a in argv if a.startswith('model_instructions_file='))
                path = Path(json.loads(spec.split('=', 1)[1]))
                seen.append(('instructions', path.read_text(), stat.S_IMODE(path.stat().st_mode)))
            Path(argv[argv.index('--output-last-message') + 1]).write_text('OK')
            return subprocess.CompletedProcess(argv, 0, stream(), '')
        return run

    def test_text_question_disables_tools_but_keeps_user_model_settings(self):
        seen = []
        with patch('tacto.codex_result._run_codex', side_effect=self.launch(seen)):
            self.assertEqual('ok', codex_result('q').status)
        argv = seen[0]
        pairs = {argv[i + 1] for i, a in enumerate(argv) if a == '-c'}
        for feature in ('shell_tool', 'unified_exec', 'computer_use', 'browser_use', 'plugins'):
            self.assertIn(f'features.{feature}=false', pairs)
        self.assertEqual(len(_TEXT_ONLY_DISABLED), sum(p.startswith('features.') for p in pairs))
        self.assertIn('web_search="disabled"', pairs)
        # The user's config.toml (their model and effort) is still loaded.
        self.assertNotIn('--ignore-user-config', argv)
        kind, text, mode = seen[1]
        self.assertIn('Do not use tools', text)
        self.assertEqual(0o600, mode)
        self.assertEqual('-', argv[-1])

    def test_image_question_keeps_its_own_invocation(self):
        seen = []
        with patch('tacto.codex_result._run_codex', side_effect=self.launch(seen)), \
                patch('tacto.provider_images.encode_image'), patch('tacto.provider_images.validate_model'):
            codex_result('q', image_bytes=b'png', model='m')
        argv = seen[0]
        self.assertNotIn('-c', argv)
        self.assertIn('--image', argv)


if __name__ == '__main__':
    unittest.main()
