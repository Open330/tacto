import contextlib
import io
import json
import os
from pathlib import Path
import tempfile
import sys
import unittest
from unittest.mock import Mock, patch

from tacto.command_help import calculator_example
from tacto.flow_batch import run_batch, RequestLines, BatchDriver
from tests.test_flow_safety_defaults import Calculator

# The signed helper is macOS-only; Windows batches run without --helper.
HELPER = [] if sys.platform == 'win32' else ['--helper']


class SignedCalculator(Calculator):
    def observe(self, *args, **kwargs):
        return super().observe(*args)


class BatchTests(unittest.TestCase):
    def execute(self, records, *, args=None, doc=None, runner=None):
        from tacto.cli import _run_flow
        with tempfile.TemporaryDirectory() as tmp:
            src=Path(tmp)/'flow.json';src.write_text(json.dumps(doc or calculator_example()))
            app=SignedCalculator();app.close=Mock();make=Mock(return_value=app);messages=[]
            read,write=os.pipe()
            try:
                os.write(write,records.encode());os.close(write);write=None
                with os.fdopen(read) as stream,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()), \
                     patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()), \
                     patch('tacto.cli._make_driver',side_effect=AssertionError('extra connection')):
                    result=run_batch([str(src),*HELPER]+(args or []),make_driver=make,
                                     run_flow=runner or _run_flow,stdin=stream,emit=messages.append)
            finally:
                if write is not None:os.close(write)
        return result,app,make,messages

    def test_multiple_jobs_reuse_driver_but_observe_and_verify_each_time(self):
        code,app,make,messages=self.execute('{"params":{}}\n'*3,args=['--max-jobs','3'])
        self.assertEqual(0,code);self.assertEqual(1,make.call_count);app.close.assert_called_once()
        results=[json.loads(s[len('TACTO_BATCH_RESULT '):]) for s in messages if s.startswith('TACTO_BATCH_RESULT ')]
        self.assertEqual([1,2,3],[r['job'] for r in results]);self.assertEqual('408',app.value)
        self.assertGreater(app.observations,3)
        self.assertNotIn('tree','\n'.join(messages))
        self.assertEqual(0,json.loads(messages[-1][len('TACTO_BATCH_DONE '):])['completed']-3)
    def test_the_helper_flag_follows_the_platform(self):
        # Required on macOS, refused on Windows, before any connection.
        make=Mock();messages=[]
        for platform,args,code in (('darwin',[],2),('win32',['--helper'],2)):
            with patch('tacto.flow_batch.sys.platform',platform):
                self.assertEqual(code,run_batch(['calc_example',*args],make_driver=make,run_flow=Mock(),
                                                stdin=io.StringIO(),emit=messages.append,load_recipe=Mock()))
        make.assert_not_called()

    def test_windows_reads_requests_without_select(self):
        read,write=os.pipe()
        os.write(write,b'{"params":{}}\n');os.close(write)
        with patch('tacto.flow_batch.select.select',side_effect=OSError('sockets only')):
            reader=RequestLines(read,platform='win32')
            self.assertEqual('{"params":{}}',reader.read(2));self.assertIsNone(reader.read(2))
        os.close(read)

    def test_a_saved_name_repeats_the_recipe_replay_would_run(self):
        from tacto.cli import _run_flow
        from tacto.flow import Flow
        loaded=[]
        def load(name):
            loaded.append(name);return Flow.from_json(json.dumps(calculator_example()))
        app=SignedCalculator();app.close=Mock();make=Mock(return_value=app);messages=[]
        read,write=os.pipe();os.write(write,b'{"params":{}}\n'*2);os.close(write)
        with os.fdopen(read) as stream,contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()), \
             patch('tacto.cli._indicator_for',return_value=contextlib.nullcontext()):
            code=run_batch(['calc_example',*HELPER,'--max-jobs','2'],make_driver=make,run_flow=_run_flow,
                           stdin=stream,emit=messages.append,load_recipe=load)
        self.assertEqual((0,['calc_example'],1),(code,loaded,make.call_count))
        self.assertEqual(2,sum(m.startswith('TACTO_BATCH_RESULT ') for m in messages))
        # A path (or a .json name) is still read as a file, never as a recipe name.
        for path in ('/missing/flow.json','flow.json'):
            messages.clear()
            self.assertEqual(2,run_batch([path,*HELPER],make_driver=make,run_flow=_run_flow,
                                         stdin=io.StringIO(),emit=messages.append,load_recipe=load))
            self.assertTrue(messages[0].startswith('invalid_flow'));self.assertEqual(['calc_example'],loaded)
        # An unknown name is refused before any connection.
        missing=Mock(side_effect=FileNotFoundError('no such recipe'))
        self.assertEqual(2,run_batch(['nope',*HELPER],make_driver=make,run_flow=_run_flow,
                                     stdin=io.StringIO(),emit=messages.append,load_recipe=missing))
        self.assertEqual(1,make.call_count)

    def test_parameter_jobs_bind_independently_without_mutating_defaults(self):
        doc=calculator_example();doc['params']={'expected':'408'}
        doc['steps'][-1]['postcondition']['equals']='{{expected}}'
        seen=[]
        def runner(flow,*args,**kwargs):seen.append(flow.steps[-1].postcondition.equals);return 0
        code,app,make,msg=self.execute('{"params":{"expected":"1"}}\n{"params":{}}\n',doc=doc,runner=runner)
        self.assertEqual(0,code);self.assertEqual(['1','408'],seen)
    def test_failure_and_unknown_completion_never_execute_next_buffered_job(self):
        for runner in (Mock(return_value=1),Mock(side_effect=TimeoutError('PRIVATE_TEXT'))):
            code,app,make,msg=self.execute('{"params":{}}\n'*3,runner=runner)
            self.assertEqual(1,code);self.assertEqual(1,runner.call_count);app.close.assert_called_once()
            self.assertNotIn('PRIVATE_TEXT','\n'.join(msg))
    def test_invalid_shape_or_incomplete_record_never_dispatches(self):
        for line in ('{"params":{"value":null}}\n','{"params":{},"flow":{}}\n','{"params":{}}','PRIVATE_TEXT\n'):
            runner=Mock(return_value=0)
            code,app,make,msg=self.execute(line,runner=runner)
            self.assertEqual(2,code);runner.assert_not_called();self.assertNotIn('PRIVATE_TEXT','\n'.join(msg))
    def test_job_limit_does_not_execute_queued_extra_records(self):
        runner=Mock(return_value=0)
        code,app,make,msg=self.execute('{"params":{}}\n'*4,args=['--max-jobs','2'],runner=runner)
        self.assertEqual(0,code);self.assertEqual(2,runner.call_count)
    def test_invalid_limits_and_target_or_model_changes_refuse_before_connection(self):
        for args in (['--max-jobs','0'],['--max-jobs','1001'],['--idle-timeout','0'],['--yes'],['--document','/tmp/doc']):
            code,app,make,msg=self.execute('',args=args)
            self.assertEqual(2,code);make.assert_not_called()
        doc=calculator_example();doc['target']='{{app}}'
        code,app,make,msg=self.execute('',doc=doc);self.assertEqual(2,code);make.assert_not_called()
        doc=calculator_example();doc['steps']=[{'action':'decide','params':{'into':'choice','question':'Which?','choices':['a','b']}}]
        code,app,make,msg=self.execute('',doc=doc);self.assertEqual(2,code);make.assert_not_called()
    def test_foreground_latch_is_kept_across_jobs(self):
        def runner(flow,args,_driver,_confirm):
            self.assertFalse(_confirm('approve'))
            _driver.inner.act=Mock(return_value={'type':'acted','activated_target':True})
            _driver.act('obs','app',{'name':'click'})
            return 0
        wrapped=Mock(side_effect=runner)
        code,app,make,msg=self.execute('{"params":{}}\n'*2,runner=wrapped)
        self.assertEqual(1,code);self.assertEqual(1,wrapped.call_count)
        self.assertIn('activation_observed','\n'.join(msg))

    def test_selector_failure_emits_packet_for_stopped_job_only(self):
        with tempfile.TemporaryDirectory() as tmp:
            packet = Path(tmp)/'packet.json'
            doc = calculator_example()
            doc['steps'] = doc['steps'][2:3]
            doc['steps'][0]['selector']['identifier'] = 'OldOne'
            code, app, make, messages = self.execute('{"params":{}}\n'*2,
                doc=doc, args=['--repair-packet',str(packet)])
            self.assertEqual(1,code)
            self.assertEqual([],app.calls)
            saved=json.loads(packet.read_text())
            self.assertEqual('not_started',saved['effect'])
            sys.platform=='win32' or self.assertEqual(0o600,packet.stat().st_mode&0o777)  # Windows: profile ACLs
            results=[json.loads(m.removeprefix('TACTO_BATCH_RESULT ')) for m in messages if m.startswith('TACTO_BATCH_RESULT ')]
            self.assertEqual(1,len(results));self.assertEqual(str(packet),results[0]['repair_packet'])
            self.assertEqual(0,json.loads(messages[-1].removeprefix('TACTO_BATCH_DONE '))['completed'])

    def test_invalid_packet_path_is_refused_before_connection(self):
        with tempfile.TemporaryDirectory() as tmp:
            existing=Path(tmp)/'existing';existing.write_text('preserve')
            for path in ('relative.json',str(existing),str(Path(tmp)/'missing'/'packet.json')):
                code,app,make,msg=self.execute('',args=['--repair-packet',path])
                self.assertEqual(2,code);make.assert_not_called()
            self.assertEqual('preserve',existing.read_text())


