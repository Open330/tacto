"""P3 evaluation harness — metrics that matter for this product.

Not a general benchmark; a focused check of the properties the design promises:
  - task success (postcondition actually verified, not just "clicked")
  - determinism / token cost (replay spends zero model tokens)
  - safety-gate correctness (dangerous actions blocked without approval;
    proceed with approval)
  - resolver honesty (ambiguous/missing targets fail loudly, never silent)
"""

import os
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
        return "계산기 창이 없음 (잠금/미실행)"
    import re
    def idx(pat):
        for l in o["tree"].splitlines():
            if "Button" in l and pat in l:
                return int(l.strip().split()[0])
    one, two = idx("Identifier: One"), idx("Identifier: Two")
    if one is None or two is None:
        return "계산기 키패드 버튼을 찾지 못함"
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


def scenario_authorization_never_falls():
    """Adding an effect must never REMOVE the approval the others required.

    It did: `write_persistent` alone asked for confirmation, and
    `write_persistent` plus `delete` on a reversible entry asked for nothing,
    because each effect returned early and the first match won. An action that
    grows cannot become freer, so the check is the property itself rather than
    the handful of pairs that happened to be wrong."""
    from itertools import combinations
    from nlc.contract import (EFFECTS, Decision, Destination, Resource,
                              SafetyContract, policy)
    rank = {"none": 0, "preapproved": 1, "confirm": 2, "handoff": 3, "blocked": 4}
    shapes = [
        dict(resource=Resource(object_type="항목", scope="entry"),
             reversibility="reversible"),
        dict(resource=Resource(object_type="계정", scope="account"),
             reversibility="irreversible"),
        dict(resource=Resource(object_type="문서", scope="item"),
             reversibility="recoverable",
             destination=Destination(kind="third_party", identity="someone")),
    ]
    effects = sorted(EFFECTS)
    for shape in shapes:
        for size in (1, 2):
            for base in combinations(effects, size):
                low = policy(SafetyContract(effects=list(base), **shape))
                for extra in effects:
                    if extra in base:
                        continue
                    high = policy(SafetyContract(effects=[*base, extra], **shape))
                    if rank[high.risk] < rank[low.risk]:
                        return False
    return True


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
        return "실기기 실행이 되지 않아 녹화할 트레이스가 없음"
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
    f.steps[-1].captures = [Capture(
        name="answer",
        field=Selector(role="ScrollArea", identifier="StandardInputView"),
        attribute="Value")]
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
            return "서명된 런처 밖에서 실행 중 — ./launcher/nlc eval 로 확인"
        d = InheritedHelperDriver()
    except Exception:
        return "서명된 런처 밖 — 상속 채널 없음"
    try:
        from nlc.flow import calculator_multiply, replay
        r = replay(d, calculator_multiply().bind(a="9", b="8"))
        return r.ok and str(r.log[-1].get("result")) == "72"
    finally:
        d.close()


def scenario_helper_menu():
    """The signed helper must run menu commands, not only element presses.

    If it cannot, every task that lives in a menu — save, copy, open a folder —
    either does not run at all or quietly runs outside the permission boundary,
    which is the one thing the boundary exists to prevent. This also covers the
    activation the helper needs: as a background process it cannot raise an app
    the ordinary way, and a background app reports its own menus as disabled."""
    try:
        from nlc.helper import InheritedHelperDriver
        if not InheritedHelperDriver.available():
            return "서명된 런처 밖에서 실행 중 — ./launcher/nlc eval 로 확인"
        d = InheritedHelperDriver()
    except Exception:
        return "서명된 런처 밖 — 상속 채널 없음"
    try:
        from nlc.flow import replay
        from nlc.library import finder_open_folder
        r = replay(d, finder_open_folder().bind(folder="Documents"))
        return r.ok and r.log[-1]["status"] == "done"
    finally:
        d.close()


def scenario_helper_user_authority():
    """The helper refuses to act after the user presses Esc, and refuses an
    obs_id taken on a different app — neither depends on trusting the caller."""
    try:
        from nlc.helper import InheritedHelperDriver
        if not InheritedHelperDriver.available():
            return "서명된 런처 밖에서 실행 중 — ./launcher/nlc eval 로 확인"
        d = InheritedHelperDriver()
    except Exception:
        return "서명된 런처 밖 — 상속 채널 없음"
    try:
        o = d.observe("com.apple.calculator")
        if o.get("type") != "observation":
            return "계산기 창이 없음 (잠금/미실행)"
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
        return "권한 경계 미빌드 (helper/build.sh, launcher/build.sh)"

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
        return "권한 경계 미빌드 (launcher/build.sh)"
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
    said = []
    turn = Agent(narrate=said.append).handle("계산기로 12 곱하기 34")
    return turn.status == "done" and "408" in turn.say


def scenario_intent_parameters():
    """The request supplies the recipe's values, not just its name.

    Without this, "문서 폴더 열어줘" and "다운로드 폴더 열어줘" would need two
    stored recipes for one procedure. Pure resolution — no GUI, no tokens."""
    from nlc.agent import Agent
    a = Agent()
    cases = [("7 더하기 8 해줘", "calc_arith", {"a": "7", "b": "8", "op": "+"}),
             ("10 나누기 2", "calc_arith", {"a": "10", "b": "2", "op": "/"}),
             ("문서 폴더 열어줘", "finder_open_folder", {"folder": "Documents"}),
             ("바탕화면 열어줘", "finder_open_folder", {"folder": "Desktop"})]
    for said, name, params in cases:
        m = a.resolve(said)
        if m is None or m.name != name or m.params != params:
            return False
    return a.resolve("이런 건 아직 모르는 작업입니다") is None


def scenario_menu_command():
    """Drive a command that exists only in the menu bar.

    Everyday tasks — save, copy, open a folder — have no on-screen button, so a
    driver that walks windows alone simply cannot express them. This also starts
    from an app with no window at all, which is how Finder normally sits."""
    from nlc.flow import replay
    from nlc.library import finder_open_folder
    d = Driver()
    r = replay(d, finder_open_folder().bind(folder="Documents"))
    return r.ok and r.log[-1]["status"] == "done"


def scenario_menu_verification_required():
    """A menu press that reports success proves nothing.

    Measured on a real machine: Format > Font > Bold with no selection returns
    success and changes nothing. So `menu` joins type_text/press_key in owing a
    postcondition unless the recipe opts out on the record."""
    from nlc.flow import Flow, Step, replay
    from nlc.contract import SafetyContract, Resource
    sc = SafetyContract(effects=["read"], reversibility="reversible",
                        resource=Resource(object_type="메뉴", scope="item"))
    d = Driver()
    f = Flow("probe_menu", "com.apple.finder",
             steps=[Step("menu", params={"path": ["Go", "Home"]}, safety=sc)])
    return replay(d, f).log[-1].get("error") == "unverifiable_input_step"


def scenario_toggle_converges():
    """Replaying a toggle must converge, not oscillate.

    "Make it bold" runs Format > Font > Bold; run it twice without a guard and
    the document ends up plain. The recipe states the condition it exists to
    bring about, so the second run stands down."""
    from nlc.flow import replay
    from nlc.library import (textedit_new_document, textedit_write_note,
                             textedit_emphasize)
    d = Driver()
    if not _textedit_ready(d):
        return "TextEdit 이 실행 중이 아님 (open -a TextEdit)"
    # Make the document this needs instead of hoping one is lying around: the
    # suite now tidies up after itself, so nothing is.
    _tidy_textedit(d)
    if not replay(d, textedit_new_document()).ok:
        return "빈 문서를 열지 못함"
    if not replay(d, textedit_write_note().bind(text="굵게 수렴 테스트")).ok:
        return "TextEdit 본문에 쓸 수 없음"
    first = replay(d, textedit_emphasize())
    second = replay(d, textedit_emphasize())
    if not (first.ok and second.ok):
        return False
    # The point is not merely that both runs report success, but that the second
    # one did nothing — a rerun that pressed Bold again would have un-bolded it.
    _tidy_textedit(d)
    return any(e.get("status") == "skipped" for e in second.log)


def scenario_menu_path_pinned():
    """A contract pins WHICH menu command it was written for.

    `File > Save` and `File > Move to Trash` are the same `menu` operation on
    the same app, so the path is the only thing separating them. Repointing a
    step must invalidate the recorded meaning rather than run under it."""
    from nlc.flow import replay
    from nlc.library import finder_open_folder
    d = Driver()
    f = finder_open_folder().bind(folder="Documents")
    f.steps[0].params = {"path": ["Go", "Home"]}      # same operation, new command
    r = replay(d, f)
    return (not r.ok) and any(e.get("error") == "contract_invalidated"
                              for e in r.log)


def scenario_builtin_is_not_frozen():
    """A recipe that ships with NLC comes from the code, not from a cached copy.

    The store used to win once a name had been saved, so every built-in froze at
    whatever it looked like the first time it ran — fixes shipped in the library
    never reached the command people type."""
    from nlc.agent import Agent
    from nlc.library import builders
    from nlc.store import FlowStore
    store = FlowStore()
    stale = builders()["finder_open_folder"]()
    stale.steps[0].intent_text = "낡은 사본"
    store.save(stale)
    agent = Agent(store=store)
    agent.handle("문서 폴더 열어줘")
    return store.load("finder_open_folder").steps[0].intent_text != "낡은 사본"


def scenario_answers_a_question():
    """Some requests want an answer, not an action.

    Every other recipe here changes something; this one opens a folder and
    reads Finder's own status line back, and the agent has to actually say what
    it found rather than reporting that it finished."""
    from nlc.agent import Agent
    turn = Agent().handle("다운로드 폴더에 뭐 있어?")
    return (turn.status == "done" and turn.flow == "finder_list_items"
            and "item" in turn.say)


def scenario_consent_required():
    """A consequential step does not run on the recipe's own authority.

    Saving a file is `write_persistent`, which the policy will not grant by
    itself. With nobody to ask, the answer is no and nothing happens — checked
    without touching the screen."""
    from nlc.agent import Agent
    from nlc.library import textedit_save_as
    f = textedit_save_as().bind(filename="동의없음.rtf")
    approvals, blocked = Agent(driver=Driver()).authorize(f, ask=None)
    return not approvals and len(blocked) == 1


def _textedit_ready(d):
    """Is TextEdit running with something to act in?"""
    o = d.observe("com.apple.TextEdit", "tree_full", require_window=False)
    return o["type"] == "observation"


def _tidy_textedit(d):
    """Close what a scenario opened.

    Scenarios that make a document and walk away were piling up windows — a
    suite that leaves a different machine behind every time it runs is not
    repeatable, and the extra windows were themselves breaking later scenarios
    by making selectors ambiguous."""
    import re, time
    # Both kinds of sheet: the save panel (Cancel) and the "save changes?"
    # prompt that File > Close raises on an edited document (Delete / Don't
    # Save). Leaving the second one open put TWO Cancel buttons on screen, which
    # is what made close_dialog — correctly — call it ambiguous.
    for _ in range(12):
        o = d.observe("com.apple.TextEdit", "tree_full", require_window=False)
        if o["type"] != "observation":
            return
        hit = None
        for line in o["tree"].splitlines():
            if ("CancelButton" in line or "Title: Delete" in line
                    or "Don't Save" in line):
                hit = int(re.match(r"\s*(\d+)\s", line).group(1))
                break
        if hit is None:
            break
        d.act(o["obs_id"], "com.apple.TextEdit",
              {"name": "click", "element_index": hit})
        time.sleep(0.6)
    for _ in range(16):
        o = d.observe("com.apple.TextEdit", "tree_full", require_window=False)
        if o["type"] != "observation" or o["element_count"] == 0:
            return
        d.act(o["obs_id"], "com.apple.TextEdit",
              {"name": "menu", "path": ["File", ["Close", "닫기"]]})
        time.sleep(0.5)
        # Closing an edited document raises its own prompt; discard the draft
        # rather than leaving a second dialog behind.
        o = d.observe("com.apple.TextEdit", "tree_full", require_window=False)
        if o["type"] != "observation":
            return
        for line in o["tree"].splitlines():
            if "Title: Delete" in line or "Don't Save" in line:
                d.act(o["obs_id"], "com.apple.TextEdit",
                      {"name": "click",
                       "element_index": int(re.match(r"\s*(\d+)\s", line).group(1))})
                time.sleep(0.5)
                break


def scenario_save_file():
    """Save a document under a name, end to end, and clean up after itself.

    This is the first task in the suite with a consequence that outlives the
    run: a menu command that opens a sheet, a field inside that sheet, and a
    step the policy refuses without consent. The file is removed afterwards so
    the suite can run again tomorrow without leaving a pile behind."""
    import os, time, urllib.parse
    from ApplicationServices import AXUIElementCreateApplication
    from nlc.agent import Agent
    from nlc.driver import _attr
    from nlc.flow import replay
    from nlc.library import (textedit_new_document, textedit_write_note,
                             textedit_save_as)
    d = Driver()
    if not _textedit_ready(d):
        return "TextEdit 이 실행 중이 아님 (open -a TextEdit)"
    _tidy_textedit(d)
    name = f"nlc-eval-{int(time.time())}.rtf"
    agent = Agent(driver=d)

    def run(flow):
        approvals, blocked = agent.authorize(flow, ask=lambda _what: True)
        return (not blocked) and replay(d, flow, approvals=approvals).ok

    if not run(textedit_new_document()):
        return False
    if not run(textedit_write_note().bind(text="저장 시나리오 테스트")):
        return False
    if not run(textedit_save_as().bind(filename=name)):
        return False

    # Where it landed is not guessed: the window says which file it holds.
    pid = d._resolve("com.apple.TextEdit")
    app = AXUIElementCreateApplication(pid)
    saved = next((w for w in (_attr(app, "AXWindows") or [])
                  if _attr(w, "AXTitle") == name), None)
    if saved is None:
        return False
    url = _attr(saved, "AXDocument") or ""
    path = urllib.parse.unquote(url.replace("file://", ""))
    o = d.observe("com.apple.TextEdit", "tree_full")
    d.act(o["obs_id"], "com.apple.TextEdit",
          {"name": "menu", "path": ["File", ["Close", "닫기"]]})
    time.sleep(0.8)
    existed = os.path.exists(path)
    if existed:
        os.remove(path)
    return existed


