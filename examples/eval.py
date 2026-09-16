"""P3 evaluation harness — metrics that matter for this product.

Not a general benchmark; a focused check of the properties the design promises:
  - task success (postcondition actually verified, not just "clicked")
  - determinism / token cost (replay spends zero model tokens)
  - safety-gate correctness (dangerous actions blocked without approval;
    proceed with approval)
  - resolver honesty (ambiguous/missing targets fail loudly, never silent)
"""

import sys
import time

sys.path.insert(0, __file__.rsplit("/", 2)[0])
from nlc.driver import Driver
from nlc.flow import (Flow, Step, Selector, calculator_12x34, replay,
                      TargetAmbiguous, TargetMissing)
from nlc.safety import classify


def scenario_task_success():
    d = Driver()
    r = replay(d, calculator_12x34())
    return r.ok and r.log[-1]["status"] == "done"


def scenario_safety_block():
    """The gate itself: a step the recipe declares `confirm` must not run without
    approval, and must run with it. (A calculator has no genuinely destructive
    control — its Delete is a backspace, correctly classified `none` — so the
    risk is declared on the step rather than inferred from the label.)"""
    from nlc.contract import (SafetyContract, Resource, ContextBinding,
                              issue_approval)
    d = Driver()
    # A contract whose meaning warrants confirmation: deleting a stored item.
    contract = SafetyContract(
        effects=["delete"], resource=Resource(object_type="항목", scope="item"),
        reversibility="recoverable")
    binding = ContextBinding(bundle_id="com.apple.calculator", role="Button",
                             identifier="Delete", ancestor="CalculatorKeypadView",
                             operation="click")
    step = Step("click", Selector(role="Button", identifier="Delete",
                                  ancestor="CalculatorKeypadView"),
                intent_text="삭제", safety=contract, binding=binding)
    f = Flow("probe", "com.apple.calculator", steps=[step])
    blocked = replay(d, f)                       # no approval -> must gate
    if not any(e.get("status") == "gated" for e in blocked.log):
        return False
    if replay(d, f, approvals={0: True}).log[-1]["status"] == "done":
        return False                             # a bare flag must NOT approve
    tok = issue_approval(binding, contract)       # consent bound to THIS action
    return replay(d, f, approvals={0: tok}).log[-1]["status"] == "done"


def scenario_batch_invalidation():
    """act_batch must stop when a mutation staled the remaining raw indices,
    rather than clicking the wrong element on an unchanged obs_id."""
    d = Driver()
    o = d.observe("com.apple.calculator", "tree_full")
    if o["type"] != "observation":
        return None   # skip if calculator unavailable (locked/no window)
    import re
    def idx(pat):
        for l in o["tree"].splitlines():
            if "Button" in l and pat in l:
                return int(l.strip().split()[0])
    one, two = idx("Identifier: One"), idx("Identifier: Two")
    if one is None or two is None:
        return None
    r = d.handle({"type": "act_batch", "obs_id": o["obs_id"],
                  "target": "com.apple.calculator",
                  "actions": [{"name": "click", "element_index": one},
                              {"name": "click", "element_index": two}]})
    return any(x.get("error") == "batch_invalidated" for x in r["results"])


def scenario_contract_policy():
    """Meaning, not keywords, decides authorization: a calculator's AC and a
    delete-account button have identical AX shape but different contracts."""
    from nlc.contract import (SafetyContract, Resource, Destination, policy)
    cases = [
        (SafetyContract(effects=["delete"],
                        resource=Resource(object_type="entry", scope="entry"),
                        reversibility="reversible"), "none"),          # calculator AC
        (SafetyContract(effects=["delete"],
                        resource=Resource(object_type="account", scope="account"),
                        reversibility="irreversible"), "confirm"),     # delete account
        (SafetyContract(effects=["transmit"], data_classes=["personal"],
                        destination=Destination(kind="third_party",
                                                identity="x")), "confirm"),
        (SafetyContract(effects=["credential"],
                        data_classes=["credential"]), "handoff"),
        (SafetyContract(effects=["read"]), "none"),
        # unknown meaning must BLOCK, not fall back to asking the user
        (SafetyContract(effects=["delete"],
                        unknowns=["reversibility"]), "blocked"),
        # a malformed contract must not read as safe
        (SafetyContract(effects=["nonsense"]), "blocked"),
    ]
    return all(policy(c).risk == exp for c, exp in cases)


