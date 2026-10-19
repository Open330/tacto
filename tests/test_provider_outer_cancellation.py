"""Owned local wrapper/CLI trees; no model, network or native GUI actions."""
import json
import itertools
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]


@unittest.skipUnless(sys.platform == 'darwin', 'Darwin launcher watchdog')
class OuterCancellationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        base = Path(cls.folder.name)
        source = (ROOT / 'launcher/tacto.swift').read_text()
        functions = source[source.index('enum ModelProcessError:'):source.index('\nfunc askModel(')]
        harness = '''
let proc = Process()
proc.executableURL = URL(fileURLWithPath: CommandLine.arguments[1])
proc.arguments = Array(CommandLine.arguments.dropFirst(2))
do {
    _ = try boundedModelProcess(proc, prompt: "owned", timeout: 1.5)
    print("unexpected_success")
} catch { print(String(describing: error)) }
_ = modelCleanupGroup.wait(timeout: .now() + 1)
'''
        swift = base / 'watchdog.swift'
        swift.write_text('import Foundation\nimport Darwin\n' + functions + harness)
        cls.binary = base / 'watchdog'
        subprocess.run(['xcrun', 'swiftc', str(swift), '-o', str(cls.binary)],
                       check=True, capture_output=True, timeout=45)

    @classmethod
    def tearDownClass(cls):
        cls.folder.cleanup()

    def test_outer_failure_stops_owned_provider_group_before_delayed_write(self):
        for runner, mode in itertools.product(('authoring', 'codex', 'claude'), ('timeout', 'output_limit')):
            with self.subTest(runner=runner, mode=mode), tempfile.TemporaryDirectory() as folder:
                base = Path(folder); ready = base / 'ready'; marker = base / 'survived'
                descendant = 'import pathlib,time;time.sleep(2);pathlib.Path(' + repr(str(marker)) + ').write_text("survived")'
                cli = ('import json,os,pathlib,subprocess,sys,time;'
                       'child=subprocess.Popen([sys.executable,"-c",' + repr(descendant) + ']);'
                       'pathlib.Path(' + repr(str(ready)) + ').write_text(json.dumps({"group":os.getpid(),"child":child.pid}));'
                       + 'time.sleep(10)')
                wrapper = base / 'wrapper.py'
                wrapper.write_text('''import sys, os, pathlib, threading, time
sys.path.insert(0, ROOT)
from tacto import authoring, ask_provider, codex_result, claude_result
def owned(*args, **kwargs):
    if MODE == 'output_limit':
        def flood():
            while not pathlib.Path(READY).exists(): time.sleep(.01)
            os.write(1, b'x' * (5*1024*1024))
        threading.Thread(target=flood, daemon=True).start()
    argv = [sys.executable, '-c', CLI]
    if RUNNER == 'authoring': authoring._run_provider(argv, 20, input_text='owned')
    elif RUNNER == 'codex': codex_result._run_codex(argv, 'owned', 20)
    else: claude_result._run_claude(argv, 'owned', 20, None)
    raise AssertionError('unexpected return')
authoring.ask_model_result = owned
sys.argv = ['owned-wrapper', '--result-envelope']
raise SystemExit(ask_provider.main())
'''.replace('ROOT', repr(str(ROOT))).replace('CLI', repr(cli))
                    .replace('MODE', repr(mode)).replace('READY', repr(str(ready))).replace('RUNNER', repr(runner)))
                try:
                    result = subprocess.run([str(self.binary), sys.executable, str(wrapper)],
                                            capture_output=True, text=True, timeout=8)
                    self.assertEqual(0, result.returncode)
                    self.assertIn('timeout' if mode == 'timeout' else 'outputLimit', result.stdout)
                    self.assertTrue(ready.exists(), 'owned CLI must have started; not a startup-only timeout')
                    # The Swift watchdog process has exited. A background cleanup
                    # task that dies with that process cannot make this pass.
                    time.sleep(2.3)
                    self.assertFalse(marker.exists(), 'owned descendant survived outer watchdog')
                    with self.assertRaises(ProcessLookupError):
                        os.kill(json.loads(ready.read_text())['group'], 0)
                finally:
                    if ready.exists():
                        group = json.loads(ready.read_text())['group']
                        try: os.killpg(group, signal.SIGKILL)
                        except ProcessLookupError: pass

    def test_hung_wrapper_still_gets_forced_termination(self):
        with tempfile.TemporaryDirectory() as folder:
            ready = Path(folder) / 'ready'
            script = ('import os,signal,time,pathlib;signal.signal(signal.SIGTERM,signal.SIG_IGN);'
                      'pathlib.Path(' + repr(str(ready)) + ').write_text(str(os.getpid()));time.sleep(20)')
            result = subprocess.run([str(self.binary), sys.executable, '-c', script],
                                    capture_output=True, text=True, timeout=8)
            self.assertIn('timeout', result.stdout)
            self.assertTrue(ready.exists())
            with self.assertRaises(ProcessLookupError): os.kill(int(ready.read_text()), 0)


@unittest.skipUnless(os.name == 'posix', 'POSIX termination handler')
class TerminationHandlerTests(unittest.TestCase):
    def test_handler_restores_previous_signal_state_on_success_and_exception(self):
        from tacto import ask_provider
        previous = signal.getsignal(signal.SIGTERM)
        with patch.object(ask_provider, '_main', return_value=0):
            self.assertEqual(0, ask_provider.main())
        self.assertEqual(previous, signal.getsignal(signal.SIGTERM))
        with patch.object(ask_provider, '_main', side_effect=ValueError('owned')), self.assertRaises(ValueError):
            ask_provider.main()
        self.assertEqual(previous, signal.getsignal(signal.SIGTERM))

    def test_termination_unwinds_without_becoming_an_answer(self):
        from tacto import ask_provider
        def terminate():
            os.kill(os.getpid(), signal.SIGTERM)
            raise AssertionError('termination swallowed')
        previous = signal.getsignal(signal.SIGTERM)
        with patch.object(ask_provider, '_main', side_effect=terminate):
            self.assertEqual(143, ask_provider.main())
        self.assertEqual(previous, signal.getsignal(signal.SIGTERM))


if __name__ == '__main__':
    unittest.main()
