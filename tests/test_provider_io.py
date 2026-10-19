"""Real local pipes: bounded capture, backpressure and group cleanup."""
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from tacto.authoring import _run_provider
from tacto.codex_result import _run_codex
from tacto.claude_result import _run_claude
from tacto.provider_io import ProviderOutputLimitExceeded, bounded_communicate


@unittest.skipUnless(os.name=='posix','POSIX planning CLI capture')
class ProviderIOTests(unittest.TestCase):
    def test_bidirectional_backpressure_unicode_and_newlines(self):
        source='import sys;sys.stdout.write("x"*200000);sys.stdout.flush();s=sys.stdin.read();sys.stderr.write(s);sys.stdout.write("한글\\r\\n끝\\r")'
        prompt='owned 한글 '*30000
        result=_run_provider([sys.executable,'-c',source],3,input_text=prompt)
        self.assertEqual(prompt,result.stderr)
        self.assertEqual('x'*200000+'한글\n끝\n',result.stdout)

    def test_stream_capacity_accepts_exact_and_refuses_one_extra_byte(self):
        for channel in ('stdout','stderr'):
            for size in (1024,1025):
                source=f'import os;os.write({1 if channel=="stdout" else 2},b"x"*{size})'
                with patch('tacto.provider_io.MAX_PROVIDER_OUTPUT_BYTES',1024):
                    if size==1024:
                        result=_run_provider([sys.executable,'-c',source],3)
                        self.assertEqual('x'*size,getattr(result,channel))
                    else:
                        with self.assertRaises(ProviderOutputLimitExceeded) as error:
                            _run_provider([sys.executable,'-c',source],3)
                        self.assertNotIn('x'*10,str(error.exception))

    def test_all_runners_reap_limit_offender_and_stop_same_group_descendant(self):
        with tempfile.TemporaryDirectory() as folder:
            for name in ('authoring','codex','claude'):
                marker=Path(folder)/name
                child='import time,pathlib;time.sleep(.6);pathlib.Path('+repr(str(marker))+').write_text("alive")'
                source='import os,subprocess,sys,time;subprocess.Popen([sys.executable,"-c",'+repr(child)+']);os.write(1,b"x"*2000);time.sleep(10)'
                argv=[sys.executable,'-c',source]
                children=[];launch=subprocess.Popen
                def capture(*args,**kwargs):
                    proc=launch(*args,**kwargs);children.append(proc);return proc
                with patch('tacto.provider_io.MAX_PROVIDER_OUTPUT_BYTES',1024), \
                     patch('subprocess.Popen',side_effect=capture),self.assertRaises(ProviderOutputLimitExceeded):
                    if name=='authoring':_run_provider(argv,3)
                    elif name=='codex':_run_codex(argv,'owned',3)
                    else:_run_claude(argv,'owned',3,folder)
                self.assertEqual(1,len(children));self.assertIsNotNone(children[0].returncode)
            time.sleep(.7)
            self.assertEqual([],list(Path(folder).iterdir()))

    def test_closed_pipes_do_not_remove_timeout_for_live_child(self):
        source='import os,time;os.close(1);os.close(2);time.sleep(10)'
        with self.assertRaises(subprocess.TimeoutExpired):
            _run_provider([sys.executable,'-c',source],.1)

    def test_decode_after_child_exit_cannot_return_a_late_answer(self):
        class SlowDecodePipe:
            def __init__(self, pipe): self.pipe = pipe
            def __getattr__(self, name): return getattr(self.pipe, name)
            @property
            def encoding(self):
                # Inject a slow local decode boundary; no remote model involved.
                time.sleep(.2)
                return self.pipe.encoding
        for channel in ('stdout', 'stderr'):
            with self.subTest(channel=channel), subprocess.Popen([sys.executable, '-c',
                    'import os;os.write(1,b"owned answer");os.write(2,b"owned diagnostic")'],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) as process:
                setattr(process, channel, SlowDecodePipe(getattr(process, channel)))
                with self.assertRaises(subprocess.TimeoutExpired) as error:
                    bounded_communicate(process, timeout=.15)
                self.assertEqual(b'owned answer', error.exception.output)
                self.assertEqual(b'owned diagnostic', error.exception.stderr)
                self.assertEqual(0, process.returncode)

    def test_successful_primary_exit_does_not_leave_owned_same_group_worker(self):
        for name in ('authoring','codex','codex_observed','claude'):
            with self.subTest(runner=name),tempfile.TemporaryDirectory() as folder:
                marker=Path(folder)/'survived'
                # The worker is bounded even if the cleanup under test fails.
                # Never signal a numeric group after its leader was reaped.
                child='import pathlib,time;time.sleep(.6);pathlib.Path('+repr(str(marker))+').write_text("survived")'
                source=('import subprocess,sys;'
                        'subprocess.Popen([sys.executable,"-c",'+repr(child)+'],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL);'
                        'sys.stdout.write("owned answer")')
                argv=[sys.executable,'-c',source]
                if name=='authoring':result=_run_provider(argv,3);code=result.returncode;stdout=result.stdout
                elif name in ('codex','codex_observed'):result=_run_codex(argv,'owned',3,[] if name=='codex_observed' else None);code=result.returncode;stdout=result.stdout
                else:code,stdout,_=_run_claude(argv,'owned',3,folder)
                self.assertEqual(0,code);self.assertEqual('owned answer',stdout)
                time.sleep(.8)
                self.assertFalse(marker.exists(),'owned worker survived successful primary completion')

    def test_non_reaping_exit_observer_keeps_owned_pid_until_explicit_wait(self):
        from tacto.provider_io import provider_process_exited_unreaped
        with subprocess.Popen([sys.executable,'-c','raise SystemExit(7)']) as process:
            deadline=time.monotonic()+3
            while not provider_process_exited_unreaped(process):
                self.assertLess(time.monotonic(),deadline);time.sleep(.01)
            self.assertIsNone(process.returncode)
            self.assertEqual(7,process.wait(timeout=1))
            with self.assertRaises(ChildProcessError):provider_process_exited_unreaped(process)

    def test_invalid_session_and_unproven_permission_error_cannot_supply_an_answer(self):
        from unittest.mock import Mock
        from tacto.provider_io import communicate_owned_provider
        process=Mock(pid=42,returncode=None)
        with patch('os.getpgid',return_value=99),patch('os.killpg') as group,self.assertRaises(RuntimeError):
            communicate_owned_provider(process,timeout=1)
        group.assert_not_called();process.kill.assert_called_once();process.wait.assert_called_once()
        with patch('os.killpg',side_effect=PermissionError('owned injection')), \
             patch('tacto.provider_io._darwin_group_has_no_other_processes',return_value=False), \
             self.assertRaises(PermissionError):
            _run_provider([sys.executable,'-c','print("owned answer")'],3)

    def test_failure_exit_status_is_preserved_after_owned_group_cleanup(self):
        result=_run_provider([sys.executable,'-c','print("owned diagnostic");raise SystemExit(7)'],3)
        self.assertEqual(7,result.returncode);self.assertEqual('owned diagnostic\n',result.stdout)

    def test_lost_child_ownership_never_signals_numeric_group(self):
        import errno
        from unittest.mock import Mock
        from tacto.provider_io import communicate_owned_provider
        for capture_error in (ChildProcessError(errno.ECHILD, 'owned fixture reaped'),
                              subprocess.TimeoutExpired('owned fixture', .1), KeyboardInterrupt()):
            with self.subTest(error=type(capture_error).__name__):
                process=Mock(pid=12345,returncode=None)
                with patch('os.getpgid',return_value=12345),patch('os.getsid',return_value=12345), \
                     patch('tacto.provider_io.bounded_communicate',side_effect=capture_error), \
                     patch('tacto.provider_io.provider_process_exited_unreaped',
                           side_effect=ChildProcessError(errno.ECHILD, 'owned fixture reaped')), \
                     patch('os.killpg') as group,self.assertRaises(ChildProcessError):
                    communicate_owned_provider(process,timeout=.1)
                group.assert_not_called();process.kill.assert_not_called();process.wait.assert_called_once()

    def test_ignored_sigchld_is_refused_before_spawn_in_isolated_host(self):
        # Only the owned wrapper changes signal state, never the test host.
        source = """
import signal
from unittest.mock import patch
from tacto.authoring import _run_provider
from tacto.codex_result import _run_codex
from tacto.claude_result import _run_claude
signal.signal(signal.SIGCHLD, signal.SIG_IGN)
for invoke in (lambda: _run_provider(['unused'],1),
               lambda: _run_codex(['unused'],'owned',1),
               lambda: _run_claude(['unused'],'owned',1,None)):
    with patch('subprocess.Popen') as spawn:
        try: invoke()
        except RuntimeError: pass
        else: raise AssertionError('unsupported handler accepted')
        spawn.assert_not_called()
print('refused before spawn: 3')
"""
        result=subprocess.run([sys.executable,'-c',source],capture_output=True,text=True,timeout=5)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual('refused before spawn: 3\n',result.stdout)

    @unittest.skipUnless(sys.platform=='darwin','Darwin native sigaction ABI')
    def test_native_no_child_wait_is_refused_in_isolated_host(self):
        source = """
import ctypes,signal
from tacto.provider_io import require_provider_child_ownership
class SigAction(ctypes.Structure):
    _fields_=[('handler',ctypes.c_void_p),('mask',ctypes.c_uint),('flags',ctypes.c_int)]
lib=ctypes.CDLL('/usr/lib/libSystem.B.dylib',use_errno=True)
call=lib.sigaction
call.argtypes=[ctypes.c_int,ctypes.POINTER(SigAction),ctypes.POINTER(SigAction)]
call.restype=ctypes.c_int
assert call(signal.SIGCHLD,ctypes.byref(SigAction(None,0,0x0020)),None)==0
assert signal.getsignal(signal.SIGCHLD)==signal.SIG_DFL
try: require_provider_child_ownership()
except RuntimeError: print('native auto-reap refused')
else: raise AssertionError('SA_NOCLDWAIT accepted')
"""
        result=subprocess.run([sys.executable,'-c',source],capture_output=True,text=True,timeout=5)
        self.assertEqual(0,result.returncode,result.stderr)
        self.assertEqual('native auto-reap refused\n',result.stdout)

    def test_externally_reaped_real_child_cannot_authorize_group_signal(self):
        from tacto.provider_io import communicate_owned_provider
        # Reap only this fresh fixture. Popen's cached returncode remains None.
        with subprocess.Popen([sys.executable,'-c','pass'],stdout=subprocess.PIPE,
                              stderr=subprocess.PIPE,text=True,start_new_session=True) as process:
            waited,status=os.waitpid(process.pid,0)
            self.assertEqual(process.pid,waited);self.assertEqual(0,status)
            self.assertIsNone(process.returncode)
            with patch('os.getpgid',return_value=process.pid), \
                 patch('os.getsid',return_value=process.pid), \
                 patch('tacto.provider_io.bounded_communicate',return_value=('owned answer','')), \
                 patch('os.killpg') as group,self.assertRaises(ChildProcessError):
                communicate_owned_provider(process,timeout=1)
            group.assert_not_called()