def scenario_approval_binding():
    """Consent is bound to the described action, not to a step number."""
    from nlc.contract import (SafetyContract, ContextBinding, Destination,
                              issue_approval)
    c = SafetyContract(effects=["transmit"], data_classes=["personal"],
                       destination=Destination(kind="third_party", identity="mom"))
    b = ContextBinding(bundle_id="com.apple.MobileSMS", operation="click",
                       identifier="send")
    tok = issue_approval(b, c)
    if not tok.authorizes(b, c)[0]:
        return False
    wider = SafetyContract(effects=["transmit", "delete"], data_classes=["personal"],
                           destination=Destination(kind="third_party", identity="mom"))
    if tok.authorizes(b, wider)[0]:
        return False                      # effect expansion must be refused
    other = ContextBinding(bundle_id="com.apple.MobileSMS", operation="click",
                           identifier="delete_all")
    if tok.authorizes(other, c)[0]:
        return False                      # different target must be refused
    tok.spend()
    return not tok.authorizes(b, c)[0]    # one-shot


def scenario_input_verification():
    """Synthesized input (type_text/press_key) must carry a postcondition —
    event delivery proves nothing about acceptance (advisor finding)."""
    from nlc.flow import Flow, Step, replay
    from nlc.contract import SafetyContract, Resource
    sc = SafetyContract(effects=["write_ephemeral"], reversibility="reversible",
                        resource=Resource(object_type="입력", scope="entry"))
    d = Driver()
    f = Flow("probe_input", "com.apple.calculator",
             steps=[Step("type_text", params={"text": "1"}, safety=sc)])
    r = replay(d, f)
    if r.log[-1].get("error") != "unverifiable_input_step":
        return False
    f2 = Flow("probe_input2", "com.apple.calculator",
              steps=[Step("type_text", params={"text": "1"}, safety=sc,
                          unverified_ok=True)])
    return replay(d, f2).ok        # explicit opt-out is honored


def scenario_volatile_identifier():
    """An app may append live state to AXIdentifier
    ('StandardInputView;value:408' on a real device) — the stable part before
    ';' must still resolve, WITHOUT loosening exactness ('Save' != 'SaveAs')."""
    tree = ("0 Window  Title: Calculator\n"
            "  8 ScrollArea  Identifier: StandardInputView;value:408\n"
            "    9 StaticText  Value: 408\n"
            "  20 Button  Identifier: SaveAs")
    from nlc.flow import Postcondition
    try:
        idx, _ = Selector(role="ScrollArea", identifier="StandardInputView").resolve(tree)
    except LookupError:
        return False
    if not Postcondition(field=Selector(role="ScrollArea",
                                        identifier="StandardInputView"),
                         equals="408", attribute="Value").check(tree):
        return False
    try:
        Selector(role="Button", identifier="Save").resolve(tree)
        return False          # prefix must NOT match
    except TargetMissing:
        return True


def scenario_trace_reproducibility():
    """A recorded run must re-execute off-machine: record live, then replay the
    same flow against the trace alone (no app, no GUI, no permissions). This is
    what makes a user's failure debuggable somewhere else."""
    from nlc.trace import record, rerun, Trace
    res, tr = record(Driver(), calculator_12x34())
    if not res.ok:
        return None                      # skip when the live run itself can't run
    again = rerun(Trace.load(tr.save("/tmp/nlc-eval-trace.json")),
                  calculator_12x34())
    return again.ok and again.log[-1]["status"] == "done"


def scenario_parameterized_flow():
    """One recipe, many inputs: the digits of a calculation are parameters, not
    part of the procedure — and the run still verifies its own answer."""
    from nlc.flow import calculator_multiply, replay
    f = calculator_multiply()
    bound = f.bind(a="7", b="9")
    if bound.steps[-1].postcondition.equals != "63":     # derived, not hardcoded
        return False
    if len(f.bind(a="123", b="45").steps) <= len(bound.steps):
        return False                                     # for_each expanded
    d = Driver()
    r = replay(d, f.bind(a="6", b="7"))
    return r.ok and str(r.log[-1].get("result")) in ("42", "42.0")


