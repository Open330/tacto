"""Durable reservation precedes input, survives worker death and hides payloads."""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch
from tacto.dispatch_journal import DurableDispatchDriver


class DispatchJournalTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.directory=Path(temp.name)/'journal'
        self.driver=Mock();self.driver.act.return_value={'type':'acted'}

    def test_reservation_is_durable_before_dispatch_without_plaintext(self):
        def act(*args,**kwargs):
            row=json.loads((self.directory/'00000.jsonl').read_text().splitlines()[0])
            self.assertEqual('reserved',row['state'])
            return {'type':'acted'}
        self.driver.act.side_effect=act
        with DurableDispatchDriver(self.driver,self.directory) as driver:
            driver.act('obs','private target',{'name':'set_value','value':'private text'})
        data=(self.directory/'00000.jsonl').read_text()
        self.assertNotIn('private text',data);self.assertNotIn('private target',data)
        self.assertEqual(0o600,(self.directory/'00000.jsonl').stat().st_mode&0o777)
        with DurableDispatchDriver(self.driver,self.directory) as driver:
            reply=driver.act('fresh','changed target',{'name':'click'})
            self.assertEqual('dispatch_journal_requires_reconciliation',reply['error'])
            self.assertEqual('dispatch_journal_unavailable',driver.act('fresh','owned',{'name':'click'})['error'])
        self.driver.act.assert_called_once()

    def test_prewrite_failure_sends_nothing_and_unknown_reply_blocks_followup(self):
        with DurableDispatchDriver(self.driver,self.directory) as driver:
            with patch('tacto.dispatch_journal.os.fsync',side_effect=OSError()):
                reply=driver.act('obs','owned',{'name':'click'})
            self.assertEqual('not_started',reply['effect']);self.driver.act.assert_not_called()
        with tempfile.TemporaryDirectory() as folder:
            self.driver.act.return_value={'type':'acted','unverified':'cannot_complete'}
            with DurableDispatchDriver(self.driver,Path(folder)/'journal') as driver:
                driver.act('obs','owned',{'name':'click'})
                self.assertEqual('not_started',driver.act('obs','owned',{'name':'click'})['effect'])
            self.driver.act.assert_called_once()

    def test_known_recipe_restarted_from_beginning_does_not_write_again(self):
        from tacto.agent import Agent
        from tests.test_replay_commit_evidence import TextEditFixture,MemoryStore
        app=TextEditFixture(write=True);app.install_evidence()
        with DurableDispatchDriver(app,self.directory) as driver:
            first=Agent(driver=driver,store=MemoryStore()).handle('"proof.rtf" 로 저장해줘',ask=lambda _:True)
        self.assertEqual('done',first.status,first)
        self.assertEqual(1,len(app.writes))
        with DurableDispatchDriver(app,self.directory) as driver:
            second=Agent(driver=driver,store=MemoryStore()).handle('"proof.rtf" 로 저장해줘',ask=lambda _:True)
        self.assertNotEqual('done',second.status)
        self.assertEqual(1,len(app.writes))

    def test_reply_log_failure_preserves_unknown_effect_and_blocks_more_input(self):
        with DurableDispatchDriver(self.driver,self.directory) as driver:
            # Reservation file + directory sync succeed; reply sync fails.
            with patch('tacto.dispatch_journal.os.fsync',side_effect=[None,None,OSError()]):
                reply=driver.act('obs','owned',{'name':'click'})
            self.assertEqual('dispatch_journal_write_failed',reply['error'])
            self.assertEqual('unknown',reply['effect'])
            self.assertEqual('not_started',driver.act('fresh','owned',{'name':'click'})['effect'])
        self.driver.act.assert_called_once()

    def test_directory_alias_and_nonprivate_directory_refused(self):
        self.directory.mkdir(mode=0o700)
        alias=self.directory.parent/'alias';alias.symlink_to(self.directory,target_is_directory=True)
        with self.assertRaises(ValueError):DurableDispatchDriver(self.driver,alias)
        self.directory.chmod(0o755)
        with self.assertRaises(ValueError):DurableDispatchDriver(self.driver,self.directory)
        self.driver.act.assert_not_called()

    def test_worker_dies_after_real_local_http_receipt_and_restart_cannot_resend(self):
        from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
        import threading
        received=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                received.append(1);self.send_response(200);self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        script=r'''
import os,sys
from urllib.request import Request,urlopen
from tacto.dispatch_journal import DurableDispatchDriver
class Driver:
    def act(self,*args,**kwargs):
        with urlopen(Request(sys.argv[2],data=b'owned',method='POST'),timeout=2) as response:
            assert response.status==200
        os._exit(17)
with DurableDispatchDriver(Driver(),sys.argv[1]) as driver:
    result=driver.act('fresh','owned',{'name':'click'})
    assert result['error']=='dispatch_journal_requires_reconciliation'
'''
        argv=[sys.executable,'-c',script,str(self.directory),f'http://127.0.0.1:{server.server_port}/']
        first=subprocess.run(argv,capture_output=True,text=True,timeout=10)
        self.assertEqual(17,first.returncode,first.stderr)
        second=subprocess.run(argv,capture_output=True,text=True,timeout=10)
        self.assertEqual(0,second.returncode,second.stderr)
        self.assertEqual(1,len(received))
        self.assertEqual(1,len((self.directory/'00000.jsonl').read_text().splitlines()))
