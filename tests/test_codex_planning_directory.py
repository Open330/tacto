"""Owned CLI processes prove planner cwd and files; no live model calls."""
import json
from pathlib import Path
import stat
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from tacto.codex_result import codex_result, _run_codex
from tests.test_codex_result import events, stream


class PlanningDirectoryTests(unittest.TestCase):
    def test_text_request_explicitly_binds_cli_root_and_process_cwd(self):
        for observed in (False, True):
            with self.subTest(observed=observed):
                seen=[]
                def launch(argv,prompt,timeout,*args,**kwargs):
                    root=Path(argv[argv.index('--cd')+1])
                    seen.append(root)
                    self.assertEqual(str(root),kwargs['cwd'])
                    self.assertTrue(root.is_absolute())
                    self.assertEqual(0o700,stat.S_IMODE(root.stat().st_mode))
                    self.assertIn('--skip-git-repo-check',argv)
                    self.assertNotIn('--ignore-user-config',argv)
                    self.assertEqual(1,argv.count('--cd'))
                    output=Path(argv[argv.index('--output-last-message')+1])
                    self.assertEqual(root,output.parent)
                    self.assertEqual({'final.txt'},set(p.name for p in root.iterdir()))
                    output.write_text('{"done":true}')
                    return subprocess.CompletedProcess(argv,0,stream(events()),'')
                with patch('tacto.codex_result._run_codex',side_effect=launch):
                    result=codex_result('owned task',process_observations=[] if observed else None)
                self.assertEqual('ok',result.status)
                self.assertFalse(seen[0].exists())

    def test_owned_cli_receives_actual_isolated_cwd_and_literal_stdin(self):
        with tempfile.TemporaryDirectory() as directory:
            marker=Path(directory)/'received.json'
            source=('import json,pathlib,sys;args=sys.argv[1:];'
                    'out=pathlib.Path(args[args.index("--output-last-message")+1]);'
                    'pathlib.Path('+repr(str(marker))+').write_text(json.dumps({"cwd":str(pathlib.Path.cwd()),"stdin":sys.stdin.read(),"output":str(out)}));'
                    'out.write_text(\'{"done":true}\');print('+repr(stream(events()))+')')
            def launch(argv,prompt,timeout,**kwargs):
                return _run_codex([sys.executable,'-c',source,*argv[1:]],prompt,timeout,**kwargs)
            with patch('tacto.codex_result._run_codex',side_effect=launch):
                result=codex_result('owned literal $(not shell)')
            self.assertEqual('ok',result.status)
            actual=json.loads(marker.read_text())
            self.assertEqual('owned literal $(not shell)',actual['stdin'])
            self.assertEqual(Path(actual['output']).parent.resolve(),Path(actual['cwd']).resolve())
            self.assertNotEqual(Path.cwd().resolve(),Path(actual['cwd']).resolve())
            self.assertFalse(Path(actual['cwd']).exists())


if __name__ == '__main__':
    unittest.main()
