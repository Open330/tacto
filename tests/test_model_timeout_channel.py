"""Socket pairs and extracted Swift against fake processes only; no UI/models."""
import contextlib
import io
import json
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from nlc.helper import InheritedHelperDriver, HelperUnavailable


class TimeoutChannelTests(unittest.TestCase):
    def channel(self, response=None, delay=0):
        client,server=socket.socketpair()
        d=object.__new__(InheritedHelperDriver)
        d._sock=client; d._f=client.makefile('rw',encoding='utf8',newline='\n'); d._ready=True
        self.addCleanup(d._f.close); self.addCleanup(client.close); self.addCleanup(server.close)
        requests=[]
        def peer():
            try:
                data=b''
                while not data.endswith(b'\n'): data+=server.recv(65536)
                requests.append(json.loads(data))
                if delay:time.sleep(delay)
                if response is not None:server.sendall(json.dumps(response).encode()+b'\n')
            except OSError:pass
        worker=threading.Thread(target=peer,daemon=True);worker.start()
        self.addCleanup(lambda:worker.join(.5))
        return d,requests

    def test_timeout_is_transmitted_and_normal_response_keeps_channel(self):
        d,requests=self.channel({'type':'answer','text':'ok'})
        self.assertEqual('ok',d.ask_launcher('q',provider='claude',timeout_seconds=.4))
        self.assertEqual(.4,requests[0]['timeout_seconds'])
        self.assertFalse(getattr(d,'_poisoned',False))

    def test_receive_timeout_permanently_poisons_even_reconstruction(self):
        d,_=self.channel({'type':'answer','text':'late'},delay=.15)
        started=time.monotonic()
        with self.assertRaises(TimeoutError):d.ask_launcher('q',timeout_seconds=.03)
        self.assertLess(time.monotonic()-started,.5)
        with self.assertRaises(HelperUnavailable):d._send({'type':'observe'})
        with patch.object(InheritedHelperDriver,'_shared',d):
            with self.assertRaises(HelperUnavailable):InheritedHelperDriver()

    def test_blocked_request_write_also_expires(self):
        client,server=socket.socketpair()
        d=object.__new__(InheritedHelperDriver)
        d._sock=client;d._f=client.makefile('rw',encoding='utf8',newline='\n');d._ready=True
        self.addCleanup(d._f.close);self.addCleanup(client.close);self.addCleanup(server.close)
        started=time.monotonic()
        with self.assertRaises(TimeoutError):
            d.ask_launcher('x'*1000000,timeout_seconds=.03)
        self.assertLess(time.monotonic()-started,.5)
        self.assertTrue(d._poisoned)

    def test_explicit_launcher_timeout_is_not_no_answer(self):
        d,_=self.channel({'type':'error','error':'model_timeout'})
        with self.assertRaises(TimeoutError):d.ask_launcher('q',timeout_seconds=.4)
        self.assertTrue(d._poisoned)

    def test_validation_rejects_bool_nonfinite_and_out_of_range_without_send(self):
        d=object.__new__(InheritedHelperDriver)
        with patch.object(d,'_send') as send:
            for value in (True,False,0,-1,421,float('nan'),float('inf'),'1'):
                with self.subTest(value=value),self.assertRaises(ValueError):
                    d.ask_launcher('q',timeout_seconds=value)
            send.assert_not_called()

    def test_ask_provider_passes_timeout_and_preserves_timeout_exit_code(self):
        from nlc.ask_provider import main
        for failure in (False,True):
            with patch.object(sys,'argv',['ask_provider','--provider','claude','--timeout','.25']), \
                 patch.object(sys,'stdin',io.StringIO('q')), \
                 contextlib.redirect_stdout(io.StringIO()),contextlib.redirect_stderr(io.StringIO()), \
                 patch('nlc.authoring._ask_model',side_effect=TimeoutError() if failure else None,return_value='ok') as model:
                self.assertEqual(124 if failure else 0,main())
                model.assert_called_once_with('q','claude',timeout=.25,expect_json=False)

    @unittest.skipUnless(sys.platform=='darwin' and shutil.which('swiftc'),'macOS Swift required')
    def test_extracted_swift_bounds_stdin_stdout_and_process_wait(self):
        source=(Path(__file__).resolve().parents[1]/'launcher/tacto.swift').read_text()
        start=source.index('@Sendable func modelTimeout(')
        end=source.index('\nfunc askModel(',start)
        block=source[start:end]
        # Extract production code verbatim, use only isolated fake Python children.
        harness='import Foundation\nimport Darwin\n'+block+'\n'
        relay=source[source.index('var pair: [Int32] = [0, 0]'):source.index('// CLOEXEC on both ends:')]
        harness+=relay+'\nclose(childEnd)\nlet failedWrite = write(relayEnd, "x", 1)\nif failedWrite != -1 { exit(8) }\nclose(relayEnd)\n'
        harness+='''
let python = CommandLine.arguments[1]
if modelTimeout(nil) != 420 || modelTimeout(0.25) != 0.25 { exit(6) }
for invalid: Any in [true, false, 0, -1, 421, Double.nan, Double.infinity, "1"] {
    if modelTimeout(invalid) != nil { exit(7) }
}
func check(_ name: String, _ code: String, _ prompt: String, _ timeout: Double, _ expectedTimeout: Bool) {
    let p=Process(); p.executableURL=URL(fileURLWithPath:python); p.arguments=["-c",code]
    p.standardError=FileHandle.nullDevice
    let start=ProcessInfo.processInfo.systemUptime
    var expired=false
    do {
        let output=try boundedModelProcess(p,prompt:prompt,timeout:timeout)
        if name == "normal" && String(decoding:output,as:UTF8.self) != "hello" { exit(3) }
    } catch ModelProcessError.timeout { expired=true } catch { print("unexpected",error);exit(4) }
    if expired != expectedTimeout || ProcessInfo.processInfo.systemUptime-start > 2 { exit(5) }
    print(name,"PASS")
}
check("normal","import sys;sys.stdout.write(sys.stdin.read())","hello",1,false)
check("blocked_stdin","import time;time.sleep(5)",String(repeating:"x",count:1000000),0.1,true)
check("closed_stdout_still_running","import os,time;os.close(1);time.sleep(5)","",0.1,true)
check("inherited_stdout","import subprocess,sys;subprocess.Popen([sys.executable,'-c','import time;time.sleep(.5)'])","",0.1,true)
'''
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);path=root/'harness.swift';path.write_text(harness)
            built=subprocess.run(['swiftc',str(path),'-o',str(root/'test')],capture_output=True,text=True,timeout=30)
            self.assertEqual(0,built.returncode,built.stderr)
            result=subprocess.run([str(root/'test'),sys.executable],capture_output=True,text=True,timeout=10)
            self.assertEqual(0,result.returncode,result.stdout+result.stderr)
            self.assertEqual(4,result.stdout.count('PASS'))

if __name__=='__main__':unittest.main()
