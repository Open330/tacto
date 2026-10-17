"""Scoped custom ARIA combobox support; native HTML select remains separate.

Discovery only follows a single explicit aria-controls ID. Opening and choosing
are separate operations with a mandatory fresh browser observation between.
The host owns this helper and must close it alongside its BrowserDriver.
"""
from __future__ import annotations
import uuid
import time


def _error(reason, effect='not_started'):
    return {'type': 'error', 'error': reason, 'effect': effect}


class CustomSelect:
    def __init__(self, driver):
        self.driver = driver
        self._snapshots = {}
        self._observation = None
        self._used_observation = None

    def close(self):
        for snapshot in self._snapshots.values():
            for handle in snapshot['handles']:
                self.driver._dispose(handle)
        self._snapshots.clear()
        self._observation = None

    def _fresh(self, obs_id, target, index):
        current = self.driver._latest.get(target)
        return (target == self.driver._target and current is not None
                and current['obs_id'] == obs_id and current['document'] == self.driver._document()
                and type(index) is int and 0 <= index < len(current['nodes'])
                and obs_id != self._used_observation)

    @staticmethod
    def _control(handle):
        return handle.evaluate('''el => ({connected:el.isConnected, tag:el.tagName,
          role:el.getAttribute('role'), controls:el.getAttribute('aria-controls'),
          label:el.getAttribute('aria-label'), labelledby:el.getAttribute('aria-labelledby'),
          expanded:el.getAttribute('aria-expanded'), disabled:el.matches(':disabled') || el.getAttribute('aria-disabled')==='true'})''')

    @staticmethod
    def _readout(handle):
        return handle.evaluate('''el => {
          if(el.hasAttribute('aria-valuetext')) return {kind:'aria-valuetext',value:el.getAttribute('aria-valuetext')};
          if(el.tagName==='INPUT' && el.readOnly) return {kind:'readonly_input',value:el.value};
          if(el.tagName==='BUTTON') return {kind:'button_text',value:el.innerText};
          return {kind:null,value:null};
        }''')

    @staticmethod
    def _option(handle):
        return handle.evaluate('''el => ({connected:el.isConnected, role:el.getAttribute('role'),
          label:el.getAttribute('aria-label') || el.innerText,
          value:el.getAttribute('data-value') ?? el.getAttribute('value'),
          disabled:el.matches(':disabled') || el.getAttribute('aria-disabled')==='true',
          selected:el.getAttribute('aria-selected')==='true'})''')

    def discover(self, obs_id, target, index):
        key = (obs_id, target)
        if key != self._observation:
            self.close()
            self._observation = key
        for old_id, snapshot in list(self._snapshots.items()):
            if snapshot['index'] == index:
                for handle in snapshot['handles']:
                    self.driver._dispose(handle)
                del self._snapshots[old_id]
        handles = []
        try:
            if not self._fresh(obs_id, target, index):
                return _error('stale_observation')
            node = self.driver._latest[target]['nodes'][index]
            probe = self.driver.probe(obs_id, target, index)
            if probe.get('type') != 'probe' or probe['node'].get('name') != node.get('name'):
                return _error('target_changed')
            control = self.driver._locator(node)
            handles.append(control)
            state = self._control(control)
            if (state['tag'] == 'SELECT' or state['role'] != 'combobox'
                    or not state['connected'] or not control.is_visible() or state['disabled']):
                return _error('unsupported_custom_select')
            ids = (state['controls'] or '').split()
            if len(ids) != 1 or state['expanded'] not in ('true', 'false'):
                return _error('ambiguous_control_scope')
            # Searching IDs resolves only the explicitly linked container; it
            # never searches document-wide option labels or clicks hidden DOM.
            if control.evaluate("(el,id)=>Array.from(el.ownerDocument.querySelectorAll('[id]')).filter(n=>n.id===id).length", ids[0]) > 1:
                return _error('ambiguous_control_scope')
            linked = control.evaluate_handle('''(el,id) => {
              const matches=Array.from(el.ownerDocument.querySelectorAll('[id]')).filter(n=>n.id===id);
              return matches.length===1 ? matches[0] : null;
            }''', ids[0])
            handles.append(linked)
            box = linked.as_element()
            options, records = [], []
            if state['expanded'] == 'true':
                if box is None or not box.is_visible() or box.get_attribute('role') != 'listbox':
                    return _error('listbox_not_visible')
                if box.get_attribute('aria-disabled') == 'true' or box.get_attribute('aria-multiselectable') == 'true':
                    return _error('unsupported_listbox')
                options = box.query_selector_all('[role="option"]')
                handles.extend(options)
                if len(options) > 200:
                    return _error('option_budget_exceeded')
                for option in options:
                    # Nested independent listboxes are outside this scope.
                    if not option.evaluate('(el,box)=>el.closest("[role=listbox]")===box', box):
                        return _error('nested_listbox')
                    if option.is_visible():
                        records.append((option, self._option(option)))
                if not records:
                    return _error('no_visible_options')
            elif box is not None and box.is_visible():
                return _error('inconsistent_expanded_state')
            snapshot_id = uuid.uuid4().hex
            self._snapshots[snapshot_id] = dict(id=snapshot_id, obs_id=obs_id, target=target, index=index,
                                  control=control, control_state=state, control_readout=self._readout(control), box=box,
                                  records=records, handles=handles, node_name=node.get('name'))
            handles = []
            return {'type': 'select_control', 'snapshot_id': snapshot_id,
                    'state': 'open' if state['expanded'] == 'true' else 'closed',
                    'options': [{k: data[k] for k in ('label', 'value', 'disabled', 'selected')}
                                for _, data in records]}
        except Exception:
            return _error('target_changed')
        finally:
            for handle in handles:
                self.driver._dispose(handle)

    def _validate(self, snapshot_id, opened):
        s = self._snapshots.get(snapshot_id)
        if s is None or s['id'] != snapshot_id or not self._fresh(s['obs_id'], s['target'], s['index']):
            return None
        if (self._control(s['control']) != s['control_state'] or not s['control'].is_visible()
                or self._readout(s['control']) != s['control_readout']):
            return None
        probe = self.driver.probe(s['obs_id'], s['target'], s['index'])
        if probe.get('type') != 'probe' or probe['node'].get('name') != s['node_name']:
            return None
        if (s['control_state']['expanded'] == 'true') != opened:
            return None
        if opened:
            box = s['box']
            if (box is None or not box.is_visible() or box.get_attribute('role') != 'listbox'
                    or box.get_attribute('aria-disabled') == 'true'
                    or box.get_attribute('aria-multiselectable') == 'true'):
                return None
            if not s['control'].evaluate('''(el,box)=> {
              const matches=Array.from(el.ownerDocument.querySelectorAll('[id]')).filter(n=>n.id===el.getAttribute('aria-controls'));
              return matches.length===1 && matches[0]===box && box.isConnected;
            }''', box):
                return None
            current = box.query_selector_all('[role="option"]')
            try:
                visible = [o for o in current if o.is_visible()]
                if len(visible) != len(s['records']):
                    return None
                for now, (old, data) in zip(visible, s['records']):
                    if not now.evaluate('(el,old)=>el===old', old) or self._option(now) != data:
                        return None
            finally:
                for option in current:
                    self.driver._dispose(option)
        return s

    def _readback(self, s, option=None, data=None, timeout=0.75):
        """Poll only pinned scope after one dispatch; never click again.

        Prefer the same retained option's unique selected state. If options
        unmount, allow only an exact changed readout on the original control
        after closing; unrelated result fields and editable search values are
        never selection evidence.
        """
        deadline = time.monotonic() + timeout
        while True:
            current = self.driver._latest.get(s['target'])
            if (current is None or current['obs_id'] != s['obs_id']
                    or current['document'] != self.driver._document()):
                return False
            state = self._control(s['control'])
            original = s['control_state']
            if (not state['connected'] or not s['control'].is_visible()
                    or state['expanded'] not in ('true', 'false')
                    or any(state[k] != original[k] for k in ('tag', 'role', 'controls', 'disabled', 'label', 'labelledby'))):
                return False
            probe = self.driver.probe(s['obs_id'], s['target'], s['index'])
            if probe.get('type') != 'probe':
                return False
            if probe['node'].get('name') != s['node_name']:
                # A button's text may be its accessible name and its selected
                # readout. Only the exact observed option label may replace it.
                if not (option is not None and s['control_readout']['kind'] == 'button_text'
                        and not original['label'] and not original['labelledby']
                        and probe['node'].get('name') == data['label']):
                    return False
            result = s['control'].evaluate('''(el,args) => {
              const matches=Array.from(el.ownerDocument.querySelectorAll('[id]'))
                .filter(n=>n.id===el.getAttribute('aria-controls'));
              if(matches.length>1) return 'invalid';
              const box=matches[0];
              if(box && args.box && box!==args.box) return 'invalid';
              if(box && (box.getAttribute('role')!=='listbox' || box.getAttribute('aria-disabled')==='true'
                 || box.getAttribute('aria-multiselectable')==='true')) return 'invalid';
              if(args.option && !args.option.isConnected) {
                // Unmounted options: only a changed readout on THIS control
                // can replace selected-state evidence. Editable search values
                // and unrelated result fields are never used.
                if(box && box.querySelector('[role=option]')) return 'invalid';
                if(el.getAttribute('aria-expanded')!=='false') return 'waiting';
                const kind=args.before.kind;
                let value=null, expected=args.label;
                if(kind==='aria-valuetext' && el.hasAttribute('aria-valuetext')) value=el.getAttribute('aria-valuetext');
                else if(kind==='readonly_input' && el.tagName==='INPUT' && el.readOnly) {
                  value=el.value; expected=args.value===null ? args.label : args.value;
                } else if(kind==='button_text' && el.tagName==='BUTTON') value=el.innerText;
                else return 'invalid';
                const oldLabel=args.option.getAttribute('aria-label') || args.option.innerText;
                const oldValue=args.option.getAttribute('data-value') ?? args.option.getAttribute('value');
                if(args.option.getAttribute('role')!=='option' || args.option.getAttribute('aria-disabled')==='true'
                   || oldLabel!==args.label || oldValue!==args.value || args.before.value===expected) return 'invalid';
                return value===expected ? 'ok' : 'waiting';
              }
              if(!box || !box.isConnected ||
                 box.getAttribute('role')!=='listbox' ||
                 box.getAttribute('aria-disabled')==='true' ||
                 box.getAttribute('aria-multiselectable')==='true') return 'invalid';
              if(!args.option) {
                const style=getComputedStyle(box);
                const visible=!!(box.offsetWidth || box.offsetHeight || box.getClientRects().length)
                  && style.visibility!=='hidden' && style.display!=='none';
                return el.getAttribute('aria-expanded')==='true' && visible ? 'ok' : 'waiting';
              }
              const option=args.option;
              const label=option.getAttribute('aria-label') || option.innerText;
              const value=option.getAttribute('data-value') ?? option.getAttribute('value');
              if(!option.isConnected || option.closest('[role=listbox]')!==box ||
                 option.getAttribute('role')!=='option' || label!==args.label || value!==args.value ||
                 option.matches(':disabled') || option.getAttribute('aria-disabled')==='true') return 'invalid';
              const selected=Array.from(box.querySelectorAll('[role=option]'))
                .filter(n=>n.closest('[role=listbox]')===box && n.getAttribute('aria-selected')==='true');
              return selected.length===1 && selected[0]===option ? 'ok' : 'waiting';
            }''', {'box': s['box'], 'option': option,
                   'before': s['control_readout'],
                   'label': data['label'] if data else None,
                   'value': data['value'] if data else None})
            if result == 'ok':
                current = self.driver._latest.get(s['target'])
                return (current is not None and current['obs_id'] == s['obs_id']
                        and current['document'] == self.driver._document())
            if result == 'invalid' or time.monotonic() >= deadline:
                return False
            self.driver._page.wait_for_timeout(25)

    def open(self, snapshot_id):
        dispatched = False
        try:
            s = self._validate(snapshot_id, False)
            if s is None:
                return _error('target_changed')
            self._used_observation = s['obs_id']
            dispatched = True
            s['control'].click(timeout=1500)
            verified = self._readback(s)
            return {'type': 'acted', 'action': 'open_custom_select',
                    **({} if verified else {'unverified': 'open_unconfirmed'}),
                    'reobserve_required': True}
        except Exception:
            return _error('action_failed', 'unknown' if dispatched else 'not_started')
        finally:
            self.close()

    def select(self, snapshot_id, *, label=None, value=None):
        dispatched = False
        try:
            if (label is None and value is None) or any(v is not None and not isinstance(v, str) for v in (label, value)):
                return _error('invalid_option')
            s = self._validate(snapshot_id, True)
            if s is None:
                return _error('target_changed')
            matches = [(handle, data) for handle, data in s['records']
                       if (label is None or data['label'] == label) and (value is None or data['value'] == value)]
            if len(matches) != 1 or matches[0][1]['disabled']:
                return _error('invalid_option')
            self._used_observation = s['obs_id']
            dispatched = True
            matches[0][0].click(timeout=1500)
            verified = self._readback(s, matches[0][0], matches[0][1])
            return {'type': 'acted', 'action': 'select_custom_option',
                    **({} if verified else {'unverified': 'selection_unconfirmed'}),
                    'reobserve_required': True}
        except Exception:
            return _error('action_failed', 'unknown' if dispatched else 'not_started')
        finally:
            self.close()
