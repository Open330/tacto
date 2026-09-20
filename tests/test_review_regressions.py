"""Offline integration checks for the September review; never operate a real UI."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from nlc.contract import Envelope, Resource, SafetyContract
from nlc.flow import Flow, Selector, Step, replay
from nlc.general import run
from nlc.helper import HelperDriver
from nlc.outcome import DONE, Check, OutcomeSpec, spec_for, verify
from nlc.promote import Candidate, candidate_from, to_flow
from nlc.trace import Trace, TraceDriver, TracingDriver


class Screen:
    def __init__(self, tree):
        self.tree = tree
        self.calls = []

    def observe(self, target="app", mode="tree_full", require_window=True):
        return {"type": "observation", "obs_id": "o1", "tree": self.tree}

    def identity(self, target):
        return target

    def act(self, obs_id, target, action, **kwargs):
        self.calls.append((action, kwargs))
        return {"type": "acted"}

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        return self.observe() if predicate(self.tree) else {
            "type": "timeout", "last_obs": self.observe()}

    def probe(self, obs_id, target, element_index):
        line = next((ln for ln in self.tree.splitlines()
                     if ln.strip().startswith(f"{element_index} ")), "")
        return {"type": "probe", "line": line}

    def selection(self, target):
        return {"type": "selection", "digest": "stable"}


def harmless():
    return SafetyContract(effects=["read"], resource=Resource(scope="entry"),
                          reversibility="reversible")


class ReviewRegressions(unittest.TestCase):
    def test_disabled_target_is_not_dispatched(self):
        d = Screen("0 Window\n  1 Button Title: Continue [disabled]")
        f = Flow("disabled", "app", [Step("click", Selector(role="Button", title="Continue"),
                                         safety=harmless())])
        r = replay(d, f)
        self.assertFalse(r.ok)
        self.assertEqual([], d.calls)

    def test_missing_schema_does_not_enable_legacy_policy(self):
        raw = '{"name":"legacy","target":"app","steps":[{"action":"click","selector":{"role":"Button","title":"Delete Account"}}]}'
        d = Screen("0 Window\n  1 Button Title: Delete Account")
        try:
            r = replay(d, Flow.from_json(raw))
        except ValueError:
            pass
        else:
            self.assertFalse(r.ok)
        self.assertEqual([], d.calls)

    def test_real_preapproved_gate_receives_authorization(self):
        class GateScreen(Screen, HelperDriver):
            act = HelperDriver.act
            def _send(self, message):
                self.calls.append(message)
                return {"type": "acted"}
        d = GateScreen("0 Window\n  1 Button Title: Save\n  2 StaticText Identifier: filename, Value: report.txt")
        def plan(g, c, done):
            return {"done": True} if done else {"actions": [{"do": "click", "candidate": c[0]}]}
        env = Envelope(quote="저장해줘", allows=frozenset({"write_persistent"}))
        spec = OutcomeSpec(required=(Check("filename", "request", "before_commit", "reports"),),
                           expected={"filename": "report.txt"})
        r = run(d, "app", "save", plan, envelope=env, spec=spec,
                ask_where=lambda name, cands: {"pick": 0, "candidate": cands[0]})
        self.assertTrue(r.ok, r.why)
        self.assertEqual(1, len(d.calls))

    def test_declined_effect_did_not_happen(self):
        d = Screen("0 Window\n  1 Button Title: Send")
        r = run(d, "app", "send", lambda g,c,done: {
            "actions": [{"do": "click", "candidate": c[0]}]},
            envelope=Envelope(allows=frozenset({"transmit"})), approve=lambda _: False)
        self.assertEqual([], d.calls)
        self.assertEqual(set(), r.effects)
        self.assertEqual([], r.did)

    @staticmethod
    def recipient_spec():
        return OutcomeSpec(required=(Check('recipient','request','before_commit','reports'),),
                           expected={'recipient':'bob'})

    @staticmethod
    def where_recipient(name, cands):
        return {'pick':0,'candidate':cands[0]}

    def test_commit_requires_exact_fresh_recipient(self):
        for observed in ('bobby', 'alice', ''):
            with self.subTest(observed=observed):
                d = Screen(f'0 Window\n  1 Button Title: Send\n  2 StaticText Identifier: recipient, Value: {observed}')
                r = run(d,'app','send',lambda g,c,done: {
                    'actions':[{'do':'click','candidate':c[0]}]},
                    envelope=Envelope(allows=frozenset({'transmit'})),
                    spec=self.recipient_spec(), ask_where=self.where_recipient,
                    approve=lambda _:True)
                self.assertFalse(r.ok)
                self.assertEqual([],d.calls)
                self.assertEqual(set(),r.effects)

    def test_recipient_changed_during_approval_stops_commit(self):
        d = Screen('0 Window\n  1 Button Title: Send\n  2 StaticText Identifier: recipient, Value: bob')
        approvals = []
        def approve(description):
            approvals.append(description)
            d.tree = d.tree.replace('Value: bob','Value: bobby')
            return True
        r = run(d,'app','send',lambda g,c,done: {
            'actions':[{'do':'click','candidate':c[0]}]},
            envelope=Envelope(allows=frozenset({'transmit'})),
            spec=self.recipient_spec(), ask_where=self.where_recipient, approve=approve)
        self.assertEqual(1,len(approvals))
        self.assertFalse(r.ok)
        self.assertEqual([],d.calls)

    def test_plan_can_continue_after_commit_witness(self):
        d = Screen('0 Window\n  1 Button Title: Send\n  2 StaticText Identifier: recipient, Value: bob')
        def plan(goal,cands,done):
            if not done:
                return {'actions':[{'do':'click','candidate':cands[0]}]}
            if len(done)==1:
                return {'actions':[{'do':'read','candidate':cands[1]}]}
            return {'done':True}
        r = run(d,'app','send',plan,
                envelope=Envelope(allows=frozenset({'transmit','read'})),
                spec=self.recipient_spec(), ask_where=self.where_recipient,
                approve=lambda _:True)
        self.assertTrue(r.ok,r.why)
        self.assertEqual(2,len(d.calls))

    def test_missing_live_probe_cannot_confirm_recipient(self):
        class NoProbe(Screen):
            def probe(self,*args):
                return {'type':'error','error':'unsupported'}
        d = NoProbe('0 Window\n  1 Button Title: Send\n  2 StaticText Identifier: recipient, Value: bob')
        r = run(d,'app','send',lambda g,c,done: {
            'actions':[{'do':'click','candidate':c[0]}]},
            envelope=Envelope(allows=frozenset({'transmit'})),
            spec=self.recipient_spec(), ask_where=self.where_recipient,
            approve=lambda _:True)
        self.assertFalse(r.ok)
        self.assertEqual([],d.calls)

    def test_actual_general_result_can_be_promoted(self):
        d = Screen("0 Window\n  1 StaticText Identifier: answer, Value: 42")
        def plan(g,c,done):
            return {"done": True} if done else {"actions": [{"do": "read", "candidate": c[0]}]}
        r = run(d, "app", "read", plan)
        self.assertTrue(r.ok)
        candidate = candidate_from(r, "app", "read")
        self.assertIsNotNone(candidate)
        self.assertTrue(replay(d, to_flow(candidate, "learned")).ok)

    def test_title_candidate_is_not_bound_as_identifier(self):
        c = Candidate(goal="read", target="app", screens=["one"], verified=True,
                      actions=[{"do": "read", "role": "StaticText", "attr": "Title",
                                "match": "answer", "label": "answer"}])
        d = Screen("0 Window\n  1 StaticText Title: answer, Value: 42")
        r = replay(d, to_flow(c, "learned"))
        self.assertTrue(r.ok, r.log)

    def test_send_requires_exact_identity_and_complete_evidence(self):
        expected = {"받는 사람": "bob", "보내는 계정": "account", "보낸 내용": "hi", "원본 보존": "original"}
        spec = spec_for("send_message", Envelope(allows=frozenset({"transmit", "read"})),
                        expected, {k: "request" for k in expected})
        result = SimpleNamespace(ok=True, verified=True, effects={"transmit"}, said=[],
                                 witnessed={"받는 사람": "bobby", "보내는 계정": "account", "보낸 내용": "hi"})
        receipt = verify(spec, result, "original")
        self.assertNotEqual(DONE, receipt.state)
        self.assertTrue(any("받는 사람" in p for p in receipt.problems))
        result.witnessed["받는 사람"] = "bob"
        receipt = verify(spec, result, "original")
        self.assertNotEqual(DONE, receipt.state)
        self.assertTrue(any("전송 1회" in u for u in receipt.unchecked))
        self.assertTrue(any("숨은 참조" in u for u in receipt.unchecked))

    def test_tracing_preserves_driver_surface(self):
        d = TracingDriver(Screen("0 Window\n  1 StaticText Value: 42"))
        self.assertEqual("observation", d.observe("app", require_window=False)["type"])
        self.assertEqual("probe", d.probe("o1", "app", 1)["type"])
        self.assertEqual("selection", d.selection("app")["type"])

    def test_trace_replays_action_failure(self):
        class FailingScreen(Screen):
            def act(self, *args, **kwargs):
                return {"type": "error", "error": "action_failed", "effect": "unknown"}
        d = TracingDriver(FailingScreen("0 Window"))
        d.observe("app")
        want = d.act("o1", "app", {"name": "click", "element_index": 0})
        recorded = TraceDriver(d.trace)
        recorded.observe("app")
        self.assertEqual(want, recorded.act("o1", "app", {"name": "click", "element_index": 0}))

    def test_trace_rejects_different_action(self):
        d = TracingDriver(Screen("0 Window"))
        d.observe("app")
        d.act("o1", "app", {"name": "click", "element_index": 0})
        recorded = TraceDriver(d.trace)
        recorded.observe("app")
        self.assertEqual("trace_divergence", recorded.act("o1", "app", {"name": "read"}).get("error"))

    def test_trace_replaces_existing_public_file_privately(self):
        with tempfile.TemporaryDirectory() as td:
            path = Path(td) / "trace.json"
            path.write_text("")
            path.chmod(0o644)
            Trace(intent="private user words").save(str(path))
            self.assertEqual(0o600, path.stat().st_mode & 0o777)
            self.assertNotIn("private user words", path.read_text())

    def test_provider_json_strings_can_contain_braces(self):
        from nlc.authoring import _extract_json
        self.assertEqual({"text": "}"}, _extract_json('```json\n{"text":"}"}\n```'))

    def test_failed_helper_capability_check_cannot_be_bypassed_by_reconstruction(self):
        from unittest.mock import patch
        from nlc.helper import InheritedHelperDriver, HelperUnavailable
        pong = {'type':'pong','trusted':True,'capabilities':[]}
        with patch.object(InheritedHelperDriver,'_shared',None), \
             patch.dict('os.environ',{'NLC_HELPER_FD':'123'}), \
             patch('socket.socket'), \
             patch.object(InheritedHelperDriver,'_send',return_value=pong):
            for _ in range(2):
                with self.assertRaises(HelperUnavailable):
                    InheritedHelperDriver()

    def test_evaluator_does_not_hide_assertion_failures_without_gui(self):
        import contextlib
        import io
        import runpy
        from unittest.mock import patch
        path = Path(__file__).resolve().parents[1] / 'examples' / 'eval.py'
        # Import the harness against an inert driver; never load the native AX
        # backend merely to test reporting on Linux or a locked workstation.
        with patch.dict('sys.modules', {'nlc.driver':SimpleNamespace(Driver=object)}):
            namespace = runpy.run_path(str(path), run_name='audit_evaluation')
        looked = [False]
        def pure_failure():
            # A previous observation is not evidence that this failure needs
            # a GUI. An assertion failure must never become a successful skip.
            looked[0] = True
            return False
        def exit_with(code):
            raise SystemExit(code)
        namespace = namespace['main'].__globals__
        namespace.update({'sys':SimpleNamespace(argv=['eval.py'],exit=exit_with),
                          '_gui_reason':lambda:'synthetic unavailable GUI',
                          '_watch_for_looking':lambda:None,'_LOOKED':looked,
                          'SCENARIOS':[('pure_failure',pure_failure)]})
        with contextlib.redirect_stdout(io.StringIO()), self.assertRaises(SystemExit) as caught:
            namespace['main']()
        self.assertEqual(1,caught.exception.code)

    def test_abort_evaluation_cannot_skip_broken_new_run(self):
        import runpy
        from unittest.mock import patch
        path = Path(__file__).resolve().parents[1] / 'examples' / 'eval.py'
        with patch.dict('sys.modules', {'nlc.driver': SimpleNamespace(Driver=object)}):
            namespace = runpy.run_path(str(path), run_name='audit_abort_evaluation')
        # Both scenarios that run in a FRESH launcher process. For either of
        # them these errors mean the run started broken — the abort latch did
        # not reset, or the channel is wrong — and a scenario that reports that
        # as "could not check" hides the thing it exists to check.
        scenarios = ('scenario_an_abort_lasts_the_run_and_no_longer',
                     'scenario_a_new_run_rejects_the_old_token')
        for name in scenarios:
            check = namespace[name]
            for error in ('aborted_by_user', 'helper_unavailable',
                          'protocol_error'):
                with self.subTest(scenario=name, error=error), \
                        patch('nlc.helper.InheritedHelperDriver') as driver:
                    driver.available.return_value = True
                    driver.return_value.observe.return_value = {
                        'type': 'error', 'error': error,
                        'effect': 'not_started'}
                    try:
                        result = check()
                    except AssertionError:
                        continue
                    self.assertIs(result, False,
                                  'a broken new run must fail, never skip')

    def test_corpus_evaluation_cannot_skip_errors_or_missing_safety_metrics(self):
        import runpy
        from unittest.mock import patch
        path = Path(__file__).resolve().parents[1] / 'examples' / 'eval.py'
        with patch.dict('sys.modules', {'nlc.driver':SimpleNamespace(Driver=object)}):
            namespace = runpy.run_path(str(path), run_name='audit_corpus_evaluation')
        check = namespace['scenario_the_corpus_measures_tasks_not_properties']
        base = '잘못된 성공 보고율 0.0%\n범위 밖 효과 0.0%\n중복 효과 0.0%\n'
        for code, output in [(1,''),(0,base),(0,base+'검증 없는 성공 보고율 5.0%\n')]:
            with patch('subprocess.run',return_value=SimpleNamespace(returncode=code,stdout=output,stderr='synthetic')):
                try:
                    result = check()
                except Exception:
                    continue  # The harness converts exceptions to FAIL.
                self.assertIs(result,False,'corpus errors and unverified success must fail, never skip')


if __name__ == "__main__":
    unittest.main()
