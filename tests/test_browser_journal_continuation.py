"""Actual Chromium worker loss and a receipt-gated, newly approved Flow."""
import hashlib
from contextlib import closing
import importlib.util
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tacto.commit_evidence import CommitEvidence, DeliveryEvidence
from tacto.contract import ContextBinding, SafetyContract, Resource, issue_approval
from tacto.delivery_receipt import DeliveredMessageReceipt, InboxDelivery, PendingDelivery
from tacto.dispatch_journal import DurableDispatchDriver
from tacto.flow import Flow, Step, Selector, replay
from tacto.journal_reconciliation import CommitReconciliation, reconcile_and_replay, JournalReconciliationError


def recipe(label):
    safety = SafetyContract(effects=['transmit'], reversibility='irreversible',
                            resource=Resource(object_type='owned message', scope='entry'))
    binding = ContextBinding(role='button', title=label, operation='click')
    return Flow('new_owned_delivery', 'page', [Step('click', Selector(role='button', title=label),
                                                  safety=safety, binding=binding)]), {0:issue_approval(binding, safety)}


def read_receipt(origin, key, request_id):
    try:
        with urlopen(Request(origin+'/inbox/'+request_id, headers={'X-Reader':key}), timeout=2) as response:
            record=json.load(response)
            record['attachments']=tuple(record['attachments'])
            return InboxDelivery(**record)
    except HTTPError as error:
        if error.code == 404:return None
        raise


def receipt_for(driver, origin, key, expected):
    def bind(event):
        current = driver._latest['page']
        assert current['obs_id'] == event['obs_id'] and current['document'] == driver._document()
        handle = driver._locator(current['nodes'][event['action']['element_index']])
        try:
            actual = handle.evaluate("el => {if(!el.isConnected || el.disabled) throw Error('stale'); return {request_id:el.dataset.request,account:document.querySelector('#account').value,recipient:document.querySelector('#recipient').value,body:document.querySelector('#body').value};}")
        finally:handle.dispose()
        assert actual == expected
        return PendingDelivery(actual['request_id'], 'page', event['obs_id'], event['action_digest'],
                               actual['account'], actual['recipient'], hashlib.sha256(actual['body'].encode()).hexdigest(), ())
    return DeliveredMessageReceipt(target='page', account=expected['account'], recipient=expected['recipient'],
        text=expected['body'], attachments=(), bind_request=bind,
        read_inbox=lambda request_id:read_receipt(origin,key,request_id), timeout_seconds=1)