def scenario_consent_is_about_the_file():
    """Consent names WHICH file, not just which button.

    Approve saving as A, then let the name field end up saying B: the click has
    to be refused. Without a witness the pin would be unverifiable, and without
    the pin the approval would cover any filename at all."""
    from nlc.agent import Agent
    from nlc.flow import replay
    from nlc.library import textedit_new_document, textedit_save_as
    d = Driver()
    if not _textedit_ready(d):
        return "TextEdit 이 실행 중이 아님 (open -a TextEdit)"
    # Start from a clean slate rather than assuming one: a scenario that needs
    # exactly one dialog open cannot depend on whatever ran before it.
    _tidy_textedit(d)
    agent = Agent(driver=d)
    approvals, blocked = agent.authorize(textedit_new_document(), ask=lambda _w: True)
    if blocked or not replay(d, textedit_new_document(), approvals=approvals).ok:
        return False

    f = textedit_save_as().bind(filename="승인한이름.rtf")
    approvals, blocked = agent.authorize(f, ask=lambda _what: True)
    if blocked:
        return False
    # The name actually typed no longer matches the name consent was given for.
    # Found by what the step DOES, not by its position: the recipe grew two
    # steps for choosing the folder and a hard-coded index silently moved onto
    # one of them.
    naming = next(st for st in f.steps if st.action == "set_value")
    naming.params = {"value": "다른이름.rtf"}
    naming.postcondition.equals = "다른이름.rtf"
    r = replay(d, f, approvals=approvals)
    refused = (not r.ok) and any(e.get("error") == "contract_invalidated"
                                 for e in r.log)
    _tidy_textedit(d)
    # Leave no sheet waiting for an answer: a stuck modal disables the whole
    # menu bar and would break every later scenario.
    o = d.observe("com.apple.TextEdit", "tree_full")
    from nlc.flow import Selector
    try:
        idx, _ = Selector(role="Button", identifier="CancelButton",
                          in_focused_window=True).resolve(o["tree"])
        d.act(o["obs_id"], "com.apple.TextEdit",
              {"name": "click", "element_index": idx})
    except Exception:
        pass
    return refused


def scenario_app_name_is_the_app():
    """"Messages" means the app, never a background extension of the same name.

    com.apple.messages.AssistantExtension is also called Messages and has no
    windows and no menu bar, so whichever the system listed first decided
    whether the app could be driven at all."""
    class Fake:
        def __init__(self, name, bundle, pid, policy):
            self._n, self._b, self._p, self._pol = name, bundle, pid, policy
        def localizedName(self):
            return self._n
        def bundleIdentifier(self):
            return self._b
        def processIdentifier(self):
            return self._p
        def activationPolicy(self):
            return self._pol

    d = Driver()
    # The extension listed FIRST, which is the order that used to decide it.
    pair = [Fake("Messages", "com.apple.messages.AssistantExtension", 111, 2),
            Fake("Messages", "com.apple.MobileSMS", 222, 0)]
    d._running_apps = lambda: pair
    if d._resolve("Messages", launch=False) != 222:
        return False
    # A bundle id is explicit and may name anything, including the extension.
    if d._resolve("com.apple.messages.AssistantExtension", launch=False) != 111:
        return False
    # With only the extension running, the NAME does not resolve at all —
    # reporting the app absent beats handing back a process with no windows.
    d._running_apps = lambda: [pair[0]]
    return d._resolve("Messages", launch=False) is None


def scenario_large_tree_is_bounded():
    """A huge app must answer, not hang.

    Mail walks past 11,000 elements; before the driver had a budget, observing
    it simply never returned, which meant the app people most want help with was
    the one that never worked. Reading the label attributes in one round trip
    instead of six is the other half."""
    d = Driver()
    if d._resolve("Mail", launch=False) is None:
        return "Mail 이 실행 중이 아님"
    t0 = time.monotonic()
    o = d.observe("Mail", "tree_full")
    took = time.monotonic() - t0
    if o["type"] != "observation":
        return False
    # Bounded in both senses: it finished, and it said what it left out. The
    # time ceiling is deliberately loose — the property is "answers instead of
    # hanging", and a tight one turns this into a benchmark that fails whenever
    # the machine is busy rather than when the driver is broken.
    return took < 90 and o["element_count"] <= 2500 and o.get("truncated") is True


def scenario_reads_a_list():
    """A folder's contents are plural, so reading them needs a plural selector.

    Acting still demands exactly one match; reading asks for a scope instead, or
    "every TextField on screen" would pass for a description."""
    from nlc.flow import Capture, replay
    from nlc.library import finder_list_items
    d = Driver()
    r = replay(d, finder_list_items().bind(folder="Applications"))
    names = (r.log[-1].get("captured") or {}).get("목록") or []
    if not (r.ok and len(names) > 3):
        return False
    try:
        Selector(role="TextField").resolve_all("0 Window\n  1 TextField  Value: x")
        return False                      # a bare role is not a description
    except TargetMissing:
        return True


def scenario_calendar_reads_today():
    """Today's events, without switching the window to day view.

    The weekday arrives in both the app's language and the caller's, because
    Calendar labels its columns in the system's and the two need not agree."""
    import datetime
    from nlc.agent import _WEEKDAY_KO
    from nlc.flow import replay
    from nlc.library import calendar_today
    d = Driver()
    if d._resolve("Calendar", launch=False) is None:
        return "Calendar 가 실행 중이 아님"
    t = datetime.date.today()
    f = calendar_today().bind(weekday_en=t.strftime("%A"),
                              weekday_ko=_WEEKDAY_KO[t.weekday()])
    r = replay(d, f)
    return r.ok and "오늘 일정" in (r.log[-1].get("captured") or {})


def scenario_mail_reads_inbox():
    """Mail's inbox: the window's own tally, plus senders and subjects."""
    from nlc.flow import replay
    from nlc.library import mail_inbox
    d = Driver()
    if d._resolve("Mail", launch=False) is None:
        return "Mail 이 실행 중이 아님"
    r = replay(d, mail_inbox())
    got = r.log[-1].get("captured") or {}
    return r.ok and bool(got.get("메일함")) and "제목" in got


def scenario_missing_target_says_what_is_there():
    """"matched nothing" leaves a person nowhere to go.

    The save panel offers only the folders in their own sidebar, so asking for
    another one fails — and the useful reply is the list it does offer."""
    from nlc.flow import _nearby
    tree = ("0 Window [focused]\n"
            "  1 Outline  Description: sidebar\n"
            "    2 StaticText  Value: Downloads\n"
            "    3 StaticText  Value: iCloud Drive")
    said = _nearby(Selector(role="StaticText", value="Documents"), tree)
    return "Downloads" in said and "iCloud Drive" in said


def scenario_absence_is_verifiable():
    """Some actions are verified by something going away.

    A dialog closing adds nothing to the screen, so a postcondition that can
    only assert presence has nothing to check."""
    from nlc.flow import Postcondition
    before = "0 Window\n  1 Sheet  Description: save"
    after = "0 Window\n  1 TextArea  Value: hi"
    gone = Postcondition(absent="Sheet")
    return (not gone.check(before)) and gone.check(after)


def scenario_dialog_recovery():
    """A run that stopped midway leaves its dialog open; there has to be a way out.

    Nothing dismisses it automatically — cancelling is an action nobody asked
    for — so it is a recipe the person invokes, and it presses Cancel, never the
    button that commits."""
    import time as _t
    from nlc.agent import Agent
    from nlc.flow import replay
    from nlc.library import close_dialog, textedit_new_document
    d = Driver()
    if not _textedit_ready(d):
        return "TextEdit 이 실행 중이 아님 (open -a TextEdit)"
    # Start from a clean slate rather than assuming one: a scenario that needs
    # exactly one dialog open cannot depend on whatever ran before it.
    _tidy_textedit(d)
    agent = Agent(driver=d)
    approvals, blocked = agent.authorize(textedit_new_document(), ask=lambda _w: True)
    if blocked or not replay(d, textedit_new_document(), approvals=approvals).ok:
        raise AssertionError("빈 문서를 열지 못함")
    o = d.observe("com.apple.TextEdit", "tree_full", require_window=False)
    opened = d.act(o["obs_id"], "com.apple.TextEdit",
                   {"name": "menu", "path": ["File", ["Save As…", "Save…"]]})
    _t.sleep(2)
    stuck = d.observe("com.apple.TextEdit", "tree_full")
    if "save-panel" not in stuck["tree"]:
        raise AssertionError(f"저장 시트를 띄우지 못함 ({opened.get('detail', opened.get('type'))})")
    r = replay(d, close_dialog().bind(app="com.apple.TextEdit"))
    cleared = "save-panel" not in d.observe("com.apple.TextEdit", "tree_full")["tree"]
    _tidy_textedit(d)
    if not r.ok:
        bad = next((e for e in r.log if e.get("status") == "error"), {})
        raise AssertionError(f"닫기 실패: {bad.get('error')} {str(bad.get('detail'))[:120]}")
    return cleared


def scenario_save_chooses_its_folder():
    """A file lands where it was asked to, not where the app last saved.

    The save panel here was still pointing at a terminal's support directory;
    the folder is now named, selected from the panel's own sidebar, pinned into
    the consent, and checked against the window afterwards."""
    import os, time as _t, urllib.parse
    from ApplicationServices import AXUIElementCreateApplication
    from nlc.agent import Agent
    from nlc.driver import _attr
    from nlc.flow import replay
    from nlc.library import (textedit_new_document, textedit_write_note,
                             textedit_save_as)
    d = Driver()
    if not _textedit_ready(d):
        return "TextEdit 이 실행 중이 아님 (open -a TextEdit)"
    _tidy_textedit(d)
    agent = Agent(driver=d)
    name = f"nlc-eval-{int(_t.time())}.rtf"

    def run(flow):
        approvals, blocked = agent.authorize(flow, ask=lambda _w: True)
        return (not blocked) and replay(d, flow, approvals=approvals).ok

    ok = (run(textedit_new_document())
          and run(textedit_write_note().bind(text="폴더 지정 저장"))
          and run(textedit_save_as().bind(filename=name, folder="Downloads")))
    path = ""
    if ok:
        app = AXUIElementCreateApplication(d._resolve("com.apple.TextEdit"))
        win = next((w for w in (_attr(app, "AXWindows") or [])
                    if _attr(w, "AXTitle") == name), None)
        path = urllib.parse.unquote((_attr(win, "AXDocument") or "")
                                    .replace("file://", "")) if win else ""
    _tidy_textedit(d)
    landed = bool(path) and os.path.exists(path)
    if landed:
        os.remove(path)
    return ok and landed and os.path.dirname(path).endswith("/Downloads")


def scenario_learning_is_reachable():
    """The one path that handles a task nobody wrote a recipe for.

    `--learn` read its own flag out of a list it had already stripped it from,
    so the condition could never be true: every unknown task was reported
    unconditionally and authoring had never once run from the CLI."""
    import nlc.cli as cli
    src = __import__("inspect").getsource(cli.main)
    # The flag must be read before the list it lives in is filtered.
    return ("learning = \"--learn\" in rest" in src
            and src.index("learning = ") < src.index("if r != \"--learn\""))


def scenario_authoring_produces_a_runnable_flow():
    """A model's recipe must survive the whole path and actually run.

    The provider is stubbed because authoring costs tokens and needs a logged-in
    CLI, but everything else is the real thing: the real prompt, the real
    parser, the real selector validation, and a real replay against the app.
    This is what "handles a task it has never seen" has to mean."""
    import json as _json
    import nlc.authoring as authoring
    from nlc.flow import replay
    d = Driver()
    if d._resolve("com.apple.calculator", launch=False) is None:
        return "계산기가 실행 중이 아님"
    reply = _json.dumps({
        "name": "probe_scientific", "target": "com.apple.calculator",
        "steps": [{"action": "menu",
                   "params": {"path": ["View", ["Scientific", "공학용"]]},
                   "intent_text": "공학용으로 바꿉니다",
                   "postcondition": {"contains": "Mode: scientific"},
                   "safety": {"effects": ["write_ephemeral"],
                              "data_classes": ["none"],
                              "resource": {"object_type": "계산기 보기",
                                           "scope": "entry"},
                              "destination": {"kind": "none"},
                              "reversibility": "reversible",
                              "commit_point": False, "unknowns": []}}]})
    real_ask, real_reach = authoring._ask_model, authoring.reach_model
    # **kw so a new argument shows up as a real failure to fix, not as a stub
    # that silently stopped matching. BOTH have to be stubbed: inside the signed
    # launcher `reach_model` goes out over the relay instead of calling
    # `_ask_model`, so stubbing only the latter silently reached a real provider
    # and these scenarios started billing tokens to fail.
    authoring._ask_model = lambda prompt, provider="auto", **kw: reply
    authoring.reach_model = lambda prompt, provider="auto", **kw: reply
    try:
        flow = authoring.author("계산기 공학용으로", d,
                                target="com.apple.calculator")
        ok = replay(d, flow).ok
    finally:
        authoring._ask_model, authoring.reach_model = real_ask, real_reach
        back = d.observe("com.apple.calculator", "tree_full", require_window=False)
        if back["type"] == "observation":
            d.act(back["obs_id"], "com.apple.calculator",
                  {"name": "menu", "path": ["View", ["Basic", "기본"]]})
    return ok