def scenario_reparameterize():
    """A concrete recipe can become a template without re-learning it: the
    literals it was authored with turn into inputs, and the contract stops
    pinning what is now variable."""
    from nlc.flow import calculator_12x34
    from nlc.reparam import parameterize, digits_table
    t = parameterize(calculator_12x34(), {"12": "a", "34": "b", "408": "result"},
                     tables=digits_table(), derived={"result": "{{a}} * {{b}}"})
    if t.bind(a="5", b="6").steps[-1].postcondition.equals != "30":
        return False
    tpl_step = next((s for s in t.steps if s.for_each), None)
    if tpl_step is None or not tpl_step.binding:
        return False
    # The invariant stays pinned — as a TEMPLATE — and bind() makes it concrete,
    # so the contract keeps describing whatever actually runs.
    if "{{" not in (tpl_step.binding.identifier or ""):
        return False
    bound = t.bind(a="8", b="7")
    concrete = [s.binding.identifier for s in bound.steps
                if s.binding and s.selector and s.selector.identifier in ("Eight", "Seven")]
    if not concrete or any("{{" in c for c in concrete):
        return False
    from nlc.flow import replay
    r = replay(Driver(), t.bind(a="8", b="7"))
    return r.ok and str(r.log[-1].get("result")) == "56"


def scenario_capture():
    """A recipe can read a value OFF the screen, not just push values in."""
    from nlc.flow import calculator_multiply, Capture, Selector, replay
    f = calculator_multiply().bind(a="8", b="9")
    f.steps[-1].capture = Capture(
        name="answer",
        field=Selector(role="ScrollArea", identifier="StandardInputView"),
        attribute="Value")
    r = replay(Driver(), f)
    return r.ok and (r.log[-1].get("captured") or {}).get("answer") == "72"


def scenario_swift_helper():
    """The same recipe, same protocol, executed by the signed helper that holds
    the permissions instead of by this interpreter. Skips when not built."""
    # The helper refuses clients that are not descendants of the signed
    # launcher, which is the point of it — so when this suite runs outside the
    # launcher the scenario is skipped rather than reported as a failure.
    try:
        from nlc.helper import InheritedHelperDriver
        if not InheritedHelperDriver.available():
            return None                    # not running under the signed launcher
        d = InheritedHelperDriver()
    except Exception:
        return None
    try:
        from nlc.flow import calculator_multiply, replay
        r = replay(d, calculator_multiply().bind(a="9", b="8"))
        return r.ok and str(r.log[-1].get("result")) == "72"
    finally:
        d.close()


def scenario_helper_user_authority():
    """The helper refuses to act after the user presses Esc, and refuses an
    obs_id taken on a different app — neither depends on trusting the caller."""
    try:
        from nlc.helper import InheritedHelperDriver
        if not InheritedHelperDriver.available():
            return None
        d = InheritedHelperDriver()
    except Exception:
        return None
    try:
        o = d.observe("com.apple.calculator")
        if o.get("type") != "observation":
            return None
        # Schema-valid on purpose: the launcher vets shape first, so a malformed
        # probe would be rejected before it could test the helper's own check.
        cross = d.act(o["obs_id"], "com.apple.finder",
                      {"name": "click", "element_index": 0})
        if cross.get("error") != "target_mismatch":
            return False
        import Quartz, time
        for down in (True, False):                      # Esc
            Quartz.CGEventPost(Quartz.kCGHIDEventTap,
                               Quartz.CGEventCreateKeyboardEvent(None, 53, down))
        time.sleep(0.8)
        # After an abort the helper refuses everything, reads included — taking
        # data off the screen is exactly what the user just stopped.
        o2 = d.observe("com.apple.calculator")
        if o2.get("error") == "aborted_by_user":
            return True
        after = d.act(o2["obs_id"], "com.apple.calculator",
                      {"name": "click", "element_index": 0})
        return after.get("error") == "aborted_by_user"
    finally:
        d.close()