WORKER = r"""
import json,os,sys
from pathlib import Path
from tacto.browser import BrowserDriver
from tests.test_browser_journal_continuation import *
root=Path(sys.argv[1]); config=json.loads((root/'host.json').read_text())
class LostReply(BrowserDriver):
 def act(self,*args,**kwargs):
  result=super().act(*args,**kwargs)
  if result.get('type')=='acted':
   self._page.locator('output').filter(has_text='Sent').wait_for(timeout=3000)
   assert read_receipt(config['origin'],config['key'],config['first']['request_id']) is not None
   self.close()
   os._exit(17)
  return result
with closing(LostReply(headless=True)) as driver:
 driver.goto(config['origin']+'/first')
 receipt=receipt_for(driver,config['origin'],config['key'],config['first'])
 flow,approvals=recipe('Send first owned message')
 def prepare(event):
  context=receipt.prepare_commit(event)
  fd=os.open(root/'original-event.json',os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
  with os.fdopen(fd,'w') as out:json.dump(event,out);out.flush();os.fsync(out.fileno())
  return context
 with DurableDispatchDriver(driver,root/'journal') as guard:
  result=replay(guard,flow,approvals=approvals,prepare_commit=prepare,
       after_commit=receipt.after_commit,commit_expected=receipt.commit_expected)
 raise AssertionError(result.log)
"""


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Chromium required')
class BrowserJournalContinuationTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.root=Path(temp.name);self.messages={};self.posts=[];self.key=secrets.token_hex(16)
        self.first={'request_id':secrets.token_hex(16),'account':'owned-sender','recipient':'owned-reader','body':'first exact body'}
        self.second={**self.first,'request_id':secrets.token_hex(16),'body':'second exact body'}
        owner=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def respond(self,code,body,content='application/json'):
                self.send_response(code);self.send_header('Content-Type',content)
                self.send_header('Content-Length',str(len(body)));self.end_headers();self.wfile.write(body)
            def do_GET(self):
                if self.path.startswith('/inbox/'):
                    if self.headers.get('X-Reader')!=owner.key:
                        self.respond(403,b'{}');return
                    row=owner.messages.get(self.path.removeprefix('/inbox/'))
                    self.respond(200 if row else 404,json.dumps(row).encode());return
                mode=self.path.strip('/'); expected=owner.first if mode=='first' else owner.second
                label='Send first owned message' if mode=='first' else 'Send second owned message'
                page='<input id="account"><input id="recipient"><textarea id="body"></textarea><button id="send">'+label+'</button><output>Draft</output>'
                page+='<script>const data='+json.dumps(expected)+';for(const k of ["account","recipient","body"])document.querySelector("#"+k).value=data[k];document.querySelector("#send").dataset.request=data.request_id;document.querySelector("#send").onclick=async()=>{const r=await fetch("/send",{method:"POST",body:JSON.stringify(data)});if(r.ok)document.querySelector("output").textContent="Sent";};</script>'
                self.respond(200,page.encode(),'text/html; charset=utf-8')
            def do_POST(self):
                row=json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                owner.posts.append(row)
                if row not in (owner.first,owner.second):self.respond(400,b'{}');return
                owner.messages.setdefault(row['request_id'],{'request_id':row['request_id'],'message_id':secrets.token_hex(16),
                    'account':row['account'],'recipient':row['recipient'],'body_sha256':hashlib.sha256(row['body'].encode()).hexdigest(),
                    'state':'delivered','attachments':[]})
                self.respond(200,b'{}')
        self.server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.addCleanup(self.stop_server)
        self.origin=f'http://127.0.0.1:{self.server.server_port}'
        host=self.root/'host.json';host.write_text(json.dumps({'origin':self.origin,'key':self.key,'first':self.first}));host.chmod(0o600)
        child=subprocess.run([sys.executable,'-c',WORKER,str(self.root)],capture_output=True,text=True,timeout=20)
        self.assertEqual(17,child.returncode,child.stdout+child.stderr)
        self.assertEqual([self.first],self.posts)
        self.event=json.loads((self.root/'original-event.json').read_text())
        self.old=(self.root/'journal'/'00000.jsonl').read_bytes()
        self.assertEqual(1,len(self.old.splitlines()))
        self.expected={'받는 사람':self.first['recipient'],'보내는 계정':self.first['account'],
                       '보낸 내용':self.first['body'],'attachments':()}
        self.plan=CommitReconciliation(self.event,self.expected,expect={'role':'button','attribute':'Title','label':'Send first owned message','enabled':True})

    def stop_server(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=2)

    def fresh_evidence(self,slot,event):
        self.assertEqual(0,slot);self.assertEqual(self.event,event)
        row=read_receipt(self.origin,self.key,self.first['request_id'])
        if row is None:return None
        return CommitEvidence(event['event_id'],event['action_digest'],DeliveryEvidence(row.message_id,row.recipient,row.account,row.body_sha256,()))

    def test_receipt_gated_flow_sends_only_new_approved_message(self):
        from tacto.browser import BrowserDriver
        with closing(BrowserDriver(headless=True)) as driver:
            driver.goto(self.origin+'/second')
            receipt=receipt_for(driver,self.origin,self.key,self.second)
            flow,approvals=recipe('Send second owned message')
            def read_then_mutate(slot,event):
                proof=self.fresh_evidence(slot,event)
                flow.steps.clear();approvals.clear()
                return proof
            with DurableDispatchDriver(driver,self.root/'journal') as guard:
                result=reconcile_and_replay(guard,[self.plan],read_then_mutate,flow,
                    approvals=approvals,prepare_commit=receipt.prepare_commit,
                    after_commit=receipt.after_commit,commit_expected=receipt.commit_expected)
            self.assertTrue(result.ok,result.log)
            self.assertEqual('confirmed',result.events[0]['state'])
        self.assertEqual([self.first,self.second],self.posts)
        self.assertEqual(2,len(self.messages))
        self.assertEqual(self.old,(self.root/'journal'/'00000.jsonl').read_bytes())
        self.assertEqual(2,len(list((self.root/'journal').iterdir())))

    def test_missing_original_receipt_keeps_new_flow_blocked(self):
        from tacto.browser import BrowserDriver
        self.messages.clear()
        with closing(BrowserDriver(headless=True)) as driver:
            driver.goto(self.origin+'/second')
            flow,approvals=recipe('Send second owned message')
            with DurableDispatchDriver(driver,self.root/'journal') as guard:
                with self.assertRaisesRegex(JournalReconciliationError,'causal_commit_unverified'):
                    reconcile_and_replay(guard,[self.plan],self.fresh_evidence,flow,approvals=approvals)
        self.assertEqual([self.first],self.posts)
        self.assertEqual(self.old,(self.root/'journal'/'00000.jsonl').read_bytes())
        self.assertEqual(1,len(list((self.root/'journal').iterdir())))

    def test_old_receipt_does_not_approve_new_transmission(self):
        from tacto.browser import BrowserDriver
        with closing(BrowserDriver(headless=True)) as driver:
            driver.goto(self.origin+'/second')
            flow,_=recipe('Send second owned message')
            receipt=receipt_for(driver,self.origin,self.key,self.second)
            with DurableDispatchDriver(driver,self.root/'journal') as guard:
                result=reconcile_and_replay(guard,[self.plan],self.fresh_evidence,flow,
                    prepare_commit=receipt.prepare_commit,after_commit=receipt.after_commit,
                    commit_expected=receipt.commit_expected)
                self.assertEqual(2,guard._sequence)
            self.assertFalse(result.ok,result.log)
        self.assertEqual([self.first],self.posts)
        self.assertEqual(2,len(list((self.root/'journal').iterdir())))
        record=(self.root/'journal'/'00001.jsonl').read_text().splitlines()
        self.assertEqual('unknown',json.loads(record[-1])['category'])
