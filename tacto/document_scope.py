"""A selected document stays pinned across replay observations and reads."""
import time
import re
import unicodedata

TARGET = 'com.apple.TextEdit'
_SAVE_CAPS=frozenset({'action.menu.document_token_save','action.menu.focused_document'})
_SCOPED_ACTIONS=frozenset({'read','click','prepare_window','press_key','set_value','type_text','secondary_action','scroll'})


# Diagnostics only: exact known helper details, never raw app/error text.
_PIN_DETAILS = {
    'initial document selection is ambiguous or absent': 'selection_ambiguous_or_absent',
    'document body is absent or ambiguous': 'body_ambiguous_or_absent',
    'document read deadline elapsed': 'read_deadline',
    'document traversal budget exceeded': 'traversal_budget',
    'AX text exceeds the UTF-16 budget': 'text_budget',
    'document application is not running': 'app_not_running',
    'document application identity unavailable': 'app_identity_unavailable',
    'application launch identity unavailable': 'app_identity_unavailable',
    'invalid application launch identity': 'app_identity_unavailable',
    'invalid document path selector': 'path_selector_invalid',
    'invalid document file URL': 'file_url_invalid',
    'document path is not canonical absolute': 'path_not_canonical',
    'document file reference is unreachable': 'file_reference_unreachable',
    'document identity changed during observation': 'document_identity_changed',
    'selected document path changed before text read': 'document_path_changed',
    'document path changed during observation': 'document_path_changed',
    'AXValue and AXStringForRange disagree': 'text_snapshot_inconsistent',
    'AX text length differs from AXNumberOfCharacters': 'text_snapshot_inconsistent',
    'AX text changed while taking the snapshot': 'text_snapshot_changed',
}


_PIN_AX_REASONS = {'AXWindows':'windows_unreadable','AXSubrole':'window_subrole_unreadable',
                'AXRole':'element_role_unreadable','AXIdentifier':'element_identifier_unreadable',
                'AXDocument':'document_path_unreadable','AXValue':'text_value_unreadable',
                'AXNumberOfCharacters':'text_count_unreadable','AXStringForRange':'text_range_unreadable',
                'document children':'children_unreadable'}
PIN_REASONS = frozenset(_PIN_DETAILS.values()) | frozenset(_PIN_AX_REASONS.values()) | {'user_aborted'}

def pin_failure_reason(reply):
    if type(reply) is not dict or reply.get('type') != 'error':return None
    if reply.get('error') == 'aborted_by_user':return 'user_aborted'
    if reply.get('error') != 'document_snapshot_unavailable':return None
    detail = reply.get('detail')
    if type(detail) is not str:return None
    if detail in _PIN_DETAILS:return _PIN_DETAILS[detail]
    match = re.fullmatch(r'(AXWindows|AXSubrole|AXRole|AXIdentifier|AXDocument|AXValue|AXNumberOfCharacters|AXStringForRange|document children) unavailable(?:: -?[0-9]{1,10})?', detail)
    if match:
        return _PIN_AX_REASONS[match.group(1)]
    return None


def _refuse(reason='scope_mismatch'):
    return {'type':'error','error':'document_scope_unavailable',
            'effect':'not_started','reason_code':reason}


