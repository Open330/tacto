"""Real headless Chromium clicks and an independent local receiving inbox."""
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import importlib.util
import json
import secrets
import threading
import unittest
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from nlc.commit_evidence import evidence_error
from nlc.contract import ContextBinding, Resource, SafetyContract, issue_approval
from nlc.delivery_receipt import (DeliveredMessageReceipt, InboxDelivery, PendingDelivery,
                                 InboxTemporarilyUnavailable)
from nlc.flow import Flow, Selector, Step, replay


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright required')
class DeliveryReceiptIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.request_id = secrets.token_hex(16)
        self.reader_key = secrets.token_hex(32)
        self.text = '소유 수신함 검증\n정확한 본문'
        self.messages = {}
        self.posts = 0
        self.inbox_failures = 0
        self.inbox_auth_failure = False
        self.inbox_reads_after_dispatch = 0
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
                    page = '''<input id="account" readonly><input id="recipient" readonly>
                        <textarea id="body"></textarea><button id="send">Send</button>
                        <output id="status">Ready</output><script>
                        const initial = INITIAL;
                        account.value=initial.account;recipient.value=initial.recipient;
                        body.value=initial.body;send.dataset.request=initial.request_id;
                        send.onclick=async()=>{
                          await fetch('/send', {method:'POST', headers:{'Content-Type':'application/json'},
                            body:JSON.stringify({request_id:send.dataset.request,
                              account:account.value,recipient:recipient.value,body:body.value})});
                          document.querySelector('#status').textContent='Sent';
                        };</script>'''.replace('INITIAL', initial)
                    self.reply(200, page, 'text/html')
                    return
                if self.headers.get('X-Inbox-Reader') != owner.reader_key:
                    self.reply(403, {})
                    return
                with owner.lock:
                    if owner.posts:
                        owner.inbox_reads_after_dispatch += 1
                    unavailable = bool(owner.posts and owner.inbox_failures > 0)
                    if unavailable:
                        owner.inbox_failures -= 1
                    unauthorized = owner.posts and owner.inbox_auth_failure
                    stored = owner.messages.get(self.path.removeprefix('/inbox/'))
                    message = ({key:value for key, value in stored.items() if key != 'body'}
                               if stored else None)
                    if message:
                        message['body_sha256'] = hashlib.sha256(stored['body'].encode()).hexdigest()
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
                        'recipient':'fixture-recipient', 'body':owner.text})
                    if valid and owner.mode != 'noop':
                        text = payload['body'] if owner.mode != 'corrupt' else 'wrong'
                        # One immutable service identity per request; duplicate
                        # requests cannot insert a second inbox message.
                        owner.messages.setdefault(owner.request_id, {
                            'request_id':owner.request_id, 'message_id':secrets.token_hex(16),
                            'account':payload['account'], 'recipient':payload['recipient'],
                            'body':text,
                            'state':'delivered'})
                self.reply(200 if valid else 400, {'sender_marker':'Sent'})

        self.server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.addCleanup(self.stop_server)
        self.origin = f'http://127.0.0.1:{self.server.server_port}'
        from nlc.browser import BrowserDriver
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
                return InboxDelivery(**json.load(response))
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
            actual = handle.evaluate('''el => {
                if (el.id !== 'send' || !el.isConnected || el.disabled) throw Error('wrong control');
                return {request_id:el.dataset.request,account:document.querySelector('#account').value,
                  recipient:document.querySelector('#recipient').value,body:document.querySelector('#body').value};
            }''')
        finally:
            handle.dispose()
        return PendingDelivery(actual['request_id'], event['target'], event['obs_id'],
            event['action_digest'], actual['account'], actual['recipient'],
            hashlib.sha256(actual['body'].encode()).hexdigest())

    def run_delivery(self, approve=True):
        receipt = DeliveredMessageReceipt(target='page', account='fixture-sender',
            recipient='fixture-recipient', text=self.text, bind_request=self.bind,
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
        self.assertIsNone(evidence_error({**event, 'commit_evidence':proof}, {'보낸 내용':self.text}))

    def test_refusal_before_dispatch_cannot_be_resolved_by_an_inbox_poll(self):
        self.driver.act = lambda *args, **kwargs: {
            'type':'error', 'error':'fixture_refusal', 'effect':'not_started'}
        result = self.run_delivery()
        self.assertFalse(result.ok)
        self.assertEqual(0, self.posts)
        self.assertEqual({}, result.commit_evidence)

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
