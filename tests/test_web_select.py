import unittest
from urllib.parse import quote

from tacto.web_select import CustomSelect

try:
    import playwright.sync_api
    AVAILABLE = True
except ImportError:
    AVAILABLE = False

PAGE = '''<button role="combobox" aria-label="Choice" aria-controls="choices" aria-expanded="false"
 onclick="this.setAttribute('aria-expanded','true');document.getElementById('choices').hidden=false">Choose</button>
<div id="choices" role="listbox" hidden>
 <div role="option" data-value="alpha" aria-selected="false" onclick="choose(this)">Alpha</div>
 <div role="option" data-value="beta" aria-selected="false" onclick="choose(this)">Beta</div>
 <div role="option" aria-disabled="true" data-value="gamma">Gamma</div>
</div><input aria-label="result" value="pending">
<script>function choose(el){document.querySelector('[aria-label=result]').value=el.dataset.value;
 el.parentElement.querySelectorAll('[role=option]').forEach(option=>option.setAttribute('aria-selected',String(option===el))); document.getElementById('choices').hidden=true;
 document.querySelector('[role=combobox]').setAttribute('aria-expanded','false')}</script>'''


class FakeSelectTests(unittest.TestCase):
    def test_unknown_snapshot_never_dispatches(self):
        class Driver:
            pass
        custom = CustomSelect(Driver())
        self.assertEqual('not_started', custom.open('missing')['effect'])
        self.assertEqual('not_started', custom.select('missing', label='Beta')['effect'])