def document_observation(driver, target, path):
    """Select by path before reading a tree; never widen to an app-wide tree."""
    import os
    import unicodedata
    def refuse(reason):
        return {'type':'error','error':'document_scope_unavailable','effect':'not_started','reason_code':reason}
    normalize=lambda value:unicodedata.normalize('NFC',value)
    required={'evidence.document_text.path_selector','observe.document_token'}
    if target!='com.apple.TextEdit':return refuse('target_unsupported')
    if not required<=set(getattr(driver,'capabilities',()) or ()):
        return refuse('capability_missing')
    if (not isinstance(path,str) or not os.path.isabs(path) or '\0' in path
            or normalize(os.path.realpath(path))!=normalize(path)):
        return refuse('path_not_canonical')
    try:
        # Zero is an empty-document assertion, not a metadata-only pin. The
        # path selector reads this document only, within the existing budget.
        pin=driver.document_text(target,document_path=path,max_utf16_units=1_000_000)
        if isinstance(pin,dict) and pin.get('type')=='error':
            refusal = refuse('pin_unavailable')
            diagnostic = pin_failure_reason(pin)
            if diagnostic is not None:refusal['pin_reason_code'] = diagnostic
            return refusal
        if (not isinstance(pin,dict) or pin.get('type')!='document_text'
                or not isinstance(pin.get('document_path'),str)
                or normalize(pin['document_path'])!=normalize(path)
                or not isinstance(pin.get('document_token'),str)
                or not 0<len(pin['document_token'])<=128 or not pin['document_token'].strip()
                or not isinstance(pin.get('text'),str) or pin.get('text_complete') is not True
                or len(pin['text'].encode('utf-16-le'))//2>1_000_000):
            return refuse('pin_malformed')
        result=driver.observe(target,'tree_full',document_token=pin['document_token'])
        if (not isinstance(result,dict) or result.get('type')!='observation'
                or result.get('document_token')!=pin['document_token']
                or not isinstance(result.get('tree'),str)):
            return refuse('scoped_observation_unavailable')
        return result
    except Exception:
        return refuse('native_read_failed')



class DocumentScopeUnavailable(ValueError):
    def __init__(self, reason, pin_reason=None):
        super().__init__(reason)
        self.pin_reason = pin_reason

    def __str__(self):
        text = super().__str__()
        return text + (f' (pin_reason={self.pin_reason})' if self.pin_reason else '')


class DocumentScopedDriver:
    """Same connection, one native token; never repin or widen after failure.

    Only the L2 operations used by replay are exposed. App-wide convenience
    APIs and image capture are intentionally unavailable through this driver.
    The underlying backend still enforces which actions its token permits.
    """
    def __init__(self, inner, target, path):
        first = document_observation(inner, target, path)
        if first.get('type') != 'observation':
            raise DocumentScopeUnavailable(first.get('reason_code','pin_unavailable'),first.get('pin_reason_code'))
        self._inner = inner
        self.target = target
        self.path = unicodedata.normalize('NFC',path)
        self._token = first['document_token']
        self._obs_id = None
        self._window_index = None
        self._supports_save=_SAVE_CAPS <= set(getattr(inner,'capabilities',()) or ())
        self.capabilities = frozenset(cap for cap in (getattr(inner,'capabilities',()) or ())
            if cap != 'evidence.document_attachment_metadata' and ((self._supports_save and cap in _SAVE_CAPS) or
            ((not cap.startswith('action.') or cap[7:] in _SCOPED_ACTIONS)
             and not cap.startswith(('action.menu.','menu.')))))
        self._indices=frozenset()

    def identity(self, target):
        if target != self.target:raise DocumentScopeUnavailable('target_mismatch')
        return self._inner.identity(target)

    def observe(self, target, mode='tree_full', require_window=True, *, document_token=None, reveal=None):
        if reveal is not None:
            # One pinned document window; looking past caps elsewhere is not its read.
            return _refuse('reveal_unavailable')
        self._obs_id = None
        self._window_index = None
        self._indices=frozenset()
        if target != self.target or document_token not in (None,self._token):return _refuse()
        try:
            reply = self._inner.observe(target,mode,require_window=require_window,document_token=self._token)
        except Exception:return _refuse('native_read_failed')
        if (not isinstance(reply,dict) or reply.get('type') != 'observation'
                or reply.get('document_token') != self._token
                or not isinstance(reply.get('tree'),str)
                or not isinstance(reply.get('obs_id'),str) or not reply['obs_id']):
            return _refuse('scoped_observation_unavailable')
        from .document_label import document_attribute
        windows=[int(re.match(r'\s*(\d+)',line).group(1)) for line in reply['tree'].splitlines()
                 if document_attribute(line)==self.path]
        if len(windows)!=1:return _refuse('document_window_unavailable')
        self._indices=frozenset(int(m.group(1)) for line in reply['tree'].splitlines()
            if (m:=re.match(r'^\s*(\d+)\s+',line)))
        self._window_index=windows[0]
        self._obs_id = reply['obs_id']
        return reply

    def act(self, obs_id, target, action, *args, **kwargs):
        if target != self.target or self._obs_id is None or obs_id != self._obs_id:return _refuse()
        if isinstance(action,dict) and action.get('name')=='menu':
            from .existing_file_receipt import SAVE_PATH
            if (not self._supports_save or set(action)!={'name','path','focused_document'}
                    or action.get('path')!=SAVE_PATH
                    or not isinstance(action.get('focused_document'),str)
                    or unicodedata.normalize('NFC',action['focused_document'])!=self.path):
                return _refuse('action_outside_document_scope')
            return self._inner.act(obs_id,target,action,*args,**kwargs)
        if isinstance(action,dict) and action.get('name')=='read' and 'element_index' not in action:
            # read is an L2 no-op; native token actions still require an element
            # in the pinned window. Bind it to the observed window itself.
            action={**action,'element_index':self._window_index}
        if (not isinstance(action,dict) or action.get('name') not in _SCOPED_ACTIONS
                or type(action.get('element_index')) is not int
                or action['element_index'] not in self._indices):return _refuse('action_outside_document_scope')
        return self._inner.act(obs_id,target,action,*args,**kwargs)

    def document_text(self,target,document_token=None,*,max_utf16_units=1000000,document_path=None):
        if (target != self.target or document_token not in (None,self._token)
                or (document_path is not None and
                    (not isinstance(document_path,str) or unicodedata.normalize('NFC',document_path)!=self.path))):
            return _refuse()
        try:
            reply=self._inner.document_text(target,document_token=self._token,max_utf16_units=max_utf16_units)
        except Exception:return _refuse('native_read_failed')
        if (not isinstance(reply,dict) or reply.get('type')!='document_text'
                or reply.get('document_token')!=self._token
                or not isinstance(reply.get('document_path'),str)
                or unicodedata.normalize('NFC',reply['document_path'])!=self.path):
            return _refuse('document_identity_changed')
        return reply

    def wait_until(self,target,predicate,timeout_ms=4000,poll_ms=250):
        deadline=time.monotonic()+timeout_ms/1000
        while True:
            reply=self.observe(target)
            if reply.get('type')!='observation':return reply
            if predicate(reply['tree']):return reply
            if time.monotonic()>=deadline:return {'type':'timeout','last_obs':reply}
            time.sleep(poll_ms/1000)

    def close(self):
        return self._inner.close()
