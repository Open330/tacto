"""The progress journal survives pending replies without storing UI contents."""
import contextlib
import importlib.util
import io
import json
import os
import subprocess
import sys
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock,patch
try:
    from tests._artifacts import requires_signed_build
except ImportError:  # discovered with tests/ as the top level
    from _artifacts import requires_signed_build

ROOT=Path(__file__).resolve().parents[1]


def load(path,name):
    spec=importlib.util.spec_from_file_location(name,ROOT/path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


class FinderStageTests(unittest.TestCase):
    @requires_signed_build
    def test_hard_worker_exit_before_or_during_request_keeps_directory_identity(self):
        script=r'''
import importlib.util,os,sys
from pathlib import Path
from unittest.mock import Mock,patch
spec=importlib.util.spec_from_file_location('finder_probe',sys.argv[1])
module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
folder=Path(sys.argv[2]);folder.mkdir()
audit=Mock();audit.__enter__=Mock(return_value=audit);audit.__exit__=Mock(return_value=False)
driver=Mock();driver.act.side_effect=lambda *a,**k:os._exit(19)
def exit_worker(staged,*args,**kwargs):
 if sys.argv[4]=='dispatch':staged.act('o','com.apple.finder',{'name':'open_folder','path':str(folder)})
 os._exit(19)
with patch.object(module,'background_precondition',return_value={'ready':True,'reason':'background_target'}),patch.object(module.tempfile,'mkdtemp',return_value=str(folder)),patch('tacto.agent.Agent') as agent,patch('tacto.input_audit_process.ProcessInputAudit',return_value=audit),patch('tacto.flow.replay',side_effect=exit_worker):
 agent.return_value.authorize.return_value=({},[])
 module.main(driver,Path(sys.argv[3]))
'''
        with tempfile.TemporaryDirectory() as directory:
            for mode in ('before','dispatch'):
                with self.subTest(mode=mode):
                    folder=Path(directory)/('owned-'+mode);output=Path(directory)/(mode+'.json')
                    result=subprocess.run([sys.executable,'-c',script,str(ROOT/'examples/probes/signed_finder_preparation.py'),str(folder),str(output),mode],cwd=ROOT,capture_output=True,text=True,timeout=30)
                    self.assertEqual(19,result.returncode,result.stderr)
                    self.assertEqual(b'',output.read_bytes())
                    path=output.with_suffix('.ownership.json');record=json.loads(path.read_text())
                    self.assertEqual(str(folder.resolve()),record['directory'])
                    self.assertEqual(folder.stat().st_dev,record['device'])
                    self.assertEqual(folder.stat().st_ino,record['inode'])
                    self.assertFalse(record['authorizes_input']);self.assertFalse(record['window_ownership_verified'])
                    self.assertEqual(0o600,path.stat().st_mode&0o777)
                    stages=[json.loads(line) for line in output.with_suffix('.stages.jsonl').read_text().splitlines()]
                    owned=[row for row in stages if row['stage']=='owned_fixture']
                    self.assertEqual(1,len(owned))
                    self.assertEqual('ready',owned[0]['phase'])
                    if mode=='dispatch':
                        self.assertEqual('open_folder',stages[-1]['stage'])
                        self.assertEqual('begin',stages[-1]['phase'])

    def test_ownership_record_is_exclusive_and_symlink_is_not_an_owned_directory(self):
        module=load('examples/probes/signed_finder_preparation.py','finder_ownership')
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'owned';folder.mkdir();output=Path(directory)/'report.json'
            path,_=module.persist_owned_directory(folder,output);original=path.read_bytes()
            with self.assertRaises(FileExistsError):module.persist_owned_directory(folder,output)
            self.assertEqual(original,path.read_bytes())
            alias=Path(directory)/'alias';alias.symlink_to(folder,target_is_directory=True)
            with self.assertRaises(RuntimeError):module.persist_owned_directory(alias,Path(directory)/'alias-report.json')
            self.assertFalse((Path(directory)/'alias-report.ownership.json').exists())

    @requires_signed_build

    def test_displayed_directory_is_not_deleted_without_window_close_proof(self):
        module=load('examples/probes/signed_finder_preparation.py','finder_cleanup')
        audit=Mock();audit.__enter__=Mock(return_value=audit);audit.__exit__=Mock(return_value=False)
        audit.report.return_value={'status':'UNCHANGED_SAMPLED'}
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)/'owned';folder.mkdir();output=Path(directory)/'report.json'
            driver=Mock();driver.act.return_value={'type':'acted'}
            def replay(staged,*args,**kwargs):
                staged.act('o','com.apple.finder',{'name':'open_folder','path':str(folder)})
                return SimpleNamespace(ok=False,events=[],preparation_diagnostics=[])
            with patch.object(module,'background_precondition',return_value={'ready':True,'reason':'background_target'}), \
                    patch.object(module.tempfile,'mkdtemp',return_value=str(folder)), \
                    patch('tacto.input_audit_process.ProcessInputAudit',return_value=audit), \
                    patch('tacto.agent.Agent') as agent,patch('tacto.flow.replay',side_effect=replay):
                agent.return_value.authorize.return_value=({},[])
                report=module.main(driver,output)
            self.assertTrue(folder.exists())
            self.assertEqual(report['cleanup'],'owned_directory_retained_without_window_close_proof')
            self.assertTrue(report['open_folder_attempted']);self.assertFalse(report['passed'])

    def test_metadata_only_journal_is_flushed_on_begin_and_exception(self):
        module=load('examples/probes/signed_finder_preparation.py','finder_stages')
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'stages.jsonl'
            with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'w') as journal:
                def observe(*args,**kwargs):
                    self.assertEqual(json.loads(path.read_text())['phase'],'begin')
                    return {'type':'observation','tree':'PRIVATE_TREE','element_count':12,'truncated':True}
                driver=Mock();driver.observe.side_effect=observe
                driver.act.side_effect=RuntimeError('PRIVATE_DRIVER_ERROR')
                staged=module.StageDriver(driver,journal)
                staged.observe('PRIVATE_TARGET')
                with self.assertRaises(RuntimeError):
                    staged.act('o','PRIVATE_TARGET',{'name':'PRIVATE_ACTION'})
            text=path.read_text();rows=[json.loads(line) for line in text.splitlines()]
            self.assertEqual([r['sequence'] for r in rows],[1,2,3,4])
            self.assertEqual(rows[-1]['phase'],'error')
            self.assertEqual(rows[-1]['stage'],'other_action')
            self.assertNotIn('PRIVATE',text)
            self.assertEqual(path.stat().st_mode&0o777,0o600)

    def test_observation_metrics_are_closed_numeric_metadata(self):
        module=load('examples/probes/signed_finder_preparation.py','finder_metrics')
        valid={'packet_attempts':5,'packet_successes':4,'packet_fallbacks':1,
               'secondary_action_reads':3}
        invalid=[{**valid,'packet_attempts':True},
                 {**valid,'packet_attempts':2501},
                 {**valid,'packet_successes':5},
                 {**valid,'PRIVATE_TREE':'PRIVATE_CONTENT'},
                 {**valid,'packet_fallbacks':-1}]
        for metrics in [valid,*invalid]:
            with self.subTest(metrics=metrics):
                journal=io.StringIO()
                driver=Mock();driver.observe.return_value={
                    'type':'observation','observation_metrics':metrics}
                with patch.object(module.os,'fsync'),patch.object(journal,'fileno',return_value=0):
                    module.StageDriver(driver,journal).observe('PRIVATE_TARGET')
                rows=[json.loads(line) for line in journal.getvalue().splitlines()]
                if metrics==valid:self.assertEqual(rows[-1]['observation_metrics'],valid)
                else:self.assertNotIn('observation_metrics',rows[-1])
                self.assertNotIn('PRIVATE',journal.getvalue())

    def test_windowless_refusal_codes_are_recorded_without_arbitrary_details(self):
        module=load('examples/probes/signed_finder_preparation.py','finder_error_codes')
        for error,reason,expected in [('indicator_unavailable','menu_item_not_on_screen',True),
                                      ('PRIVATE_ERROR','PRIVATE_REASON',False)]:
            journal=io.StringIO();driver=Mock()
            driver.observe.return_value={'type':'error','error':error,'reason':reason,
                                         'effect':'not_started','detail':'PRIVATE_WINDOW_CONTENT'}
            with patch.object(module.os,'fsync'),patch.object(journal,'fileno',return_value=0):
                module.StageDriver(driver,journal).observe('PRIVATE_TARGET',require_window=False)
            row=json.loads(journal.getvalue().splitlines()[-1])
            if expected:
                self.assertEqual(error,row['error']);self.assertEqual(reason,row['reason'])
            else:
                self.assertNotIn('error',row);self.assertNotIn('reason',row)
            self.assertNotIn('PRIVATE',journal.getvalue())

    def test_window_listing_diagnostic_is_journalled_without_contents(self):
        module=load('examples/probes/signed_finder_preparation.py','finder_window_listing')
        valid={'version':1,'status':'ok','ax_error':0,'raw_count':2,
               'role_window_count':1,'role_unavailable_count':0,'role_transient_count':0,'filtered_count':1,'badge_count':0}
        for listing in [valid,{**valid,'PRIVATE_TREE':'PRIVATE_CONTENT'}]:
            journal=io.StringIO();driver=Mock()
            driver.wait_until.return_value={'type':'error','error':'no_window_content',
                                           'window_listing_diagnostic':listing}
            with patch.object(module.os,'fsync'),patch.object(journal,'fileno',return_value=0):
                module.StageDriver(driver,journal).wait_until('PRIVATE_TARGET',Mock())
            end=json.loads(journal.getvalue().splitlines()[-1])
            if listing==valid:self.assertEqual(end['window_listing_diagnostic'],valid)
            else:self.assertNotIn('window_listing_diagnostic',end)
            self.assertNotIn('PRIVATE',journal.getvalue())

    @requires_signed_build

    def test_interruption_keeps_report_and_journal_and_does_not_replace_them(self):
        module=load('examples/probes/signed_finder_preparation.py','finder_interrupt')
        audit=Mock();audit.__enter__=Mock(return_value=audit);audit.__exit__=Mock(return_value=False)
        audit.report.return_value={'status':'UNCHANGED_SAMPLED'}
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'report.json';folder=Path(directory)/'owned';folder.mkdir()
            with patch.object(module,'background_precondition',return_value={'ready':True,'reason':'background_target'}), \
                    patch.object(module.tempfile,'mkdtemp',return_value=str(folder)), \
                    patch('tacto.input_audit_process.ProcessInputAudit',return_value=audit), \
                    patch('tacto.agent.Agent') as agent,patch('tacto.flow.replay',side_effect=KeyboardInterrupt), \
                    self.assertRaises(KeyboardInterrupt):
                agent.return_value.authorize.return_value=({},[])
                module.main(Mock(),output)
            report=json.loads(output.read_text())
            self.assertFalse(report['passed']);self.assertEqual(report['error_class'],'KeyboardInterrupt')
            self.assertEqual(report['cleanup'],'left_as_is');self.assertTrue(folder.exists())
            original=output.read_bytes()
            with self.assertRaises(FileExistsError):module.main(Mock(),output)
            self.assertEqual(output.read_bytes(),original)

    def test_isolated_offline_plan_and_private_failure_summary(self):
        module=load('examples/eval.py','finder_eval')
        _,selected,cases=module._parse_plan(['signed_finder_preparation','--offline'])
        self.assertEqual(len(selected),1);self.assertEqual(cases[0]['status'],'skipped')
        self.assertTrue(cases[0]['isolated'])
        report={'passed':False,'input_audit':{'value':'PRIVATE_AUDIT'}}
        with patch('runpy.run_path',return_value={'main':Mock(return_value=report)}), \
                patch('tacto.helper.InheritedHelperDriver') as driver, \
                contextlib.redirect_stdout(io.StringIO()),self.assertRaises(AssertionError) as error:
            driver.available.return_value=True;module.scenario_signed_finder_preparation()
        self.assertNotIn('PRIVATE_AUDIT',str(error.exception))