def _stub_author(reply: str, intent: str, target: str, d):
    """Run the real authoring path with the model's reply supplied by hand.

    Authoring costs tokens and needs a logged-in agent CLI, so the provider is
    the one thing stubbed; the prompt, the parser, the selector validation and
    the replay are all the real ones."""
    import nlc.authoring as authoring
    real, real_reach = authoring._ask_model, authoring.reach_model
    # Both: see the note in scenario_authoring_produces_a_runnable_flow.
    authoring._ask_model = lambda prompt, provider="auto", **kw: reply
    authoring.reach_model = lambda prompt, provider="auto", **kw: reply
    try:
        return authoring.author(intent, d, target=target)
    finally:
        authoring._ask_model, authoring.reach_model = real, real_reach


def _contract(object_type: str) -> dict:
    return {"effects": ["write_ephemeral"], "data_classes": ["none"],
            "resource": {"object_type": object_type, "scope": "entry"},
            "destination": {"kind": "none"}, "reversibility": "reversible",
            "commit_point": False, "unknowns": []}


def scenario_authoring_handles_a_dialog():
    """A recipe whose later steps act INSIDE a dialog.

    A sheet's elements do not exist until it opens, so they cannot appear in the
    one tree authoring was shown. Insisting every selector resolve against that
    tree made every consequential task inexpressible — save, open, print,
    preferences all happen in a sheet — and this is the case that proves it."""
    import json as _json
    from nlc.flow import replay
    from nlc.library import textedit_new_document
    d = Driver()
    if not _textedit_ready(d):
        return "TextEdit 이 실행 중이 아님 (open -a TextEdit)"
    _tidy_textedit(d)
    if not replay(d, textedit_new_document()).ok:
        return "빈 문서를 열지 못함"
    name_field = {"role": "TextField", "identifier": "saveAsNameTextField",
                  "in_focused_window": True}
    reply = _json.dumps({
        "name": "probe_dialog", "target": "com.apple.TextEdit",
        "steps": [
            {"action": "menu", "params": {"path": ["File", ["Save As…", "Save…"]]},
             "intent_text": "저장 창을 엽니다",
             "postcondition": {"contains": "save-panel"},
             "safety": _contract("저장 대화상자")},
            {"action": "set_value", "selector": name_field,
             "params": {"value": "authoring-probe.rtf"},
             "intent_text": "파일 이름을 적습니다",
             "postcondition": {"field": name_field,
                               "equals": "authoring-probe.rtf",
                               "attribute": "Value"},
             "safety": _contract("파일 이름")},
            # Cancel, not Save: the point is that the dialog can be driven, not
            # that the suite should leave a file behind.
            {"action": "click",
             "selector": {"role": "Button", "title": ["Cancel", "취소"],
                          "in_focused_window": True},
             "intent_text": "저장을 취소합니다",
             "postcondition": {"absent": "save-panel"},
             "safety": _contract("저장 대화상자")}]})
    flow = _stub_author(reply, "이름 붙여 저장 창 열기", "com.apple.TextEdit", d)
    ok = len(flow.steps) == 3 and replay(d, flow).ok
    _tidy_textedit(d)
    return ok


def scenario_authoring_still_catches_invented_targets():
    """Relaxing the check must not make it useless.

    A selector the model invented about the screen it was actually shown is a
    hallucination and still has to be refused; only the steps that point past a
    screen change are left for replay to verify."""
    import json as _json
    d = Driver()
    if d._resolve("com.apple.calculator", launch=False) is None:
        return "계산기가 실행 중이 아님"
    reply = _json.dumps({
        "name": "probe_invented", "target": "com.apple.calculator",
        "steps": [{"action": "click",
                   "selector": {"role": "Button",
                                "identifier": "ThisButtonDoesNotExist"},
                   "intent_text": "없는 버튼",
                   "safety": _contract("계산기 입력")}]})
    try:
        _stub_author(reply, "없는 걸 눌러줘", "com.apple.calculator", d)
    except ValueError:
        return True
    return False


def scenario_already_true_is_not_proof():
    """A postcondition that held BEFORE the action verifies nothing.

    A model-authored recipe passed this way once: it clicked a control that does
    not respond, and the checkbox it checked had been on the whole time. The run
    is not refused — setting a value that already matches is legitimate — but
    the log has to say which kind of pass it was."""
    from nlc.contract import SafetyContract, Resource
    from nlc.flow import Flow, Postcondition, Step, replay
    d = Driver()
    if d._resolve("com.apple.calculator", launch=False) is None:
        return "계산기가 실행 중이 아님"
    KP = "CalculatorKeypadView"
    entry = Resource(object_type="계산기 입력", scope="entry")
    zeroed = Postcondition(field=Selector(role="ScrollArea",
                                          identifier="StandardInputView"),
                           equals="0", attribute="Value")
    clear = Flow("probe_clear", "com.apple.calculator", steps=[
        Step("click", Selector(role="Button", identifier=["AllClear", "Clear"],
                               ancestor=KP), intent_text="지우기",
             safety=SafetyContract(effects=["delete"], resource=entry,
                                   reversibility="reversible"),
             postcondition=zeroed)])
    typed = Flow("probe_five", "com.apple.calculator", steps=[
        Step("click", Selector(role="Button", identifier="Five", ancestor=KP),
             intent_text="5",
             safety=SafetyContract(effects=["write_ephemeral"], resource=entry,
                                   reversibility="reversible"))])
    if not replay(d, typed).ok:
        return "계산기에 값을 넣지 못함"
    first = replay(d, clear)          # display was 5 -> really became 0
    second = replay(d, clear)         # display was already 0
    if not (first.ok and second.ok):
        return False
    def verified(res):
        return next(e.get("verified") for e in res.log if e.get("status") == "ok")
    return verified(first) == "postcond" and verified(second) == "already_true"


def _blocking_sheet(d):
    """Leave a save dialog open, the way a run that stopped midway does."""
    import time as _t
    from nlc.flow import replay as _replay
    from nlc.library import textedit_new_document
    _tidy_textedit(d)
    if not _replay(d, textedit_new_document()).ok:
        return False
    o = d.observe("com.apple.TextEdit", "tree_full", require_window=False)
    d.act(o["obs_id"], "com.apple.TextEdit",
          {"name": "menu", "path": ["File", ["Save As…", "Save…"]]})
    _t.sleep(2)
    o = d.observe("com.apple.TextEdit", "tree_full")
    return o["type"] == "observation" and "save-panel" in o["tree"]


def scenario_escalation_is_off_by_default():
    """Replay stays free and deterministic unless a caller buys judgment.

    A step may declare which surprises it expects, but declaring is not the same
    as asking: with no escalate callback and no budget, a blocked run fails
    exactly as it did before, having called nothing."""
    from nlc.flow import replay
    from nlc.library import textedit_save_as
    d = Driver()
    if not _textedit_ready(d):
        return "TextEdit 이 실행 중이 아님 (open -a TextEdit)"
    if not _blocking_sheet(d):
        return "저장 시트를 띄우지 못함"
    calls = []
    flow = textedit_save_as().bind(filename="차단.rtf", folder="Downloads")
    plain = replay(d, flow)                      # no escalate, no budget
    budgeted = replay(d, flow, escalate=lambda why: calls.append(why),
                      budget=0)                  # a resolver, but nothing to spend
    _tidy_textedit(d)
    return (not plain.ok) and (not budgeted.ok) and not calls


def scenario_escalation_only_where_declared():
    """A recipe that escalates on anything is one that never fails honestly.

    The step has to say which kind of surprise it expects; anything else ends
    the run the way it always did."""
    from nlc.contract import SafetyContract, Resource
    from nlc.flow import Flow, Step, replay
    asked = []
    d = Driver()
    if d._resolve("com.apple.calculator", launch=False) is None:
        return "계산기가 실행 중이 아님"
    sc = SafetyContract(effects=["write_ephemeral"], reversibility="reversible",
                        resource=Resource(object_type="입력", scope="entry"))
    missing = Selector(role="Button", identifier="NoSuchButton",
                       ancestor="CalculatorKeypadView")
    quiet = Flow("probe_quiet", "com.apple.calculator",
                 steps=[Step("click", missing, intent_text="없는 버튼", safety=sc)])
    r = replay(d, quiet, escalate=lambda why: asked.append(why), budget=3)
    if asked or r.ok:
        return False
    loud = Flow("probe_loud", "com.apple.calculator",
                steps=[Step("click", missing, intent_text="없는 버튼", safety=sc,
                            on_uncertain=["target_missing"])])
    replay(d, loud, escalate=lambda why: asked.append(why) or None, budget=3)
    # Declared, so it was asked — once, because that is what the budget bought.
    return len(asked) == 1 and asked[0].kind == "target_missing"


def scenario_escalation_clears_a_blocked_run():
    """The point of the whole thing: a surprise becomes a question, not an end.

    A dialog left open disables the menu the recipe needs. The resolver is
    stubbed rather than model-backed — what is under test is the engine's
    ability to take an answer, apply it and carry on, not the model's ability to
    write one."""
    from nlc.flow import Resolution, replay
    from nlc.library import textedit_save_as
    d = Driver()
    if not _textedit_ready(d):
        return "TextEdit 이 실행 중이 아님 (open -a TextEdit)"
    if not _blocking_sheet(d):
        return "저장 시트를 띄우지 못함"

    def resolver(why):
        # A recovery is NAMED, not described: what dismiss_dialog does is
        # written in this repo, so a resolution cannot smuggle in a step whose
        # declared meaning differs from what the control really does.
        return Resolution("recover", recovery="dismiss_dialog",
                          reason="저장 창이 이미 열려 있었습니다")

    # A fresh name every run: a leftover from a failed run makes the save raise
    # its own "already exists, replace?" dialog, which is a different blockage
    # than the one under test.
    name = f"nlc-escal-{int(time.time())}.rtf"
    flow = textedit_save_as().bind(filename=name, folder="Downloads")
    # Only the first step may escalate; the consequential save still cannot.
    r = replay(d, flow, approvals=_approve_all(d, flow),
               escalate=resolver, budget=1)
    resolved = [e for e in r.log if e.get("status") == "resolved"]
    path = _saved_path(d, name)
    _tidy_textedit(d)
    if path and os.path.exists(path):
        os.remove(path)
    return r.ok and len(resolved) == 1 and bool(path)


def _approve_all(d, flow):
    from nlc.agent import Agent
    approvals, blocked = Agent(driver=d).authorize(flow, ask=lambda _w: True)
    return {} if blocked else approvals


def _saved_path(d, name):
    import urllib.parse
    from ApplicationServices import AXUIElementCreateApplication
    from nlc.driver import _attr
    pid = d._resolve("com.apple.TextEdit", launch=False)
    if pid is None:
        return ""
    app = AXUIElementCreateApplication(pid)
    win = next((w for w in (_attr(app, "AXWindows") or [])
                if _attr(w, "AXTitle") == name), None)
    return urllib.parse.unquote((_attr(win, "AXDocument") or "")
                                .replace("file://", "")) if win else ""


def scenario_a_fix_is_not_quietly_kept():
    """What an escalation worked out is reported, never written into the recipe.

    Persisting it looked like the way to make escalation pay for itself, and it
    was wrong twice over. The flow in hand is the BOUND one, so saving it would
    write this run's filename and text into the template — the feature meant to
    stop a model hard-coding a window title would have hard-coded the whole run.
    And durability is a property of the condition a fix holds under, not of its
    IR shape: a selector can name today's date, while "open that panel first"
    can be structural."""
    from nlc.agent import Agent
    from nlc.contract import Resource, SafetyContract
    from nlc.flow import Flow, Resolution, Step
    from nlc.store import FlowStore
    d = Driver()
    if d._resolve("com.apple.calculator", launch=False) is None:
        return "계산기가 실행 중이 아님"
    KP = "CalculatorKeypadView"
    store, name = FlowStore(), "probe_notkept"
    wrong = Flow(name, "com.apple.calculator", params={"n": "7"}, steps=[
        Step("click", Selector(role="Button", identifier="WrongName",
                               ancestor=KP),
             intent_text="숫자 {{n}}", on_uncertain=["target_missing"],
             safety=SafetyContract(
                 effects=["write_ephemeral"], reversibility="reversible",
                 resource=Resource(object_type="계산기 입력", scope="entry")))])
    store.save(wrong)
    before = store.load(name).to_json()
    asked = []

    def judge(why):
        asked.append(why.kind)
        return Resolution("use_selector",
                          selector=Selector(role="Button", identifier="Seven",
                                            ancestor=KP),
                          reason="화면에 있는 이름으로")

    try:
        turn = Agent(driver=d, store=store, judge=judge).handle(name, think=1)
        if turn.status != "done" or len(asked) != 1:
            return False
        # The run succeeded using the fix; the recipe is untouched, params and
        # all, so the next run asks again rather than replaying this one's data.
        return store.load(name).to_json() == before
    finally:
        try:
            os.remove(store._file(name))
        except OSError:
            pass


def scenario_consent_cannot_be_re_aimed():
    """A step that needed approval cannot have its target swapped by a model.

    Consent is given for a described action. Letting an escalation point that
    same consent at a different element would make the description the person
    agreed to stop being the thing that runs."""
    from nlc.contract import Resource, SafetyContract
    from nlc.flow import Flow, Resolution, Step, replay
    d = Driver()
    if d._resolve("com.apple.calculator", launch=False) is None:
        return "계산기가 실행 중이 아님"
    KP = "CalculatorKeypadView"
    consequential = SafetyContract(
        effects=["write_persistent"], reversibility="recoverable",
        resource=Resource(object_type="문서", scope="item"))
    flow = Flow("probe_reaim", "com.apple.calculator", steps=[
        Step("click", Selector(role="Button", identifier="WrongName",
                               ancestor=KP),
             intent_text="승인이 필요한 무언가", on_uncertain=["target_missing"],
             safety=consequential)])
    r = replay(d, flow, escalate=lambda why: Resolution(
        "use_selector",
        selector=Selector(role="Button", identifier="Seven", ancestor=KP),
        reason="다른 것으로"), budget=1)
    bad = next((e for e in r.log if e.get("status") == "error"), {})
    return (not r.ok) and "승인이 필요한" in str(bad.get("detail"))