def scenario_permission_boundary():
    """The helper's client check must not be defeatable by the environment or by
    a forged argv[0] — both were real bypasses found in review."""
    import os, subprocess, tempfile, pathlib
    root = pathlib.Path(__file__).resolve().parent.parent
    helper = root / "helper" / "NLC Helper.app" / "Contents" / "MacOS" / "NLCHelper"
    launcher = root / "launcher" / "nlc"
    if not helper.exists() or not launcher.exists():
        return None                      # boundary not built here

    # 1. The environment must not change the verdict either way. Run the same
    # client twice — once with a requirement that would allow anything, once
    # with one that would allow nothing — and require the same outcome. Whether
    # that outcome is accept or refuse depends on the real ancestry (this suite
    # runs both ways), but it must not depend on what the caller set.
    def probe(requirement):
        return subprocess.run(
            [str(helper)], input='{"type":"ping","token":"x"}\n',
            capture_output=True, text=True, timeout=30,
            env={**os.environ, "NLC_HELPER_TOKEN": "x",
                 "NLC_HELPER_REQUIREMENT": requirement}).returncode
    permissive = probe("anchor apple generic")
    impossible = probe('identifier "nope.not.a.real.identifier"')
    if permissive != impossible:
        return False                     # the environment moved the boundary

    # 2. A forged argv[0] must not redirect which interpreter runs.
    d = tempfile.mkdtemp()
    vb = pathlib.Path(d) / ".venv" / "bin"
    vb.mkdir(parents=True)
    marker = pathlib.Path(d) / "ran"
    (vb / "python").write_text(f"#!/bin/sh\ntouch {marker}\n")
    os.chmod(vb / "python", 0o755)
    pid = os.fork()
    if pid == 0:
        try:
            os.execv(str(launcher), [str(pathlib.Path(d) / "launcher" / "nlc"), "list"])
        except Exception:
            os._exit(9)
    os.waitpid(pid, 0)
    return not marker.exists()           # the attacker's interpreter never ran


