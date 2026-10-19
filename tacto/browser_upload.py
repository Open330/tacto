"""Host-registered immutable browser payloads; recipes never name local paths."""
from dataclasses import dataclass, asdict
from copy import deepcopy
import hashlib
import json
import re
from types import MappingProxyType


def upload_params_valid(params):
    return (type(params) is dict and set(params) == {'scope', 'attachment_digest'}
            and type(params['scope']) is str
            and re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]{0,39}', params['scope']) is not None
            and type(params['attachment_digest']) is str
            and re.fullmatch(r'[0-9a-f]{64}', params['attachment_digest']) is not None)


@dataclass(frozen=True)
class BrowserUploadFile:
    filename: str
    content: bytes
    mime_type: str = 'application/octet-stream'

    def __post_init__(self):
        if (type(self.filename) is not str or not 1 <= len(self.filename) <= 255
                or self.filename in ('.', '..') or any(c in self.filename for c in '/\\')
                or any(ord(c) < 32 or ord(c) == 127 for c in self.filename)
                or type(self.content) is not bytes or len(self.content) > 16 * 1024 * 1024
                or type(self.mime_type) is not str or len(self.mime_type) > 127 or not re.fullmatch(
                    r'[A-Za-z0-9.+-]+/[A-Za-z0-9.+-]+', self.mime_type)):
            raise ValueError('invalid host upload file')


@dataclass(frozen=True)
class BrowserUploadScope:
    origin: str
    opener: object
    chooser: object
    files: tuple[BrowserUploadFile, ...]

    def __post_init__(self):
        from .flow import Selector
        if (type(self.origin) is not str or not 1 <= len(self.origin) <= 2048
                or type(self.opener) is not Selector or type(self.chooser) is not Selector
                or not self.opener._names_something() or not self.chooser._names_something()
                or type(self.files) is not tuple or not 1 <= len(self.files) <= 16
                or any(type(f) is not BrowserUploadFile for f in self.files)
                or len({f.filename for f in self.files}) != len(self.files)
                or sum(len(f.content) for f in self.files) > 64 * 1024 * 1024):
            raise ValueError('invalid host upload scope')

    @property
    def digest(self):
        value = {'origin': self.origin, 'opener': asdict(self.opener),
                 'chooser': asdict(self.chooser), 'files': [
                     {'filename': f.filename, 'mime_type': f.mime_type,
                      'size_bytes': len(f.content),
                      'sha256': hashlib.sha256(f.content).hexdigest()} for f in self.files]}
        return hashlib.sha256(json.dumps(value, sort_keys=True,
            ensure_ascii=False, separators=(',', ':')).encode()).hexdigest()


class BrowserUploadDriver:
    """Explicit host adapter adding upload to BrowserDriver's L2 surface.

    Content and selectors are copied on registration. A registry key is not
    approval: replay must still authorize transmit and verify a typed receipt.
    """
    def __init__(self, driver, scopes):
        from .browser import BrowserDriver
        if not isinstance(driver, BrowserDriver) or type(scopes) is not dict or not scopes:
            raise ValueError('browser upload requires a host scope registry')
        for name, scope in scopes.items():
            if (type(scope) is not BrowserUploadScope
                    or not upload_params_valid({'scope': name, 'attachment_digest': scope.digest})):
                raise ValueError('invalid host upload registration')
        self.driver = driver
        self._scopes = MappingProxyType(deepcopy(scopes))
        self.capabilities = driver.capabilities | {'action.upload'}

    def __getattr__(self, name):
        return getattr(self.driver, name)

    def parameters(self, name):
        return {'scope': name, 'attachment_digest': self._scopes[name].digest}

    def upload_candidates(self, target, tree, candidates):
        """Offer registered manifests on their observed opener, never bytes."""
        result = deepcopy(candidates)
        current = self.driver._latest.get(target)
        if not current or current.get('tree') != tree:
            return result
        origin = self.driver.identity(target)
        for name, scope in self._scopes.items():
            if scope.origin != origin:
                continue
            try:
                opener, _ = scope.opener.resolve(tree)
                chooser, _ = scope.chooser.resolve(tree)
            except LookupError:
                continue
            if current['nodes'][chooser].get('disabled'):
                continue
            for candidate in result:
                if candidate['element_index'] != opener or candidate.get('disabled'):
                    continue
                manifest = {**self.parameters(name), 'files': [
                    {'filename': f.filename, 'size_bytes': len(f.content),
                     'sha256': hashlib.sha256(f.content).hexdigest()} for f in scope.files]}
                candidate.setdefault('uploads', []).append(manifest)
                if 'upload' not in candidate['can']:
                    candidate['can'].append('upload')
        return result

    def act(self, obs_id, target, action, expect=None, risk_class='none',
            intent_text='', approved=False):
        if action.get('name') != 'upload':
            return self.driver.act(obs_id, target, action, expect=expect,
                risk_class=risk_class, intent_text=intent_text, approved=approved)
        def refused(code):
            return {'type': 'error', 'error': code, 'effect': 'not_started'}
        if risk_class not in ('confirm', 'preapproved'):
            return refused('upload_contract_required')
        if approved is not True:
            return {'type': 'confirm_request', 'what': '파일 업로드'}
        if set(action) != {'name', 'element_index', 'scope', 'attachment_digest'}:
            return refused('invalid_action')
        params = {k: action[k] for k in ('scope', 'attachment_digest')}
        if not upload_params_valid(params):
            return refused('upload_scope_required')
        scope = self._scopes.get(params['scope'])
        if scope is None or scope.digest != params['attachment_digest']:
            return refused('upload_scope_required')
        current = self.driver._latest.get(target)
        if not current or current['obs_id'] != obs_id:
            return refused('stale_observation')
        if self.driver.identity(target) != scope.origin:
            return refused('upload_origin_changed')
        # Resolve both meanings against this original observation, never a new
        # index registry. The raw driver verifies document and DOM identities.
        try:
            # Preserve hierarchy for ancestor-scoped selectors.
            tree = current['tree']
            opener, _ = scope.opener.resolve(tree)
            chooser, _ = scope.chooser.resolve(tree)
        except Exception:
            return refused('upload_target_changed')
        if type(action['element_index']) is not int or action['element_index'] != opener:
            return refused('upload_target_changed')
        for index in {opener, chooser}:
            observed = current['nodes'][index]
            live = self.driver.probe(obs_id, target, index)
            if (live.get('type') != 'probe' or live['node'].get('disabled')
                    or any(live['node'].get(k) != observed.get(k) for k in ('role', 'name'))):
                return refused('upload_target_changed')
        if expect:
            checked = self.driver.act(obs_id, target, {'name': 'read', 'element_index': opener},
                                      expect=expect)
            if checked.get('type') != 'acted':
                return checked
        return self.driver.upload(obs_id, target, opener, [
            {'name': f.filename, 'mimeType': f.mime_type, 'buffer': f.content}
            for f in scope.files], approved=True, chooser_selector=chooser)