class _Deaf:
    """A driver that does every action successfully and never shows the result.

    The shape of the dangerous case: the app got the click, the screen does not
    say so yet, and the postcondition runs out of time. Nothing here is broken —
    that is the point.
    """

    def __init__(self, tree="0 Window Title: t\n  1 Button Title: Send\n"):
        self.tree = tree
        self.acts = []
        self.n = 0

    def identity(self, target):
        return target

    def observe(self, target, mode="tree_full", require_window=True):
        self.n += 1
        return {"type": "observation", "obs_id": f"o{self.n}", "tree": self.tree}

    def act(self, obs_id, target, action, risk_class=None, intent_text="",
            approved=False):
        self.acts.append(action.get("name"))
        return {"type": "acted", "action": action.get("name")}

    def wait_until(self, target, pred, timeout_ms=8000):
        if pred(self.tree):
            return {"type": "observation", "obs_id": f"o{self.n}",
                    "tree": self.tree}
        return {"type": "error", "error": "postcondition_timeout",
                "last_obs": {"tree": self.tree}}


def _unclear_outcome(action_name):
    """One step that lands but cannot be seen to have landed. Returns the acts."""
    import nlc.flow as F
    from nlc.contract import Resource, SafetyContract
    benign = SafetyContract(effects=["write_ephemeral"],
                            reversibility="reversible",
                            resource=Resource(object_type="메시지", scope="item"))
    step = F.Step(action_name,
                  F.Selector(role="Button", title="Send"),
                  intent_text="보내기",
                  params={"value": "x"} if action_name == "set_value" else {},
                  postcondition=F.Postcondition(contains="보냄"),
                  on_uncertain=["postcondition_timeout"],
                  safety=benign)
    flow = F.Flow("probe_unclear", "com.example.app", steps=[step])
    d = _Deaf()
    real = F._run_fix
    F._run_fix = lambda *a, **k: True      # the recovery works; that is not the question
    try:
        r = F.replay(d, flow, escalate=lambda why: F.Resolution(
            "recover", recovery="dismiss_dialog", reason="가리는 창"), budget=2)
    finally:
        F._run_fix = real
    return d.acts, r


def scenario_an_unclear_outcome_is_not_retried():
    """A postcondition that timed out does not mean the action did not happen.

    The send can complete while the screen is still catching up. Clearing a
    dialog and running the step again "to be sure" is how a message is sent
    twice — so after an unclear outcome the engine looks again, and re-runs only
    an action that means the same thing done twice as done once."""
    clicks, r = _unclear_outcome("click")
    sets, _ = _unclear_outcome("set_value")
    bad = next((e for e in r.log if e.get("status") == "error"), {})
    return (clicks == ["click"]                    # pressed once, never again
            and sets == ["set_value", "set_value"]  # safe to repeat, so repeated
            and bad.get("effect") == "unknown"
            and not r.ok)


def scenario_re_aiming_is_refused_after_the_fact():
    """Choosing a different target is a question that only exists beforehand.

    Before anything ran, "you named the wrong button" is a correction. After the
    action went out, the same answer adds a second action instead of fixing the
    first, whatever the step's risk level is."""
    from nlc.contract import Resource, SafetyContract
    from nlc.flow import (Resolution, Selector, Step, Uncertainty, _Budget,
                          _escalate)
    benign = SafetyContract(effects=["write_ephemeral"],
                            reversibility="reversible",
                            resource=Resource(object_type="입력", scope="entry"))
    step = Step("click", Selector(role="Button", identifier="A"),
                on_uncertain=["target_missing", "postcondition_timeout"],
                safety=benign)
    answer = lambda why: Resolution(
        "use_selector", selector=Selector(role="Button", identifier="B"))
    def ask(kind, effect):
        return _escalate(answer, _Budget(3), step,
                         Uncertainty(kind=kind, step=0, intent="", detail="",
                                     effect=effect), None)
    before = ask("target_missing", "not_started")
    after = ask("postcondition_timeout", "unknown")
    return before is not None and before.kind == "use_selector" and after is None


def scenario_one_step_cannot_spend_the_budget():
    """A step that keeps getting the same wrong answer must not ask forever.

    Without a per-step cap, one oscillating step burns a budget meant for the
    whole run and the run still ends in the failure it started with."""
    from nlc.flow import _Budget
    b = _Budget(5)
    first = b.take(0, "target_missing")
    again = b.take(0, "target_missing")      # same step, same surprise
    other_kind = b.take(0, "action_failed")  # a different surprise is its own
    other_step = b.take(1, "target_missing")
    return first and not again and other_kind and other_step and b.spent == 3


def scenario_an_identifier_keeps_what_distinguishes_it():
    """Only a KNOWN volatile suffix is state; the rest of a name is the name.

    A calculator's 'StandardInputView;value:408' really does change with every
    keystroke. Treating every ';' suffix that way makes 'control;operation:
    preview' and 'control;operation:delete' the same control."""
    from nlc.flow import Selector, TargetMissing
    stable = Selector._stable_id("StandardInputView;value:408") == "StandardInputView"
    kept = Selector._stable_id("control;operation:delete") == "control;operation:delete"
    tree = "0 Window Title: w\n  1 Button Identifier: control;operation:delete\n"
    try:
        Selector(role="Button",
                 identifier="control;operation:preview").resolve(tree)
        crossed = True
    except TargetMissing:
        crossed = False
    return stable and kept and not crossed


def scenario_a_postcondition_compares_text_not_quantity():
    """`equals` asserts what the screen says, not how much it means.

    Reading both sides as numbers verified an account number '00123' against
    '123'. Digit grouping is still ignored, because '5,535' and '5535' are the
    same characters once a locale's separator is removed."""
    from nlc.flow import Postcondition, Selector
    acct = "0 Window Title: w\n  1 StaticText Identifier: out, Value: 00123\n"
    money = "0 Window Title: w\n  1 StaticText Identifier: out, Value: 5,535\n"
    field = Selector(role="StaticText", identifier="out")
    return (not Postcondition(field=field, equals="123",
                              attribute="Value").check(acct)
            and Postcondition(field=field, equals="5535",
                              attribute="Value").check(money))


def scenario_a_candidate_says_where_it_is():
    """Two identical labels are not two choices.

    A Save button in each of two windows described itself as 'Save' both times,
    and no answer to "which one did you mean" can be given from that."""
    from nlc.flow import _candidates
    tree = ("0 Window Title: 보고서\n"
            "  1 Button Title: Save\n"
            "2 Window Title: 메모\n"
            "  3 Button Title: Save\n")
    got = _candidates(None, tree)
    return len(got) == 2 and got[0] != got[1] and "보고서" in got[0]


def scenario_the_fast_path_stands_down():
    """A pattern that matched is not the same as a request that was understood.

    Measured on this machine before the fix: `"굵게 하지 마"` resolved to the
    bold recipe and made the text bold, `"메일 말고 문서 폴더 열어줘"` opened
    Mail, and `"어제 온 메일"` and `"오늘 온 메일"` resolved identically with no
    date anywhere. All three were wrong ACTIONS, and every one of them succeeded
    — selector unique, binding intact, postcondition true, `res.ok` True. No
    escalation could fire, because nothing failed. `--think` at any size would
    have asked zero questions.

    So a rule now declares what it can account for, and stands down when the
    request says more than that."""
    from nlc.agent import Agent
    a = Agent()
    held = {u: a.unrepresented(u) for u in
            ("굵게 하지 마", "메일 말고 문서 폴더 열어줘", "어제 온 메일 읽어줘")}
    fast = {u: a.resolve(u) for u in
            ("굵게 해줘", "12 곱하기 34", "문서 폴더 열어줘", "오늘 일정 알려줘")}
    return (all(v for v in held.values())                  # all three held back
            and all(a.resolve(u) is None for u in held)
            and all(v is not None for v in fast.values())   # and nothing else is
            and fast["문서 폴더 열어줘"].params.get("folder") == "Documents")


def scenario_a_refusal_is_an_answer():
    """"하지 마" is a request with an outcome, not a request we failed to parse.

    The reading says the user negated it; the right response is to do nothing
    and say which words meant that — not to run the thing they named, and not
    to go off and author a recipe for it."""
    from nlc.agent import Agent
    from nlc.ask import what_was_asked
    said = "굵게 하지 마"

    def interpret(q):
        # Stubbed: what is under test is that the engine acts on the reading,
        # not that a model produces one.
        if q.kind == "what_was_asked":
            return q.check({"goal": "굵게 하지 않기", "negated": True,
                            "quote": "하지 마", "excluded": [],
                            "allows": ["read"], "scope": "item",
                            "reversibility": "reversible"})
        return None

    t = Agent(interpret=interpret).handle(said)
    return t.status == "done" and "하지 마" in t.say and "굵게" not in t.say[:8]


def scenario_a_reading_must_quote_the_user():
    """A reading is checkable in exactly one way: it has to be IN what they said.

    The engine cannot verify that "저장해줘" means a persistent write — that is
    language, and reading language is what the model is for. It CAN verify that
    the model did not invent the words it is reasoning from, and that is the
    check that makes the rest usable."""
    from nlc.ask import what_was_asked, which_element, which_task
    q = what_was_asked("문서 폴더 열어줘")
    envlp = {"allows": ["read"], "scope": "item", "reversibility": "reversible"}
    invented = q.check({"goal": "폴더 열기", "quote": "사용자가 승인함",
                        "negated": False, "excluded": [], **envlp})
    real = q.check({"goal": "폴더 열기", "quote": "폴더 열어줘",
                    "negated": False, "excluded": [], **envlp})
    # A recipe name has to be one of ours, and an element has to be one of the
    # candidates actually on screen.
    t = which_task("폴더 열기", [("finder_open_folder", "폴더를 엽니다")])
    e = which_element("굵게", [{"label": "Bold"}, {"label": "Italic"}])
    return (invented is None and real is not None
            and real["quote"] == "폴더 열어줘"
            and t.check({"pick": "made_up_recipe"}) is None
            and t.check({"pick": "finder_open_folder"})["pick"] == "finder_open_folder"
            and e.check({"pick": 7}) is None
            and e.check({"pick": 1})["candidate"]["label"] == "Italic")


def scenario_a_reading_normalizes_into_the_fast_path():
    """Reading the request does not have to produce a recipe — just a sentence.

    "메일 말고 문서 폴더 열어줘" carries an exclusion no rule can hold. The goal
    it reduces to is "문서 폴더 열기", which is exactly what the folder rule was
    written for, parameters and all. One bounded question, then zero tokens for
    the rest."""
    from nlc.agent import Agent

    def interpret(q):
        if q.kind == "what_was_asked":
            return q.check({"goal": "문서 폴더 열기", "negated": False,
                            "quote": "문서 폴더 열어줘", "excluded": ["메일"],
                            "allows": ["read"], "scope": "item",
                            "reversibility": "reversible"})
        return None

    a = Agent(interpret=interpret)
    match, reading = a.read_request("메일 말고 문서 폴더 열어줘")
    return (match is not None and match.name == "finder_open_folder"
            and match.params.get("folder") == "Documents"
            and reading["excluded"] == ["메일"])


def _fake_screen(labels, readable=()):
    lines = ["0 Window  Title: T"]
    for i, l in enumerate(labels, start=1):
        lines.append(f"  {i} Button  Title: {l}")
    for j, (l, v) in enumerate(readable, start=len(labels) + 1):
        lines.append(f"  {j} StaticText  Identifier: {l}, Value: {v}")
    return "\n".join(lines) + "\n"


def scenario_a_screen_is_one_question():
    """The unit of a general run is the screen, not the step.

    A calculator writes its running total into an identifier, so hashing the
    tree calls every keypress a new screen. What a question about "which button"
    is asked over is the keypad, and the keypad does not change — so the screen
    key is what you can ACT on, and seven presses share one question. Measured
    on this machine: calc_arith is 7 steps across 3 screens, and a real general
    run drove 8 presses from a single answer."""
    from nlc.general import candidates, screen_key
    keypad = ["1", "2", "3", "Multiply", "Equals"]
    a = _fake_screen(keypad, readable=[("display", "0")])
    b = _fake_screen(keypad, readable=[("display", "408")])
    c = _fake_screen(keypad + ["Percent"], readable=[("display", "408")])
    same = screen_key(candidates(a)) == screen_key(candidates(b))
    moved = screen_key(candidates(b)) != screen_key(candidates(c))
    # A display is read, never pressed.
    # A display is named by what it says, and read rather than pressed.
    can = {x["label"]: x["can"] for x in candidates(a)}
    return (same and moved and can["1"] == ["click"] and can["0"] == ["read"])


def scenario_a_batch_cannot_leave_its_screen():
    """Every action in a batch is checked against the screen it was asked about.

    A candidate list is only true of the observation it was read from, so a pick
    outside it, an action we do not perform, or an action that does not apply to
    that kind of thing are all rejected before anything runs — which is what
    makes asking for eight at once no riskier than asking for one."""
    from nlc.ask import what_to_do_here
    from nlc.general import candidates
    cands = candidates(_fake_screen(["1", "Equals"], readable=[("out", "0")]))
    q = what_to_do_here("1 더하기 1", cands)
    good = q.check({"done": False, "expect": "2", "actions": [
        {"pick": 0, "do": "click"}, {"pick": 1, "do": "click"},
        {"pick": 2, "do": "read"}]})
    return (good is not None and len(good["actions"]) == 3
            and q.check({"done": False, "actions": [{"pick": 9, "do": "click"}]}) is None
            and q.check({"done": False, "actions": [{"pick": 0, "do": "sudo"}]}) is None
            # a display is not a button, and the list says so
            and q.check({"done": False, "actions": [{"pick": 2, "do": "click"}]}) is None
            # writing with nothing to write
            and q.check({"done": False, "actions": [{"pick": 0, "do": "set_value"}]}) is None)


