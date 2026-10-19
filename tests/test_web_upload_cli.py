"""Public CLI upload boundary against an owned browser and independent inbox."""
import base64
import contextlib
import copy
import io
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from tacto import cli
from tacto.web_upload_cli import load_registration, ConfigurationRefused, _canonical_url
from tests import test_delivery_receipt_integration as fixture_module


@unittest.skipUnless(importlib.util.find_spec('playwright'), 'optional Playwright required')
class WebUploadCLITests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixture_module.DeliveryReceiptIntegrationTests()
        self.fixture.auto_upload = True
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.setUp()
        self.fixture.driver.close()
        self.folder = tempfile.TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.root = Path(self.folder.name)
        self.file = self.root / '검토.txt'
        self.data = '소유 첨부 원문 🧪'.encode()
        self.file.write_bytes(self.data)
        f = self.fixture
        f.attachment_payload = [{'filename':self.file.name,'content':base64.b64encode(self.data).decode()}]
        self.config = {'version':1,'url':f.origin+'/', 'scopes': {'owned': {
            'opener':{'title':'attachments'},'chooser':{'title':'attachments'},
            'files':[{'path':str(self.file),'filename':self.file.name,'mime_type':'text/plain'}],
            'receipt':{'account':'fixture-sender','recipient':'fixture-recipient','text':f.text,
                'request_id':f.request_id,'request_control':{'role':'button','title':'Send'},
                'request_attribute':'data-request','fields':{name:{'title':name} for name in ('account','recipient','body')},
                'inbox_url':f.origin+'/inbox/'+f.request_id,
                'headers':{'X-Inbox-Reader':f.reader_key}, 'timeout_seconds':.5}}}}
        self.path = self.root/'registration.json'
        self.write_config()

    def write_config(self):
        self.path.write_text(json.dumps(self.config,ensure_ascii=False))
        self.path.chmod(0o600)

    def run_cli(self, *options):
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            code = cli.main(['web-upload',str(self.path),'--scope','owned',*options])
        self.assertNotIn(self.fixture.reader_key, output.getvalue())
        return code, json.loads(output.getvalue().splitlines()[-1])

    def test_check_reads_only_selected_registration_without_browser_or_network(self):
        self.config['scopes']['unselected'] = {'files':[{'path':'/does/not/exist'}]}
        self.write_config()
        with patch('tacto.browser.BrowserDriver', side_effect=AssertionError('no browser')):
            code, result = self.run_cli('--check')
        self.assertEqual(0,code); self.assertFalse(result['authorizes_input'])
        self.assertEqual(0,self.fixture.posts); self.assertEqual(0,self.fixture.inbox_reads)

    def test_snapshot_is_immutable_after_original_file_changes(self):
        registered = load_registration(self.path,'owned')
        self.file.write_bytes(b'changed')
        self.assertEqual(self.data,registered.scope.files[0].content)

    def test_symlink_and_nonprivate_configuration_refuse_before_browser(self):
        self.path.chmod(0o644)
        with patch('tacto.browser.BrowserDriver', side_effect=AssertionError('no browser')):
            self.assertEqual(1,self.run_cli('--yes')[0])
            self.path.chmod(0o600)
            self.file.unlink(); self.file.symlink_to(self.path)
            self.assertEqual(1,self.run_cli('--yes')[0])
        self.assertEqual(0,self.fixture.posts)

    def test_no_terminal_approval_means_no_input(self):
        with patch('sys.stdin.isatty',return_value=False):
            code,result=self.run_cli()
        self.assertEqual(1,code); self.assertEqual('approval_declined',result['error'])
        self.assertEqual([],result['events']); self.assertEqual(0,self.fixture.posts)

    def test_public_upload_confirms_independent_exact_receipt(self):
        report=self.root/'report.json'
        code,result=self.run_cli('--yes','--report',str(report))
        self.assertEqual(0,code,result)
        self.assertEqual(1,result['confirmed_receipts']); self.assertEqual(1,self.fixture.posts)
        self.assertEqual('confirmed',result['events'][0]['state'])
        self.assertEqual(result,json.loads(report.read_text()))
        self.assertEqual(0o600,report.stat().st_mode & 0o777)
        self.assertEqual(self.fixture.attachment_payload,self.fixture.messages[self.fixture.request_id]['attachments'])

    def test_equivalent_loopback_urls_deliver_exactly_once(self):
        # Both navigation and the authenticated inbox use their canonical URL.
        for host in ('LOCALHOST', '127.1', '0x7f000001'):
            with self.subTest(host=host):
                alternate = self.fixture.origin.replace('127.0.0.1', host)
                self.config['url'] = alternate + '/'
                self.config['scopes']['owned']['receipt']['inbox_url'] = alternate + '/inbox/' + self.fixture.request_id
                self.fixture.messages.clear()
                before = self.fixture.posts
                self.write_config()
                code, result = self.run_cli('--yes')
                self.assertEqual(0, code, result)
                self.assertEqual(1, result['confirmed_receipts'])
                self.assertEqual(before + 1, self.fixture.posts)
                message = self.fixture.messages[self.fixture.request_id]
                self.assertEqual(self.fixture.attachment_payload, message['attachments'])
                self.assertEqual(self.fixture.text, message['body'])
                self.assertEqual('fixture-sender', message['account'])
                self.assertEqual('fixture-recipient', message['recipient'])

    def test_url_canonicalization_matches_blank_browser_without_navigation(self):
        from playwright.sync_api import sync_playwright
        urls = ['HTTP://LOCALHOST:80/a', 'https://example.com:443/',
                'https://faß.de/검토', 'https://BÜCHER.example/a b',
                'https://example。com/', 'http://[0:0:0:0:0:0:0:1]:80/',
                'http://127.1:8080/', 'http://0177.0.0.1/', 'http://0x7f000001/',
                'https://example.com/a/../b?x=한글']
        with sync_playwright() as runtime:
            browser = runtime.chromium.launch(headless=True)
            try:
                page = browser.new_page()
                for value in urls:
                    with self.subTest(value=value):
                        expected = page.evaluate('(s) => { const u = new URL(s); return [u.href, u.origin]; }', value)
                        self.assertEqual(tuple(expected), _canonical_url(value))
                self.assertEqual('about:blank', page.url)
            finally:
                browser.close()
        self.assertEqual(0, self.fixture.posts)
        self.assertEqual(0, self.fixture.inbox_reads)

    def test_ambiguous_or_invalid_urls_refuse_before_attachment_read(self):
        values = ['http://@example.com/', 'http://user:pass@example.com/',
                  'http://example.com/#', 'http://example.com/#fragment',
                  ' http://example.com/', 'http://example.com/ ',
                  'http://example.com/\x7f', 'http://example.com/\n',
                  'http://example.com\\@other.example/', 'http:example.com',
                  'http:///example.com/', 'http://example.com:65536/',
                  'http://[broken]/', 'file:///tmp/a', 'http://%40example.com/']
        from tacto.web_upload_cli import _snapshot
        for value in values:
            with self.subTest(value=value):
                self.config['url'] = value
                self.write_config()
                with patch('tacto.web_upload_cli._snapshot', wraps=_snapshot) as snapshot:
                    with self.assertRaises(ConfigurationRefused):
                        load_registration(self.path, 'owned')
                    self.assertEqual(1, snapshot.call_count)
        self.assertEqual(0, self.fixture.posts)

    def test_missing_url_dependency_refuses_without_browser(self):
        with patch.dict('sys.modules', {'ada_url': None}):
            with self.assertRaisesRegex(ConfigurationRefused, 'web_url_dependency_required'):
                _canonical_url('https://example.com/')

    def test_corrupt_receiver_attachment_stops_without_resend(self):
        self.fixture.mode='corrupt_attachment'
        code,result=self.run_cli('--yes')
        self.assertEqual(1,code,result); self.assertEqual(0,result['confirmed_receipts'])
        self.assertEqual(1,self.fixture.posts); self.assertNotEqual('confirmed',result['events'][0]['state'])

    def test_wrong_account_or_request_identity_never_selects(self):
        for key in ('account','request_id'):
            with self.subTest(key=key):
                config=copy.deepcopy(self.config)
                self.config['scopes']['owned']['receipt'][key]='wrong'
                self.write_config()
                code,result=self.run_cli('--yes')
                self.assertEqual(1,code,result); self.assertEqual(0,self.fixture.posts)
                self.config=config

    def test_existing_report_is_not_overwritten_and_does_not_dispatch(self):
        report=self.root/'report.json';report.write_text('original')
        self.assertEqual(1,self.run_cli('--yes','--report',str(report))[0])
        self.assertEqual('original',report.read_text());self.assertEqual(0,self.fixture.posts)

    def test_interruption_keeps_closed_report_and_original_interrupt(self):
        report=self.root/'interrupted.json'
        with patch('tacto.web_upload_cli.execute',side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):self.run_cli('--yes','--report',str(report))
        result=json.loads(report.read_text())
        self.assertFalse(result['ok']);self.assertEqual('unknown',result['effect'])
        self.assertEqual('web_upload_interrupted',result['error'])
        self.assertEqual(0,self.fixture.posts)

    def test_absent_receipt_after_selection_never_completes_or_resends(self):
        self.fixture.mode='noop'
        code,result=self.run_cli('--yes')
        self.assertEqual(1,code,result);self.assertEqual(0,result['confirmed_receipts'])
        self.assertEqual(1,self.fixture.posts)
        self.assertEqual('dispatched',result['events'][0]['state'])
        self.assertEqual('commit_unverified',result['log'][-1]['error'])

    def test_inbox_authentication_refusal_prevents_selection(self):
        self.config['scopes']['owned']['receipt']['headers']['X-Inbox-Reader']='invalid'
        self.write_config()
        code,result=self.run_cli('--yes')
        self.assertEqual(1,code,result);self.assertEqual(0,self.fixture.posts)

    def test_duplicate_json_keys_refuse_even_when_last_value_is_valid(self):
        text=self.path.read_text().replace('"version": 1','"version": 0, "version": 1')
        self.path.write_text(text)
        with self.assertRaises(ConfigurationRefused):load_registration(self.path,'owned')

    def test_fifo_attachment_is_refused_without_blocking(self):
        self.file.unlink();os.mkfifo(self.file)
        with self.assertRaises(ConfigurationRefused):load_registration(self.path,'owned')

    def test_inbox_redirect_cannot_forward_reader_credentials(self):
        requests=[]
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                requests.append(self.path)
                self.send_response(302)
                self.send_header('Location','/credential-target')
                self.send_header('Content-Length','0')
                self.end_headers()
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        try:
            self.config['scopes']['owned']['receipt']['inbox_url']=f'http://127.0.0.1:{server.server_port}/start'
            self.write_config()
            code,result=self.run_cli('--yes')
            self.assertEqual(1,code,result);self.assertEqual(0,self.fixture.posts)
            self.assertEqual(['/start'],requests)
        finally:
            server.shutdown();server.server_close();thread.join(timeout=2)

    def test_signed_entry_and_journal_restart_do_not_send_twice(self):
        journal=self.root/'journal'; journal.mkdir(mode=0o700)
        # Exercise this checkout's signed entry, not an unrelated installed
        # preview selected through the person's global command alias.
        command=[str(Path(__file__).resolve().parents[1]/'launcher/tacto'),'web-upload',str(self.path),'--scope','owned','--yes',
                 '--journal-dir',str(journal)]
        first=subprocess.run(command,capture_output=True,text=True,timeout=30)
        self.assertEqual(0,first.returncode,first.stdout+first.stderr)
        self.assertEqual(1,json.loads(first.stdout.splitlines()[-1])['confirmed_receipts'])
        original={p.name:p.read_bytes() for p in journal.iterdir()}
        self.assertTrue(original)
        # Remove receiving proof to exercise the durable input barrier itself.
        # This is fixture fault injection, not a proposed recovery operation.
        self.fixture.messages.clear()
        second=subprocess.run(command,capture_output=True,text=True,timeout=30)
        self.assertNotEqual(0,second.returncode,second.stdout+second.stderr)
        self.assertEqual(1,self.fixture.posts)
        result=json.loads(second.stdout.splitlines()[-1])
        self.assertEqual('dispatch_journal_requires_reconciliation',result['log'][-1]['detail']['error'])
        self.assertEqual(original,{p.name:p.read_bytes() for p in journal.iterdir()})
        self.assertNotIn(self.fixture.reader_key,first.stdout+second.stdout)
