"""Correction budgets do not implicitly opt into UI verification."""
import contextlib
import io
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from tacto import cli
from tacto.flow import Flow


class CompileRoundsTests(unittest.TestCase):
    def test_correction_opt_out_preserves_no_verify_and_cleans_intent(self):
        for options,verify,rounds in (([],False,1),(['--repair-rounds','0'],False,0),
                (['--repair-rounds','2'],False,2),(['--verify-rounds','0'],True,0)):
            with tempfile.TemporaryDirectory() as directory, \
                 patch.object(cli,'_make_driver',return_value=object()), \
                 patch('tacto.authoring.author',return_value=Flow('compiled','app')) as model, \
                 contextlib.redirect_stdout(io.StringIO()):
                result=cli.main(['compile','read data',*options,'-o',str(Path(directory)/'flow.json')])
            self.assertEqual(result,0)
            self.assertEqual(model.call_args.args[0],'read data')
            self.assertEqual(model.call_args.kwargs['verify'],verify)
            self.assertEqual(model.call_args.kwargs['rounds'],rounds)

    def test_invalid_budgets_fail_before_driver_or_model(self):
        for options in (['--repair-rounds'],['--repair-rounds','-1'],['--repair-rounds','a'],
                ['--repair-rounds','0','--repair-rounds','1'],
                ['--repair-rounds','0','--verify-rounds','1']):
            with patch.object(cli,'_make_driver') as driver, \
                 patch('tacto.authoring.author') as model, contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(cli.main(['compile','read',*options]),2)
            driver.assert_not_called();model.assert_not_called()