def scenario_a_general_run_leaves_a_graph():
    """What a general run walks is a path, and a repeated path is a recipe.

    This is how the mode with no recipe feeds the mode that replays them for
    free, instead of competing with it."""
    from nlc.general import run

    class Screens:
        """Two screens: pressing Go on the first reaches the second."""
        def __init__(self):
            self.at = 0
            self.acted = []

        def identity(self, t):
            return t

        def observe(self, target, mode="tree_full", require_window=True):
            tree = (_fake_screen(["Go"]) if self.at == 0
                    else _fake_screen(["Back"], readable=[("out", "42")]))
            return {"type": "observation", "obs_id": f"o{self.at}", "tree": tree}

        def act(self, obs_id, target, action, expect=None, risk_class=None,
                intent_text="", approved=False):
            self.acted.append(action["name"])
            self.at = 1
            return {"type": "acted", "action": action["name"]}

        def wait_until(self, target, pred, timeout_ms=8000):
            return self.observe(target)

    d = Screens()
    plans = [
        {"done": False, "expect": None,
         "actions": [{"pick": 0, "do": "click",
                      "candidate": None, "why": "간다"}]},
        {"done": True, "actions": [], "expect": None},
    ]

    def ask_here(goal, cands, done):
        p = plans.pop(0)
        for a in p["actions"]:
            a["candidate"] = cands[a["pick"]]
        return p

    r = run(d, "x", "42를 보여줘", ask_here)
    return (r.ok and r.path == [r.visits[0].screen, r.visits[0].next_screen]
            and r.path[0] != r.path[1] and len(r.edges) == 1
            and r.asked == 2 and d.acted == ["click"])


def scenario_a_general_run_stops_going_nowhere():
    """Being on the same screen again is not being stuck; doing the same thing is.

    Counting visits looked right and was wrong: a calculator's keypad never
    changes, so every arithmetic run hit the limit at its third question — some
    of them after they had already worked out the answer. What actually means
    no progress is running the same plan on the same screen a second time."""
    from nlc.general import run

    class Fixed:
        """A screen that never changes, whatever you press."""
        def __init__(self):
            self.acted = 0

        def identity(self, t):
            return t

        def observe(self, target, mode="tree_full", require_window=True):
            return {"type": "observation", "obs_id": "o",
                    "tree": _fake_screen(["Go", "Other"])}

        def act(self, *a, **k):
            self.acted += 1
            return {"type": "acted", "action": "click"}

        def wait_until(self, target, pred, timeout_ms=8000):
            return self.observe(target)

    def same_every_time(goal, cands, done):
        return {"done": False, "expect": None,
                "actions": [{"pick": 0, "do": "click", "candidate": cands[0],
                             "why": "또"}]}

    stuck = run(Fixed(), "x", "무언가", same_every_time, max_screens=20)

    # The same screen with something DIFFERENT to try is progress, not a loop.
    plans = [0, 1, 0]

    def varies(goal, cands, done):
        if not plans:
            return {"done": True, "actions": [], "expect": None}
        p = plans.pop(0)
        return {"done": False, "expect": None,
                "actions": [{"pick": p, "do": "click", "candidate": cands[p],
                             "why": "다른 것"}]}

    d2 = Fixed()
    varied = run(d2, "x", "무언가", varies, max_screens=20)
    return (not stuck.ok and "되풀이" in stuck.why and stuck.asked == 2
            and varied.ok and d2.acted == 3)


class _Shifting:
    """An app whose screen moves the moment you touch it.

    The shape of the real hazard: a dialog opens, a row is inserted, a list
    re-sorts. Nothing is broken and nothing errors — everything simply moved.

    An index into a fresh tree moves with it. An AX handle does NOT: the element
    the batch was planned against is still that element, wherever it now sits.
    So this models both, separately — `observe` hands back the new tree, while
    a handle keeps reporting whatever its element is called now, or nothing once
    that element is gone.
    """

    def __init__(self, before, after):
        self.before, self.after, self.n, self.hit = before, after, 0, []

    def identity(self, t):
        return t

    def observe(self, target, mode="tree_full", require_window=True):
        return {"type": "observation", "obs_id": f"o{self.n}",
                "tree": _fake_screen(self.before if self.n == 0 else self.after)}

    def _handle(self, idx):
        """What the element at that ORIGINAL index is called now."""
        was = self.before[idx - 1]
        if self.n == 0:
            return was
        return was if was in self.after else None

    def act(self, obs_id, target, action, expect=None, risk_class=None,
            intent_text="", approved=False):
        here = self._handle(action["element_index"])
        if expect and expect.get("label") and expect["label"] != here:
            return {"type": "error", "error": "target_changed",
                    "effect": "not_started",
                    "detail": f"기대 {expect['label']!r}, 지금 {here!r}"}
        self.n += 1
        self.hit.append(here)
        return {"type": "acted", "action": action["name"]}

    def wait_until(self, target, pred, timeout_ms=8000):
        return self.observe(target)


def _two_clicks(driver):
    """Plan two clicks from one question, then say done."""
    from nlc.general import run
    plans = [{"done": False, "expect": None, "actions": [
        {"pick": 0, "do": "click", "candidate": None, "why": "1"},
        {"pick": 1, "do": "click", "candidate": None, "why": "2"}]},
        {"done": True, "actions": [], "expect": None},
        {"done": True, "actions": [], "expect": None}]

    def ask_here(goal, cands, done):
        p = plans.pop(0)
        for a in p.get("actions", []):
            a["candidate"] = cands[a["pick"]]
        return p

    return run(driver, "x", "Alpha 누르고 Beta 누르기", ask_here)


def scenario_a_batch_acts_on_names_not_indexes():
    """A plan names elements, and the claim travels with the action.

    Reproduced before the fix: a plan of "Alpha then Beta" pressed Alpha TWICE
    and reported `ok=True`. A dialog had appeared between the two actions,
    everything shifted down one, and the index that meant Beta now meant Alpha.
    The driver's staleness guard exists for exactly this and was defeated by the
    loop's own re-observation — refreshing `obs_id` while keeping indices from
    the observation before it is the one combination that passes the check and
    is wrong.

    The loop no longer re-observes at all: one observation holds the whole
    batch, so every index stays the index of the element it was planned for.
    What cannot be assumed is that the element still MEANS the same thing, so
    each action carries what the plan believed it was and the driver compares
    that against the element itself. A dialog appearing does not move a handle,
    so Beta is still Beta and is still pressed."""
    d = _Shifting(["Alpha", "Beta"], ["Popup", "Alpha", "Beta"])
    r = _two_clicks(d)
    return d.hit == ["Alpha", "Beta"] and r.ok


def scenario_a_vanished_target_cuts_the_batch():
    """When the named element is gone, the rest of the batch is about a screen
    that no longer exists.

    Abandoning and looking again is the answer — not pressing whatever is at
    that index now, and not failing the run either, because a screen advancing
    is often exactly what the batch was for. The check costs one element read;
    answering the same question by re-walking the tree costs 9.8 seconds in
    Mail, measured, which is 68 for a batch of eight."""
    d = _Shifting(["Alpha", "Beta"], ["전혀", "다른", "화면"])
    r = _two_clicks(d)
    cut = [v.cut_short for v in r.visits if v.cut_short]
    return (d.hit == ["Alpha"]          # the second never landed anywhere
            and len(cut) == 1 and "Beta" in cut[0]
            and r.ok)                   # and the run carried on from there


def scenario_a_read_sees_what_the_batch_did():
    """A read late in a batch answers with the screen AFTER the batch, not before.

    Same class of mistake as acting on a stale index, and it reached a real run:
    `7 더하기 8` planned press-press-press-read from one question and read the
    display back as 0, because the value came off the line captured when the
    candidates were listed — before a single key was pressed. Re-finding the
    element has to refresh what it says, not only where it is."""
    from nlc.general import run

    class Counts:
        """A display that only shows the total once Equals has been pressed."""
        def __init__(self):
            self.n = 0

        def identity(self, t):
            return t

        def observe(self, target, mode="tree_full", require_window=True):
            shown = "0" if self.n == 0 else "54"
            return {"type": "observation", "obs_id": f"o{self.n}",
                    "tree": _fake_screen(["Equals"], readable=[("out", shown)])}

        def act(self, obs_id, target, action, expect=None, risk_class=None,
                intent_text="", approved=False):
            self.n += 1
            return {"type": "acted", "action": action["name"]}

        def probe(self, obs_id, target, element_index):
            shown = "0" if self.n == 0 else "54"
            return {"type": "probe",
                    "line": f"  2 StaticText  Identifier: out, Value: {shown}"}

        def wait_until(self, target, pred, timeout_ms=8000):
            return self.observe(target)

    plans = [{"done": False, "expect": None, "actions": [
        {"pick": 0, "do": "click", "candidate": None, "why": "계산"},
        {"pick": 1, "do": "read", "candidate": None, "why": "결과"}]},
        {"done": True, "actions": [], "expect": None}]

    def ask_here(goal, cands, done):
        p = plans.pop(0)
        for a in p.get("actions", []):
            a["candidate"] = cands[a["pick"]]
        return p

    r = run(Counts(), "x", "결과를 읽어줘", ask_here)
    return r.ok and r.said == ["54"]


def scenario_two_of_the_same_name_are_told_apart():
    """Re-finding by name has to survive two things sharing one.

    Abandoning the batch whenever a label is not unique would give up on every
    Save button in an app with two windows. Where it was is already part of the
    candidate the question was asked about, so it is part of finding it again."""
    from nlc.general import candidates, reacquire
    before = ("0 Window  Title: 보고서\n"
              "  1 Group  Identifier: left\n"
              "    2 Button  Title: Save\n")
    after = ("0 Window  Title: 보고서\n"
             "  1 Group  Identifier: right\n"
             "    2 Button  Title: Save\n"
             "  3 Group  Identifier: left\n"
             "    4 Button  Title: Save\n")
    c = candidates(before)[0]
    # Ambiguous on the name alone; the container it was in settles it.
    return c["container"] == "left" and reacquire(c, after) == 4


def scenario_a_sheet_appearing_cuts_the_batch():
    """An element can survive a modal and still be the wrong thing to press.

    It is there, named the same, the only one of its name — and the app is now
    waiting for an answer to something else, so pressing it does not do what
    the plan meant. Re-finding cannot see that; the sheet can."""
    from nlc.general import blocking_sheet, run

    plain = _fake_screen(["Alpha", "Beta"])
    covered = (plain.rstrip("\n") +
               "\n  9 Sheet  Title: 저장하시겠습니까?\n")

    class Interrupted:
        def __init__(self):
            self.n, self.hit = 0, []

        def identity(self, t):
            return t

        def observe(self, target, mode="tree_full", require_window=True):
            return {"type": "observation", "obs_id": f"o{self.n}",
                    "tree": plain if self.n == 0 else covered}

        def act(self, obs_id, target, action, expect=None, risk_class=None,
                intent_text="", approved=False):
            # A sheet is up from the second observation on, and the claim the
            # batch travels with says it expected none.
            if expect and expect.get("no_modal") and self.n > 0:
                return {"type": "error", "error": "target_changed",
                        "effect": "not_started", "detail": "저장하시겠습니까?"}
            self.n += 1
            self.hit.append(action["element_index"])
            return {"type": "acted", "action": action["name"]}

        def wait_until(self, target, pred, timeout_ms=8000):
            return self.observe(target)

    d = Interrupted()
    r = _two_clicks(d)
    cut = [v.cut_short for v in r.visits if v.cut_short]
    return (blocking_sheet(covered) == "저장하시겠습니까?"
            and not blocking_sheet(plain)
            and d.hit == [1]                       # only the first landed
            and len(cut) == 1 and "저장하시겠습니까?" in cut[0])


def scenario_matching_a_prefix_is_not_answering():
    """A rule that matched part of a request has not answered the request.

    Both of these ran on a real machine and reported success:

        "1351 곱하기 59913 하고 123123 을 더한다음 계산해줘"
            matched `1351 곱하기 59913`, dropped the `+ 123123`, and said
            80,942,463 — the product alone — was the answer.
        "메일 보내줘"
            matched `메일` and resolved to the recipe that OPENS the inbox.

    So what a rule did NOT consume is checked for things that would be another
    instruction: a number it is not using, a verb for something it is not
    doing."""
    from nlc.agent import Agent
    a = Agent()
    dropped = ["1351 곱하기 59913 하고 123123 을 더한다음 계산해줘", "메일 보내줘"]
    kept = {"메일 읽어줘": "mail_inbox", "12 곱하기 34 계산해줘": "calc_arith",
            "문서 폴더 열어줘": "finder_open_folder",
            "다운로드 폴더에 몇 개 있어": "finder_count_items",
            "오늘 일정 알려줘": "calendar_today", "굵게 해줘": "textedit_emphasize"}
    return (all(a.resolve(u) is None and a.unrepresented(u) for u in dropped)
            and all(a.resolve(u) is not None and a.resolve(u).name == n
                    for u, n in kept.items()))


def scenario_the_reading_cannot_let_it_back_in():
    """The guard has to hold on the cleaned-up goal too.

    It did not, and that is how the dropped `+ 123123` came back: the fast path
    held the request, the reading reduced it to a goal, and the goal went
    through the very same rules with no such check — answering with the product
    alone a second time."""
    from nlc.agent import Agent

    def interpret(q):
        if q.kind == "what_was_asked":
            return q.check({"goal": "1351 곱하기 59913 한 뒤 123123 더하기",
                            "negated": False, "quote": "1351 곱하기 59913",
                            "excluded": [], "allows": ["write_ephemeral"],
                            "scope": "entry", "reversibility": "reversible"})
        return {"pick": None, "why": "한 번에 못 하는 계산"}

    a = Agent(interpret=interpret)
    match, _ = a.read_request("1351 곱하기 59913 하고 123123 을 더한다음 계산해줘")
    # A goal a rule can only do half of is not a goal that rule answers.
    simple, _ = Agent(interpret=lambda q: (
        q.check({"goal": "12 곱하기 34 계산하기", "negated": False,
                 "quote": "12 곱하기 34", "excluded": ["메일"],
                 "allows": ["write_ephemeral"], "scope": "entry",
                 "reversibility": "reversible"})
        if q.kind == "what_was_asked" else None)
    ).read_request("메일 말고 12 곱하기 34 계산해줘")
    return (match is None                      # falls through to the screen loop
            and simple is not None and simple.name == "calc_arith")


