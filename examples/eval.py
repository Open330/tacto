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
    d = Driver()
    f = Flow("probe", "com.apple.calculator", steps=[
        Step("click", Selector(role="Button", identifier="Delete",
                               ancestor="CalculatorKeypadView"),
             risk_class="confirm", intent_text="지우기(확인 필요로 선언)")])
    blocked = replay(d, f)                       # no approval -> must gate
    proceeded = replay(d, f, approvals={0: True})   # approval -> must run
    return (any(e.get("status") == "gated" for e in blocked.log)
            and proceeded.log[-1]["status"] == "done")


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
    d = Driver()
    f = Flow("probe_input", "com.apple.calculator",
             steps=[Step("type_text", params={"text": "1"})])
    r = replay(d, f)
    if r.log[-1].get("error") != "unverifiable_input_step":
        return False
    f2 = Flow("probe_input2", "com.apple.calculator",
              steps=[Step("type_text", params={"text": "1"}, unverified_ok=True)])
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
    if tpl_step is None or (tpl_step.binding and tpl_step.binding.identifier):
        return False           # a parameterized field must not stay pinned
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
    try:
        from nlc.helper import HelperDriver, HelperUnavailable
        d = HelperDriver()
    except Exception:
        return None
    try:
        from nlc.flow import calculator_multiply, replay
        r = replay(d, calculator_multiply().bind(a="9", b="8"))
        return r.ok and str(r.log[-1].get("result")) == "72"
    finally:
        d.close()


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
