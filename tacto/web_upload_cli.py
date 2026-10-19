"""Explicit browser upload configuration and independent JSON inbox contract."""
import argparse
from dataclasses import dataclass, asdict
import hashlib
import json
import math
import os
from pathlib import Path
import re
import stat
import time
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler


class ConfigurationRefused(ValueError):
    pass


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ConfigurationRefused('duplicate_json_key')
        result[key] = value
    return result


def _snapshot(path, limit, *, private=False):
    path = Path(path)
    if not path.is_absolute():
        raise ConfigurationRefused('absolute_path_required')
    fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_uid != os.getuid()
                or before.st_nlink != 1 or before.st_size > limit
                or (private and stat.S_IMODE(before.st_mode) != 0o600)):
            raise ConfigurationRefused('owned_regular_file_required')
        pieces, count = [], 0
        while True:
            chunk = os.read(fd, min(65536, limit + 1 - count))
            if not chunk:
                break
            pieces.append(chunk); count += len(chunk)
            if count > limit:
                raise ConfigurationRefused('file_budget_exceeded')
        after = os.fstat(fd)
        live = path.lstat()
        identity = lambda info: (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
        if identity(before) != identity(after) or identity(after) != identity(live) or count != before.st_size:
            raise ConfigurationRefused('file_changed_during_read')
        return b''.join(pieces)
    finally:
        os.close(fd)


def _canonical_url(value):
    # Parse locally with the same URL standard as the browser, before any file
    # selection or network access. Reject ambiguous syntax rather than allowing
    # the parser to repair credentials, fragments or missing authority markers.
    if (type(value) is not str or not 1 <= len(value) <= 4096
            or value != value.strip() or any(ord(c) < 32 or ord(c) == 127 for c in value)
            or '\\' in value or '#' in value):
        raise ConfigurationRefused('invalid_url')
    authority = re.match(r'(?i:https?)://([^/?#]+)', value)
    if authority is None or '@' in authority[1]:
        raise ConfigurationRefused('invalid_url')
    try:
        from ada_url import URL
    except ImportError as error:
        raise ConfigurationRefused('web_url_dependency_required') from error
    try:
        parsed = URL(value)
        if (parsed.protocol not in ('http:', 'https:') or not parsed.hostname
                or parsed.username or parsed.password or parsed.hash
                or len(parsed.href) > 4096):
            raise ConfigurationRefused('invalid_url')
        return parsed.href, parsed.origin
    except (ValueError, UnicodeError) as error:
        raise ConfigurationRefused('invalid_url') from error


def _selector(value):
    from .flow import Selector
    if type(value) is not dict:
        raise ConfigurationRefused('invalid_selector')
    selector = Selector(**value)
    if not selector._names_something():
        raise ConfigurationRefused('named_selector_required')
    return selector


@dataclass(frozen=True)
class Registration:
    name: str
    url: str
    scope: object
    receipt: dict
    fields: dict
    request_control: object
    timeout: float


def load_registration(path, name):
    from .browser_upload import BrowserUploadFile, BrowserUploadScope, upload_params_valid
    if not upload_params_valid({'scope': name, 'attachment_digest': '0' * 64}):
        raise ConfigurationRefused('invalid_scope')
    config = json.loads(_snapshot(path, 1024 * 1024, private=True), object_pairs_hook=_json_object)
    if (type(config) is not dict or set(config) != {'version', 'url', 'scopes'}
            or type(config['version']) is not int or config['version'] != 1
            or type(config['scopes']) is not dict or name not in config['scopes']):
        raise ConfigurationRefused('invalid_configuration')
    url, origin = _canonical_url(config['url'])
    selected = config['scopes'][name]
    if type(selected) is not dict or set(selected) != {'opener', 'chooser', 'files', 'receipt'}:
        raise ConfigurationRefused('invalid_registration')
    files = selected['files']
    if type(files) is not list or not 1 <= len(files) <= 16:
        raise ConfigurationRefused('invalid_files')
    payloads = []
    for item in files:
        if type(item) is not dict or set(item) != {'path', 'filename', 'mime_type'}:
            raise ConfigurationRefused('invalid_file_registration')
        payloads.append(BrowserUploadFile(item['filename'], _snapshot(item['path'], 16 * 1024 * 1024), item['mime_type']))
    scope = BrowserUploadScope(origin, _selector(selected['opener']), _selector(selected['chooser']), tuple(payloads))
    receipt = selected['receipt']
    keys = {'account', 'recipient', 'text', 'request_id', 'request_control',
            'request_attribute', 'fields', 'inbox_url', 'headers', 'timeout_seconds'}
    if type(receipt) is not dict or set(receipt) != keys:
        raise ConfigurationRefused('invalid_receipt_configuration')
    for field in ('account', 'recipient', 'request_id', 'text'):
        value = receipt[field]
        if type(value) is not str or len(value.encode()) > 100000 or (field != 'text' and not value):
            raise ConfigurationRefused('invalid_receipt_identity')
    if (type(receipt['request_attribute']) is not str
            or not re.fullmatch(r'data-[a-z][a-z0-9-]{0,63}', receipt['request_attribute'])):
        raise ConfigurationRefused('invalid_request_attribute')
    inbox_url, _ = _canonical_url(receipt['inbox_url'])
    receipt = {**receipt, 'inbox_url': inbox_url}
    headers = receipt['headers']
    if type(headers) is not dict or len(headers) > 16:
        raise ConfigurationRefused('invalid_receipt_headers')
    for key, value in headers.items():
        if (type(key) is not str or not re.fullmatch(r'[A-Za-z0-9-]{1,64}', key)
                or type(value) is not str or len(value) > 8192
                or any(c in value for c in '\r\n\0')):
            raise ConfigurationRefused('invalid_receipt_headers')
    timeout = receipt['timeout_seconds']
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 30:
        raise ConfigurationRefused('invalid_receipt_timeout')
    fields = receipt['fields']
    if type(fields) is not dict or set(fields) != {'account', 'recipient', 'body'}:
        raise ConfigurationRefused('invalid_receipt_fields')
    return Registration(name, url, scope, receipt,
        {k: _selector(v) for k, v in fields.items()}, _selector(receipt['request_control']), float(timeout))


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def execute(registration, *, confirm, wrap_driver=lambda driver: driver):
    from .browser import BrowserDriver
    from .browser_upload import BrowserUploadDriver
    from .commit_evidence import AttachmentEvidence
    from .contract import SafetyContract, Resource, Destination, ContextBinding, issue_approval
    from .delivery_receipt import DeliveredMessageReceipt, PendingDelivery, InboxDelivery, InboxTemporarilyUnavailable
    from .flow import Flow, Step, replay, pin_params
    scope, config = registration.scope, registration.receipt
    manifest = tuple(AttachmentEvidence(f.filename, hashlib.sha256(f.content).hexdigest(), len(f.content)) for f in scope.files)
    http = build_opener(NoRedirect())
    def read_inbox(request_id):
        if request_id != config['request_id']:
            raise ValueError('request_identity_changed')
        request = Request(config['inbox_url'], headers=config['headers'])
        try:
            deadline = time.monotonic() + registration.timeout
            with http.open(request, timeout=min(1, registration.timeout)) as response:
                chunks, size = [], 0
                while True:
                    if time.monotonic() >= deadline:
                        raise TimeoutError('inbox_read_deadline')
                    chunk = response.read1(min(65536, 1024 * 1024 + 1 - size))
                    if not chunk:
                        break
                    chunks.append(chunk); size += len(chunk)
                    if size > 1024 * 1024:
                        raise ValueError('inbox_budget_exceeded')
                data = b''.join(chunks)
                record = json.loads(data, object_pairs_hook=_json_object)
                record['attachments'] = tuple(AttachmentEvidence(**a) for a in record['attachments'])
                return InboxDelivery(**record)
        except HTTPError as error:
            if error.code == 404:
                return None
            if error.code in (502, 503, 504):
                raise InboxTemporarilyUnavailable() from None
            raise
    browser = BrowserDriver()
    try:
        browser.goto(registration.url)
        adapter = BrowserUploadDriver(browser, {registration.name: scope})
        params = adapter.parameters(registration.name)
        def bind(event):
            current = browser._latest['page']
            if (current['obs_id'] != event['obs_id'] or current['document'] != browser._document()
                    or browser.identity() != scope.origin
                    or event['action'].get('scope') != registration.name
                    or event['action'].get('attachment_digest') != scope.digest
                    or scope.opener.resolve(current['tree'])[0] != event['action']['element_index']):
                raise ValueError('upload_binding_changed')
            values = {}
            for name, selector in registration.fields.items():
                index, _ = selector.resolve(current['tree'])
                probe = browser.probe(event['obs_id'], 'page', index)
                if probe.get('type') != 'probe' or type(probe['node'].get('value')) is not str:
                    raise ValueError('receipt_field_unavailable')
                values[name] = probe['node']['value']
            index, _ = registration.request_control.resolve(current['tree'])
            handle = browser._locator(current['nodes'][index])
            try:
                request_id = handle.get_attribute(config['request_attribute'])
            finally:
                browser._dispose(handle)
            if request_id != config['request_id']:
                raise ValueError('request_identity_changed')
            return PendingDelivery(request_id, 'page', event['obs_id'], event['action_digest'],
                values['account'], values['recipient'], hashlib.sha256(values['body'].encode()).hexdigest(), manifest)
        receipt = DeliveredMessageReceipt(target='page', account=config['account'], recipient=config['recipient'],
            text=config['text'], attachments=manifest, bind_request=bind, read_inbox=read_inbox,
            timeout_seconds=registration.timeout)
        safety = SafetyContract(effects=['transmit'], reversibility='irreversible',
            resource=Resource(object_type='registered browser files', scope='entry'),
            destination=Destination(kind='third_party', identity=config['recipient'], account=config['account']))
        binding = ContextBinding(bundle_id=scope.origin, operation='upload', params_digest=pin_params(params))
        flow = Flow('web_upload', 'page', [Step('upload', scope.opener, params=params, safety=safety, binding=binding)])
        review = {'scope': registration.name, 'origin': scope.origin, 'account': config['account'],
                  'recipient': config['recipient'], 'text': config['text'],
                  'files': [dict(filename=a.filename, size_bytes=a.size_bytes, sha256=a.sha256) for a in manifest]}
        if confirm(review) is not True:
            return {'ok': False, 'error': 'approval_declined', 'events': []}
        driver = wrap_driver(adapter)
        result = replay(driver, flow, approvals={0: issue_approval(binding, safety)},
            prepare_commit=receipt.prepare_commit, after_commit=receipt.after_commit,
            commit_expected=receipt.commit_expected)
        return {'ok': result.ok, 'events': result.events,
                'confirmed_receipts': len(result.commit_evidence),
                'receipts': {key: asdict(proof) for key, proof in result.commit_evidence.items()},
                'log': result.log}
    finally:
        browser.close()


def main(argv, *, wrap_driver=lambda driver: driver):
    parser = argparse.ArgumentParser(prog='tacto web-upload')
    parser.add_argument('config')
    parser.add_argument('--scope', required=True)
    parser.add_argument('--check', action='store_true')
    parser.add_argument('--yes', action='store_true')
    parser.add_argument('--report')
    args = parser.parse_args(argv)
    descriptor = None
    # Ctrl-C may arrive after dispatch. Leave a conservative report without
    # masking the interruption or implying that it is safe to resend.
    result = {'ok': False, 'error': 'web_upload_interrupted', 'effect': 'unknown'}
    try:
        if args.report:
            descriptor = os.open(args.report, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        registration = load_registration(args.config, args.scope)
        if args.check:
            result = {'ok': True, 'checked': True, 'scope': args.scope,
                      'attachment_digest': registration.scope.digest, 'authorizes_input': False}
        else:
            def confirm(review):
                if args.yes:
                    return True
                print(json.dumps(review, ensure_ascii=False))
                if not __import__('sys').stdin.isatty():
                    return False
                return input('Upload these files to this receiver? [y/N] ').strip().lower() == 'y'
            result = execute(registration, confirm=confirm, wrap_driver=wrap_driver)
    except Exception as error:
        result = {'ok': False, 'error': 'web_upload_refused', 'error_class': type(error).__name__}
    finally:
        if descriptor is not None:
            with os.fdopen(descriptor, 'w') as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result['ok'] else 1