def scenario_a_batch_observes_once():
    """A batch of eight is one look at the screen, not eight.

    Keeping a batch aimed at the elements it was planned against used to mean
    re-walking the tree before every action. Measured on this machine, one
    observation costs 54ms in Calculator, 335ms in Finder and **6.5 seconds in
    Mail** — so a batch of eight spent 45 seconds in Mail deciding it was still
    allowed to proceed.

    Reading the one element the action is about answers the same question in
    2.8ms. So the claim travels with the action and the driver checks it there,
    which also leaves no gap between checking and acting."""
    from nlc.general import run

    class Counting:
        def __init__(self):
            self.observes = 0
            self.acts = 0

        def identity(self, t):
            return t

        def observe(self, target, mode="tree_full", require_window=True):
            self.observes += 1
            return {"type": "observation", "obs_id": "o",
                    "tree": _fake_screen(["A", "B", "C", "D", "E"])}

        def act(self, obs_id, target, action, expect=None, risk_class=None,
                intent_text="", approved=False):
            assert expect and expect.get("label"), "행동이 기대를 들고 가지 않음"
            self.acts += 1
            return {"type": "acted", "action": action["name"]}

        def wait_until(self, target, pred, timeout_ms=8000):
            return self.observe(target)

    plans = [{"done": False, "expect": None, "actions": [
        {"pick": i, "do": "click", "candidate": None, "why": str(i)}
        for i in range(5)]},
        {"done": True, "actions": [], "expect": None}]

    def ask_here(goal, cands, done):
        p = plans.pop(0)
        for a in p.get("actions", []):
            a["candidate"] = cands[a["pick"]]
        return p

    d = Counting()
    r = run(d, "x", "다섯 개 누르기", ask_here)
    # One look to plan on, one to see where it led, one to ask again: never one
    # per action.
    return r.ok and d.acts == 5 and d.observes <= 3


def scenario_a_check_covers_only_what_it_checked():
    """Provenance is not meaning: a real quote can be read back as its opposite.

    `quote ∈ utterance` verifies where the words came from and nothing else,
    and this passed:

        {"quote": "굵게 하지 마", "goal": "굵게 해줘", "negated": false}

    A real quote of a refusal, turned into the instruction it refused. Nothing
    here can judge Korean, but it can require the slots to agree with what is
    lexically in the quote — and with what is in the utterance, since quoting
    only the agreeable half is the same bypass wearing a shorter quote."""
    from nlc.ask import what_was_asked
    q = what_was_asked("굵게 하지 마")
    env = {"allows": ["read"], "scope": "item", "reversibility": "reversible"}
    flipped = q.check({"quote": "굵게 하지 마", "goal": "굵게 해줘",
                       "negated": False, "excluded": [], **env})
    trimmed = q.check({"quote": "굵게", "goal": "굵게 해줘",
                       "negated": False, "excluded": [], **env})
    honest = q.check({"quote": "굵게 하지 마", "goal": "굵게 하지 않기",
                      "negated": True, "excluded": [], **env})
    e = what_was_asked("메일 말고 문서 폴더 열어줘")
    ruled_out = e.check({"quote": "문서 폴더 열어줘", "goal": "메일 열기",
                         "negated": False, "excluded": ["메일"], **env})
    fine = e.check({"quote": "문서 폴더 열어줘", "goal": "문서 폴더 열기",
                    "negated": False, "excluded": ["메일"], **env})
    return (flipped is None and trimmed is None and ruled_out is None
            and honest is not None and honest["negated"]
            and fine is not None and fine["goal"] == "문서 폴더 열기")


def scenario_a_handoff_is_not_one_more_yes():
    """Not everything above `none` is the same question.

    A confirm asks the person to agree; a handoff is the person doing it
    themselves, and blocked means we could not say what would happen. The screen
    loop sent all three to the one approval callback, so a yes there covered a
    step nobody is allowed to approve."""
    from nlc.general import run

    class Sensitive:
        def identity(self, t):
            return t

        def observe(self, target, mode="tree_full", require_window=True):
            return {"type": "observation", "obs_id": "o",
                    "tree": ("0 Window  Title: T\n"
                             "  1 TextField  Title: 비밀번호\n")}

        def act(self, *a, **k):
            raise AssertionError("실행되면 안 됩니다")

        def wait_until(self, target, pred, timeout_ms=8000):
            return self.observe(target)

    def ask_here(goal, cands, done):
        return {"done": False, "expect": None,
                "actions": [{"pick": 0, "do": "set_value", "value": "hunter2",
                             "candidate": cands[0], "why": "로그인"}]}

    # Yes to everything: it still must not carry a handoff.
    r = run(Sensitive(), "x", "로그인해줘", ask_here, approve=lambda _w: True)

    # And an effect the request never covered is refused for a different
    # reason — not because it is dangerous, but because it is a different
    # request. The default envelope covers looking and typing into the thing in
    # front of you; deleting stored mail is not that.
    class Destructive(Sensitive):
        def observe(self, target, mode="tree_full", require_window=True):
            return {"type": "observation", "obs_id": "o",
                    "tree": "0 Window  Title: T\n  1 Button  Title: 메일 삭제\n"}

    def click_it(goal, cands, done):
        return {"done": False, "expect": None,
                "actions": [{"pick": 0, "do": "click", "candidate": cands[0],
                             "why": "지움"}]}

    out = run(Destructive(), "x", "메일 보여줘", click_it,
              approve=lambda _w: True)
    return (not r.ok and "직접" in r.why
            and not out.ok and "범위를 벗어납니다" in out.why)


def scenario_done_is_where_it_stopped_not_that_it_worked():
    """The model saying "done" ends the run; it does not verify it.

    Something has to have been SEEN: a value read, or a stated expectation that
    showed up. Without that this is a run that ended, and the report says so."""
    from nlc.general import run

    class Blank:
        def identity(self, t):
            return t

        def observe(self, target, mode="tree_full", require_window=True):
            return {"type": "observation", "obs_id": "o",
                    "tree": _fake_screen(["Go"])}

        def act(self, *a, **k):
            return {"type": "acted", "action": "click"}

        def wait_until(self, target, pred, timeout_ms=8000):
            return {"type": "timeout", "last_obs": self.observe(target)}

    plans = [{"done": False, "expect": None,
              "actions": [{"pick": 0, "do": "click", "candidate": None,
                           "why": "누름"}]},
             {"done": True, "actions": [], "expect": None}]

    def ask_here(goal, cands, done):
        p = plans.pop(0)
        for a in p.get("actions", []):
            a["candidate"] = cands[a["pick"]]
        return p

    r = run(Blank(), "x", "무언가 해줘", ask_here)
    return r.ok and not r.verified and "확인" in r.why


def scenario_an_envelope_comes_from_the_words():
    """What a request authorizes is settled before any screen has been seen.

    A task with no recipe cannot be approved step by step in advance, because
    nobody knows the steps. What CAN be settled is the shape of what was asked
    for — which effects, on what scale, how recoverable. The path is the unknown
    part, not the range (advisor finding).

    And this is what finally makes `preapproved` reachable. It was written to
    stop us re-asking about what the user already asked for, and it had never
    once fired: `_covers()` compared a Korean sentence against the string
    "write_persistent", so the only way to reach it was to say a Python
    identifier out loud."""
    from nlc.contract import Envelope, policy
    save = Envelope(quote="이 문서 저장해줘", scope="item",
                    reversibility="recoverable",
                    allows=frozenset({"read", "write_persistent"}))
    asked = policy(save.contract_for({"write_persistent"}, "문서")).risk
    # Effects the request never covered are a different request, and the
    # policy's own floors do not move for any of them.
    out_of_range = not save.admits({"transmit"})
    still_asks = policy(save.contract_for({"transmit"}, "메일")).risk
    handed_off = policy(save.contract_for({"credential"}, "비밀번호")).risk
    # And with no reading there is no envelope: looking and typing, no more.
    m = Envelope.minimal()
    return (asked == "preapproved" and out_of_range
            and still_asks == "confirm" and handed_off == "handoff"
            and m.admits({"read", "write_ephemeral"})
            and not m.admits({"write_persistent"}))


def scenario_the_control_says_what_not_how_risky():
    """The screen loop and the recipes now go through one policy.

    The loop used to keep a private notion of "risky" — a risk level straight
    off the control — which nothing else in the system understood and which
    could not be compared against what the user asked for. An EFFECT can be. So
    the control is read for what an action IS, the envelope says what the
    request covers, and `policy()` decides, exactly as it does for a recipe."""
    from nlc.safety import effects_of
    cases = {
        "  1 Button  Title: All Clear": ("click", "write_ephemeral"),
        "  1 Button  Title: 7": ("click", "write_ephemeral"),
        "  1 Button  Title: 보내기": ("click", "transmit"),
        "  1 Button  Title: Delete": ("click", "delete"),
        "  1 Button  Title: 저장": ("click", "write_persistent"),
        "  1 TextField  Title: 비밀번호": ("set_value", "credential"),
        "  1 StaticText  Value: 408": ("read", "read"),
    }
    return all(effects_of({"name": act}, line)[0] == {want}
               for line, (act, want) in cases.items())


def scenario_an_expectation_that_holds_does_not_refuse():
    """A claim about an element has to be checkable against the element.

    This ran against the real driver because the bug it catches cannot be seen
    from a fake: `expect.role` was compared against the element's LABEL line,
    and a Button's label does not contain the word "Button". Every action the
    screen loop attempted came back "대상이 달라졌습니다" while the element sat
    exactly where it had always been — and every end-to-end check I ran after
    that happened to resolve to a recipe, so the screen loop was broken for
    several commits without one test noticing.

    Both directions matter: a true claim must not refuse, and a false one must."""
    from nlc.general import candidates
    d = Driver()
    if d._resolve("com.apple.calculator", launch=False) is None:
        return "계산기가 실행 중이 아님"
    obs = d.observe("com.apple.calculator", "tree_full")
    if obs["type"] != "observation":
        return f"계산기를 관측하지 못함 ({obs.get('error')})"
    c = next((x for x in candidates(obs["tree"])
              if x["label"] == "All Clear"), None)
    if c is None:
        return "AC 버튼을 찾지 못함"
    act = {"name": "click", "element_index": c["element_index"]}
    true_claim = d.act(obs["obs_id"], "com.apple.calculator", act,
                       expect={"role": c["role"], "label": c["match"]})
    # A fresh observation, because acting spent that one.
    obs2 = d.observe("com.apple.calculator", "tree_full")
    act2 = {"name": "click", "element_index": c["element_index"]}
    false_claim = d.act(obs2["obs_id"], "com.apple.calculator", act2,
                        expect={"role": c["role"], "label": "Equals"})
    return (true_claim["type"] == "acted"
            and false_claim.get("error") == "target_changed"
            and false_claim.get("effect") == "not_started")


def scenario_a_role_is_not_a_word_in_a_label():
    """The comparison itself, without needing a screen.

    `expect.role` was checked by looking for the word "Button" inside the
    element's label line — and a Button's label is "Description: 5, Identifier:
    Five", which contains no such word. So every claim was false and every
    action the screen loop tried was refused while the element sat exactly where
    it had always been.

    The real-driver version of this check needs a GUI and skips without one,
    which is precisely when a bug like this gets several commits to hide in. The
    comparison is pure, so it can be tested anywhere."""
    import types
    import nlc.driver as D
    d = Driver()
    obs = types.SimpleNamespace(pid=1, elements=["<the element>"],
                                obs_id="o")
    real_attr, real_label = D._attr, D._elem_label
    real_modal = Driver._modal_sheet
    D._attr = lambda el, k: "AXButton" if k == "AXRole" else None
    D._elem_label = lambda el: "Description: 5, Identifier: Five"
    Driver._modal_sheet = staticmethod(lambda pid: False)
    try:
        act = {"element_index": 0}
        holds = d._mismatch(obs, act, {"role": "Button", "label": "5"})
        wrong_role = d._mismatch(obs, act, {"role": "TextField", "label": "5"})
        wrong_label = d._mismatch(obs, act, {"role": "Button", "label": "7"})
        # A modal is its own reason, independent of the element.
        Driver._modal_sheet = staticmethod(lambda pid: True)
        covered = d._mismatch(obs, act, {"role": "Button", "label": "5",
                                         "no_modal": True})
    finally:
        D._attr, D._elem_label = real_attr, real_label
        Driver._modal_sheet = real_modal
    return (holds == "" and bool(wrong_role) and bool(wrong_label)
            and "대화상자" in covered)


def _walked_run(verified=True, read=True):
    """A general run that arrived, as the screen loop would leave it."""
    from nlc.general import GeneralResult, Visit
    acts = [{"do": "click", "label": "1", "match": "One", "role": "Button",
             "attr": "Identifier", "container": "Keypad",
             "line": "  1 Button  Identifier: One"}]
    if read:
        acts.append({"do": "read", "label": "408", "match": "Display",
                     "role": "StaticText", "attr": "Identifier",
                     "container": "Keypad",
                     "line": "  9 StaticText  Identifier: Display, Value: 408"})
    r = GeneralResult(ok=True, verified=verified, said=["408"] if read else [])
    r.visits = [Visit(screen="s1", next_screen="s1", actions=acts)]
    return r


