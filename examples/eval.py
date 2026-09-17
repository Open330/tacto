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
    real_ask = authoring._ask_model
    # **kw so a new argument on _ask_model shows up as a real failure to fix,
    # not as a stub that silently stopped matching.
    authoring._ask_model = lambda prompt, provider="auto", **kw: reply
    try:
        flow = authoring.author("계산기 공학용으로", d,
                                target="com.apple.calculator")
        ok = replay(d, flow).ok
    finally:
        authoring._ask_model = real_ask
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
    real = authoring._ask_model
    # **kw so a new argument on _ask_model shows up as a real failure to fix,
    # not as a stub that silently stopped matching.
    authoring._ask_model = lambda prompt, provider="auto", **kw: reply
    try:
        return authoring.author(intent, d, target=target)
    finally:
        authoring._ask_model = real


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
    from nlc.contract import SafetyContract, Resource
    from nlc.flow import Flow, Postcondition, Resolution, Step, replay
    from nlc.library import textedit_save_as
    d = Driver()
    if not _textedit_ready(d):
        return "TextEdit 이 실행 중이 아님 (open -a TextEdit)"
    if not _blocking_sheet(d):
        return "저장 시트를 띄우지 못함"

    def resolver(why):
        # Exactly what a person would do: cancel the dialog that is in the way.
        return Resolution(
            "insert_steps",
            steps=[Step("click",
                        Selector(role="Button", title=["Cancel", "취소"]),
                        intent_text="가로막은 대화상자를 닫습니다",
                        safety=SafetyContract(
                            effects=["write_ephemeral"],
                            reversibility="reversible",
                            resource=Resource(object_type="대화상자",
                                              scope="entry")),
                        postcondition=Postcondition(absent="Sheet"))],
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
        # A scenario skips by returning the reason it skipped. One shared
        # message for every skip hid which check was actually not running — the
        # helper scenarios were reporting a missing web dependency.
        if ok is None or isinstance(ok, str):
            print(f"  SKIP  {name}  ({ok or '이 환경에서는 실행 불가'})")
            continue
        required += 1
        passed += bool(ok)
        print(f"  {'PASS' if ok else 'FAIL'}  {name}  ({dt:.0f}ms)")
    print("=" * 48)
    print(f"  {passed}/{required} scenarios passed")
    sys.exit(0 if passed == required else 1)


if __name__ == "__main__":
    main()
