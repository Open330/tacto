"""The distribution exposes one runtime package and one command."""
import json
import subprocess
import sys
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

class CanonicalNamespaceTests(unittest.TestCase):
    def test_distribution_exports_only_the_tacto_runtime_and_command(self):
        data=tomllib.loads((ROOT/'pyproject.toml').read_text())
        self.assertEqual({'tacto':'tacto.cli:main'},data['project']['scripts'])
        self.assertEqual(['tacto'],data['tool']['setuptools']['packages'])

    def test_import_is_lightweight_and_does_not_register_alias_finders(self):
        result=subprocess.run([sys.executable,'-B','-c',
            'import json,sys; before=list(sys.meta_path); import tacto; '
            'print(json.dumps({"unchanged":before==sys.meta_path,"native":any(m in sys.modules for m in ("AppKit","Quartz","Foundation")),"runtime":tacto.__tacto_runtime__}))'],
            cwd=ROOT,capture_output=True,text=True,timeout=20)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual({'unchanged':True,'native':False,'runtime':True},json.loads(result.stdout))

if __name__=='__main__':unittest.main()