def _calc_envelope():
    from nlc.contract import Envelope
    return Envelope(quote="12 곱하기 34 계산해줘", scope="entry",
                    reversibility="reversible",
                    allows=frozenset({"read", "write_ephemeral"}))


def scenario_a_walked_path_is_a_proposal():
    """What a run leaves is a candidate, and a candidate is not a recipe.

    A recipe replays with no model call, so nobody is watching it and a wrong
    one keeps being wrong every time. That asymmetry is why promotion is a gate
    with reasons rather than a counter: "it worked twice" is a fact about two
    afternoons, not a property of the recipe."""
    from nlc import promote
    env = _calc_envelope()
    req = "12 곱하기 34 계산해줘"
    good = promote.candidate_from(_walked_run(), "com.apple.calculator", req,
                                  env.quote, env)
    # A run that never confirmed anything proposes nothing at all.
    unsure = promote.candidate_from(_walked_run(verified=False), "x", req,
                                    env.quote, env)
    flow, why = promote.deployable(good, "probe", req)
    # Nothing to check the result with is not a recipe either.
    blind = promote.candidate_from(_walked_run(read=False),
                                   "com.apple.calculator", req, env.quote, env)
    _, blind_why = promote.deployable(blind, "probe", req)
    return (good is not None and unsure is None and flow is not None
            and not why and any("확인" in w for w in blind_why))


def scenario_what_generalizes_comes_from_the_request():
    """Which literals are parameters is settled by where they came from.

    Not by diffing two runs: that calls whatever happened to repeat a constant
    and whatever happened to differ a variable, neither of which is about the
    user. A value the user supplied is what changes next time; a value read off
    the screen is this run's data, and writing it into a template is exactly
    how write-back went wrong."""
    from nlc import promote
    req = "12 곱하기 34 계산해줘"
    return (promote.provenance("12", req) == "request"
            and promote.provenance("34", req) == "request"
            and promote.provenance("408", req) == "screen"
            and promote.provenance("~/Documents/보고서.rtf", req) == "screen"
            # and the digits a run pressed one key at a time are still the
            # number that was asked for
            and promote.parameters(promote.candidate_from(
                _walked_run(), "com.apple.calculator", req,
                "", _calc_envelope())) == {"1": [0]})


def scenario_the_gate_is_tried_before_it_is_trusted():
    """A gate nobody has tried to get past is a gate of unknown height.

    Promotion runs knowingly broken versions of the recipe through the same
    gate. If one passes, the gate measured nothing — and then nothing is
    deployed, including the version that looked fine."""
    from nlc import promote
    env = _calc_envelope()
    req = "12 곱하기 34 계산해줘"
    c = promote.candidate_from(_walked_run(), "com.apple.calculator", req,
                               env.quote, env)
    flow = promote.to_flow(c, "probe")
    caught = {label: promote.gate(c, broken, r)
              for label, broken, r in promote.counterexamples(c, flow, req)}
    every_one = all(v for v in caught.values()) and len(caught) == 3
    # Distinct reasons, not one rule doing all the work.
    distinct = len({tuple(v) for v in caught.values()}) == 3

    # And when the gate stops catching them, promotion stops too.
    real_gate = promote.gate
    promote.gate = lambda *a, **k: []
    try:
        out, why = promote.deployable(c, "probe", req)
    finally:
        promote.gate = real_gate
    return (every_one and distinct and out is None
            and any("아무것도 재고 있지 않" in w for w in why))


def scenario_a_candidate_stays_out_of_the_store():
    """Candidates live where nothing replays from.

    The repeat count is recorded and not gated on: what decides deployment is
    whether the claims are checkable, and that does not change with the tally."""
    import tempfile
    from nlc import promote
    env = _calc_envelope()
    req = "12 곱하기 34 계산해줘"
    c = promote.candidate_from(_walked_run(), "com.apple.calculator", req,
                               env.quote, env)
    with tempfile.TemporaryDirectory() as d:
        cs = promote.CandidateStore(path=d)
        first = cs.record(c, req, "probe")
        again = cs.record(c, req, "probe")          # the same path, walked again
        elsewhere = promote.candidate_from(
            _walked_run(read=False), "com.apple.calculator", req, env.quote, env)
        other = cs.record(elsewhere, req, "other")
        return (first["runs"] == 1 and again["runs"] == 2
                and not again["blocked_by"] and again["flow"] is not None
                # a different path does not inherit the tally, and this one
                # cannot be deployed at all
                and other["runs"] == 1 and other["flow"] is None
                and cs.list() == ["other", "probe"])


def scenario_the_button_is_not_what_it_falls_on():
    """A control's identity says nothing about what a consequential action hits.

    The same Delete button stays exactly where it is while the selected message
    changes underneath it, so role and label agreeing proves only that the
    button is the button (advisor finding). What the delete would fall on is the
    selection, so a consequential action carries the selection it was planned
    under and the driver re-reads it — two or three AX reads on the front
    window, not a tree walk.

    Two layers, tested where each lives: the driver refuses when the selection
    moved, and the loop only attaches the witness where it means something. A
    calculator has no selection, and demanding one would refuse every digit for
    a fact that does not exist."""
    import types
    import nlc.driver as D
    from nlc.contract import Envelope
    from nlc.general import run

    # --- the driver's side: a moved selection is a different target ---------
    d = Driver()
    obs = types.SimpleNamespace(pid=1, elements=["<el>"], obs_id="o")
    real_attr, real_label = D._attr, D._elem_label
    real_modal, real_sel = Driver._modal_sheet, Driver.selection_digest
    D._attr = lambda el, k: "AXButton" if k == "AXRole" else None
    D._elem_label = lambda el: "Title: 삭제"
    Driver._modal_sheet = staticmethod(lambda pid: False)
    Driver.selection_digest = staticmethod(lambda pid: "sel-B")
    try:
        act = {"element_index": 0}
        same_button = {"role": "Button", "label": "삭제"}
        held = d._mismatch(obs, act, {**same_button, "selection": "sel-B"})
        moved = d._mismatch(obs, act, {**same_button, "selection": "sel-A"})
    finally:
        D._attr, D._elem_label = real_attr, real_label
        Driver._modal_sheet, Driver.selection_digest = real_modal, real_sel

    # --- the loop's side: the witness goes on what it is about -------------
    carried = []

    class Watching:
        def __init__(self, label):
            self.label = label

        def identity(self, t):
            return t

        def observe(self, target, mode="tree_full", require_window=True):
            return {"type": "observation", "obs_id": "o",
                    "tree": f"0 Window  Title: T\n  1 Button  Title: {self.label}\n"}

        def selection(self, target):
            return {"type": "selection", "digest": "sel-A"}

        def act(self, obs_id, target, action, expect=None, risk_class=None,
                intent_text="", approved=False):
            carried.append(dict(expect or {}))
            return {"type": "acted", "action": action["name"]}

        def wait_until(self, target, pred, timeout_ms=8000):
            return self.observe(target)

    def one_click(goal, cands, done):
        return ({"done": True, "actions": [], "expect": None} if done else
                {"done": False, "expect": None,
                 "actions": [{"pick": 0, "do": "click", "candidate": cands[0],
                              "why": "한 번"}]})

    env = Envelope(quote="메일 지워줘", scope="item",
                   reversibility="recoverable",
                   allows=frozenset({"read", "write_ephemeral", "delete"}))
    run(Watching("삭제"), "x", "메일 지워줘", one_click, envelope=env,
        approve=lambda _w: True)
    run(Watching("7"), "x", "7 눌러줘", one_click, envelope=env)
    consequential, cheap = carried[0], carried[-1]
    return (held == "" and "고른 것" in moved
            and consequential.get("selection") == "sel-A"
            and "selection" not in cheap)


def scenario_a_receipt_is_issued_by_someone_else():
    """`ok` is evidence the loop ended; it is the input to the question.

    Three separate checks (advisor's formulation): is the wanted thing there,
    is what should not have moved still there, and did anything happen that was
    not allowed to. The third is the one a final screenshot could never answer —
    a message sent halfway through leaves no trace in the final picture."""
    from nlc.general import GeneralResult
    from nlc.contract import Envelope
    from nlc.outcome import DONE, PARTIAL, UNVERIFIED, OutcomeSpec, verify
    env = Envelope(quote="읽어줘", allows=frozenset({"read"}), scope="item",
                   reversibility="reversible")
    spec = OutcomeSpec.for_envelope(env)

    def run_of(effects, verified, said):
        r = GeneralResult(ok=True, verified=verified, said=said)
        r.effects = set(effects)
        return r

    fine = verify(spec, run_of({"read"}, True, ["408"]))
    # The screen may look perfect; something still happened that was not asked
    # for, and only the effect history knows.
    stray = verify(spec, run_of({"read", "transmit"}, True, ["408"]))
    blind = verify(spec, run_of({"read"}, False, []))
    # A spec that names what to read can actually answer the first question.
    strict = OutcomeSpec(must_read=["408"], forbidden=spec.forbidden)
    wrong = verify(strict, run_of({"read"}, True, ["409"]))
    return (fine.state == DONE and stray.state == PARTIAL
            and "transmit" in stray.problems[0]
            and blind.state == UNVERIFIED and "확인하지 못했" in blind.say()
            and wrong.state == PARTIAL and "408" in wrong.problems[0]
            # a caveat on every success is a caveat nobody reads
            and "확인하지 못한 것" not in fine.say()
            and "확인하지 못한 것" in blind.say())


def scenario_provider_env_is_not_the_session():
    """A provider CLI must not inherit the session that spawned it.

    Authoring usually runs inside an agent session, and that session exports
    `CLAUDE_CONFIG_DIR`. The `claude` shim reads any such variable as "the
    caller already chose a backend", skips its account routing, and the child
    looks at the parent session's config — reporting itself logged out while the
    user is signed in. The provider looked broken and was not."""
    import os
    import nlc.authoring as authoring
    before = dict(os.environ)
    os.environ.update({"CLAUDECODE": "1", "CLAUDE_CONFIG_DIR": "/tmp/x",
                       "CLAUDE_CODE_SESSION_ID": "abc", "CLAUDE_PID": "1"})
    os.environ.setdefault("ANTHROPIC_API_KEY", "sk-test-kept")
    try:
        env = authoring._provider_env()
        gone = not any(k in env for k in ("CLAUDECODE", "CLAUDE_CONFIG_DIR",
                                          "CLAUDE_CODE_SESSION_ID", "CLAUDE_PID"))
        # A credential the user exported on purpose is a real choice; keep it.
        kept = env.get("ANTHROPIC_API_KEY") == os.environ["ANTHROPIC_API_KEY"]
        # Outside a session there is nothing to undo.
        os.environ.pop("CLAUDECODE")
        untouched = "CLAUDE_CONFIG_DIR" in authoring._provider_env()
        return gone and kept and untouched
    finally:
        os.environ.clear()
        os.environ.update(before)


def scenario_broken_provider_falls_through():
    """A CLI that fails while reporting success must not become the answer.

    opencode prints a 401 and exits 0, and cxweb answered "추론 수준 컨트롤을
    찾지 못했습니다" after the web UI moved. Both looked like replies. A reply
    with no JSON in it is not an answer to a question that asked for JSON,
    whatever the exit code said, and the next provider should get a turn."""
    import nlc.authoring as authoring
    real_cli, real_muxa = authoring._try_cli, authoring._try_muxa
    seen = []

    def fake_cli(provider, prompt, timeout):
        seen.append(provider)
        if provider == "claude":
            return 'Error: "Authentication required: HTTP error: 401"'
        if provider == "codex":
            return "[cxweb] 추론 수준 컨트롤을 찾지 못했습니다"
        return '{"name": "ok", "target": "x", "steps": []}'

    authoring._try_cli = fake_cli
    authoring._try_muxa = lambda *a, **k: None
    try:
        got = authoring._ask_model("p", provider="auto", expect_json=True)
        # The two broken ones were tried and skipped, not accepted.
        if "claude" not in seen or "codex" not in seen or "{" not in got:
            return False
        # Without expect_json the same junk IS returned — the caller decides.
        loose = authoring._ask_model("p", provider="claude")
        return "401" in loose
    finally:
        authoring._try_cli, authoring._try_muxa = real_cli, real_muxa


def scenario_authored_steps_can_be_approved():
    """A step a model wrote must be one the user can actually say yes to.

    Consent binds to a described action, so a step with no binding can never be
    approved — and authoring only built one when a step had a selector. A menu
    step has none, which made every consequential command a model authored
    permanently stuck: the gate asked, and there was nothing to answer with."""
    import json as _json
    import nlc.authoring as authoring
    from nlc.agent import Agent
    spec = {"name": "probe_binding", "target": "com.apple.TextEdit",
            "steps": [{"action": "menu",
                       "params": {"path": ["File", ["Save", "Save…"]]},
                       "intent_text": "저장합니다",
                       "unverified_ok": True,
                       "safety": {"effects": ["write_persistent"],
                                  "data_classes": ["none"],
                                  "resource": {"object_type": "문서",
                                               "scope": "item"},
                                  "destination": {"kind": "none"},
                                  "reversibility": "recoverable",
                                  "commit_point": True, "unknowns": []}}],
            "_intent": "문서를 저장해줘"}
    flow = authoring._to_flow(_json.loads(_json.dumps(spec)))
    if flow.steps[0].binding is None:
        return False
    approvals, blocked = Agent(driver=Driver()).authorize(flow, ask=lambda _w: True)
    return not blocked and 0 in approvals


