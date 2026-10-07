"""Offline boundary regressions: observations and effects belong to a fake app."""
import contextlib
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch, Mock

from nlc.contract import ContextBinding, Envelope, Resource, SafetyContract, POLICY_VERSION
from nlc.flow import Capture, Flow, Postcondition, Selector, Step, replay
from nlc.general import run
from nlc.outcome import Check, OutcomeSpec, spec_for, verify, DONE
from nlc.store import FlowStore
from nlc.trace import TracingDriver, TraceDriver, Trace


class App:
    def __init__(self, recipient="mom", action="Send"):
        self.recipient = recipient
        self.action = action
        self.calls = []
        self.observations = 0
        self.fail = None

    def identity(self, target):
        return target

    @property
    def tree(self):
        return (f"0 Window  Title: Mail\n  1 Button  Title: {self.action}\n"
                f"  2 TextField  Identifier: recipient, Value: {self.recipient}\n"
                "  3 StaticText  Identifier: result, Value: 42")

    def observe(self, target, mode="tree_full", require_window=True):
        self.observations += 1
        return dict(type="observation", obs_id="o", tree=self.tree)

    def selection(self, target):
        return dict(type="selection", digest="stable")

    def probe(self, obs_id, target, element_index):
        line = next((s for s in self.tree.splitlines() if s.strip().startswith(f"{element_index} ")), "")
        return dict(type="probe", line=line)

    def act(self, obs_id, target, action, expect=None, risk_class="none", intent_text="", approved=False):
        if risk_class in ("preapproved", "confirm") and not approved:
            return dict(type="confirm_request")
        if risk_class == "handoff":
            return dict(type="handoff_request")
        if self.fail:
            return self.fail
        self.calls.append(dict(action))
        return dict(type="acted")

    def wait_until(self, target, predicate, timeout_ms=4000, poll_ms=250):
        obs = self.observe(target)
        return obs if predicate(obs["tree"]) else dict(type="timeout", last_obs=obs)


def where(name, candidates):
    c = next(c for c in candidates if c.get("match") == "recipient")
    return dict(pick=candidates.index(c), candidate=c)


def plan(goal, candidates, done):
    if not done:
        c = next(c for c in candidates if c["label"] in ("Send", "Save"))
        return {"actions": [{"do": "click", "candidate": c}]}
    if len(done) == 1:
        c = next(c for c in candidates if c["match"] == "result")
        return {"actions": [{"do": "read", "candidate": c}]}
    return {"done": True}


def spec():
    return OutcomeSpec(required=(Check("recipient", "request", "before_commit", "reports"),),
                       expected={"recipient": "mom"})


def contract(effects=("read",)):
    return SafetyContract(effects=list(effects), reversibility="reversible", resource=Resource(scope="entry"))


