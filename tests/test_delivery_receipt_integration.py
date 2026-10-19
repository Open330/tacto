"""Real headless Chromium clicks and an independent local receiving inbox."""
import hashlib
import base64
import tempfile
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import secrets
import threading
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from tacto.commit_evidence import AttachmentEvidence, evidence_error
from tacto.contract import ContextBinding, Resource, SafetyContract, issue_approval
from tacto.delivery_receipt import (DeliveredMessageReceipt, InboxDelivery, PendingDelivery,
                                 InboxTemporarilyUnavailable)
from tacto.flow import Flow, Selector, Step, replay


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright required')
class DeliveryReceiptIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.request_id = secrets.token_hex(16)
        self.reader_key = secrets.token_hex(32)
        self.text = '소유 수신함 검증\n정확한 본문'
        self.messages = {}
        self.attachment_payload = []
        self.posts = 0
        self.inbox_failures = 0
        self.inbox_auth_failure = False
        self.inbox_reads_after_dispatch = 0
        self.inbox_reads = 0
        self.mode = 'deliver'
        self.lock = threading.Lock()
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args): pass

            def reply(self, code, value, content_type='application/json'):
                data = (value if type(value) is str else json.dumps(value)).encode()
                self.send_response(code)
                self.send_header('Content-Type', content_type + '; charset=utf-8')
                self.send_header('Content-Length', str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                if self.path == '/':
                    # All accounts, recipients and contents are local fixtures.
                    # Host receipt credentials are never included in the page.
                    initial = json.dumps({'request_id':owner.request_id,
                        'account':'fixture-sender', 'recipient':'fixture-recipient',
                        'body':owner.text}, ensure_ascii=False)
                    page = '''<input id="account" aria-label="account" readonly><input id="recipient" aria-label="recipient" readonly>
                        <textarea id="body" aria-label="body"></textarea><input type="file" id="attachments" aria-label="attachments" multiple><button id="send">Send</button>
                        <output id="status">Ready</output><script>
                        const initial = INITIAL;
                        account.value=initial.account;recipient.value=initial.recipient;
                        body.value=initial.body;send.dataset.request=initial.request_id;
                        send.onclick=async()=>{
                          const attached=await Promise.all([...attachments.files].map(async f=>({filename:f.name,content:btoa(String.fromCharCode(...new Uint8Array(await f.arrayBuffer())))})));
                          await fetch('/send', {method:'POST', headers:{'Content-Type':'application/json'},
                            body:JSON.stringify({request_id:send.dataset.request,
                              account:account.value,recipient:recipient.value,body:body.value,attachments:attached})});
                          document.querySelector('#status').textContent='Sent';
                        };</script>'''.replace('INITIAL', initial)
                    if getattr(owner, 'auto_upload', False):
                        page += '<script>attachments.onchange=()=>send.onclick();</script>'
                    self.reply(200, page, 'text/html')
                    return
                if self.headers.get('X-Inbox-Reader') != owner.reader_key:
                    self.reply(403, {})
                    return
                with owner.lock:
                    owner.inbox_reads += 1
                    if owner.posts:
                        owner.inbox_reads_after_dispatch += 1
                    unavailable = bool(owner.posts and owner.inbox_failures > 0)
                    if unavailable:
                        owner.inbox_failures -= 1
                    unauthorized = owner.posts and owner.inbox_auth_failure
                    stored = owner.messages.get(self.path.removeprefix('/inbox/'))
                    message = ({key:value for key, value in stored.items() if key not in ('body','attachments')}
                               if stored else None)
                    if message:
                        message['body_sha256'] = hashlib.sha256(stored['body'].encode()).hexdigest()
                        message['attachments']=[{'filename':a['filename'],'sha256':hashlib.sha256(base64.b64decode(a['content'])).hexdigest(),'size_bytes':len(base64.b64decode(a['content']))} for a in stored.get('attachments',[])]
                if unauthorized:
                    self.reply(403, {})
                    return
                if unavailable:
                    self.reply(503, {})
                    return
                self.reply(200 if message else 404, message or {})

            def do_POST(self):
                payload = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
                with owner.lock:
                    owner.posts += 1
                    valid = (self.path == '/send' and payload == {
                        'request_id':owner.request_id, 'account':'fixture-sender',
                        'recipient':'fixture-recipient', 'body':owner.text,'attachments':owner.attachment_payload})
                    if valid and owner.mode != 'noop':
                        text = payload['body'] if owner.mode != 'corrupt' else 'wrong'
                        # One immutable service identity per request; duplicate
                        # requests cannot insert a second inbox message.
                        owner.messages.setdefault(owner.request_id, {
                            'request_id':owner.request_id, 'message_id':secrets.token_hex(16),
                            'account':payload['account'], 'recipient':payload['recipient'],
                            'body':text,
                            'attachments':([{**a,'content':base64.b64encode(b'corrupt').decode()} for a in payload['attachments']] if owner.mode=='corrupt_attachment' else payload['attachments']),
                            'state':'delivered'})
                self.reply(200 if valid else 400, {'sender_marker':'Sent'})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        from tacto.browser import BrowserDriver
        self.driver = BrowserDriver(headless=True)
        self.addCleanup(self.driver.close)
        self.driver.goto(self.origin + '/')

    def stop_server(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def read_inbox(self, request_id):
        request = Request(self.origin + '/inbox/' + request_id,
                          headers={'X-Inbox-Reader':self.reader_key})
        try:
            with urlopen(request, timeout=1) as response:
                record=json.load(response)
                record['attachments']=tuple(AttachmentEvidence(**a) for a in record.get('attachments',[]))
                return InboxDelivery(**record)
        except HTTPError as error:
            if error.code == 404: return None
            if error.code in (502, 503, 504):
                raise InboxTemporarilyUnavailable() from None
            raise

    def bind(self, event):
        current = self.driver._latest['page']
        assert current['obs_id'] == event['obs_id']
        assert current['document'] == self.driver._document()
        handle = self.driver._locator(current['nodes'][event['action']['element_index']])
        try:
            actual = handle.evaluate('''async el => {
                if (el.id !== 'send' || !el.isConnected || el.disabled) throw Error('wrong control');
                return {request_id:el.dataset.request,account:document.querySelector('#account').value,
                  recipient:document.querySelector('#recipient').value,body:document.querySelector('#body').value,
                  attachments:await Promise.all([...document.querySelector('#attachments').files].map(async f=>({filename:f.name,content:btoa(String.fromCharCode(...new Uint8Array(await f.arrayBuffer())))})))};
            }''')
        finally:
            handle.dispose()
        return PendingDelivery(actual['request_id'], event['target'], event['obs_id'],
            event['action_digest'], actual['account'], actual['recipient'],
            hashlib.sha256(actual['body'].encode()).hexdigest(),self.manifest(actual['attachments']))

    @staticmethod
    def manifest(payload):
        return tuple(AttachmentEvidence(a['filename'],hashlib.sha256(base64.b64decode(a['content'])).hexdigest(),
            len(base64.b64decode(a['content']))) for a in payload)

    def upload_owned_attachment(self):
        folder=tempfile.TemporaryDirectory();self.addCleanup(folder.cleanup)
        path=Path(folder.name)/'검토.txt';data='소유 첨부 원문 🧪'.encode();path.write_bytes(data)
        self.attachment_payload=[{'filename':path.name,'content':base64.b64encode(data).decode()}]
        # Only file creation is fixture bootstrap. The public NLC chooser
        # route selects this non-sensitive file into the owned loopback form.
        observation=self.driver.observe()
        indices=[i for i,node in enumerate(self.driver._latest['page']['nodes'])
                 if node['name']=='attachments']
        self.assertEqual(1,len(indices))
        response=self.driver.upload(observation['obs_id'],'page',indices[0],[str(path)],approved=True)
        self.assertEqual('acted',response.get('type'),response)
        selected=self.driver._page.eval_on_selector('#attachments','''async el =>
            Promise.all([...el.files].map(async f=>({filename:f.name,
                content:btoa(String.fromCharCode(...new Uint8Array(await f.arrayBuffer())))})))''')
        self.assertEqual(self.attachment_payload,selected)
        self.assertEqual(0,self.posts,'chooser selection must not masquerade as server delivery')

    def test_actual_attachment_bytes_have_receiving_manifest(self):
        self.upload_owned_attachment()
        result=self.run_delivery()
        self.assertTrue(result.ok,result.log)
        self.assertEqual(1,self.posts)
        self.assertEqual(self.manifest(self.attachment_payload),result.commit_evidence[result.events[0]['event_id']].evidence.attachments)

    def test_corrupt_received_attachment_cannot_complete_or_resend(self):
        self.upload_owned_attachment();self.mode='corrupt_attachment'
        result=self.run_delivery()
        self.assertFalse(result.ok,result.log)
        self.assertEqual(1,self.posts)
        self.assertIn('unverified',str(result.log))

    def run_delivery(self, approve=True):
        receipt = DeliveredMessageReceipt(target='page', account='fixture-sender',
            recipient='fixture-recipient', text=self.text, attachments=self.manifest(self.attachment_payload), bind_request=self.bind,
            read_inbox=self.read_inbox, timeout_seconds=.15, poll_interval=.01)
        safety = SafetyContract(effects=['transmit'], reversibility='irreversible',
                                resource=Resource(object_type='fixture message', scope='entry'))
        binding = ContextBinding(role='button', title='Send', operation='click')
        flow = Flow('owned_inbox_send', 'page', [Step('click', Selector(role='button', title='Send'),
                                                   safety=safety, binding=binding)])
        self.last_receipt, self.last_flow = receipt, flow
        # Only the test's own loopback service, accounts and message are in
        # this authorization scope; the adapter itself never approves.
        approvals = {0:issue_approval(binding, safety)} if approve else {}
        return replay(self.driver, flow, approvals=approvals,
                      prepare_commit=receipt.prepare_commit, after_commit=receipt.after_commit,
                      commit_expected=receipt.commit_expected)

    def test_clock_failure_after_actual_delivery_never_confirms_or_resends(self):
        original=DeliveredMessageReceipt
        def receipt_with_fault(**kwargs):
            kwargs['clock']=lambda:float('nan') if self.posts else 0.
            return original(**kwargs)
        with patch(__name__+'.DeliveredMessageReceipt',side_effect=receipt_with_fault):
            result=self.run_delivery()
        self.assertFalse(result.ok,result.log)
        self.assertEqual(1,self.posts)
        self.assertEqual(self.text,self.messages[self.request_id]['body'])
        self.assertFalse(result.commit_evidence)
        self.assertNotEqual('confirmed',result.events[0]['state'])
        self.assertIn('host_commit_evidence_failed',str(result.log))
        # A new host receipt must still see the existing receiving record and
        # refuse preparation; a clock error never permits another send.
        second=self.run_delivery()
        self.assertFalse(second.ok,second.log)
        self.assertEqual(1,self.posts)
        self.assertIn('delivery_already_present',str(second.log))

    def test_click_delivers_one_exact_new_message_with_independent_http_evidence(self):
        result = self.run_delivery()
        self.assertTrue(result.ok, result.log)
        self.assertEqual(1, self.posts)
        self.assertEqual(1, len(self.messages))
        self.assertEqual(self.text, self.messages[self.request_id]['body'])
        self.assertEqual('confirmed', result.events[0]['state'])
        event = result.events[0]
        proof = result.commit_evidence[event['event_id']]
        self.assertEqual(self.messages[self.request_id]['message_id'], proof.evidence.receipt_id)
        self.assertIsNone(evidence_error({**event, 'commit_evidence':proof}, {**self.last_receipt.commit_expected,'보낸 내용':self.text}))

    def test_body_change_during_preparation_inbox_read_sends_no_request(self):
        original=self.read_inbox
        def read_then_change(request_id):
            absent=original(request_id)
            if self.posts==0:
                self.driver._page.evaluate("document.querySelector('#body').value='changed during inbox read'")
            return absent
        self.read_inbox=read_then_change
        result=self.run_delivery()
        self.assertFalse(result.ok,result.log)
        self.assertEqual(0,self.posts)
        self.assertEqual({},self.messages)
        self.assertIsNone(self.last_receipt._prepared)

    def test_recipient_or_request_identity_change_during_absence_read_sends_nothing(self):
        original=self.read_inbox
        for mode in ('recipient','request_id'):
            with self.subTest(mode=mode):
                self.driver._page.evaluate("id=>{document.querySelector('#recipient').value='fixture-recipient';document.querySelector('#send').dataset.request=id}",self.request_id)
                def read_then_change(request_id):
                    absent=original(request_id)
                    if mode=='recipient':
                        self.driver._page.evaluate("document.querySelector('#recipient').value='another-owned-recipient'")
                    else:
                        self.driver._page.evaluate("document.querySelector('#send').dataset.request='another-owned-request'")
                    return absent
                self.read_inbox=read_then_change
                result=self.run_delivery()
                self.assertFalse(result.ok,result.log)
                self.assertEqual(0,self.posts)
                self.assertEqual({},self.messages)
                self.assertIsNone(self.last_receipt._prepared)

    def test_selected_attachment_replacement_during_absence_read_sends_nothing(self):
        self.upload_owned_attachment()
        replacement=tempfile.TemporaryDirectory();self.addCleanup(replacement.cleanup)
        path=Path(replacement.name)/'검토.txt';path.write_bytes(b'replacement owned bytes')
        original=self.read_inbox
        def read_then_replace(request_id):
            absent=original(request_id)
            self.driver._page.set_input_files('#attachments',str(path))
            return absent
        self.read_inbox=read_then_replace
        result=self.run_delivery()
        self.assertFalse(result.ok,result.log)
        self.assertEqual(0,self.posts)
        self.assertEqual({},self.messages)
        self.assertIsNone(self.last_receipt._prepared)


    def run_general_delivery(self, *, lose_response=False, fail_receipt=False):
        from tacto.general import run
        from tacto.contract import Envelope
        from tacto.outcome import Check, OutcomeSpec
        receipt = DeliveredMessageReceipt(target='page', account='fixture-sender',
            recipient='fixture-recipient', text=self.text, attachments=(), bind_request=self.bind,
            read_inbox=self.read_inbox, timeout_seconds=.15, poll_interval=.01)
        original = self.driver.act
        def act(*args, **kwargs):
            response = original(*args, **kwargs)
            self.driver._page.wait_for_function("document.querySelector('#status').textContent === 'Sent'")
            if fail_receipt:
                self.inbox_auth_failure = True
            if lose_response:
                raise TimeoutError('private transport details')
            return response
        self.driver.act = act
        self.plan_calls = 0
        def plan(goal, candidates, history):
            self.plan_calls += 1
            if history and not fail_receipt:
                return {'done':True}
            # If completion is uncertain, an unsafe second plan would send
            # again. The runtime must stop before calling this planner again.
            candidate = next(c for c in candidates if c['label'] == 'Send')
            return {'actions':[{'do':'click','candidate':candidate}], 'expect':'Sent'}
        spec = OutcomeSpec(kind='send_message', must_read=(self.text,),
            required=(Check('recipient','request','before_commit','reports'),
                      Check('account','prior','before_commit','reports'),
                      Check('body','request','before_commit','reports'),
                      Check('once','request','after','exactly_once')),
            expected={**receipt.commit_expected,'recipient':'fixture-recipient',
                      'account':'fixture-sender','body':self.text})
        def where(name, candidates):
            candidate = next(c for c in candidates if c['match'] == name)
            return {'pick':candidates.index(candidate),'candidate':candidate}
        result = run(self.driver, 'page', 'send one owned loopback message', plan,
            envelope=Envelope(quote='send one owned loopback message',
                allows=frozenset({'read','transmit'}), scope='entry'),
            spec=spec, ask_where=where, approve=lambda _:True,
            prepare_commit=receipt.prepare_commit, after_commit=receipt.after_commit)
        return result, spec

    def test_general_lost_response_resolves_from_independent_inbox_without_resend(self):
        from tacto.outcome import DONE, verify
        result, spec = self.run_general_delivery(lose_response=True)
        self.assertTrue(result.ok, result.why)
        outcome = verify(spec,result,result.after_tree)
        self.assertEqual(DONE, outcome.state, outcome)
        self.assertEqual(1,self.posts)
        self.assertEqual(1,len(result.events))
        event = result.events[0]
        self.assertEqual('confirmed',event['state'])
        self.assertEqual({'type':'error','effect':'unknown'},event['dispatch_response'])
        self.assertNotIn('private transport details',str(result))

    def test_general_failed_inbox_receipt_stops_before_second_plan_or_send(self):
        result, spec = self.run_general_delivery(fail_receipt=True)
        self.assertFalse(result.ok,result.why)
        self.assertEqual(1,self.plan_calls)
        self.assertEqual(1,self.posts)
        self.assertEqual(1,len(self.messages))
        self.assertEqual(1,len(result.events))
        self.assertEqual('unknown',result.certainty)
        self.assertEqual({},result.commit_evidence)

    def test_refusal_before_dispatch_cannot_be_resolved_by_an_inbox_poll(self):
        self.driver.act = lambda *args, **kwargs: {
            'type':'error', 'error':'fixture_refusal', 'effect':'not_started'}
        result = self.run_delivery()
        self.assertFalse(result.ok)
        self.assertEqual(0, self.posts)
        self.assertEqual({}, result.commit_evidence)

    def test_known_refusal_is_not_overridden_by_a_matching_receiving_record(self):
        def refused(*args,**kwargs):
            # A service record becoming visible cannot turn known non-dispatch
            # into this event's send. Only the owned receiver is populated.
            with self.lock:
                self.messages[self.request_id]={'request_id':self.request_id,
                    'message_id':secrets.token_hex(16),'account':'fixture-sender',
                    'recipient':'fixture-recipient','body':self.text,'state':'delivered'}
            return {'type':'error','error':'fixture_refusal','effect':'not_started'}
        self.driver.act=refused
        result=self.run_delivery()
        self.assertFalse(result.ok)
        self.assertEqual(0,self.posts)
        self.assertEqual(1,len(self.messages))
        self.assertEqual({},result.commit_evidence)
        self.assertEqual(0,self.inbox_reads_after_dispatch)
        self.assertEqual(1,self.inbox_reads)  # Only preparation checked absence.

    def test_sender_sent_marker_without_delivery_is_unverified(self):
        self.mode = 'noop'
        result = self.run_delivery()
        self.assertFalse(result.ok)
        self.assertEqual(1, self.posts)
        self.assertEqual({}, self.messages)
        self.assertEqual('commit_unverified', result.log[-1]['error'])

    def test_corrupt_receiver_body_cannot_supply_done(self):
        self.mode = 'corrupt'
        result = self.run_delivery()
        self.assertFalse(result.ok)
        self.assertEqual(1, self.posts)
        self.assertEqual('commit_unverified', result.log[-1]['error'])

    def test_lost_dispatch_reply_uses_receipt_and_never_resends(self):
        act = self.driver.act
        def lost(*args, **kwargs):
            act(*args, **kwargs)
            raise TimeoutError('fixture response lost after dispatch')
        self.driver.act = lost
        result = self.run_delivery()
        self.assertTrue(result.ok, result.log)
        self.assertEqual(1, self.posts)
        self.assertEqual(1, len(self.messages))
        self.assertEqual({'type':'error', 'effect':'unknown'}, result.events[0]['dispatch_response'])

    def test_event_change_after_actual_inbox_read_refuses_evidence_without_resending(self):
        self.upload_owned_attachment()
        act=self.driver.act
        def lost(*args,**kwargs):
            act(*args,**kwargs)
            raise TimeoutError('fixture reply lost after dispatch')
        self.driver.act=lost
        after=DeliveredMessageReceipt.after_commit
        def after_with_changed_binding(receipt,event,response,context):
            read=receipt._inbox
            def changed(request):
                delivered=read(request)
                if delivered is not None:event['event_id']='changed-after-real-inbox-read'
                return delivered
            receipt._inbox=changed
            return after(receipt,event,response,context)
        with patch.object(DeliveredMessageReceipt,'after_commit',after_with_changed_binding):
            result=self.run_delivery()
        self.assertFalse(result.ok,result.log)
        self.assertEqual(1,self.posts)
        self.assertEqual(1,len(self.messages))
        self.assertEqual(self.text,self.messages[self.request_id]['body'])
        self.assertEqual(self.attachment_payload,self.messages[self.request_id]['attachments'])
        self.assertEqual({},result.commit_evidence)
        self.assertTrue(self.last_receipt._after_used)

    def test_existing_request_receipt_stops_before_another_post(self):
        self.messages[self.request_id] = {
            'request_id':self.request_id, 'message_id':'old-message',
            'account':'fixture-sender', 'recipient':'fixture-recipient',
            'body':self.text, 'state':'delivered'}
        result = self.run_delivery()
        self.assertFalse(result.ok)
        self.assertEqual(0, self.posts)

    def test_refused_approval_never_transmits(self):
        result = self.run_delivery(approve=False)
        self.assertFalse(result.ok)
        self.assertEqual(0, self.posts)
        self.assertEqual({}, self.messages)

    def test_gated_execution_needs_fresh_adapter_before_approved_new_request(self):
        gated = self.run_delivery(approve=False)
        self.assertFalse(gated.ok)
        self.assertEqual(0, self.posts)
        receipt, flow = self.last_receipt, self.last_flow
        step = flow.steps[0]
        reused = replay(self.driver, flow, approvals={0:issue_approval(step.binding, step.safety)},
                        prepare_commit=receipt.prepare_commit, after_commit=receipt.after_commit,
                        commit_expected=receipt.commit_expected)
        self.assertFalse(reused.ok)
        self.assertEqual('host_evidence_preparation_failed', reused.log[-1]['detail']['error'])
        self.assertEqual(0, self.posts)
        original_request = self.request_id
        self.request_id = secrets.token_hex(16)
        self.driver.goto(self.origin + '/')
        approved = self.run_delivery(approve=True)
        self.assertTrue(approved.ok, approved.log)
        self.assertEqual(1, self.posts)
        self.assertNotIn(original_request, self.messages)
        self.assertEqual(self.text, self.messages[self.request_id]['body'])

    def test_changed_recipient_in_actual_form_is_refused_before_dispatch(self):
        self.driver._page.eval_on_selector('#recipient', "el=>el.value='other-recipient'")
        result = self.run_delivery()
        self.assertFalse(result.ok)
        self.assertEqual(0, self.posts)

    def test_http_read_outage_recovers_with_one_send_and_exact_receiving_body(self):
        self.inbox_failures = 2
        result = self.run_delivery()
        self.assertTrue(result.ok, result.log)
        self.assertEqual(3, self.inbox_reads_after_dispatch)
        self.assertEqual(1, self.posts)
        self.assertEqual(1, len(self.messages))
        self.assertEqual(self.text, self.messages[self.request_id]['body'])

    def test_lost_dispatch_reply_and_http_read_outage_do_not_resend(self):
        self.inbox_failures = 1
        act = self.driver.act

        def lost(*args, **kwargs):
            act(*args, **kwargs)
            raise TimeoutError('fixture reply lost after one dispatch')

        self.driver.act = lost
        result = self.run_delivery()
        self.assertTrue(result.ok, result.log)
        self.assertEqual(2, self.inbox_reads_after_dispatch)
        self.assertEqual(1, self.posts)
        self.assertEqual(1, len(self.messages))
        self.assertEqual({'type':'error', 'effect':'unknown'}, result.events[0]['dispatch_response'])

    def test_permanent_http_outage_remains_unverified_and_never_resends(self):
        self.inbox_failures = float('inf')
        result = self.run_delivery()
        self.assertFalse(result.ok)
        self.assertEqual(1, self.posts)
        self.assertEqual(1, len(self.messages))
        self.assertGreater(self.inbox_reads_after_dispatch, 1)
        self.assertEqual({}, result.commit_evidence)
        self.assertEqual('commit_unverified', result.log[-1]['error'])

    def test_http_authentication_failure_after_delivery_is_not_retried(self):
        self.inbox_auth_failure = True
        result = self.run_delivery()
        self.assertFalse(result.ok)
        self.assertEqual(1, self.posts)
        self.assertEqual(1, self.inbox_reads_after_dispatch)
        self.assertEqual({}, result.commit_evidence)
        self.assertEqual('commit_unverified', result.log[-1]['error'])

    def run_registered_upload(self, *, approve=True, corrupt=False, after_read=False, general=False, live_provider=None, allow_upload=True, change_after_approval=False, paged=False, registration_count=2, paging_budget=2500, max_pages=8, planner_goal=None, wanted_scope='owned_attachment', selection_callback='send'):
        from tacto.browser_upload import BrowserUploadDriver, BrowserUploadScope, BrowserUploadFile
        from tacto.flow import pin_params
        # Only this owned loopback form opts into automatic send on selection.
        if selection_callback=='send_and_clear':
            self.driver._page.evaluate("() => { attachments.onchange=()=>{const pending=send.onclick();attachments.value='';return pending}; }")
        elif selection_callback=='clear_only':
            self.driver._page.evaluate("() => { attachments.onchange=()=>{attachments.value=''}; }")
        elif selection_callback=='send':
            self.driver._page.evaluate("() => { attachments.onchange=()=>send.onclick(); }")
        else:raise ValueError('unsupported owned selection callback')
        self.assertEqual(0,self.posts,'fixture configuration must not transmit')
        payload=BrowserUploadFile('검토.txt','소유 첨부 원문 🧪'.encode(),'text/plain')
        self.attachment_payload=[{'filename':payload.filename,'content':base64.b64encode(payload.content).decode()}]
        selector=Selector(title='attachments')
        scope=BrowserUploadScope(self.origin,selector,selector,(payload,))
        scopes={'wrong_attachment':BrowserUploadScope(self.origin,selector,selector,(BrowserUploadFile('wrong.txt',b'wrong owned bytes'),))}
        for index in range(registration_count-2):
            scopes[f'distractor_{index:03}']=BrowserUploadScope(self.origin,selector,selector,
                (BrowserUploadFile('무관한_'+'한글'*70+f'_{index:03}.txt',b'owned distractor bytes'),))
        scopes[wanted_scope]=scope
        adapter=BrowserUploadDriver(self.driver,scopes)
        params=adapter.parameters(wanted_scope)
        expected_manifest=self.manifest(self.attachment_payload)
        def bind_upload(event):
            current=self.driver._latest['page']
            assert current['obs_id']==event['obs_id']
            assert event['action']['attachment_digest']==scope.digest
            handle=self.driver._locator(current['nodes'][event['action']['element_index']])
            try:
                actual=handle.evaluate("el=>{if(el.id!=='attachments'||!el.isConnected||el.disabled)throw Error('wrong input');return {request_id:send.dataset.request,account:account.value,recipient:recipient.value,body:body.value}}")
            finally:handle.dispose()
            return PendingDelivery(actual['request_id'],event['target'],event['obs_id'],event['action_digest'],
                actual['account'],actual['recipient'],hashlib.sha256(actual['body'].encode()).hexdigest(),expected_manifest)
        receipt=DeliveredMessageReceipt(target='page',account='fixture-sender',recipient='fixture-recipient',
            text=self.text,attachments=expected_manifest,bind_request=bind_upload,read_inbox=self.read_inbox,
            timeout_seconds=.15,poll_interval=.01)
        safety=SafetyContract(effects=['transmit'],reversibility='irreversible',
            resource=Resource(object_type='owned attachment',scope='entry'))
        binding=ContextBinding(title='attachments',operation='upload',params_digest=pin_params(params))
        steps=[Step('upload',selector,params=params,safety=safety,binding=binding)]
        if after_read:
            from tacto.flow import Postcondition
            steps.insert(0,Step('read',Selector(title='body'),
                safety=SafetyContract(effects=['read']),
                postcondition=Postcondition(field=Selector(title='body'),attribute='Value',equals=self.text.replace('\n',' '))))
        flow=Flow('owned_upload', 'page',steps)
        # This host grants only the owned payload, form and receiver.
        approvals={int(after_read):issue_approval(binding,safety)} if approve else {}
        if corrupt:self.mode='corrupt_attachment'
        if general:
            from tacto.general import run
            from tacto.contract import Envelope
            from tacto.outcome import OutcomeSpec, Check
            from tacto.ask import what_to_do_here, ask
            self.plan_calls=0;self.upload_approval_descriptions=[];self.paged_plan_records=[]
            def plan(goal,candidates,history):
                self.plan_calls+=1
                if history:
                    return {'done':True}
                question=what_to_do_here(goal,candidates,history)
                if live_provider:
                    if paged:
                        from tacto.ask import ask_here_paged
                        import tiktoken
                        counter=lambda text:len(tiktoken.get_encoding('o200k_base').encode_ordinary(text))
                        answer=ask_here_paged(goal,candidates,history,provider=live_provider,
                            budget=paging_budget,max_pages=max_pages,token_counter=counter,
                            timeout_seconds=90)
                        self.paged_plan_records.append({
                            'stop_reason':answer.get('stop_reason') if answer else None,
                            'paging':answer.get('paging') if answer else None,
                            'actions':[{k:a.get(k) for k in ('pick','do','scope','attachment_digest')} for a in answer.get('actions',[])] if answer else []})
                        return answer
                    return ask(question,provider=live_provider,timeout_seconds=60)
                candidate=next(c for c in candidates if 'upload' in c.get('can',()))
                return question.check({'actions':[{'pick':candidates.index(candidate),'do':'upload','scope':wanted_scope}]})
            spec=OutcomeSpec(kind='send_message',must_read=(self.text,),
                required=(Check('recipient','request','before_commit','reports'),
                          Check('account','prior','before_commit','reports'),
                          Check('body','request','before_commit','reports'),
                          Check('once','request','after','exactly_once')),
                expected={**receipt.commit_expected,'recipient':'fixture-recipient',
                          'account':'fixture-sender','body':self.text})
            def where(name,candidates):
                candidate=next(c for c in candidates if c['match']==name)
                return {'pick':candidates.index(candidate),'candidate':candidate}
            def approval(description):
                self.upload_approval_descriptions.append(description)
                if change_after_approval:
                    self.driver._page.evaluate("() => {attachments.setAttribute('aria-label','changed attachment')} ")
                return approve
            result=run(adapter,'page',
                planner_goal or 'Upload only the registered owned_attachment payload 검토.txt to fixture-recipient from fixture-sender. Never upload wrong_attachment or wrong.txt.',
                plan,envelope=Envelope(quote='send owned attachment',
                    allows=frozenset({'read','transmit'} if allow_upload else {'read'}),scope='entry'),
                spec=spec,ask_where=where,approve=approval,
                prepare_commit=receipt.prepare_commit,after_commit=receipt.after_commit)
            self.general_upload_spec=spec
            return result
        result=replay(adapter,flow,approvals=approvals,
            prepare_commit=receipt.prepare_commit,
            after_commit=receipt.after_commit,
            commit_expected=receipt.commit_expected)
        return result

    def test_registered_upload_flow_confirms_exact_new_http_receipt(self):
        result=self.run_registered_upload()
        self.assertTrue(result.ok,result.log)
        self.assertEqual(1,self.posts)
        self.assertEqual(1,len(self.messages))
        self.assertEqual('confirmed',result.events[0]['state'])
        proof=result.commit_evidence[result.events[0]['event_id']]
        self.assertEqual(self.manifest(self.attachment_payload),proof.evidence.attachments)
        self.assertNotIn('소유 첨부 원문',str(result.log))
        self.assertEqual('upload',result.events[0]['action']['name'])

    def test_registered_upload_flow_without_approval_never_selects_or_sends(self):
        result=self.run_registered_upload(approve=False)
        self.assertFalse(result.ok,result.log)
        self.assertEqual(0,self.posts)
        self.assertEqual(0,self.driver._page.evaluate('attachments.files.length'))
        self.assertEqual([],result.events)

    def test_registered_upload_corrupt_receiving_bytes_cannot_complete_or_resend(self):
        result=self.run_registered_upload(corrupt=True)
        self.assertFalse(result.ok,result.log)
        self.assertEqual(1,self.posts)
        self.assertEqual({},result.commit_evidence)
        self.assertEqual('commit_unverified',result.log[-1]['error'])

    def test_registered_upload_after_observed_read_uses_fresh_wait_tree(self):
        result=self.run_registered_upload(after_read=True)
        self.assertTrue(result.ok,result.log)
        self.assertEqual(1,self.posts)
        self.assertEqual(['read','upload'],[event['action']['name'] for event in result.events])
        self.assertEqual('confirmed',result.events[-1]['state'])

    def test_general_registered_upload_chooses_host_scope_and_receives_exact_bytes_once(self):
        from tacto.outcome import verify, DONE
        result=self.run_registered_upload(general=True)
        self.assertTrue(result.ok,result.why)
        self.assertEqual(DONE,verify(self.general_upload_spec,result,result.after_tree).state)
        self.assertEqual(1,self.posts)
        self.assertEqual(1,len(result.events))
        self.assertEqual('owned_attachment',result.events[0]['action']['scope'])
        self.assertEqual('confirmed',result.events[0]['state'])
        self.assertIn('검토.txt',self.upload_approval_descriptions[0])
        self.assertNotIn('wrong.txt',self.upload_approval_descriptions[0])
        recorded=next(a for visit in result.visits for a in visit.actions if a['do']=='upload')
        self.assertEqual('owned_attachment',recorded['scope'])
        self.assertEqual(result.events[0]['action']['attachment_digest'],recorded['attachment_digest'])
        from tacto.promote import candidate_from, to_flow
        from tacto.contract import Envelope
        candidate=candidate_from(result,'page','send owned attachment',
            envelope=Envelope(quote='send owned attachment',allows=frozenset({'read','transmit'}),scope='entry'))
        promoted=to_flow(candidate,'owned_upload')
        uploaded=next(step for step in promoted.steps if step.action=='upload')
        self.assertEqual(recorded['scope'],uploaded.params['scope'])
        self.assertEqual(recorded['attachment_digest'],uploaded.params['attachment_digest'])


    def test_general_registered_upload_declined_approval_selects_and_sends_nothing(self):
        result=self.run_registered_upload(general=True,approve=False)
        self.assertFalse(result.ok,result.why)
        self.assertEqual(0,self.posts)
        self.assertEqual(0,self.driver._page.evaluate('attachments.files.length'))
        self.assertEqual([],result.events)

    def test_general_registered_upload_outside_envelope_cannot_ask_for_approval(self):
        result=self.run_registered_upload(general=True,allow_upload=False)
        self.assertFalse(result.ok,result.why)
        self.assertEqual([],self.upload_approval_descriptions)
        self.assertEqual(0,self.posts)
        self.assertEqual(0,self.driver._page.evaluate('attachments.files.length'))

    def test_general_registered_upload_corrupt_receipt_never_replans_or_resends(self):
        result=self.run_registered_upload(general=True,corrupt=True)
        self.assertFalse(result.ok,result.why)
        self.assertEqual(1,self.posts)
        self.assertEqual(1,self.plan_calls)
        self.assertEqual({},result.commit_evidence)

    def test_general_registered_upload_rechecks_opener_after_approval(self):
        result=self.run_registered_upload(general=True,change_after_approval=True)
        self.assertFalse(result.ok,result.why)
        self.assertEqual(1,len(self.upload_approval_descriptions))
        self.assertEqual(0,self.posts)
        self.assertEqual(0,self.driver._page.evaluate('attachments.files.length'))
        self.assertEqual([],result.events)

    def test_upload_cleared_after_send_is_settled_by_exact_receipt_once(self):
        result=self.run_registered_upload(selection_callback='send_and_clear')
        self.assertTrue(result.ok,result.log)
        self.assertEqual(1,self.posts)
        self.assertEqual('confirmed',result.events[0]['state'])
        self.assertEqual({'type':'error','effect':'unknown'},result.events[0]['dispatch_response'])
        self.assertEqual(0,self.driver._page.evaluate('attachments.files.length'))

    def test_general_upload_cleared_after_send_receipt_prevents_resend(self):
        result=self.run_registered_upload(general=True,selection_callback='send_and_clear')
        self.assertTrue(result.ok,result.why)
        self.assertTrue(result.verified)
        self.assertEqual(1,self.posts)
        self.assertEqual('confirmed',result.events[0]['state'])
        self.assertEqual({'type':'error','effect':'unknown'},result.events[0]['dispatch_response'])
        self.assertEqual(0,self.driver._page.evaluate('attachments.files.length'))

    def test_general_upload_cleared_without_receiving_stops_before_replan(self):
        result=self.run_registered_upload(general=True,selection_callback='clear_only')
        self.assertFalse(result.ok,result.why)
        self.assertEqual(0,self.posts)
        self.assertEqual(1,self.plan_calls)
        self.assertEqual(1,len(result.events))
        self.assertEqual('unknown',result.events[0]['state'])
        self.assertEqual({},result.commit_evidence)
        self.assertEqual(0,self.driver._page.evaluate('attachments.files.length'))