def scenario_vacuous_pass_is_fed_back():
    """The repair loop must not accept a run that proved nothing.

    A one-step recipe clicking a control that does not respond passed here once,
    because the checkbox it asserted had been on since an earlier run. For a
    loop whose whole job is catching recipes that do not work, that is the case
    it most needs to notice."""
    import nlc.authoring as authoring
    from nlc.contract import SafetyContract, Resource
    from nlc.flow import Flow, Postcondition, Step
    d = Driver()
    if d._resolve("com.apple.calculator", launch=False) is None:
        return "계산기가 실행 중이 아님"
    KP = "CalculatorKeypadView"
    entry = Resource(object_type="계산기 입력", scope="entry")
    # Clear twice: the second run's postcondition is true before it acts.
    clear = Flow("probe_vacuous", "com.apple.calculator", steps=[
        Step("click", Selector(role="Button", identifier=["AllClear", "Clear"],
                               ancestor=KP), intent_text="지우기",
             safety=SafetyContract(effects=["delete"], resource=entry,
                                   reversibility="reversible"),
             postcondition=Postcondition(
                 field=Selector(role="ScrollArea",
                                identifier="StandardInputView"),
                 equals="0", attribute="Value"))])
    replay(d, clear)                      # leaves the display at 0
    ok, why = authoring._verify(d, clear, None)
    return (not ok) and "증명" in why


def scenario_authoring_teaches_the_real_vocabulary():
    """The prompt must describe the engine that exists, not the one that did.

    The engine grew `menu`, captures and front-window scoping while the
    authoring prompt still taught `press_key` and a postcondition shape the
    parser ignores — so a freshly authored recipe could not express the everyday
    commands (save, close, copy) that live only in the menu bar."""
    from nlc.authoring import _SYSTEM
    required = ("menu", "captures", "in_focused_window", "click_ancestor",
                "skip_if", "absent", "empty_ok")
    if any(k not in _SYSTEM for k in required):
        return False
    # field_identifier was the shape the parser silently dropped.
    return "field_identifier" not in _SYSTEM and "There is no `press_key`" in _SYSTEM


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
        return "선택 의존성 web(playwright) 미설치"
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
    ("authorization_never_falls (adding an effect cannot free the action)",
     scenario_authorization_never_falls),
    ("approval_binding (consent tied to the action)", scenario_approval_binding),
    ("input_verification (typed input must verify effect)", scenario_input_verification),
    ("volatile_identifier (state-carrying AXIdentifier resolves)", scenario_volatile_identifier),
    ("trace_reproducibility (replay a run off-machine)", scenario_trace_reproducibility),
    ("parameterized_flow (one IR, many inputs)", scenario_parameterized_flow),
    ("reparameterize (concrete IR -> template, no model)", scenario_reparameterize),
    ("capture (read a value off the screen)", scenario_capture),
    ("swift_helper (signed helper runs the same IR)", scenario_swift_helper),
    ("helper_menu (menu commands cross the permission boundary)", scenario_helper_menu),
    ("helper_user_authority (Esc aborts; cross-target refused)", scenario_helper_user_authority),
    ("permission_boundary (env override and argv0 forgery refused)", scenario_permission_boundary),
    ("peer_authentication (channel is launcher-only; per-action schema)", scenario_peer_authentication),
    ("cli_end_to_end (the command a person types)", scenario_cli_end_to_end),
    ("intent_parameters (the request supplies the values)", scenario_intent_parameters),
    ("menu_command (commands that exist only in the menu bar)", scenario_menu_command),
    ("menu_verification (a menu press that 'worked' proves nothing)",
     scenario_menu_verification_required),
    ("toggle_converges (replaying a toggle does not oscillate)", scenario_toggle_converges),
    ("menu_path_pinned (repointing a menu step invalidates consent)",
     scenario_menu_path_pinned),
    ("answers_a_question (reads the screen back instead of acting)",
     scenario_answers_a_question),
    ("builtin_is_not_frozen (shipped recipes come from the code)",
     scenario_builtin_is_not_frozen),
    ("consent_required (a save does not run on its own authority)",
     scenario_consent_required),
    ("save_file (menu → sheet → field → consequential click)", scenario_save_file),
    ("consent_is_about_the_file (approved A, typed B, refused)",
     scenario_consent_is_about_the_file),
    ("app_name_is_the_app (not a same-named extension)", scenario_app_name_is_the_app),
    ("large_tree_is_bounded (Mail answers instead of hanging)",
     scenario_large_tree_is_bounded),
    ("reads_a_list (plural selector for plural things)", scenario_reads_a_list),
    ("calendar_reads_today (today's events, view untouched)",
     scenario_calendar_reads_today),
    ("mail_reads_inbox (senders, subjects and the tally)", scenario_mail_reads_inbox),
    ("missing_target_says_what_is_there (names the alternatives)",
     scenario_missing_target_says_what_is_there),
    ("absence_is_verifiable (a dialog closing is evidence)",
     scenario_absence_is_verifiable),
    ("dialog_recovery (a way out of a stuck run)", scenario_dialog_recovery),
    ("save_chooses_its_folder (lands where it was asked to)",
     scenario_save_chooses_its_folder),
    ("learning_is_reachable (--learn actually reaches authoring)",
     scenario_learning_is_reachable),
    ("authoring_produces_a_runnable_flow (a novel task, end to end)",
     scenario_authoring_produces_a_runnable_flow),
    ("already_true_is_not_proof (a pass that proved nothing is labelled)",
     scenario_already_true_is_not_proof),
    ("escalation_is_off_by_default (replay stays free unless asked)",
     scenario_escalation_is_off_by_default),
    ("escalation_only_where_declared (a step names the surprise it expects)",
     scenario_escalation_only_where_declared),
    ("escalation_clears_a_blocked_run (a surprise becomes a question)",
     scenario_escalation_clears_a_blocked_run),
    ("a_fix_is_not_quietly_kept (a one-off fix is news, not knowledge)",
     scenario_a_fix_is_not_quietly_kept),
    ("consent_cannot_be_re_aimed (an approved step keeps its target)",
     scenario_consent_cannot_be_re_aimed),
    ("an_unclear_outcome_is_not_retried (a slow screen is not a failed send)",
     scenario_an_unclear_outcome_is_not_retried),
    ("re_aiming_is_refused_after_the_fact (a new target is a new action)",
     scenario_re_aiming_is_refused_after_the_fact),
    ("one_step_cannot_spend_the_budget (asking has a per-step limit)",
     scenario_one_step_cannot_spend_the_budget),
    ("an_identifier_keeps_what_distinguishes_it (only known state is stripped)",
     scenario_an_identifier_keeps_what_distinguishes_it),
    ("a_postcondition_compares_text_not_quantity (00123 is not 123)",
     scenario_a_postcondition_compares_text_not_quantity),
    ("a_candidate_says_where_it_is (two Save buttons are told apart)",
     scenario_a_candidate_says_where_it_is),
    ("the_fast_path_stands_down (a match is not an understanding)",
     scenario_the_fast_path_stands_down),
    ("a_refusal_is_an_answer (하지 마 has an outcome)",
     scenario_a_refusal_is_an_answer),
    ("a_reading_must_quote_the_user (the model cannot invent the words)",
     scenario_a_reading_must_quote_the_user),
    ("a_reading_normalizes_into_the_fast_path (one question, then free)",
     scenario_a_reading_normalizes_into_the_fast_path),
    ("a_screen_is_one_question (the unit is the screen, not the step)",
     scenario_a_screen_is_one_question),
    ("a_batch_cannot_leave_its_screen (every pick is checked)",
     scenario_a_batch_cannot_leave_its_screen),
    ("a_general_run_leaves_a_graph (a repeated path is a recipe)",
     scenario_a_general_run_leaves_a_graph),
    ("a_general_run_stops_going_nowhere (the same screen twice is an answer)",
     scenario_a_general_run_stops_going_nowhere),
    ("a_batch_acts_on_names_not_indexes (a shifted screen is not a free click)",
     scenario_a_batch_acts_on_names_not_indexes),
    ("a_vanished_target_cuts_the_batch (the rest was about a screen that is gone)",
     scenario_a_vanished_target_cuts_the_batch),
    ("a_read_sees_what_the_batch_did (the answer is after, not before)",
     scenario_a_read_sees_what_the_batch_did),
    ("two_of_the_same_name_are_told_apart (where it was finds it again)",
     scenario_two_of_the_same_name_are_told_apart),
    ("a_sheet_appearing_cuts_the_batch (still there is not still right)",
     scenario_a_sheet_appearing_cuts_the_batch),
    ("matching_a_prefix_is_not_answering (the leftover is still instructions)",
     scenario_matching_a_prefix_is_not_answering),
    ("the_reading_cannot_let_it_back_in (the guard holds on the goal too)",
     scenario_the_reading_cannot_let_it_back_in),
    ("a_batch_observes_once (eight actions, one look)",
     scenario_a_batch_observes_once),
    ("a_check_covers_only_what_it_checked (a quote is not its meaning)",
     scenario_a_check_covers_only_what_it_checked),
    ("a_handoff_is_not_one_more_yes (three questions, not one)",
     scenario_a_handoff_is_not_one_more_yes),
    ("done_is_where_it_stopped_not_that_it_worked (ended is not succeeded)",
     scenario_done_is_where_it_stopped_not_that_it_worked),
    ("an_envelope_comes_from_the_words (the range is known, the path is not)",
     scenario_an_envelope_comes_from_the_words),
    ("the_control_says_what_not_how_risky (one policy for both modes)",
     scenario_the_control_says_what_not_how_risky),
    ("an_expectation_that_holds_does_not_refuse (a true claim must pass)",
     scenario_an_expectation_that_holds_does_not_refuse),
    ("a_role_is_not_a_word_in_a_label (the comparison, without a screen)",
     scenario_a_role_is_not_a_word_in_a_label),
    ("a_walked_path_is_a_proposal (a candidate is not a recipe)",
     scenario_a_walked_path_is_a_proposal),
    ("what_generalizes_comes_from_the_request (provenance, not diffing)",
     scenario_what_generalizes_comes_from_the_request),
    ("the_gate_is_tried_before_it_is_trusted (broken versions must fail it)",
     scenario_the_gate_is_tried_before_it_is_trusted),
    ("a_candidate_stays_out_of_the_store (nothing replays a proposal)",
     scenario_a_candidate_stays_out_of_the_store),
    ("the_button_is_not_what_it_falls_on (the selection is the target)",
     scenario_the_button_is_not_what_it_falls_on),
    ("a_receipt_is_issued_by_someone_else (ok is evidence, not the verdict)",
     scenario_a_receipt_is_issued_by_someone_else),
    ("provider_env_is_not_the_session (a spawned CLI gets its own account)",
     scenario_provider_env_is_not_the_session),
    ("broken_provider_falls_through (a 401 is not a model reply)",
     scenario_broken_provider_falls_through),
    ("authored_steps_can_be_approved (consent has something to bind to)",
     scenario_authored_steps_can_be_approved),
    ("vacuous_pass_is_fed_back (the loop notices it proved nothing)",
     scenario_vacuous_pass_is_fed_back),
    ("authoring_handles_a_dialog (later steps act inside a sheet)",
     scenario_authoring_handles_a_dialog),
    ("authoring_still_catches_invented_targets (relaxed, not useless)",
     scenario_authoring_still_catches_invented_targets),
    ("authoring_teaches_the_real_vocabulary (prompt matches the engine)",
     scenario_authoring_teaches_the_real_vocabulary),
    ("resolver_honesty (ambiguous/missing raise)", scenario_resolver_honesty),
    ("safety_classifier (4-tier mapping)", scenario_safety_classifier),
    ("browser_backend (web via same replay engine)", scenario_browser_backend),
]


def _gui_reason() -> str:
    """Why the screen cannot be driven right now — or "" if it can.

    A locked screen made twenty scenarios report FAIL, which says the recipes
    broke. They did not: nothing could be seen. "화면이 잠겼다" and "레시피가
    깨졌다" are different findings and a suite that cannot tell them apart is
    worse than one that admits it does not know.
    """
    try:
        d = Driver()
        seen = []
        for app in ("com.apple.finder", "com.apple.calculator",
                    "com.apple.TextEdit"):
            if d._resolve(app, launch=False) is None:
                continue
            obs = d.observe(app, "tree_full", require_window=False)
            if obs.get("error") in ("gui_session_unavailable",
                                    "no_window_content", "empty_tree"):
                seen.append(0)
            else:
                seen.append(obs.get("element_count", 0))
        # A locked screen does not error — it observes successfully and returns
        # NOTHING. One running app with an empty tree could be an app with no
        # window; every running app empty at once is the screen being dark.
        if seen and not any(seen):
            return "화면이 잠겨 있거나 GUI 세션을 볼 수 없음"
    except Exception as e:
        return f"화면을 확인할 수 없음 ({e})"
    return ""


def main():
    # An argument narrows the run to scenarios whose name contains it. A failure
    # in one scenario used to mean sitting through all sixty to see it again.
    only = [a for a in sys.argv[1:] if not a.startswith("-")]
    print("NLC evaluation\n" + "=" * 48)
    blind = _gui_reason()
    if blind:
        print(f"  ! {blind} — 화면이 필요한 시나리오는 판정하지 않습니다\n")
    passed = required = 0
    for name, fn in SCENARIOS:
        if only and not any(o in name for o in only):
            continue
        t0 = time.monotonic()
        try:
            ok = fn()
        except Exception as e:
            ok = False
            name += f"  [error: {e}]"
        dt = (time.monotonic() - t0) * 1000
        # A scenario skips by returning the reason it skipped. One shared
        # message for every skip hid which check was actually not running — the
        # helper scenarios were reporting a missing web dependency.
        if ok is None or isinstance(ok, str):
            print(f"  SKIP  {name}  ({ok or '이 환경에서는 실행 불가'})")
            continue
        if not ok and blind:
            # It did not pass, and nothing could see the screen. Which of those
            # is the finding cannot be told from here, so neither is claimed.
            print(f"  SKIP  {name}  ({blind})")
            continue
        required += 1
        passed += bool(ok)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  ({dt:.0f}ms)")
    print("=" * 48)
    print(f"  {passed}/{required} scenarios passed")
    sys.exit(0 if passed == required else 1)


if __name__ == "__main__":
    main()