class RuntimeContract(unittest.TestCase):
    def test_reacquire_never_drops_original_container_when_sibling_disappears(self):
        from nlc.general import candidates, reacquire
        tree = ("0 Window  Title: form\n  1 Group  Title: billing\n"
                "    2 TextField  Title: name, Value: Alice\n"
                "  3 Group  Title: shipping\n    4 TextField  Title: name, Value: Bob")
        billing = next(c for c in candidates(tree) if c["container"] == "billing")
        self.assertEqual(2, reacquire(billing, tree))
        remaining = "0 Window  Title: form\n  1 Group  Title: shipping\n    2 TextField  Title: name, Value: Bob"
        self.assertIsNone(reacquire(billing, remaining))
        renamed = tree.replace("Title: billing", "Title: other")
        self.assertIsNone(reacquire(billing, renamed))

    def test_named_invariant_requires_direct_witness_even_if_text_is_elsewhere(self):
        from nlc.general import GeneralResult
        expected = OutcomeSpec(kind="test", must_read=["answer"], must_not_change=["original"],
            required=(Check("source", "prior", "after", "unchanged"),),
            expected={"source": "original"})
        result = GeneralResult(ok=True, verified=True, said=["answer"])
        self.assertNotEqual(DONE, verify(expected, result, "Value: original copy").state)
        result.witnessed["source"] = "changed"
        self.assertNotEqual(DONE, verify(expected, result, "Value: original").state)
        result.witnessed["source"] = "original"
        self.assertEqual(DONE, verify(expected, result).state)

    def test_scrolling_reobserves_and_reaches_later_content(self):
        class Pages(App):
            page = 0
            @property
            def tree(self):
                return ("0 Window  Title: Reader\n  1 ScrollArea  Identifier: article, "
                        f"Value: page {self.page}\n  2 StaticText  Identifier: result, "
                        f"Value: {'answer' if self.page == 2 else 'loading'}")
            def act(self, obs_id, target, action, **kwargs):
                result = super().act(obs_id, target, action, **kwargs)
                if action["name"] == "scroll":
                    self.page += action["pages"]
                return result
        app = Pages()
        def here(goal, cands, done):
            if app.page < 2:
                return {"actions": [
                    {"do": "scroll", "candidate": next(c for c in cands if c["match"] == "article"),
                     "direction": "down", "pages": 1},
                    {"do": "read", "candidate": next(c for c in cands if c["match"] == "result")}]}
            if len(done) == 2:
                return {"actions": [{"do": "read", "candidate": next(c for c in cands if c["match"] == "result")}]}
            return {"done": True}
        result = run(app, "app", "read", here)
        self.assertTrue(result.ok, result.why)
        self.assertEqual(["answer"], result.said)
        self.assertEqual(["scroll", "scroll", "read"], [a["name"] for a in app.calls])
        self.assertTrue(all(v.cut_short for v in result.visits[:2]))

    def test_candidates_do_not_silently_drop_later_controls(self):
        from nlc.general import candidates
        tree = "\n".join(f"{i} Button  Title: item{i}" for i in range(100))
        self.assertEqual(100, len(candidates(tree)))

    def test_element_bound_navigation_preserves_candidate_and_expectations(self):
        from nlc.general import candidates
        app = App(action="Next")
        app.capabilities = {"action.press_key", "press_key.element_bound", "press_key.no_escape"}
        choices = candidates(app.tree, capabilities=app.capabilities)
        self.assertNotIn("press_key", next(c for c in choices if c['role']=='StaticText')['can'])
        text = next(c for c in choices if c['role']=='TextField')
        self.assertNotIn('Escape', text['navigation_keys'])
        seen = []
        original = app.act
        def act(*args, **kwargs):
            seen.append(kwargs.get('expect'))
            return original(*args, **kwargs)
        app.act = act
        def here(goal, cands, done):
            if done: return {'done': True}
            candidate = next(c for c in cands if c['role']=='TextField')
            return {'actions':[{'do':'press_key','key':'Tab','candidate':candidate}]}
        result = run(app, 'app', 'navigate', here)
        self.assertTrue(result.ok, result.why)
        self.assertEqual(app.calls[0]['element_index'], 2)
        self.assertEqual(app.calls[0]['key'], 'Tab')
        self.assertEqual(seen[0]['role'], 'TextField')
        self.assertEqual(seen[0]['label'], 'recipient')

    def test_navigation_keys_require_capability_and_preserve_parameters(self):
        from nlc.general import candidates
        from nlc.promote import candidate_from, to_flow
        app = App(action="Next")
        def here(goal, cands, done):
            if done:
                return {"done": True}
            return {"actions": [{"do": "press_key", "key": "Tab", "candidate": cands[0]}]}
        self.assertFalse(any("press_key" in c["can"] for c in candidates(app.tree)))
        result = run(app, "app", "navigate", here)
        self.assertFalse(result.ok)
        self.assertEqual([], app.calls)
        app.capabilities = {"action.press_key"}
        traced = TracingDriver(app)
        result = run(traced, "app", "navigate", here)
        self.assertTrue(result.ok, result.why)
        self.assertEqual("Tab", app.calls[0]["key"])
        self.assertTrue(result.visits[0].cut_short)
        with tempfile.TemporaryDirectory() as folder:
            path = str(Path(folder) / "trace.json")
            traced.trace.save(path, redact=False)
            replayed = run(TraceDriver(Trace.load(path)), "app", "navigate", here)
            self.assertTrue(replayed.ok, replayed.why)
        result.verified = True
        candidate = candidate_from(result, "app", "navigate", envelope=Envelope.minimal())
        self.assertEqual({"key": "Tab"}, to_flow(candidate, "navigate").steps[0].params)
        app.calls.clear()
        def destructive(goal, cands, done):
            return {"actions": [{"do": "press_key", "key": "cmd+delete", "candidate": cands[0]}]}
        self.assertFalse(run(app, "app", "navigate", destructive).ok)
        self.assertEqual([], app.calls)

    def test_agent_real_send_kind_with_trusted_prior_and_empty_bcc_completes(self):
        from nlc.agent import Agent
        from nlc.promote import CandidateStore
        class Mail(App):
            def __init__(self, bcc=""):
                super().__init__()
                self.bcc, self.sent = bcc, 0
            def prepare_commit(self, event):
                return self.sent
            def after_commit(self, event, result, before):
                from nlc.commit_evidence import CommitEvidence, DeliveryEvidence
                import hashlib
                if self.sent == before + 1:
                    return CommitEvidence(event['event_id'],event['action_digest'],
                        DeliveryEvidence(f'message-{self.sent}', 'mom', 'sender',
                                         hashlib.sha256(b'hello').hexdigest()))
            @property
            def tree(self):
                fields = [("recipient", "mom"), ("account", "sender"),
                          ("body", "hello"), ("bcc", self.bcc),
                          ("original", "draft text"),
                          ("status", "Sent" if self.sent else "Draft")]
                return "0 Window  Title: Mail\n  1 Button  Title: Send\n" + "\n".join(
                    f"  {i} TextField  Identifier: {key}, Value: {value}"
                    for i, (key, value) in enumerate(fields, start=2))
            def act(self, *args, **kwargs):
                result = super().act(*args, **kwargs)
                if result["type"] == "acted" and args[2]["name"] == "click":
                    self.sent += 1
                return result
        def here(goal, candidates, done):
            if done:
                return {"done": True}
            button = next(c for c in candidates if c["label"] == "Send")
            return {"actions": [{"do": "click", "candidate": button}],
                    "expect": "Value: Sent"}
        def locate(name, candidates):
            key = {"받는 사람": "recipient", "보내는 계정": "account",
                   "보낸 내용": "body", "숨은 참조 없음": "bcc",
                   "원본 보존": "original"}[name]
            c = next(c for c in candidates if c["match"] == key)
            return {"pick": candidates.index(c), "candidate": c}
        reading = {"goal": "메시지 전송하기", "quote": "보내줘",
                   "allows": ["read", "transmit"], "scope": "item",
                   "reversibility": "irreversible", "kind": "send_message",
                   "values": {"recipient": "mom", "account": "sender", "body": "hello"}}
        for bcc, expected_sends in (("", 1), ("hidden", 0)):
            with self.subTest(bcc=bcc), tempfile.TemporaryDirectory() as root:
                app = Mail(bcc)
                def general(target, goal, confirm, env, expected):
                    self.assertEqual("send_message", expected.kind)
                    return run(app, target, goal, here, envelope=env,
                               spec=expected, ask_where=locate, approve=confirm)
                agent = Agent(driver=app, store=FlowStore(root + "/flows"),
                    interpret=lambda q: q.check(reading) if q.kind == "what_was_asked" else None,
                    general=general, outcome_context=lambda intent, request: (
                        {"원본 보존": "draft text"}, {"원본 보존": "prior"}))
                with patch("nlc.authoring.guess_target", return_value="app"), \
                     patch("nlc.promote.CandidateStore", return_value=CandidateStore(root + "/candidates")):
                    turn = agent.handle("sender 계정에서 mom에게 hello 보내줘", ask=lambda _: True)
                self.assertEqual(expected_sends, app.sent, turn.say)
                self.assertEqual(expected_sends == 1, turn.status == DONE, turn.say)

    def test_corpus_rejects_wrong_read_and_does_not_count_refusal_as_completion(self):
        from types import SimpleNamespace
        from examples.corpus import Case, World, judge
        case = Case("read", "plain", "normal", "read", "act")
        turn = SimpleNamespace(status="done", say="결과는 999 입니다", detail={})
        verdict = judge(case, World(display="42"), turn)
        self.assertFalse(verdict.success)
        self.assertTrue(verdict.wrong_success)
        refusal = Case("send", "plain", "normal", "send", "consequential")
        verdict = judge(refusal, World(), SimpleNamespace(status="failed", say="", detail={}))
        self.assertTrue(verdict.success)
        self.assertFalse(verdict.completed)

    def test_native_rejects_unknown_and_blocked_risk_before_touching_os(self):
        try:
            from nlc.driver import Driver
        except ImportError:
            self.skipTest("native dependencies unavailable")
        driver = Driver.__new__(Driver)
        for risk in ("blocked", "invented"):
            result = driver.act("o", "app", {"name": "click"},
                                risk_class=risk, approved=True)
            self.assertEqual("not_started", result["effect"])
        from types import SimpleNamespace
        for index in (True, 0.5, -1):
            with self.assertRaises(Exception):
                driver._element(SimpleNamespace(elements=[object(), object()]),
                                {"element_index": index})

    def test_unknown_actual_fields_cannot_authorize_unattended_mutation(self):
        from nlc.contract import policy, Evidence
        for c in (SafetyContract(effects=["write_ephemeral"]),
                  SafetyContract(effects=["write_persistent"], evidence=[
                      Evidence("user_intent", "request", "save", covers=("write_persistent",))])):
            self.assertEqual([], c.unknowns)
            self.assertEqual("blocked", policy(c).risk)
            c.effects.append("transmit")
            self.assertEqual("blocked", policy(c).risk)
        self.assertEqual("none", policy(SafetyContract(effects=["read"])).risk)

    def test_action_payload_cannot_override_dispatch_identity(self):
        for params in ({"name": "click"}, {"element_index": 99}):
            with self.assertRaises(ValueError):
                Flow.from_json(json.dumps({"name": "bad", "target": "app",
                    "steps": [{"action": "read", "params": params}]}))
            app = App()
            result = replay(app, Flow(name="bad", target="app", steps=[
                Step(action="read", params=params, safety=contract())]))
            self.assertFalse(result.ok)
            self.assertEqual(0, app.observations)

    def test_replay_gate_is_not_success(self):
        app = App(action="Save")
        flow = Flow(name="save", target="app", steps=[Step(action="click",
                    selector=Selector(role="Button", title="Save"),
                    safety=contract(("write_persistent",)))])
        result = replay(app, flow)
        self.assertFalse(result.ok)
        self.assertEqual("gated", result.log[-1]["status"])
        self.assertFalse(app.calls)

    def test_dispatch_exception_preserves_uncertain_effect(self):
        app = App()
        def throws(*args, **kwargs):
            raise OSError("lost driver channel")
        app.act = throws
        result = self.general(app, spec=spec(), approve=lambda _: True)
        self.assertFalse(result.ok)
        self.assertEqual("unknown", result.events[-1]["state"])
        self.assertIn("transmit", result.effects)
        replayed = replay(app, Flow(name="read", target="app", steps=[
            Step(action="read", safety=contract())]))
        self.assertFalse(replayed.ok)
        self.assertIn("driver_exception", str(replayed.log))

    def test_store_can_replace_corrupt_nonlegacy_file(self):
        with tempfile.TemporaryDirectory() as folder:
            store = FlowStore(folder)
            Path(folder, "read.json").write_text("{broken")
            store.save(Flow(name="read", target="app", steps=[]))
            self.assertEqual("read", store.load("read").name)

    def test_confirmed_commit_can_finish_and_duplicate_cannot(self):
        from copy import deepcopy
        from nlc.general import GeneralResult
        from nlc.commit_evidence import make_event, CommitEvidence, DeliveryEvidence
        import hashlib
        expected = OutcomeSpec(kind="send_message", must_read=["mom"], required=(
            Check("recipient", "request", "before_commit", "reports"),
            Check("once", "request", "after", "exactly_once")),
            expected={"recipient": "mom"})
        event = make_event({'name':'click'}, {'transmit'}, 'app', state='confirmed')
        proof = CommitEvidence(event['event_id'],event['action_digest'],
                               DeliveryEvidence('message-1','mom','sender',hashlib.sha256(b'hello').hexdigest()))
        result = GeneralResult(ok=True, verified=True, effects={"transmit"},
            witnessed={"recipient": "mom"}, events=[event],
            commit_evidence={event['event_id']:proof})
        self.assertEqual(DONE, verify(expected, result).state)
        result.events.append(deepcopy(result.events[0]))
        self.assertNotEqual(DONE, verify(expected, result).state)
        result.events = [{"effects": ["transmit"], "state": "unknown"}]
        self.assertNotEqual(DONE, verify(expected, result).state)

    def test_trace_records_driver_exception_as_unknown(self):
        app = App()
        app.act = Mock(side_effect=OSError("channel lost"))
        traced = TracingDriver(app)
        original = traced.act("o", "app", {"name": "click", "element_index": 1})
        self.assertEqual("unknown", original["effect"])
        self.assertEqual(original, TraceDriver(traced.trace).act(
            "o", "app", {"name": "click", "element_index": 1}))

    def general(self, app, **kwargs):
        return run(app, "app", "send to mom", plan, envelope=Envelope(
            quote="send to mom", allows=frozenset({"read", "transmit"}), scope="item"),
            ask_where=where, **kwargs)

    def test_wrong_recipient_never_dispatches_or_requests_approval(self):
        app = App("mom-other")
        consent = Mock(return_value=True)
        result = self.general(app, spec=spec(), approve=consent)
        self.assertFalse(result.ok)
        self.assertEqual([], app.calls)
        self.assertIn("mom-other", result.why)
        consent.assert_not_called()

    def test_missing_expected_value_or_spec_never_dispatches(self):
        for expected in (None, OutcomeSpec(required=spec().required)):
            with self.subTest(expected=expected):
                app = App()
                result = self.general(app, spec=expected, approve=lambda _: True)
                self.assertFalse(result.ok)
                self.assertEqual([], app.calls)

    def test_failed_probe_never_uses_snapshot_as_witness(self):
        app = App()
        app.probe = lambda *a: {"type": "error", "error": "stale_observation"}
        result = self.general(app, spec=spec(), approve=lambda _: True)
        self.assertFalse(result.ok)
        self.assertEqual([], app.calls)

    def test_recipient_changed_during_consent_is_rechecked(self):
        app = App()
        def consent(description):
            self.assertIn("mom", description)
            app.recipient = "dad"
            return True
        result = self.general(app, spec=spec(), approve=consent)
        self.assertFalse(result.ok)
        self.assertEqual([], app.calls)

    def test_commit_followed_by_another_screen_does_not_crash(self):
        app = App()
        result = self.general(app, spec=spec(), approve=lambda _: True)
        self.assertTrue(result.ok, result.why)
        self.assertEqual(["click", "read"], [c["name"] for c in app.calls])
        self.assertEqual("mom", result.witnessed["recipient"])
        self.assertEqual("unknown", result.certainty)  # dispatch alone is not proof
        self.assertEqual("dispatched", result.events[0]["state"])

    def test_gate_and_not_started_failures_are_not_effects(self):
        for error in ({"type": "error", "error": "target_changed", "effect": "not_started"},
                      {"type": "confirm_request"}):
            app = App()
            app.fail = error
            result = self.general(app, spec=spec(), approve=lambda _: True, max_screens=1)
            self.assertEqual(set(), result.effects)
            self.assertEqual([], result.events)

    def test_unknown_dispatch_is_not_safe_to_undo(self):
        app = App()
        app.fail = {"type": "error", "error": "transport_lost", "effect": "unknown"}
        result = self.general(app, spec=spec(), approve=lambda _: True)
        self.assertEqual("unknown", result.certainty)
        self.assertEqual("unknown", result.events[0]["state"])
        self.assertEqual({"transmit"}, result.effects)

    def test_schema_is_checked_before_any_observation(self):
        for schema in (1, 0, 3, True, "2"):
            app = App()
            flow = Flow("bad", "app", schema=schema)
            self.assertFalse(replay(app, flow).ok)
            self.assertEqual(0, app.observations)

    def test_missing_safety_fails_before_observation(self):
        app = App()
        result = replay(app, Flow("bad", "app", [Step("click")]))
        self.assertEqual("contract_required", result.log[-1]["error"])
        self.assertEqual(0, app.observations)

    def test_policy_version_cannot_validate_itself(self):
        flow = Flow("old", "app", [Step("read", safety=contract(),
            binding=ContextBinding(bundle_id="app", operation="read", policy_version=POLICY_VERSION + 1))])
        app = App()
        self.assertFalse(replay(app, flow).ok)
        self.assertEqual([], app.calls)

    def test_legacy_store_requires_migration_without_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            store = FlowStore(tmp)
            path = Path(tmp) / "old.json"
            original = '{"schema":1,"name":"old","target":"app","steps":[]}'
            path.write_text(original)
            for operation in (lambda: store.has("old"), lambda: store.load("old"),
                              lambda: store.save(Flow("old", "app"))):
                with self.assertRaises(FlowStore.MigrationRequired):
                    operation()
            self.assertEqual(original, path.read_text())

    def test_authoring_preserves_witnesses_and_uncertainty(self):
        from nlc.authoring import _to_flow
        flow = _to_flow(dict(name="save", target="app", steps=[dict(
            action="click", selector={"title": "Save"}, safety={"effects": ["write_persistent"]},
            binding={"resource_identity": "report.txt", "resource_identity_from": {"identifier": "filename"}},
            on_uncertain=["target_missing"])]))
        self.assertEqual("report.txt", flow.steps[0].binding.resource_identity)
        self.assertEqual({"identifier": "filename"}, flow.steps[0].binding.resource_identity_from)
        self.assertEqual(["target_missing"], flow.steps[0].on_uncertain)

    def test_provider_pin_crosses_launcher_channel(self):
        from nlc.authoring import reach_model
        with patch("nlc.helper.InheritedHelperDriver") as helper:
            helper.available.return_value = True
            helper.return_value.ask_launcher.return_value = "{}"
            self.assertEqual("{}", reach_model("question", "gemini", expect_json=True))
            helper.return_value.ask_launcher.assert_called_once_with("question", True, provider="gemini", timeout_seconds=420)

    def test_malformed_json_falls_through_even_with_open_brace(self):
        from nlc.authoring import _ask_model
        with patch("nlc.authoring._try_muxa", return_value="401 error {broken"), \
             patch("nlc.authoring._try_cli", return_value='{"text":"}"}'):
            self.assertEqual('{"text":"}"}', _ask_model("q", "codex", expect_json=True))

    def test_model_attempts_share_one_deadline(self):
        from nlc.authoring import _ask_model
        now = [10]
        def exhausted_route(*args):
            now[0] = 21
            return None
        with patch("nlc.authoring.time.monotonic", side_effect=lambda: now[0]), \
             patch("nlc.authoring._try_muxa", side_effect=exhausted_route), \
             patch("nlc.authoring._try_cli") as cli:
            with self.assertRaises(TimeoutError):
                _ask_model("q", "codex", timeout=10)
            cli.assert_not_called()

    def test_sensitive_capture_is_removed_everywhere_in_saved_trace(self):
        app = App("review-secret-value")
        cap = Capture("recipient", Selector(identifier="recipient"), sensitive=True)
        flow = Flow("read", "app", [Step("read", safety=contract(), captures=[cap])])
        traced = TracingDriver(app, Trace(intent="review-secret-value"))
        self.assertTrue(replay(traced, flow).ok)
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "trace.json"
            traced.trace.save(str(path))
            self.assertNotIn("review-secret-value", path.read_text())
            self.assertEqual(0o600, path.stat().st_mode & 0o777)

    def test_record_replay_keeps_requests_results_and_order(self):
        app = App()
        app.fail = {"type": "error", "error": "action_failed", "effect": "unknown"}
        flow = Flow("test", "app", [Step("read", Selector(identifier="result"), safety=contract())])
        traced = TracingDriver(app)
        original = replay(traced, flow)
        recorded = replay(TraceDriver(traced.trace), flow)
        self.assertEqual(original.log, recorded.log)

    def test_cli_run_can_approve_and_trace_without_claiming_unverified_save(self):
        from nlc import cli
        app = App(action="Save")
        flow = Flow("save", "app", [Step("click", Selector(title="Save"),
            safety=contract(["write_persistent"]), binding=ContextBinding(bundle_id="app", operation="click"))])
        with tempfile.TemporaryDirectory() as tmp:
            src, trace = Path(tmp) / "flow.json", Path(tmp) / "trace.json"
            src.write_text(flow.to_json())
            with patch.object(cli, "_make_driver", return_value=app), \
                 contextlib.redirect_stdout(io.StringIO()):
                code = cli.main(["run", str(src), "--yes", "--no-indicator", "--trace", str(trace)])
            # Approval dispatched one Save, but this fixture writes no file and
            # supplies no host receipt; a trace is not completion evidence.
            self.assertEqual(1, code)
            self.assertEqual(1, len(app.calls))
            self.assertTrue(trace.exists())

    def test_core_imports_without_macos_dependencies(self):
        script = '''import builtins
old = builtins.__import__
def restricted(name, *args, **kwargs):
    if name.split('.')[0] in ('AppKit','Quartz','ApplicationServices'):
        raise ImportError('macOS dependency forbidden in core: ' + name)
    return old(name, *args, **kwargs)
builtins.__import__ = restricted
import nlc.flow, nlc.agent, nlc.authoring, nlc.cli, nlc.trace, nlc.promote
'''
        result = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True)
        self.assertEqual(0, result.returncode, result.stderr)


if __name__ == "__main__":
    unittest.main()
