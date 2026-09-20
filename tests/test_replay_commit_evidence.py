"""Known recipes cannot replace commit evidence with a changed window title."""
import hashlib
import unittest

from nlc.agent import Agent
from nlc.commit_evidence import ArtifactEvidence, CommitEvidence
from nlc.contract import ContextBinding, Resource, SafetyContract, issue_approval
from nlc.flow import Flow, Postcondition, Selector, Step, replay


class MemoryStore:
    def __init__(self):
        self.items = {}

    def list(self):
        return list(self.items)

    def has(self, key):
        return key in self.items

    def save(self, flow):
        self.items[flow.name] = flow

    def load(self, key):
        return self.items[key]


class TextEditFixture:
    """Actual recipe UI transitions; writes are separate, inspectable records."""
    def __init__(self, write=False, content='hello'):
        self.sheet = False
        self.name = 'Untitled'
        self.clicks = 0
        self.write = write
        self.content = content
        self.writes = []
        self.fail_after_dispatch = False

    def identity(self, target):
        return target

    def observe(self, *args, **kwargs):
        tree = f'0 Window Title: {self.name} [focused]'
        if self.sheet:
            tree += ('\n  1 Sheet Identifier: save-panel'
                     f'\n    2 TextField Identifier: saveAsNameTextField, Value: {self.name}'
                     '\n    3 PopUpButton Identifier: where popup, Value: Downloads'
                     '\n    4 Outline Description: sidebar'
                     '\n    5 Button Identifier: OKButton, Title: Save')
        return {'type': 'observation', 'obs_id': 'obs', 'tree': tree}

    def act(self, obs_id, target, action, **kwargs):
        if action['name'] == 'menu':
            self.sheet = True
        elif action['name'] == 'set_value':
            self.name = action['value']
        elif action['name'] == 'click':
            self.clicks += 1
            self.sheet = False
            if self.write:
                self.writes.append({'path': '/tmp/Downloads/' + self.name,
                                    'content': self.content})
            if self.fail_after_dispatch:
                raise TimeoutError('response lost after click')
        return {'type': 'acted'}

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250, **kwargs):
        observation = self.observe()
        return observation if predicate(observation['tree']) else {'type': 'timeout', 'last_obs': observation}

    def install_evidence(self):
        self.commit_expected = {'저장 위치': '/tmp/Downloads', '저장한 이름': 'proof.rtf',
                                '다시 읽은 내용': 'hello'}
        self.prepare_commit = lambda event: len(self.writes)
        def after(event, response, count):
            if len(self.writes) != count + 1:
                return None
            write = self.writes[-1]
            return CommitEvidence(event['event_id'], event['action_digest'],
                                  ArtifactEvidence(f'write-{len(self.writes)}', write['path'],
                                      hashlib.sha256(write['content'].encode()).hexdigest()))
        self.after_commit = after


