"""Private report lifecycle, native array shape and owned callback provenance."""
import json
import importlib.util
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
from examples.probes import signed_readiness_scale as probe


class ReadinessScaleProbeTests(unittest.TestCase):
    def record(self):
        return {'nonce':'n'*32,'seq':2,'leaf_count':512,'activations':1,'A_key':True,
                'B_key':False,'A_mouse_downs':1,'B_mouse_downs':0,'route_errors':0}

    def test_callback_requires_identity_shape_and_complete_record(self):
        row=self.record();data=json.dumps(row).encode()+b'\n'
        self.assertEqual(probe.receiving_state(data+b'{"partial":','n'*32,512),row)
        self.assertIsNone(probe.receiving_state(b'{"partial":','n'*32,512))
        for updates in ({'nonce':'wrong'},{'seq':True},{'seq':0},{'leaf_count':511},
                        {'A_key':1},{'B_mouse_downs':-1},{'route_errors':10001},
                        {'private_filename':'PRIVATE'}, {'seq':1.5}):
            with self.subTest(updates=updates),self.assertRaises(ValueError):
                probe.receiving_state(json.dumps({**row,**updates}).encode()+b'\n','n'*32,512)
        with self.assertRaises(ValueError):probe.receiving_state(b' '*131073,'n'*32,512)

    def test_existing_or_symlink_report_refuses_before_runtime(self):
        with tempfile.TemporaryDirectory() as directory:
            existing=Path(directory)/'existing';existing.write_text('KEEP')
            link=Path(directory)/'link';link.symlink_to(existing)
            for output in (existing,link):
                with patch.object(probe,'_run') as run,self.assertRaises(FileExistsError):
                    probe.main(None,output)
                run.assert_not_called();self.assertEqual(existing.read_text(),'KEEP')

    def test_interrupt_keeps_partial_failed_case_and_private_file(self):
        def interrupted(driver,report,**kwargs):
            report['cases'].append({'requested_B_leaves':6000,'passed':False,'response_type':'error'})
            raise KeyboardInterrupt()
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'report.json'
            with patch.object(probe,'_run',side_effect=interrupted),self.assertRaises(KeyboardInterrupt):
                probe.main(None,output)
            report=json.loads(output.read_text())
            self.assertFalse(report['passed']);self.assertFalse(report['all_dependencies_frozen'])
            self.assertEqual(report['effect'],'unknown');self.assertEqual(report['error_class'],'KeyboardInterrupt')
            self.assertEqual(report['cases'][0]['requested_B_leaves'],6000)
            self.assertEqual(output.stat().st_mode&0o777,0o600)

    def test_unsupported_backend_is_recorded_before_fixture_launch(self):
        with tempfile.TemporaryDirectory() as directory:
            output=Path(directory)/'report.json'
            with patch.object(probe.subprocess,'Popen') as spawn,self.assertRaises(RuntimeError):
                probe.main(object(),output)
            spawn.assert_not_called();self.assertFalse(json.loads(output.read_text())['passed'])

    def test_activation_callback_waits_for_key_and_never_masks_misrouting(self):
        prior={**self.record(),'seq':1,'activations':0,'A_key':False,'A_mouse_downs':0}
        activated={**prior,'seq':2,'activations':1}
        complete={**self.record(),'seq':3}
        self.assertFalse(probe.preparation_received(activated,prior))
        ticks=[0.0];reads=[]
        states=iter([activated,complete])
        def read():
            row=next(states);reads.append(row);return row
        def sleep(seconds):ticks[0]+=seconds
        self.assertEqual(complete,probe.wait_preparation_state(read,prior,clock=lambda:ticks[0],sleep=sleep))
        self.assertEqual(2,len(reads))
        bad={**activated,'B_mouse_downs':1}
        self.assertEqual(bad,probe.wait_preparation_state(lambda:bad,prior,clock=lambda:0,sleep=lambda _:self.fail('must stop')))
        self.assertFalse(probe.preparation_received(bad,prior))
        with self.assertRaisesRegex(ValueError,'receiving_sequence_regressed'):
            probe.wait_preparation_state(lambda:{**prior,'seq':0},prior)
        self.assertEqual(activated,probe.wait_preparation_state(lambda:activated,prior,timeout=.05,
            clock=lambda:ticks[0],sleep=sleep))

    def test_unknown_audit_strategy_cannot_allocate_report_or_run_fixture(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'report.json'
            with patch.object(probe,'_run') as run,self.assertRaises(ValueError):
                probe.main(None,path,audit_strategy='unknown')
            run.assert_not_called();self.assertFalse(path.exists())

    @unittest.skipUnless(importlib.util.find_spec('ApplicationServices'),'macOS AX required')
    def test_native_arrays_reject_non_elements_and_capacity(self):
        from ApplicationServices import AXUIElementCreateApplication
        element=AXUIElementCreateApplication(91001)
        self.assertEqual(len(probe.native_elements([element])),1)
        for value in (None,'PRIVATE',{'not':'an array'},['PRIVATE'],[True],[element]*6001):
            with self.subTest(kind=type(value).__name__),self.assertRaises((ValueError,TypeError)):
                probe.native_elements(value)

    @unittest.skipUnless(shutil.which('xcrun'),'macOS Swift required')
    def test_invalid_fixture_arguments_exit_before_application_bootstrap(self):
        root=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory() as directory:
            binary=Path(directory)/'Scale'
            built=subprocess.run(['xcrun','swiftc','-warnings-as-errors','-parse-as-library',
                str(root/'tests/fixtures/NativeReadinessScaleFixture.swift'),'-o',str(binary)],
                capture_output=True,text=True,timeout=60)
            self.assertEqual(built.returncode,0,built.stderr)
            for arguments in ([],['-1','n'*32],['6001','n'*32],['512','short']):
                result=subprocess.run([str(binary),*arguments],capture_output=True,timeout=5)
                self.assertEqual(result.returncode,2)
