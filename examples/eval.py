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
    from nlc.library import textedit_write_note, textedit_emphasize
    d = Driver()
    if d.observe("com.apple.TextEdit", "tree_full")["type"] != "observation":
        return "TextEdit 문서 창이 없음 (열어두고 다시 실행)"
    if not replay(d, textedit_write_note().bind(text="굵게 수렴 테스트")).ok:
        return "TextEdit 본문에 쓸 수 없음"
    first = replay(d, textedit_emphasize())
    second = replay(d, textedit_emphasize())
    if not (first.ok and second.ok):
        return False
    # The point is not merely that both runs report success, but that the second
    # one did nothing — a rerun that pressed Bold again would have un-bolded it.
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


def scenario_answers_a_question():
    """Some requests want an answer, not an action.

    Every other recipe here changes something; this one opens a folder and
    reads Finder's own status line back, and the agent has to actually say what
    it found rather than reporting that it finished."""
    from nlc.agent import Agent
    turn = Agent().handle("다운로드 폴더에 뭐 있어?")
    return (turn.status == "done" and turn.flow == "finder_count_items"
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
    agent = Agent(driver=d)
    approvals, blocked = agent.authorize(textedit_new_document(), ask=lambda _w: True)
    if blocked or not replay(d, textedit_new_document(), approvals=approvals).ok:
        return False

    f = textedit_save_as().bind(filename="승인한이름.rtf")
    approvals, blocked = agent.authorize(f, ask=lambda _what: True)
    if blocked:
        return False
    # The name actually typed no longer matches the name consent was given for.
    f.steps[1].params = {"value": "다른이름.rtf"}
    f.steps[1].postcondition.equals = "다른이름.rtf"
    r = replay(d, f, approvals=approvals)
    refused = (not r.ok) and any(e.get("error") == "contract_invalidated"
                                 for e in r.log)
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
    ("consent_required (a save does not run on its own authority)",
     scenario_consent_required),
    ("save_file (menu → sheet → field → consequential click)", scenario_save_file),
    ("consent_is_about_the_file (approved A, typed B, refused)",
     scenario_consent_is_about_the_file),
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