class VerifiedBoundaryTests(unittest.TestCase):
    def test_only_verified_previous_action_waives_initial_settle_and_still_observes(self):
        inner=Mock();inner.act.return_value={'type':'acted'}
        inner.wait_until.return_value={'type':'observation','tree':'done'}
        driver=BatchDriver(inner)
        driver.begin_job();driver.observe('app','tree_full')
        self.assertNotIn('_settle_after_action',inner.observe.call_args.kwargs)
        driver.act('obs','app',{'name':'click'})
        driver.wait_until('app',lambda t:t=='done')
        driver.begin_job();driver.observe('app','tree_full')
        self.assertFalse(inner.observe.call_args.kwargs['_settle_after_action'])
        self.assertEqual(2,inner.observe.call_count)
        # Only the initial observation gets the waiver, never an arbitrary read.
        driver.observe('app','tree_full')
        self.assertNotIn('_settle_after_action',inner.observe.call_args.kwargs)
        driver.act('new','app',{'name':'click'})
        driver.begin_job();driver.observe('app','tree_full')
        self.assertNotIn('_settle_after_action',inner.observe.call_args.kwargs)
    def test_timeout_does_not_mark_action_verified(self):
        inner=Mock();inner.act.return_value={'type':'acted'}
        inner.wait_until.return_value={'type':'timeout','last_obs':{'type':'observation','tree':'done'}}
        driver=BatchDriver(inner);driver.act('obs','app',{'name':'click'})
        driver.wait_until('app',lambda t:t=='done')
        self.assertFalse(driver.verified)
    def test_activation_allows_read_but_never_another_input(self):
        inner=Mock();inner.act.return_value={'type':'acted','activated_target':True}
        driver=BatchDriver(inner);driver.act('obs','app',{'name':'click'})
        driver.act('obs','app',{'name':'read'})
        denied=driver.act('obs','app',{'name':'click'})
        self.assertEqual('not_started',denied['effect']);self.assertEqual(2,inner.act.call_count)


class FramingTests(unittest.TestCase):
    def test_buffered_lines_and_clean_eof(self):
        read,write=os.pipe()
        try:
            os.write(write,b'one\ntwo\n');os.close(write);write=None
            reader=RequestLines(read)
            self.assertEqual('one',reader.read(1));self.assertEqual('two',reader.read(1))
            self.assertIsNone(reader.read(1))
        finally:
            os.close(read)
            if write is not None:os.close(write)
    def test_over_budget_and_idle_timeout(self):
        read,write=os.pipe()
        try:
            reader=RequestLines(read)
            with self.assertRaises(TimeoutError):reader.read(.001)
            os.write(write,b'12345\n')
            with patch('tacto.flow_batch.MAX_REQUEST_BYTES',4),self.assertRaises(ValueError):reader.read(1)
        finally:
            # Writer first: on Windows a thread may still be reading `read`,
            # and closing an fd under a pending read blocks until it returns.
            os.close(write);os.close(read)

if __name__=='__main__':unittest.main()