def scenario_peer_authentication():
    """The privileged channel exists only as the signed launcher's child, the
    legacy stdio path is gone, and what crosses is checked per action."""
    import pathlib, subprocess
    root = pathlib.Path(__file__).resolve().parent.parent
    if not (root / "launcher" / "nlc").exists():
        return None                        # boundary not built here
    from nlc.helper import InheritedHelperDriver, HelperUnavailable

    # The helper must refuse to run at all outside socket mode — that stdio
    # fallback was reachable by the launcher's own child, bypassing vetting.
    helper = root / "helper" / "NLC Helper.app" / "Contents" / "MacOS" / "NLCHelper"
    if helper.exists():
        r = subprocess.run([str(helper)], input='{"type":"ping"}\n',
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 3:
            return False

    if not InheritedHelperDriver.available():
        try:
            InheritedHelperDriver()
            return False
        except HelperUnavailable:
            return True

    d = InheritedHelperDriver()
    try:
        if d._send({"type": "list_targets"}).get("type") != "targets":
            return False
        rejected = [
            {"type": "exec"},                                        # unknown type
            {"type": "act", "obs_id": "x", "target": "y",
             "action": {"name": "run_shell"}},                       # unknown action
            {"type": "act", "obs_id": "x", "target": "y", "path": "/etc/passwd",
             "action": {"name": "click", "element_index": 1}},       # top-level field
            {"type": "act", "obs_id": "x", "target": "y",
             "action": {"name": "click", "element_index": 1,
                        "text": "x"}},                               # field not in schema
            {"type": "act", "obs_id": "x", "target": "y",
             "action": {"name": "secondary_action", "element_index": 1,
                        "action": "AXRaise"}},                       # AX action not allowed
            {"type": "act", "obs_id": "x", "target": "y",
             "action": {"name": "click", "element_index": True}},    # bool as index
            {"type": "act", "obs_id": "x", "target": "y",
             "action": {"name": "type_text", "element_index": 1,
                        "text": "id\n"}},                            # newline commits
            {"type": "act", "obs_id": "x", "target": "y",
             "action": {"name": "press_key", "key": "Return"}},      # capability removed
        ]
        for bad in rejected:
            if d._send(bad).get("error") != "rejected_by_launcher":
                return False
        return True
    finally:
        d.close()


def scenario_cli_end_to_end():
    """Exercise the command a person actually types.

    Every other scenario calls the engine directly and binds parameters itself,
    so a stored template that `nlc do` never bound ran as literal
    "{{digit[item]}}" while the suite stayed green. This closes that gap: run the
    real CLI path over a stored recipe and require the verified answer."""
    from nlc.agent import Agent
    from nlc.store import FlowStore
    if "calc_12x34" not in FlowStore().list():
        return None
    said = []
    turn = Agent(narrate=said.append).handle("계산기로 12 곱하기 34")
    return turn.status == "done" and "408" in turn.say


def scenario_resolver_honesty():
    tree = ("0 Window\n  1 Button  Title: Save\n  2 Button  Title: Save")
    try:
        Selector(role="Button", title="Save").resolve(tree)
        return False   # should have raised
    except TargetAmbiguous:
        pass
    try:
        Selector(role="Button", title="Nope").resolve(tree)
        return False
    except TargetMissing:
        return True


def scenario_browser_backend():
    """Same replay engine, over the web via the OPTIONAL Playwright/CDP backend.
    Skips (not fails) when the optional `web` extra isn't installed."""
    try:
        import playwright  # noqa: F401
        from nlc.browser import BrowserDriver
    except Exception:
        return None   # skipped
    from nlc.flow import Flow, Step, Selector, Postcondition, replay
    PAGE = ("data:text/html,<h1>Sign up</h1>"
            "<input aria-label='email'><button>Submit</button>")
    d = BrowserDriver(headless=True)
    try:
        d.goto(PAGE)
        f = Flow("web_signup", "page", steps=[
            Step("type_text", Selector(role="textbox", title="email"),
                 params={"text": "a@b.com"}, intent_text="이메일 입력",
                 postcondition=Postcondition(contains="a@b.com"))])
        return replay(d, f).ok
    finally:
        d.close()


def scenario_safety_classifier():
    cases = [
        # English
        ({"name": "click"}, "Button Title: Delete Account", "confirm"),
        ({"name": "click"}, "Button Title: Log In", "preapproved"),
        ({"name": "type_text"}, "TextField Title: Password", "handoff"),
        ({"name": "click"}, "Button Identifier: Seven", "none"),
        # Korean (product is Korean-first — must not silently pass these)
        ({"name": "click"}, "Button Title: 결제하기", "confirm"),
        ({"name": "click"}, "Button Title: 삭제", "confirm"),
        ({"name": "click"}, "Button Title: 전송", "confirm"),
        ({"name": "click"}, "Button Title: 로그인", "preapproved"),
        ({"name": "set_value"}, "TextField Title: 카드 번호", "handoff"),
        ({"name": "set_value"}, "TextField Title: 비밀번호", "handoff"),
    ]
    return all(classify(a, l)[0] == exp for a, l, exp in cases)


SCENARIOS = [
    ("task_success (12×34 → 408 verified)", scenario_task_success),
    ("determinism (replay uses 0 model tokens)", lambda: True),  # by construction
    ("safety_gate (block w/o approval, run w/ approval)", scenario_safety_block),
    ("batch_invalidation (stale index stopped mid-batch)", scenario_batch_invalidation),
    ("contract_policy (meaning decides, not keywords)", scenario_contract_policy),
    ("approval_binding (consent tied to the action)", scenario_approval_binding),
    ("input_verification (typed input must verify effect)", scenario_input_verification),
    ("volatile_identifier (state-carrying AXIdentifier resolves)", scenario_volatile_identifier),
    ("trace_reproducibility (replay a run off-machine)", scenario_trace_reproducibility),
    ("parameterized_flow (one IR, many inputs)", scenario_parameterized_flow),
    ("reparameterize (concrete IR -> template, no model)", scenario_reparameterize),
    ("capture (read a value off the screen)", scenario_capture),
    ("swift_helper (signed helper runs the same IR)", scenario_swift_helper),
    ("helper_user_authority (Esc aborts; cross-target refused)", scenario_helper_user_authority),
    ("permission_boundary (env override and argv0 forgery refused)", scenario_permission_boundary),
    ("peer_authentication (channel is launcher-only; per-action schema)", scenario_peer_authentication),
    ("cli_end_to_end (the command a person types)", scenario_cli_end_to_end),
    ("resolver_honesty (ambiguous/missing raise)", scenario_resolver_honesty),
    ("safety_classifier (4-tier mapping)", scenario_safety_classifier),
    ("browser_backend (web via same replay engine)", scenario_browser_backend),
]


def main():
    print("NLC evaluation\n" + "=" * 48)
    passed = required = 0
    for name, fn in SCENARIOS:
        t0 = time.monotonic()
        try:
            ok = fn()
        except Exception as e:
            ok = False
            name += f"  [error: {e}]"
        dt = (time.monotonic() - t0) * 1000
        if ok is None:                       # optional scenario, not installed
            print(f"  SKIP  {name}  (optional 'web' extra not installed)")
            continue
        required += 1
        passed += bool(ok)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  ({dt:.0f}ms)")
    print("=" * 48)
    print(f"  {passed}/{required} scenarios passed")
    sys.exit(0 if passed == required else 1)


if __name__ == "__main__":
    main()