@unittest.skipUnless(AVAILABLE, 'optional playwright dependency not installed')
class BrowserSelectTests(unittest.TestCase):
    def setUp(self):
        from tacto.browser import BrowserDriver
        self.driver = BrowserDriver()
        self.custom = CustomSelect(self.driver)
        self.driver.goto('data:text/html,' + quote(PAGE))

    def tearDown(self):
        self.custom.close()
        self.driver.close()

    def discover(self):
        obs = self.driver.observe()
        index = next(i for i, n in enumerate(self.driver._latest['page']['nodes']) if n['name'] == 'Choice')
        return obs, index, self.custom.discover(obs['obs_id'], 'page', index)

    def opened(self):
        _, _, snapshot = self.discover()
        self.assertEqual('acted', self.custom.open(snapshot['snapshot_id'])['type'])
        return self.discover()[2]

    def test_open_requires_new_observation_and_exact_selection(self):
        obs, index, closed = self.discover()
        self.assertEqual('closed', closed['state'])
        self.assertEqual([], closed['options'])
        self.assertTrue(self.custom.open(closed['snapshot_id'])['reobserve_required'])
        self.assertEqual('stale_observation', self.custom.discover(obs['obs_id'], 'page', index)['error'])
        _, _, opened = self.discover()
        self.assertEqual('open', opened['state'])
        self.assertEqual(['Alpha', 'Beta', 'Gamma'], [o['label'] for o in opened['options']])
        result = self.custom.select(opened['snapshot_id'], label='Beta', value='beta')
        self.assertEqual('acted', result['type'])
        self.assertNotIn('unverified', result)
        self.assertEqual('beta', self.driver._page.locator('[aria-label=result]').input_value())

    def test_ambiguity_disabled_hidden_and_exact_match_refused(self):
        for mutation, selection in [
            ('', {'label': 'Bet'}), ('', {'value': 'gamma'}),
            ("document.querySelectorAll('[role=option]')[0].textContent='Beta'", {'label': 'Beta'}),
            ("document.querySelectorAll('[role=option]')[1].hidden=true", {'label': 'Beta'})]:
            with self.subTest(mutation=mutation, selection=selection):
                self.driver.goto('data:text/html,' + quote(PAGE))
                self.opened()
                if mutation:
                    self.driver._page.evaluate(mutation)
                snapshot = self.discover()[2]
                result = self.custom.select(snapshot['snapshot_id'], **selection)
                self.assertEqual('not_started', result['effect'])
                self.assertEqual('pending', self.driver._page.locator('[aria-label=result]').input_value())

    def test_option_replacement_or_mutation_invalidates_snapshot(self):
        for mutation in [
            "document.querySelectorAll('[role=option]')[1].replaceWith(document.querySelectorAll('[role=option]')[1].cloneNode(true))",
            "document.querySelectorAll('[role=option]')[1].setAttribute('data-value','changed')",
            "document.querySelectorAll('[role=option]')[1].setAttribute('aria-disabled','true')",
            "document.querySelector('[role=combobox]').setAttribute('aria-controls','elsewhere')"]:
            with self.subTest(mutation=mutation):
                self.driver.goto('data:text/html,' + quote(PAGE))
                snapshot = self.opened()
                self.driver._page.evaluate(mutation)
                self.assertEqual('not_started', self.custom.select(snapshot['snapshot_id'], label='Beta')['effect'])
                self.assertEqual('pending', self.driver._page.locator('[aria-label=result]').input_value())

    def test_scope_cannot_retarget_unconnected_or_duplicate_listbox(self):
        for mutation in [
            "document.querySelector('[role=combobox]').setAttribute('aria-controls','choices other')",
            "document.body.insertAdjacentHTML('beforeend','<div id=choices role=listbox><div role=option>Beta</div></div>')",
            "document.querySelector('[role=combobox]').removeAttribute('aria-controls')"]:
            with self.subTest(mutation=mutation):
                self.driver.goto('data:text/html,' + quote(PAGE))
                self.driver._page.evaluate(mutation)
                snapshot = self.discover()[2]
                # Duplicate IDs may be refused immediately or when opening,
                # but they must never provide a selectable open snapshot.
                if snapshot['type'] == 'select_control':
                    self.custom.open(snapshot['snapshot_id'])
                    snapshot = self.discover()[2]
                self.assertEqual('error', snapshot['type'])

    def test_new_observation_invalidates_old_snapshot(self):
        snapshot = self.opened()
        self.driver.observe()
        self.assertEqual('stale_observation', self.custom.discover('old', 'page', 0)['error'])
        self.assertEqual('not_started', self.custom.select(snapshot['snapshot_id'], label='Beta')['effect'])

    def test_multiple_controls_share_observation_without_retargeting(self):
        self.driver._page.evaluate("""document.body.insertAdjacentHTML('beforeend',
          '<button role=combobox aria-label=Other aria-controls=others aria-expanded=false>Other</button><div id=others role=listbox hidden></div>')""")
        obs = self.driver.observe()
        nodes = self.driver._latest['page']['nodes']
        first = next(i for i,n in enumerate(nodes) if n['name'] == 'Choice')
        second = next(i for i,n in enumerate(nodes) if n['name'] == 'Other')
        a = self.custom.discover(obs['obs_id'], 'page', first)
        b = self.custom.discover(obs['obs_id'], 'page', second)
        self.assertEqual('closed', b['state'])
        self.assertEqual('acted', self.custom.open(a['snapshot_id'])['type'])
        self.assertEqual('true', self.driver._page.locator('[aria-label=Choice]').get_attribute('aria-expanded'))
        self.assertEqual('false', self.driver._page.locator('[aria-label=Other]').get_attribute('aria-expanded'))
        self.assertEqual('not_started', self.custom.open(b['snapshot_id'])['effect'])

    def test_label_only_option_does_not_invent_value(self):
        self.opened()
        self.driver._page.evaluate("document.querySelectorAll('[role=option]')[1].removeAttribute('data-value')")
        snapshot = self.discover()[2]
        self.assertIsNone(snapshot['options'][1]['value'])
        result = self.custom.select(snapshot['snapshot_id'], label='Beta')
        self.assertEqual('acted', result['type'])

    def test_open_noop_or_replaced_listbox_never_claims_verified(self):
        for handler in ("window.openClicks++", "window.openClicks++;this.setAttribute('aria-expanded','true');const b=document.getElementById('choices');b.replaceWith(b.cloneNode(true));document.getElementById('choices').hidden=false"):
            with self.subTest(handler=handler):
                self.driver.goto('data:text/html,' + quote(PAGE))
                self.driver._page.evaluate("window.openClicks=0")
                self.driver._page.locator('[role=combobox]').evaluate('(el,handler)=>el.setAttribute("onclick",handler)', handler)
                snapshot = self.discover()[2]
                result = self.custom.open(snapshot['snapshot_id'])
                self.assertEqual('open_unconfirmed', result.get('unverified'))
                self.assertEqual(1, self.driver._page.evaluate('window.openClicks'))
                self.assertEqual('not_started', self.custom.open(snapshot['snapshot_id'])['effect'])
                self.assertEqual(1, self.driver._page.evaluate('window.openClicks'))

    def test_selection_noop_wrong_duplicate_or_replaced_option_unverified(self):
        for handler in (
            "window.optionClicks++",  # no-op
            "window.optionClicks++;document.querySelector('[role=option]').setAttribute('aria-selected','true')",  # wrong option
            "window.optionClicks++;document.querySelectorAll('[role=option]').forEach(e=>e.setAttribute('aria-selected','true'))",  # ambiguous
            "window.optionClicks++;this.setAttribute('aria-selected','true');this.replaceWith(this.cloneNode(true))",  # different node
            "window.optionClicks++;this.setAttribute('aria-selected','true');this.dataset.value='other'",  # changed value
        ):
            with self.subTest(handler=handler):
                self.driver.goto('data:text/html,' + quote(PAGE))
                self.opened()
                self.driver._page.evaluate('window.optionClicks=0')
                self.driver._page.locator('[role=option]').nth(1).evaluate('(el,handler)=>el.setAttribute("onclick",handler)', handler)
                snapshot = self.discover()[2]
                result = self.custom.select(snapshot['snapshot_id'], label='Beta')
                self.assertEqual('selection_unconfirmed', result.get('unverified'), result)
                self.assertEqual(1, self.driver._page.evaluate('window.optionClicks'))
                self.assertEqual('not_started', self.custom.select(snapshot['snapshot_id'], label='Beta')['effect'])
                self.assertEqual(1, self.driver._page.evaluate('window.optionClicks'))

    def test_async_open_and_selection_readback_without_reclick(self):
        self.driver._page.evaluate('window.openClicks=0;window.optionClicks=0')
        self.driver._page.locator('[role=combobox]').evaluate('(el,handler)=>el.setAttribute("onclick",handler)',
            "window.openClicks++;setTimeout(()=>{this.setAttribute('aria-expanded','true');document.getElementById('choices').hidden=false},100)")
        snapshot = self.discover()[2]
        self.assertNotIn('unverified', self.custom.open(snapshot['snapshot_id']))
        self.driver._page.locator('[role=option]').nth(1).evaluate('(el,handler)=>el.setAttribute("onclick",handler)',
            "window.optionClicks++;setTimeout(()=>choose(this),100)")
        snapshot = self.discover()[2]
        self.assertNotIn('unverified', self.custom.select(snapshot['snapshot_id'], label='Beta'))
        self.assertEqual([1, 1], self.driver._page.evaluate('[window.openClicks,window.optionClicks]'))

    def unmount_snapshot(self, control, selection):
        page = f'''{control}<div id="choices" role="listbox" hidden>
          <div role="option" data-value="beta" aria-selected="false" onclick="choose(this)">Beta</div></div>
          <button id="other">Other</button><script>window.optionClicks=0;
          const control=document.querySelector('[role=combobox]');
          control.onclick=()=>{{control.setAttribute('aria-expanded','true');document.getElementById('choices').hidden=false}};
          function choose(option){{window.optionClicks++;{selection};
            document.getElementById('choices').remove();control.setAttribute('aria-expanded','false')}}
          </script>'''
        self.driver.goto('data:text/html,' + quote(page))
        def snapshot():
            obs = self.driver.observe()
            index = next(i for i,n in enumerate(self.driver._latest['page']['nodes']) if n['role']=='combobox')
            return self.custom.discover(obs['obs_id'],'page',index)
        closed = snapshot()
        self.assertNotIn('unverified', self.custom.open(closed['snapshot_id']))
        return snapshot()

    def test_unmounted_options_require_changed_exact_control_readout(self):
        cases = [
            ('<button role="combobox" aria-label="Choice" aria-valuetext="Choose" aria-controls="choices" aria-expanded="false">Choose</button>', "control.setAttribute('aria-valuetext','Beta')"),
            ('<input readonly role="combobox" aria-label="Choice" value="Choose" aria-controls="choices" aria-expanded="false">', "control.value='beta'"),
            ('<button role="combobox" aria-controls="choices" aria-expanded="false">Choose</button>', "control.textContent='Beta'"),
        ]
        for control, selection in cases:
            with self.subTest(control=control):
                snapshot = self.unmount_snapshot(control, selection)
                result = self.custom.select(snapshot['snapshot_id'], label='Beta', value='beta')
                self.assertEqual('acted', result['type'], result)
                self.assertNotIn('unverified', result, result)
                self.assertEqual(1, self.driver._page.evaluate('window.optionClicks'))

    def test_unmounted_options_do_not_accept_search_unchanged_wrong_or_other_control(self):
        cases = [
            ('<input role="combobox" aria-label="Choice" value="" aria-controls="choices" aria-expanded="false">', "control.value='beta'"),
            ('<button role="combobox" aria-label="Choice" aria-controls="choices" aria-expanded="false">Beta</button>', "control.textContent='Beta'"),
            ('<button role="combobox" aria-label="Choice" aria-controls="choices" aria-expanded="false">Choose</button>', "control.textContent='Alpha'"),
            ('<button role="combobox" aria-label="Choice" aria-controls="choices" aria-expanded="false">Choose</button>', "document.getElementById('other').textContent='Beta'"),
        ]
        for control, selection in cases:
            with self.subTest(control=control, selection=selection):
                snapshot = self.unmount_snapshot(control, selection)
                result = self.custom.select(snapshot['snapshot_id'], label='Beta')
                self.assertEqual('selection_unconfirmed', result.get('unverified'), result)
                self.assertEqual(1, self.driver._page.evaluate('window.optionClicks'))

    def test_unmount_readout_cannot_switch_scope_or_duplicate_linked_id(self):
        control = '<button role="combobox" aria-label="Choice" aria-controls="choices" aria-expanded="false">Choose</button>'
        for replacement in (
            "document.body.insertAdjacentHTML('beforeend','<div id=choices role=listbox hidden></div>')",
            "document.body.insertAdjacentHTML('beforeend','<div id=choices role=listbox hidden></div><div id=choices role=listbox hidden></div>')",
        ):
            with self.subTest(replacement=replacement):
                snapshot = self.unmount_snapshot(control, "control.textContent='Beta'")
                self.driver._page.evaluate("""replacement => {
                  const chooseOriginal=window.choose;
                  window.choose=option=>{chooseOriginal(option);(new Function(replacement))()};
                }""", replacement)
                result = self.custom.select(snapshot['snapshot_id'], label='Beta')
                self.assertEqual('selection_unconfirmed', result.get('unverified'), result)
                self.assertEqual(1, self.driver._page.evaluate('window.optionClicks'))
