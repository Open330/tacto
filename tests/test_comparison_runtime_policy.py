"""Both arms receive the host's timeout and background task constraints."""
import json
import subprocess
import sys
import tempfile
import types
import unittest
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

from tests.test_product_comparison_provenance import comparison


class RuntimePolicy(unittest.TestCase):
    def test_both_layers_get_the_same_timeout_and_background_constraint(self):
        class Witness:
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def summary(self): return {'initial':'owned.sentinel', 'activations':[]}
        prompts = []
        def run(argv, prompt, timeout, records):
            prompts.append((argv, prompt, timeout))
            Path(argv[argv.index('-o')+1]).write_text('owned result')
            return subprocess.CompletedProcess(argv, 0, '', '')
        task = comparison.Task('owned-read', 'Read only the target app.', lambda a:(True,'owned oracle'))
        manifest = {'version':2,'sha256':'a'*64,'files':{}}
        with patch.dict(sys.modules, {'focus_witness':types.SimpleNamespace(FocusWitness=Witness)}), \
             patch.object(comparison, 'source_manifest', return_value=manifest), \
             patch.object(comparison, '_locked', return_value=False), \
             patch('nlc.helper.InheritedHelperDriver.available', return_value=True), \
             patch.object(comparison, '_run_agent', side_effect=run):
            for arm in ('nlc','sky'):
                result = comparison.run_one(task, arm, timeout=27)
                self.assertEqual(27, result['timeout_seconds'])
                self.assertEqual('background-v2', result['runtime_policy'])
                self.assertEqual(comparison.BACKGROUND_CONSTRAINT, result['background_constraint'])
                self.assertFalse(result['comparison_eligible'])
        for argv, prompt, timeout in prompts:
            self.assertEqual('-',argv[-1])
            self.assertEqual(27,timeout)
            self.assertIn(comparison.BACKGROUND_CONSTRAINT,prompt)
            self.assertIn('Do not activate',prompt)
        self.assertNotIn('--helper',prompts[1][1])
        self.assertIn('--helper',prompts[0][1])

    def test_main_passes_timeout_to_every_arm_and_preserves_not_run(self):
        def outcome(task,arm,timeout):
            self.assertEqual(23,timeout)
            return {'task':task.name,'arm':arm,'status':'not_run','usage':None,
                    'seconds':0,'activations':[],'observed':'owned limitation',
                    'comparison_eligible':False}
        with tempfile.TemporaryDirectory() as folder, \
             patch.object(comparison,'ROOT',Path(folder)), \
             patch.object(sys,'argv',['comparison','--only','appearance_mode','--timeout','23']), \
             patch('nlc.helper.InheritedHelperDriver.available',return_value=True), \
             patch.object(comparison,'_locked',return_value=False), \
             patch.object(comparison,'run_one',side_effect=outcome) as run, \
             redirect_stdout(StringIO()):
            self.assertEqual(2,comparison.main())
            records=list(Path(folder).rglob('*.jsonl'))
            self.assertEqual(1,len(records))
            self.assertEqual(2,len(records[0].read_text().splitlines()))
            self.assertEqual(0o600, records[0].stat().st_mode & 0o777)
            self.assertEqual({'nlc','sky'},{json.loads(s)['arm'] for s in records[0].read_text().splitlines()})
        self.assertEqual(2,run.call_count)

    def test_invalid_timeout_never_starts_model_or_fixture(self):
        for value in ('0','-1'):
            with patch.object(sys,'argv',['comparison','--timeout',value]), \
                 patch.object(comparison,'run_one') as run, \
                 redirect_stderr(StringIO()), self.assertRaises(SystemExit) as error:
                comparison.main()
            self.assertEqual(2,error.exception.code)
            run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