class ReplayCommitEvidenceTests(unittest.TestCase):
    def run_save(self, driver):
        return Agent(driver=driver, store=MemoryStore()).handle('"proof.rtf" 로 저장해줘', ask=lambda _: True)

    def test_real_known_recipe_window_title_without_write_is_not_done(self):
        driver = TextEditFixture()
        turn = self.run_save(driver)
        self.assertEqual(turn.status, 'unverified')
        self.assertEqual(driver.clicks, 1)
        self.assertEqual(driver.writes, [])
        self.assertEqual(turn.detail['event_state'], 'dispatched')

    def test_event_bound_receipt_checks_actual_mock_write_and_expected_content(self):
        driver = TextEditFixture(write=True)
        driver.install_evidence()
        turn = self.run_save(driver)
        self.assertEqual(turn.status, 'done', turn)
        self.assertEqual(driver.writes, [{'path': '/tmp/Downloads/proof.rtf', 'content': 'hello'}])

    def test_live_trace_wrapper_preserves_host_receipt_hooks(self):
        from nlc.trace import TracingDriver
        driver = TextEditFixture(write=True)
        driver.install_evidence()
        traced = TracingDriver(driver)
        turn = self.run_save(traced)
        self.assertEqual(turn.status, 'done', turn)
        self.assertEqual(len(driver.writes), 1)
        self.assertTrue(traced.trace.events)

    def test_no_write_or_corrupt_content_never_receives_done(self):
        for driver in (TextEditFixture(), TextEditFixture(write=True, content='corrupt')):
            with self.subTest(content=driver.content, write=driver.write):
                driver.install_evidence()
                self.assertEqual(self.run_save(driver).status, 'unverified')
                self.assertEqual(driver.clicks, 1)

    def test_wrong_event_path_or_filename_receipt_is_rejected(self):
        for field, value in (('event_id', 'old-event'), ('path', '/tmp/Elsewhere/proof.rtf'),
                             ('path', '/tmp/Downloads/other.rtf')):
            with self.subTest(field=field, value=value):
                driver = TextEditFixture(write=True)
                driver.install_evidence()
                original = driver.after_commit
                def changed(event, response, context):
                    from dataclasses import replace
                    proof = original(event, response, context)
                    return replace(proof, event_id=value) if field == 'event_id' else replace(
                        proof, evidence=replace(proof.evidence, path=value))
                driver.after_commit = changed
                self.assertEqual(self.run_save(driver).status, 'unverified')

    def test_dispatched_timeout_preserves_unknown_and_never_retries(self):
        driver = TextEditFixture(write=True)
        driver.fail_after_dispatch = True
        turn = self.run_save(driver)
        self.assertEqual(turn.status, 'unverified')
        self.assertEqual(turn.detail['event_state'], 'unknown')
        self.assertEqual(driver.clicks, 1)
        self.assertEqual(len(driver.writes), 1)

    def test_prepare_failure_prevents_commit_dispatch(self):
        driver = TextEditFixture(write=True)
        driver.install_evidence()
        def refuse(event):
            raise ValueError('destination already exists')
        driver.prepare_commit = refuse
        turn = self.run_save(driver)
        self.assertNotEqual(turn.status, 'done')
        self.assertEqual(driver.clicks, 0)
        self.assertEqual(driver.writes, [])

    def test_expected_content_cannot_be_replaced_with_result_after_dispatch(self):
        driver = TextEditFixture(write=True, content='corrupt')
        driver.install_evidence()
        original = driver.act
        def changing_expectation(*args, **kwargs):
            result = original(*args, **kwargs)
            if driver.writes:
                driver.commit_expected['다시 읽은 내용'] = 'corrupt'
            return result
        driver.act = changing_expectation
        self.assertEqual(self.run_save(driver).status, 'unverified')

    def test_declared_entry_delete_cannot_bypass_evidence_for_stored_file(self):
        safety = SafetyContract(effects=['delete'], resource=Resource(scope='entry'), reversibility='reversible')
        binding = ContextBinding(bundle_id='app', role='Button', title='Delete file', operation='click')
        step = Step('click', Selector(role='Button', title='Delete file'), safety=safety, binding=binding)
        class Driver:
            def identity(self, target): return target
            def observe(self, *args, **kwargs):
                return {'type': 'observation', 'obs_id': 'o', 'tree': '0 Button Title: Delete file'}
            def act(self, *args, **kwargs): return {'type': 'acted'}
        result = replay(Driver(), Flow('delete', 'app', [step]),
                        approvals={0: issue_approval(binding, safety)})
        self.assertFalse(result.ok)
        self.assertEqual(result.log[-1]['status'], 'unverified')
        self.assertEqual(result.events[0]['effects'], ['delete'])

    def test_calculator_clear_and_read_still_work_without_commit_receipt(self):
        safety = SafetyContract(effects=['delete'], resource=Resource(scope='entry'), reversibility='reversible')
        steps = [Step('click', Selector(role='Button', identifier='AllClear'), safety=safety),
                 Step('read', safety=SafetyContract(effects=['read'], reversibility='reversible',
                                                   resource=Resource(scope='entry')))]
        class Driver:
            def identity(self, target): return target
            def observe(self, *args, **kwargs):
                return {'type': 'observation', 'obs_id': 'o', 'tree': '0 Button Identifier: AllClear'}
            def act(self, *args, **kwargs): return {'type': 'acted'}
        result = replay(Driver(), Flow('clear', 'com.apple.calculator', steps))
        self.assertTrue(result.ok, result.log)
        self.assertEqual(result.log[-1]['status'], 'done')

    def test_selected_send_option_cannot_hide_behind_ephemeral_contract(self):
        calls = []
        step = Step('select_option', Selector(role='combobox', title='Action'),
                    params={'value':'send', 'option_label':'Send'},
                    safety=SafetyContract(effects=['write_ephemeral'],
                        resource=Resource(scope='entry'), reversibility='reversible'))
        class Driver:
            def identity(self, target): return target
            def observe(self, *args, **kwargs):
                return {'type':'observation','obs_id':'o','tree':'0 combobox Title: Action'}
            def act(self, *args, **kwargs):
                calls.append(args)
                return {'type':'acted'}
        result = replay(Driver(), Flow('hidden_send', 'app', [step]))
        self.assertFalse(result.ok)
        self.assertEqual(result.log[-1]['detail']['error'], 'contract_effect_mismatch')
        self.assertEqual(calls, [])
        self.assertEqual(result.events, [])


if __name__ == '__main__':
    unittest.main()
