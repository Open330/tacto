"""Helper discovery must keep permission checks on the bundled native process."""
from pathlib import Path
import tempfile
import unittest
from tacto.helper import _runtime_root

class HelperRuntimePathTests(unittest.TestCase):
    def test_source_checkout_is_independent_of_interpreter_location(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder).resolve(); module=root/'source/tacto/helper.py'
            module.parent.mkdir(parents=True);module.touch()
            self.assertEqual(str(root/'source'),_runtime_root(module,root/'unrelated/.venv'))

    def test_relocated_standalone_finds_its_own_helper(self):
        with tempfile.TemporaryDirectory() as folder:
            runtime=Path(folder).resolve()/'moved/Resources/runtime'
            environment=runtime/'.venv'
            module=environment/'lib/python3.11/site-packages/tacto/helper.py'
            module.parent.mkdir(parents=True);module.touch()
            helper=runtime/'helper/Tacto Helper.app/Contents/MacOS/TactoHelper'
            helper.parent.mkdir(parents=True);helper.touch()
            self.assertEqual(str(runtime),_runtime_root(module,environment))
            helper.unlink()
            self.assertEqual(str(module.parent.parent),_runtime_root(module,environment))
            outside=Path(folder)/'outside';outside.touch();helper.symlink_to(outside)
            self.assertEqual(str(module.parent.parent),_runtime_root(module,environment))
