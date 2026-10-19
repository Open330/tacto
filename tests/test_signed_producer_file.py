"""Owned producer evaluation must be explicit and preserve private failures."""
import contextlib
import importlib.util
import io
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT=Path(__file__).resolve().parents[1]


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class ProducerEvaluationTests(unittest.TestCase):
    def test_worker_options_only_bind_the_explicit_owned_worker(self):
        module=load('examples/eval.py','owned_worker_options')
        args,selected,cases=module._parse_plan(['signed_native_journal_worker','--offline',
            '--owned-worker-config','/owned/worker-config.json','--owned-worker-phase','restart'])
        self.assertEqual({'config_path':'/owned/worker-config.json','phase':'restart'},selected[0][1].keywords)
        self.assertEqual('skipped',cases[0]['status'])
        for argv in (['signed_producer_worker_restart','--owned-worker-config','/owned','--owned-worker-phase','restart'],
                     ['signed_native_journal_worker','--owned-worker-config','/owned'],
                     ['signed_native_journal_worker','--owned-worker-phase','restart']):
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):module._parse_plan(argv)

    def test_offline_skips_native_and_cannot_mix_gui_scenarios(self):
        module=load('examples/eval.py','producer_eval')
        for scenario in ('signed_producer_file','signed_producer_file_large','signed_producer_file_reply_loss',
                         'signed_producer_worker_restart','signed_producer_worker_reconcile','signed_native_journal_worker'):
            _,selected,cases=module._parse_plan([scenario,'--offline'])
            self.assertEqual(len(selected),1)
            self.assertEqual(selected[0][0].split(' ')[0],scenario)
            self.assertTrue(cases[0]['isolated'])
            self.assertEqual(set(cases[0]['requirements']),{'gui','native'})
            self.assertEqual(cases[0]['status'],'skipped')
            with contextlib.redirect_stderr(io.StringIO()),self.assertRaises(SystemExit):
                module._parse_plan([scenario,'signed_element_click'])

    def test_public_failure_never_echoes_input_audit(self):
        module=load('examples/eval.py','producer_eval_failure')
        report={'passed':False,'input_audit':{'private':'PRIVATE_INPUT_SENTINEL'}}
        with patch('runpy.run_path',return_value={'main':Mock(return_value=report)}), \
                patch('tacto.helper.InheritedHelperDriver') as driver, \
                contextlib.redirect_stdout(io.StringIO()) as output,self.assertRaises(AssertionError) as error:
            driver.available.return_value=True
            module.scenario_signed_producer_file()
        self.assertNotIn('PRIVATE_INPUT_SENTINEL',str(error.exception))
        self.assertNotIn('PRIVATE_INPUT_SENTINEL',output.getvalue())

    def test_reconcile_scope_is_forwarded_and_not_inferred_from_passed(self):
        module=load('examples/eval.py','producer_reconcile_scope')
        report={'passed':True,'worker_reconciliation_requested':True,'native_causal_continuation_verified':True}
        main=Mock(return_value=report)
        with patch('runpy.run_path',return_value={'main':main}),patch('tacto.helper.InheritedHelperDriver') as driver, \
                contextlib.redirect_stdout(io.StringIO()):
            driver.available.return_value=True
            module.scenario_signed_producer_worker_reconcile()
            self.assertTrue(main.call_args.kwargs['journal_worker_restart'])
            self.assertTrue(main.call_args.kwargs['journal_worker_reconcile'])
            report['native_causal_continuation_verified']=False
            with self.assertRaisesRegex(AssertionError,'native continuation scope unverified'):
                module.scenario_signed_producer_worker_reconcile()

    def test_reconcile_worker_phase_stays_explicit_and_offline(self):
        module=load('examples/eval.py','reconcile_worker_options')
        _,selected,cases=module._parse_plan(['signed_native_journal_worker','--offline',
            '--owned-worker-config','/owned/worker-config.json','--owned-worker-phase','reconcile'])
        self.assertEqual('reconcile',selected[0][1].keywords['phase'])
        self.assertEqual('skipped',cases[0]['status'])

    def test_interrupt_report_is_private_exclusive_and_still_raised(self):
        module=load('examples/probes/signed_save_writer.py','producer_probe')
        audit=Mock();audit.__enter__=Mock(return_value=audit);audit.__exit__=Mock(return_value=False)
        audit.report.return_value={'status':'UNCHANGED_SAMPLED'}
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'interrupted.json'
            with patch('tacto.input_audit_process.ProcessInputAudit',return_value=audit), \
                    patch.object(module,'run',side_effect=KeyboardInterrupt),self.assertRaises(KeyboardInterrupt):
                module.main(object(),output)
            report=json.loads(output.read_text())
            self.assertFalse(report['passed']);self.assertTrue(report['interrupted'])
            self.assertTrue(report['source_subset_stable'])
            self.assertEqual(os.stat(output).st_mode&0o777,0o600)
            original=output.read_bytes()
            with self.assertRaises(FileExistsError):module.main(object(),output)
            self.assertEqual(original,output.read_bytes())


if __name__=='__main__':unittest.main()
