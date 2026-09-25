"""General computer use — the tasks no recipe covers.

A Flow is the answer when the same job comes back: write it once, replay it for
nothing. But most of what a person asks for the first time has no recipe, and
"저는 그 작업을 몰라요" is not an answer for someone who cannot do it themselves.

So this is the other half. No recipe, no stored plan — look at the screen, ask
what to do on it, do that, look again. What keeps it from costing a model call
per click is the unit:

    질문은 스텝당이 아니라 **화면당** 이다.

A calculator's keypad does not change while its display does, so seven presses
share one set of candidates and therefore one question. Measured on this
machine: `calc_arith` is 7 steps across 3 screens. That ratio is what a general
run actually costs, and it is why the batch stops exactly where the screen
changes — a candidate list is only true of the screen it was read from.

Everything the model returns lands in a slot this module can reject: a pick must
index the candidates it was given, an action must be one we perform, and
`expect` has to actually show up afterwards. The risk class still comes from
`safety.classify()` reading the control itself, never from anything the model
says about it.

What a run leaves behind is a small graph — which screen, which actions, which
screen next. A path walked more than once is a recipe waiting to be written,
which is how general mode feeds the replay engine rather than competing with it.
"""

from __future__ import annotations

import hashlib
import json
from copy import deepcopy
from .commit_evidence import make_event, evidence_error
import math
import time
from dataclasses import dataclass, field

import re

from . import safety
from .contract import Envelope, describe, policy
from .flow import Selector, _same_role
from .outcome import OutcomeSpec
from .model_budget import model_deadline
from .provider_observation import capture_provider_calls

# What can be done to a thing depends on what it is, and saying so is what
# stops "read the answer" from being offered as "click the answer". The first
# run of this loop pressed all seven calculator keys from one question and then
# had nothing to read the result from, because a display is not a button.
_CAN = {
    "Button": ("click",), "MenuItem": ("click",), "CheckBox": ("click",),
    "RadioButton": ("click",), "PopUpButton": ("click", "read"), "Link": ("click",),
    "MenuButton": ("click",), "Row": ("click",), "Cell": ("click",),
    "TextField": ("click", "set_value", "read"),
    "TextArea": ("click", "set_value", "read"),
    "textbox": ("click", "set_value", "read"),
    "combobox": ("click", "set_value", "read"),
    "StaticText": ("read",), "ScrollArea": ("read", "scroll"), "Image": ("read",),
}
# A visible anchor lets drivers find its scrollable ancestor, including pages
# whose accessibility tree has no named scroll container.
_CAN = {role: tuple(dict.fromkeys((*actions, "scroll")))
        for role, actions in _CAN.items()}
_CAN["Window"] = ("prepare_window",)
ACTIONABLE = tuple(_CAN)



def candidates(tree: str, *, capabilities=()) -> list[dict]:
    """What could be acted on right now, with enough context to be told apart."""
    lines = tree.splitlines()
    act, read = [], []
    for i, line in enumerate(lines):
        toks = line.strip().split()
        if len(toks) < 2:
            continue
        role = next((r for r in ACTIONABLE if _same_role(toks[1], r)), None)
        if role is None:
            continue
        can = _CAN[role]
        if role == "Window" and "action.prepare_window" not in capabilities:
            continue
        if role != "Window" and "action.press_key" in capabilities:
            can = (*can, "press_key")
        bucket = act if "click" in can or "prepare_window" in can else read
        # What to CALL it and what it IS are different questions, and a
        # display answers them differently: the useful name for a readout is
        # what it currently says, but a thing that is named by what it says
        # cannot be found again once it says something else. A calculator
        # display listed as "0" was unfindable the moment it became "54".
        container, where = _place(lines, i)
        show = _pick(line, ("Title", "Description", "Value", "Identifier"))
        ident = _pick(line, ("Identifier", "Description", "Title")
                      if "click" not in can
                      else ("Title", "Description", "Identifier", "Value"))
        if not show or not ident:
            continue
        from .ask import NAVIGATION_KEYS
        keys = NAVIGATION_KEYS - ({"Escape"} if "press_key.no_escape" in capabilities else set())
        bucket.append({"element_index": int(toks[0]), "role": toks[1],
                       "navigation_keys": sorted(keys),
                       "label": show[1][:80], "match": ident[1],
                       "attr": ident[0], "container": container,
                       "where": where, "can": list(can),
                       "disabled": "[disabled]" in line, "line": line})
    return act + read


def _pick(line: str, keys) -> tuple[str, str] | None:
    for k in keys:
        v = Selector._attr(line, k)
        if v:
            return k, v
    return None


def _place(lines: list[str], i: int) -> tuple[str, str]:
    """(nearest named container, text saying where this is).

    The container is kept separately from the sentence built out of it, because
    a name is what tells two identical labels apart and a sentence is not.
    """
    indent = len(lines[i]) - len(lines[i].lstrip())
    window = container = ""
    for j in range(i - 1, -1, -1):
        prev = lines[j]
        if not prev.strip():
            continue
        pind = len(prev) - len(prev.lstrip())
        if pind >= indent:
            continue
        indent = pind
        toks = prev.strip().split()
        role = toks[1] if len(toks) >= 2 else ""
        got = _pick(prev, ("Identifier", "Title", "Description"))
        name = got[1] if got else ""
        if _same_role(role, "Window") and not window:
            window = name or "(제목 없음)"
            break
        if name and not container:
            # The container's NAME, not what it says right now. A calculator
            # display is identified as `StandardInputView;value:25`, and a
            # recipe that remembered that could only ever find it showing 25.
            container = Selector._stable_id(name)
    bits = ([f"안: {container}"] if container else []) + \
           ([f"창: {window}"] if window else [])
    return container, " / ".join(bits)


def blocking_sheet(tree: str) -> str:
    """The title of a sheet or dialog sitting over this app, if there is one.

    An element can survive a modal appearing: it is still there, still named
    the same, still the only one of its name — and pressing it now does not do
    what the plan meant, because the app is waiting for an answer to something
    else. Re-finding an element cannot see that; the sheet itself can.
    """
    for line in tree.splitlines():
        toks = line.strip().split()
        if len(toks) >= 2 and (_same_role(toks[1], "Sheet") or toks[1].lower() in ("dialog", "alertdialog")):
            got = _pick(line, ("Title", "Description", "Identifier"))
            return got[1] if got else "(이름 없는 대화상자)"
    return ""


_ATTR_FIELD = {"Title": "title", "Description": "desc",
               "Value": "value", "Identifier": "identifier"}


def _line_for(tree: str, idx: int) -> str:
    return next((l for l in tree.splitlines()
                 if re.match(rf"\s*{idx}\s", l)), "")


def _selection(driver, target: str) -> str:
    """What the app has selected right now, if it can say."""
    fn = getattr(driver, "selection", None)
    if fn is None:
        return ""
    try:
        r = fn(target)
    except Exception:
        return ""
    return r.get("digest", "") if r.get("type") == "selection" else ""


def _witness(driver, obs, target: str, field_name: str, cands: list,
             ask_where, say) -> str | None:
    """Read what the screen currently says for one named fact.

    Returns None when nothing on this screen shows it — which is not a failure
    here. It is a fact nobody confirmed, and the receipt reports it as
    unchecked rather than assuming it was fine.
    """
    if ask_where is None:
        return None
    readable = [c for c in cands if "read" in c.get("can", ())]
    if not readable:
        return None
    try:
        answer = ask_where(field_name, readable)
    except Exception:
        return None
    if not answer or answer.get("pick") is None:
        return None
    c = answer["candidate"]
    line = _probe(driver, obs["obs_id"], target, c["element_index"])
    if not line:
        return None                   # stale snapshots cannot witness a commit
    value = _value_of({**c, "line": line})
    say(f"  ({field_name}: {value})")
    return value


def _probe(driver, obs_id: str, target: str, idx: int) -> str:
    """What one element says right now, without walking the tree.

    A read late in a batch has to answer with the screen AFTER the earlier
    actions — "7 더하기 8" read its display back as 0 because the value came
    off the line captured before a single key was pressed. Asking the element
    is a millisecond; asking the screen is most of a minute in a big app.
    """
    probe = getattr(driver, "probe", None)
    if probe is None:
        return ""
    try:
        r = probe(obs_id, target, idx)
    except Exception:
        return ""
    return r.get("line", "") if r.get("type") == "probe" else ""


def reacquire(c: dict, tree: str) -> int | None:  # noqa: C901
    """Where this candidate is NOW — or None if the screen moved under it.

    A batch is a list of things to do to named elements, and an index is not a
    name. Acting on the index the candidate HAD, against a tree the app has
    since rebuilt, is how a plan of "Alpha then Beta" pressed Alpha twice while
    reporting success: a dialog appeared, everything shifted down one, and the
    index that used to mean Beta meant Alpha. Nothing failed, so nothing caught
    it.

    So each action re-finds its element by what it is called, and a name that no
    longer picks out exactly one thing means the rest of the batch is about a
    screen that is gone.
    """
    field = _ATTR_FIELD.get(c.get("attr") or "")
    if field is None:
        return None
    try:
        # A previously observed container remains part of the identity even
        # when another same-named field becomes the only remaining match.
        idx, _ = Selector(role=c["role"], ancestor=c.get("container") or None,
                          **{field: c["match"]}).resolve(tree)
        return idx
    except LookupError:
        return None



def screen_key(cands: list[dict]) -> str:
    """Identity of a SCREEN, which is what you can act on — not what it says.

    Hashing the tree would call every keystroke a new screen, because the
    calculator writes its running total into an identifier. The keypad is what
    a question about which button is asked over, so the keypad is the key.
    """
    # Only the things you can act on: what a screen SAYS changes constantly (a
    # calculator writes its running total into an identifier) while what you
    # can press does not, and it is the latter a question is asked over.
    names = sorted(f"{c['role']}:{Selector._stable_id(c['label'])}"
                   for c in cands if any(a in c.get("can", ()) for a in ("click", "inspect_menu", "menu")))
    return hashlib.sha1("|".join(names).encode()).hexdigest()[:12]


def menu_effect(before: str, after: str, path) -> dict | None:
    """What a menu command visibly changed, as something a recipe can check.

    A menu command has no element to read back, so a recipe made from one had
    nothing to verify and could not be promoted. What it does have is a
    screen before and after. The effect is taken to be an element that exists
    in both, is named the same in both, and whose state changed to something
    that names the command's own leaf — Calculator's `Mode` button going from
    `Mode: basic` to `Mode: scientific` after View › Scientific.

    Exactly one such element, or nothing: two would be a guess about which one
    the command meant, and a recipe built on a guess replays it unattended.
    """
    leaf = path[-1] if isinstance(path, (list, tuple)) and path else None
    leaves = [x.lower() for x in (leaf if isinstance(leaf, list) else [leaf])
              if isinstance(x, str) and x.strip()]
    if not leaves:
        return None

    old, new = _named_elements(before), _named_elements(after)
    found = []
    for key, line in new.items():
        if key not in old:
            continue
        for attribute in ("Identifier", "Value"):
            was, now = Selector._attr(old[key], attribute), Selector._attr(line, attribute)
            if now and was != now and any(x in now.lower() for x in leaves):
                found.append({"role": key[0], "attr": key[1], "match": key[2],
                              "attribute": attribute, "equals": now})
    return found[0] if len(found) == 1 else None


def _named_elements(tree: str) -> dict:
    """(role, naming attribute, name) -> line, for elements named once."""
    seen = {}
    for line in tree.splitlines():
        toks = line.strip().split()
        if len(toks) < 2 or not toks[0].isdigit():
            continue
        name = _pick(line, ("Title", "Description"))
        if name is None:
            continue
        key = (toks[1], name[0], name[1])
        # A name that repeats cannot be found again by that name.
        seen[key] = None if key in seen else line
    return {k: v for k, v in seen.items() if v is not None}


def screen_change(before: str, after: str) -> dict:
    """What happened to the screen between two observations, by element.

    `appeared` and `vanished` are named elements that came or went; `changed`
    says whether any element present in both now reads differently. Kept
    small and by name, so a recipe can reason about it without the trees.
    """
    old, new = _named_elements(before), _named_elements(after)
    changed = any(Selector._attr(old[k], a) != Selector._attr(new[k], a)
                  for k in old.keys() & new.keys() for a in ("Identifier", "Value"))
    return {"appeared": sorted([k[0], k[2]] for k in new.keys() - old.keys()),
            "vanished": sorted([k[0], k[2]] for k in old.keys() - new.keys()),
            "changed": changed}


def _observe_menu_effect(driver, target: str, before: str, path) -> dict | None:
    """Wait briefly for a menu command's effect to show, and describe it."""
    try:
        got = driver.wait_until(
            target, lambda t: menu_effect(before, t, path) is not None,
            timeout_ms=2000)
    except Exception:
        return None
    return menu_effect(before, got.get("tree") or "", path) if got else None


def _value_of(c: dict) -> str:
    """What a read actually answers with.

    The label is how a thing is NAMED, which for a display is often its
    identifier — and the calculator writes its running total into that
    (`StandardInputView;value:408`). A person asked for 408.
    """
    v = Selector._attr(c["line"], "Value")
    if v is not None:
        return v
    label = c.get("label", "")
    head, sep, tail = label.partition(";")
    if sep and tail.lower().startswith("value:"):
        return tail.split(":", 1)[1].strip()
    return label


@dataclass
class Visit:
    """One screen, what was done on it, and where that led."""
    screen: str
    actions: list[dict] = field(default_factory=list)
    next_screen: str = ""
    expect: str | None = None
    verified: bool | None = None
    # Set when the screen moved mid-batch and the rest was abandoned rather
    # than aimed at elements that had shifted underneath it.
    cut_short: str = ""


@dataclass
class GeneralResult:
    ok: bool = False
    why: str = ""
    visits: list[Visit] = field(default_factory=list)
    said: list[str] = field(default_factory=list)   # values read out
    asked: int = 0                                  # planner invocations
    provider_calls: list[dict] = field(default_factory=list)
    # Whether anything was actually SEEN to be true — a value read, or a stated
    # expectation that showed up. `ok` says the run reached its end; this says
    # there is evidence it got somewhere, and they are different claims.
    verified: bool = False
    # Every effect this run actually caused, for a verifier that does not take
    # the run's word for how it went.
    effects: set = field(default_factory=set)
    events: list[dict] = field(default_factory=list)
    # What the screen said, just before a commit, for each fact the task kind
    # requires. Observations only — never expectations.
    witnessed: dict = field(default_factory=dict)
    after_tree: str = ""
    # Whether the consequential effects this run caused are known to have
    # landed. "unknown" is what a driver reports when it dispatched and could
    # not see the result, and it is the state in which nothing may be undone.
    certainty: str = "confirmed"
    stop_reason: str = ""
    commit_evidence: dict = field(default_factory=dict)  # typed host receipts, never model output
    target_identity: str | None = None
    identity_changed: bool = False

    @property
    def did(self) -> list[str]:
        """What actually happened, whether or not the run got where it meant to.

        A batch is not a transaction: a run that stops at its fifth action has
        still done four, and "작성창까지 열었고, 전송은 하지 않았습니다" is the
        report — not a bare failure that leaves someone guessing what state
        their computer is in (advisor finding).
        """
        return [a["label"] for v in self.visits for a in v.actions
                if a.get("label")]

    @property
    def path(self) -> list[str]:
        """Every screen this run stood on, the one it finished at included.

        Two runs that walk the same path did the same job, which is the first
        thing a recipe needs to be worth writing. The last screen counts: it is
        where the answer was, and a run that ends somewhere else did something
        else.
        """
        out = [v.screen for v in self.visits]
        if self.visits and self.visits[-1].next_screen:
            out.append(self.visits[-1].next_screen)
        return out

    @property
    def edges(self) -> list[tuple[str, str]]:
        """The screen graph: which screen led to which, by what was done."""
        return [(v.screen, v.next_screen) for v in self.visits
                if v.next_screen]


def run(driver, target: str, goal: str, ask_here, *, max_screens: int = 8,
        approve=None, narrate=None, envelope: Envelope | None = None,
        witnesses: list | None = None, ask_where=None,
        spec: OutcomeSpec | None = None, timeout_seconds: float | None = None,
        cancelled=None, prepare_commit=None, after_commit=None) -> GeneralResult:
    """Drive `target` toward `goal`, one question per screen.

    `ask_here(goal, candidates, done_so_far)` returns a checked answer or None.
    `envelope` is what the request authorizes, issued from the user's words
    before any of this was seen; without one, only looking and typing into the
    thing in front of you is on the table.
    `approve(what)` is consulted where the policy asks for confirmation.
    `witnesses` names the facts that must be read off the screen BEFORE a
    consequential action — the recipient, the account, the destination — and
    `ask_where(field, candidates)` finds where each one is shown. What comes
    back is an observation; what it should be comes from the request, and the
    comparison happens in the verifier.

    prepare_commit(event) and after_commit(event, driver_result, context) are
    trusted host callbacks (or driver methods), never planner output. Preparation
    is read-only; after_commit must inspect an actual operation receipt, not just
    a pre-existing matching file or a success label. Only typed, event-bound host
    evidence can confirm consequential effects. Model expect is a navigation hint.

    Deadline/cancellation checks are cooperative boundaries for driver calls.
    Built-in model adapters inherit the remaining deadline; arbitrary callbacks
    and blocking driver calls are not interrupted. Remote cancellation is not guaranteed.
    An action dispatched before stopping remains recorded; it is never retried
    here merely because its result is unknown.
    """
    if timeout_seconds is not None and (
            isinstance(timeout_seconds, bool) or not isinstance(timeout_seconds, (int, float))
            or not math.isfinite(timeout_seconds) or timeout_seconds < 0):
        raise ValueError("timeout_seconds must be a finite nonnegative number")
    deadline = time.monotonic() + timeout_seconds if timeout_seconds is not None else None
    env = envelope or Envelope.minimal()
    say = narrate or (lambda _s: None)
    res = GeneralResult()
    done_so_far: list[str] = []
    seen_plans: dict[object, tuple] = {}      # screen -> the plan last run on it
    if isinstance(witnesses, OutcomeSpec):
        spec, witnesses = witnesses, None
    required = [c.name for c in spec.commit_checks] if spec else list(witnesses or [])
    capabilities = getattr(driver, "capabilities", ()) or ()
    select_options = {}
    custom_selects = {}
    prepare_commit = prepare_commit or getattr(driver, "prepare_commit", None)
    after_commit = after_commit or getattr(driver, "after_commit", None)

    def stopped(visit=None):
        reason = res.stop_reason
        if not reason:
            try:
                if cancelled is not None and cancelled():
                    reason = "cancelled"
            except Exception:
                reason = "cancellation_check_failed"
            if not reason and deadline is not None and time.monotonic() >= deadline:
                reason = "deadline_exceeded"
        if not reason:
            return False
        res.stop_reason = reason
        res.ok = False
        descriptions = {
            "cancelled": "사용자 취소 요청으로 중단했습니다",
            "model_timeout": "모델 응답 시간 예산을 초과했습니다",
            "deadline_exceeded": "작업 시간 예산을 초과해 중단했습니다",
            "cancellation_check_failed": "취소 상태를 확인하지 못해 중단했습니다",
        }
        res.why = descriptions.get(reason, reason)
        if visit is not None and not any(v is visit for v in res.visits):
            res.visits.append(visit)
        return True

    original_ask_where = ask_where
    if original_ask_where is not None:
        def bounded_ask_where(*args):
            if stopped():
                return None
            calls = []
            try:
                with capture_provider_calls() as calls, model_deadline(deadline):
                    result = original_ask_where(*args)
            except TimeoutError:
                if not stopped():
                    res.stop_reason = "model_timeout"
                    stopped()
                return None
            finally:
                res.provider_calls.extend(calls)
            return None if stopped() else result
        ask_where = bounded_ask_where

    from .menu_navigation import MenuNavigation
    menus = MenuNavigation(driver,target,capabilities)

    def available(tree):
        result = candidates(tree, capabilities=capabilities)
        if "action.select_option" in capabilities:
            for c in result:
                options = select_options.get(str(c["element_index"]))
                if options is not None:
                    c["options"] = options
                    c["can"] = [a for a in c["can"] if a != "set_value"] + ["select_option"]
        for c in result:
            custom = custom_selects.get(str(c['element_index']))
            if custom is not None:
                c['can'] = [a for a in c['can'] if a not in ('click', 'set_value')]
                c['options'] = custom.get('options', [])
                operation = {'closed': 'open_select', 'open': 'select_custom_option'}.get(custom.get('state'))
                if operation is not None and 'action.' + operation in capabilities:
                    c['can'].append(operation)
        return result + menus.candidates()

    def observe():
        nonlocal select_options, custom_selects
        try:
            result = driver.observe(target, "tree_full", require_window=False)
            if result.get("type") == "observation":
                res.after_tree = result["tree"]
                select_options = result.get("select_options", {})
                custom_selects = result.get("custom_selects", {})
                identity_fn = getattr(driver, "identity", None)
                if identity_fn:
                    identity = identity_fn(target)
                    if res.target_identity is None:
                        res.target_identity = identity
                    elif identity != res.target_identity:
                        res.identity_changed = True
            return result
        except Exception as exc:
            return {"type": "error", "error": "observation_failed", "detail": str(exc)}

    planned_on = None     # the tree the previous batch was planned against
    for _ in range(max_screens):
        if stopped():
            return res
        obs = observe()
        if stopped():
            return res
        if obs["type"] != "observation":
            res.why = f"화면을 볼 수 없습니다: {obs.get('error')}"
            return res
        # What the last action of the previous batch left behind. Only the
        # last one: the screen between two observations is the sum of the
        # whole batch, and only for the final action is that sum its own.
        if planned_on is not None and res.visits and res.visits[-1].actions:
            res.visits[-1].actions[-1]["screen_change"] = screen_change(planned_on, obs["tree"])
        planned_on = obs["tree"]
        try:
            with model_deadline(deadline):
                menus.refresh()
        except Exception as exc:
            res.why = f"메뉴를 확인하지 못했습니다: {exc}"
            return res
        if stopped():return res
        cands = available(obs["tree"])
        if not cands:
            res.why = "화면에 조작할 수 있는 것이 없습니다"
            return res
        key = screen_key(cands)

        # Standing on the same screen is not being stuck: a calculator's
        # keypad never changes, so counting visits ended every arithmetic run
        # at the third question — including ones that had already worked out
        # the answer. Doing the SAME THING on the same screen is being stuck.

        if stopped():
            return res
        calls = []
        try:
            with capture_provider_calls() as calls, model_deadline(deadline):
                answer = ask_here(goal, cands, done_so_far)
        except TimeoutError:
            if not stopped():
                res.stop_reason = "model_timeout"
                stopped()
            return res
        except Exception as exc:
            if stopped():
                return res
            res.why = f"판단을 받지 못했습니다: {exc}"
            return res
        finally:
            res.provider_calls.extend(calls)
        res.asked += 1
        if stopped():
            return res
        if answer is None:
            res.why = "다음에 할 일을 정하지 못했습니다"
            return res
        if answer.get("stop_reason"):
            res.stop_reason = str(answer["stop_reason"])
            details = answer.get("paging") or {}
            res.why = (f"후보 탐색을 중단했습니다: {res.stop_reason} "
                       f"(페이지 {details.get('page', '?')}, "
                       f"입력 {details.get('token_count', '?')}/{details.get('budget', '?')})")
            return res
        if answer.get("done"):
            if spec is not None:
                for check in spec.required:
                    if check.when == "after" and check.kind in ("reports", "unchanged") \
                            and check.name in spec.expected:
                        if stopped():
                            return res
                        value = _witness(driver, obs, target, check.name, cands, ask_where, say)
                        if stopped():
                            return res
                        if value is not None:
                            res.witnessed[check.name] = value
            # The model saying it is done is where the run STOPS, not proof it
            # succeeded (advisor finding). Something has to have been seen: a
            # value read, or an `expect` that showed up. Without that this is a
            # run that ended, and saying so is the honest report.
            res.ok = True
            res.verified = bool(res.said) or any(v.verified for v in res.visits)
            res.why = ("목표에 도달했습니다" if res.verified else
                       "끝났다고 하는데, 확인할 수 있는 결과는 못 봤습니다")
            return res
        acts = answer.get("actions") or []
        if not acts:
            res.why = "이 화면에서 할 수 있는 일이 없습니다"
            return res

        if any(a.get("do") in ("inspect_menu", "menu") for a in acts):
            if len(acts) != 1 or acts[0].get("candidate") not in cands:
                res.why = "관측되지 않은 메뉴 또는 혼합 메뉴 계획입니다"
                return res
            if acts[0]["do"] == "inspect_menu":
                try:
                    with model_deadline(deadline):
                        menus.inspect(acts[0]["candidate"])
                except Exception as exc:
                    res.why = f"메뉴를 조회하지 못했습니다: {exc}"
                    return res
                if stopped():return res
                done_so_far.append("메뉴 목록 조회: " + json.dumps(menus.path,ensure_ascii=False))
                continue

        plan = tuple((a["do"], a["candidate"]["match"],
                      repr(a.get("value")), a.get("option_label"), a.get("direction"), a.get("pages")) for a in acts)
        # Scrolling can change the viewport without changing the AX tree.
        # Permit repeated navigation, bounded by max_screens, and replan each
        # time instead of mistaking the stable tree for a failed action.
        navigating = any(a["do"] in ("scroll", "press_key") for a in acts)
        plan_key = key
        if any(a["do"] == "menu" for a in acts):
            # A repeated command may be intentional after observed content changes.
            # This fingerprint permits replanning; it is not causal completion evidence.
            plan_key += ":" + hashlib.sha256(obs["tree"].encode()).hexdigest()
        if not navigating and seen_plans.get(plan_key) == plan:
            res.why = "같은 화면에서 같은 일을 되풀이하고 있습니다"
            return res
        seen_plans[plan_key] = plan

        # A sheet that was already up when the plan was made is what the plan
        # is about; one that appears partway through is not.
        planned_over = bool(blocking_sheet(obs["tree"]))
        # What was selected when this batch was planned. Only consequential
        # actions carry it: a calculator has no selection, and demanding one
        # would refuse every keypress for a fact that does not exist.
        selected = _selection(driver, target)
        visit = Visit(screen=key, expect=answer.get("expect"))
        event_start = len(res.events)
        pre_satisfied = bool(visit.expect and visit.expect in obs["tree"])
        moved = False
        for n, a in enumerate(acts):
            if stopped(visit):
                return res
            c = a["candidate"]
            if c["disabled"]:
                res.why = f"'{c['label']}' 은 지금 누를 수 없습니다"
                res.visits.append(visit)
                return res
            # The whole batch runs against ONE observation, so every index in
            # it stays the index of the element it was planned for — an AX
            # handle, not a position that other elements can shift. What the
            # batch cannot assume is that the element still MEANS the same
            # thing, so the claim travels with the action and the driver checks
            # it against the element itself. Re-walking the tree between
            # actions would answer the same question and cost 9.8 seconds per
            # action in Mail, measured — 68 seconds for a batch of eight.
            expect = {"label": c["match"], "attribute": c["attr"],
                      "role": c["role"], "enabled": True}
            if a["do"] == "prepare_window":
                expect.pop("enabled", None)
            if not planned_over:
                expect["no_modal"] = True
            elif "expect.modal_scope" in capabilities:
                expect["modal_scope"] = True
            if a["do"] == "read":
                probed = _probe(driver, obs["obs_id"], target,
                                c["element_index"])
                if stopped(visit):
                    return res
                if probed:
                    c = {**c, "line": probed}
            action = {"name": a["do"], "element_index": c["element_index"]}
            if a["do"] == "menu":
                try:
                    with model_deadline(deadline):
                        menus.validate_command(c)
                except Exception as exc:
                    res.why = f"메뉴 실행 대상을 확인하지 못했습니다: {exc}"
                    res.visits.append(visit)
                    return res
                action = {"name":"menu", "path":list(c["menu_path"])}
                for field in ("role", "label", "attribute", "enabled"):
                    expect.pop(field,None)
            if a["do"] == "prepare_window":
                if (not _same_role(c["role"], "Window") or "action.prepare_window" not in capabilities
                        or "prepare_window" not in c.get("can", ())):
                    res.why = "관측된 창의 준비 capability가 없습니다"
                    res.visits.append(visit)
                    return res
            if a["do"] in ("set_value", "select_option"):
                if a["do"] == "select_option" and "action.select_option" not in capabilities:
                    res.why = "이 드라이버에서 선택 상자를 지원하지 않습니다"
                    res.visits.append(visit)
                    return res
                action["value"] = a["value"]
                if a["do"] == "select_option":
                    options = [o for o in c.get("options", [])
                               if o.get("value") == a["value"]]
                    if len(options) != 1 or options[0].get("disabled") or not isinstance(options[0].get("label"), str):
                        res.why = "선택 항목이 없거나 모호하거나 비활성 상태입니다"
                        res.visits.append(visit)
                        return res
                    action["option_label"] = options[0]["label"]
            elif a['do'] in ('open_select', 'select_custom_option'):
                if 'action.' + a['do'] not in capabilities or a['do'] not in c.get('can', ()):
                    res.why = "현재 화면에서 지원하지 않는 선택 상자 동작입니다"
                    res.visits.append(visit)
                    return res
                if a['do'] == 'select_custom_option':
                    label = a.get('option_label')
                    if not isinstance(label, str) or ('value' in a and not isinstance(a['value'], str)):
                        res.why = "선택 항목의 이름과 값이 올바르지 않습니다"
                        res.visits.append(visit)
                        return res
                    options = [o for o in c.get('options', []) if o.get('label') == label
                               and ('value' not in a or o.get('value') == a['value'])]
                    if len(options) != 1 or options[0].get('disabled'):
                        res.why = "선택 항목이 없거나 모호하거나 비활성 상태입니다"
                        res.visits.append(visit)
                        return res
                    action['option_label'] = label
                    if 'value' in a:
                        action['value'] = a['value']
            elif a["do"] == "press_key":
                from .ask import NAVIGATION_KEYS
                if ("action.press_key" not in capabilities or a.get("key") not in NAVIGATION_KEYS
                        or (a.get("key") == "Escape" and "press_key.no_escape" in capabilities)):
                    res.why = "이 드라이버에서 지원하지 않는 탐색 키입니다"
                    res.visits.append(visit)
                    return res
                action["key"] = a["key"]
                action.pop("element_index", None)
                for field in ("role", "label", "attribute", "enabled"):
                    expect.pop(field, None)
            elif a["do"] == "scroll":
                from .ask import MAX_SCROLL_PAGES
                direction, pages = a.get("direction", "down"), a.get("pages", 1)
                if direction not in ("up", "down", "left", "right") or type(pages) is not int or not 1 <= pages <= MAX_SCROLL_PAGES:
                    res.why = "스크롤 방향 또는 페이지 수가 올바르지 않습니다"
                    res.visits.append(visit)
                    return res
                action.update(direction=direction, pages=pages)
            # The control says what the action IS; the envelope says what the
            # request covers; `policy()` says what that needs. One executor for
            # both modes — a recipe's authored contract and this derived one go
            # through the same function, instead of the screen loop keeping a
            # private notion of "risky" that nothing else understands.
            effects, why_eff = safety.effects_of(action, c["line"], target=target)
            what = f"{c['label']} — {a.get('why') or goal}"
            if selected and not effects <= {"read", "write_ephemeral"}:
                # The button being the button says nothing about what it would
                # fall on. A Delete that stays exactly where it is while the
                # selected message changes underneath is the case this catches.
                # Only consequential actions carry it: a calculator has no
                # selection and every keypress would be refused for a fact that
                # does not exist.
                expect["selection"] = selected
            decision = policy(env.contract_for(effects, c["label"]))
            risk = decision.risk
            approved = risk == "preapproved"
            # Handoff and blocked come first because they are about the effect
            # itself, not about scope: "you have to type this yourself" is what
            # a person can act on, where "outside what you asked" tells them
            # nothing about a password field.
            if risk not in ("handoff", "blocked") and not env.admits(effects):
                # Not a risky action — a different request. Nothing the screen
                # says can widen what the person asked for.
                res.why = (f"{what} — 부탁하신 일의 범위를 벗어납니다 "
                           f"({why_eff}: {', '.join(sorted(effects))})")
                res.visits.append(visit)
                return res
            if risk in ("handoff", "blocked"):
                # Not everything above `none` is the same question. A handoff
                # is the person doing it themselves; treating it as one more
                # thing to say yes to made it approvable by the same callback
                # that approves a confirm (advisor finding).
                res.why = (f"{what} — 이 단계는 직접 해주셔야 해요 ({decision.reason})"
                           if risk == "handoff" else
                           f"{what} — {decision.reason}")
                res.visits.append(visit)
                return res
            consequential = not effects <= {"read", "write_ephemeral"}
            if consequential and any(prior.get("do") != "read" for prior in visit.actions):
                # Input earlier in this batch may deliberately change focus or
                # selection. Commit against a newly observed plan, rather than
                # weakening the original selection guard to forgive any drift.
                visit.cut_short = "입력 뒤 커밋 전에 재관찰"
                visit.expect = None
                moved = True
                break

            def check_commit():
                nonlocal obs, cands, c
                if stopped():
                    return res.why
                if spec is None or not required:
                    return "이 작업의 실행 대상과 결과를 확인할 방법이 없어 진행하지 않았습니다"
                fresh = observe()
                if stopped():
                    return res.why
                if fresh.get("type") != "observation":
                    return "실행 전에 화면을 확인하지 못했습니다"
                if selected and _selection(driver, target) != selected:
                    return "고른 것이 바뀌었습니다"
                if not planned_over and blocking_sheet(fresh["tree"]):
                    return "대화상자가 열렸습니다"
                if a["do"] == "menu":
                    try:
                        with model_deadline(deadline):
                            menus.validate_command(c)
                    except Exception:
                        return "실행할 메뉴가 달라졌습니다"
                    obs, cands = fresh, available(fresh["tree"])
                else:
                    idx = reacquire(c, fresh["tree"])
                    if idx is None:
                        return "실행 대상이 달라졌습니다"
                    obs, cands = fresh, available(fresh["tree"])
                    c = next((item for item in cands if item["element_index"] == idx), None)
                    if c is None or c["disabled"]:
                        return "실행 대상이 없거나 비활성 상태입니다"
                    action["element_index"] = idx
                current = {}
                for field_name in required:
                    if stopped():
                        return res.why
                    value = _witness(driver, obs, target, field_name, cands, ask_where, say)
                    if stopped():
                        return res.why
                    if value is not None:
                        current[field_name] = value
                # Replace, never reuse evidence from an earlier commit.
                for field_name in required:
                    res.witnessed.pop(field_name, None)
                res.witnessed.update(current)
                return "; ".join(spec.before_commit(current))

            if consequential:
                problem = check_commit()
                if problem:
                    res.why = problem
                    res.visits.append(visit)
                    return res
            if risk == "confirm":
                description = describe(env.contract_for(effects, c["label"]), decision)
                if required:
                    description += " — " + ", ".join(f"{k}: {res.witnessed[k]}" for k in required)
                accepted = approve is not None and approve(description)
                if stopped(visit):
                    return res
                if not accepted:
                    res.why = f"{what} — 진행하지 않았습니다"
                    res.visits.append(visit)
                    return res
                approved = True
                # The human may have spent minutes answering, or changed the UI.
                if consequential:
                    problem = check_commit()
                    if problem:
                        res.why = problem
                        res.visits.append(visit)
                        return res
            say(f"  {c['label']}")
            if stopped(visit):
                return res
            event = make_event(action, effects, target, obs_id=obs['obs_id'])
            commit_context = None
            if consequential and prepare_commit is not None:
                try:
                    commit_context = prepare_commit(deepcopy(event))
                except Exception:
                    res.why = "커밋 전 호스트 증거 준비에 실패했습니다"
                    res.visits.append(visit)
                    return res
                if stopped(visit):
                    return res
            try:
                # Keep the run budget active through transport preparation,
                # not only while asking the planner. Native click drivers
                # use it to bound the absolute input-start deadline.
                with model_deadline(deadline):
                    r = driver.act(obs["obs_id"], target, action, expect=expect,
                                   risk_class=risk, intent_text=what, approved=approved)
            except Exception as exc:
                r = {"type": "error", "error": "driver_exception", "effect": "unknown", "detail": str(exc)}
            dispatched = r.get("type") == "acted" or (
                r.get("type") == "error" and r.get("error") != "target_changed"
                and r.get("effect", "unknown") != "not_started")
            # A PID/window-addressed click has no delivery acknowledgement.
            # For reversible navigation only, stop this batch and observe its
            # effect. This is not permission to retry uncertain AX actions or
            # to confirm a consequential operation without a receipt.
            needs_observation = (
                r.get("type") == "acted" and r.get("unverified") == "observe_effect"
                and (action.get("name"), r.get("input_route")) in
                    (("click", "window_bound_mouse"), ("prepare_window", "window_prepare"))
                and r.get("reobserve_required") is True
                and not consequential
                and effects <= {"read", "write_ephemeral"})
            if dispatched:
                res.effects |= effects
                event["state"] = ("confirmed" if effects == {"read"} and r.get("type") == "acted"
                                  else "dispatched" if r.get("type") == "acted" and (not r.get("unverified") or needs_observation)
                                  else "unknown")
                res.events.append(event)
                if action.get("name") == "menu" and event["state"] != "confirmed":
                    res.certainty = "unknown"
                if needs_observation:
                    res.certainty = "unknown"
                    event["observation_required"] = True
                if r.get("unverified") and not needs_observation:
                    res.certainty = "unknown"
                    res.why = f"'{c['label']}' 실행 결과를 확인하지 못했습니다: {r['unverified']}"
                    visit.actions.append({**c, "do": a["do"], "result": r["unverified"]})
                    res.visits.append(visit)
                    return res
                if consequential:
                    res.certainty = "unknown"
                    if not stopped() and after_commit is not None:
                        try:
                            proof = after_commit(deepcopy(event), deepcopy(r), commit_context)
                            problem = evidence_error({**event, "commit_evidence": proof},
                                                     spec.expected if spec else {})
                            if problem is None:
                                res.commit_evidence[event["event_id"]] = proof
                                event["state"] = "confirmed"
                                if all(e["state"] == "confirmed" for e in res.events):
                                    res.certainty = "confirmed"
                            else:
                                event["evidence_error"] = problem
                        except Exception:
                            event["evidence_error"] = "host_commit_evidence_failed"
            if stopped():
                visit.actions.append({**c, "do": a["do"],
                                      "result": r.get("error") or r.get("type")})
                stopped(visit)
                return res
            if r.get("error") == "target_changed":
                say(f"  ({r.get('detail')} — '{c['label']}' 부터는 중단하고 "
                    f"다시 봅니다)")
                visit.cut_short = f"{c['label']} 이후 — {r.get('detail')}"
                moved = True
                break
            if r["type"] != "acted":
                # A refusal that names what a PERSON can do is not a failure
                # report, and calling it one wastes the only thing it carries.
                # The driver refuses rather than taking the front or the
                # pointer for itself; this is the layer that can say so.
                stop = ("needs_foreground" if r.get("needs_foreground")
                        else "needs_pointer" if r.get("needs_pointer") else "")
                if stop:
                    res.stop_reason = stop
                    res.why = (
                        f"'{c['label']}' — 이 단계는 {target} 창이 앞에 있어야 해요. "
                        f"앱을 앞으로 가져와 주시면 이어서 할게요"
                        if stop == "needs_foreground" else
                        f"'{c['label']}' — 이 단계는 포인터를 움직여야 해서 하지 않았어요")
                    visit.actions.append({**c, "do": a["do"], "result": stop})
                    res.visits.append(visit)
                    return res
                res.why = f"'{c['label']}' 실행 실패: {r.get('detail') or r.get('error') or r['type']}"
                visit.actions.append({**a, "result": r.get("error")})
                res.visits.append(visit)
                return res
            visit.actions.append({**c, "do": a["do"],
                                  "read_attribute": "Value" if Selector._attr(c["line"], "Value") is not None else c["attr"],
                                  # What else this could have been: the same
                                  # kind of control on the same screen. A step
                                  # that later becomes a decision may only
                                  # choose among what was actually there.
                                  "screen_options": list(dict.fromkeys(
                                      x["label"] for x in cands
                                      if x.get("role") == c.get("role") and x.get("label")))[:40],
                                  # What the planner could SEE when it chose:
                                  # a display's value is often the whole reason
                                  # for a choice, even when it was never read.
                                  "screen_reads": [
                                      {k: x.get(k) for k in ("role", "attr", "match", "container", "label")}
                                      | {"value": str(_value_of(x))[:500]}
                                      # Displays and text fields — a field's
                                      # text is what a later step may copy.
                                      for x in cands if "read" in x.get("can", ())
                                      and ("click" not in x.get("can", ())
                                           or "set_value" in x.get("can", ()))][:12],
                                  **{k: action[k] for k in ("value", "direction", "pages", "key", "option_label") if k in action}})
            if a["do"] == "menu":
                # Recorded so the recipe made from this run can check the
                # command landed, rather than trust that it did.
                seen = _observe_menu_effect(driver, target, obs["tree"], c.get("menu_path"))
                if seen:
                    visit.actions[-1]["effect"] = seen
            if a["do"] == "read":
                # Labels are abbreviated navigation aids, not read results.
                # Preserve the actual value so the planner need not reread it.
                # Named as the planner saw it named: "Beta (read)" could be
                # the popup or the result display, and a planner that cannot
                # tell which it already read reads it again.
                name = (f" [{c['match']}]" if c.get("attr") == "Identifier"
                        and c.get("match") != c.get("label") else "")
                done_so_far.append(f"{c['label']}{name} (read) 읽은 값: " +
                                   json.dumps(_value_of(c), ensure_ascii=False))
            else:
                done_so_far.append(f"{c['label']} ({a['do']})")
            if needs_observation or a["do"] in ("scroll", "press_key", "select_option", "open_select", "select_custom_option", "menu"):
                if needs_observation:
                    # The expectation described the whole original batch,
                    # including actions we deliberately did not execute.
                    visit.expect = None
                visit.cut_short = f"{c['label']} 다음 — 탐색 뒤 재관찰"
                moved = True
                break
            if approved:
                # A batch is not a transaction, and the screen after a
                # consequential step is not the screen the rest was planned on.
                # Stop here and look, rather than carry seven more actions past
                # the one thing someone had to think about (advisor finding).
                visit.cut_short = f"{c['label']} 다음 — 승인된 단계 뒤"
                moved = True
                break
            if a["do"] == "read":
                res.said.append(_value_of(c))

        if stopped(visit):
            return res
        if visit.expect:
            try:
                got = driver.wait_until(target, lambda t: visit.expect in t)
            except Exception as exc:
                got = {"type": "error", "error": "observation_failed", "detail": str(exc)}
            if stopped(visit):
                return res
            visit.verified = got["type"] == "observation"
            if visit.verified and not pre_satisfied:
                for event in res.events[event_start:]:
                    if event["state"] == "dispatched" and set(event["effects"]) <= {"read", "write_ephemeral"}:
                        event["state"] = "confirmed"
                if all(e["state"] == "confirmed" for e in res.events):
                    res.certainty = "confirmed"
            elif not visit.verified:
                say(f"  ({visit.expect!r} 가 나타나지 않았습니다)")

        if moved:
            # Not a failure: the screen advanced, which is often exactly what
            # the batch was for. Look again and ask about what is there now.
            after = observe()
            if stopped(visit):
                return res
            visit.next_screen = (screen_key(available(after["tree"]))
                                 if after["type"] == "observation" else "")
            res.visits.append(visit)
            continue

        after = observe()
        if stopped(visit):
            return res
        visit.next_screen = (screen_key(available(after["tree"]))
                             if after["type"] == "observation" else "")
        res.visits.append(visit)

    res.stop_reason = "screen_limit"
    res.why = f"{max_screens}개 화면을 지나도 끝나지 않았습니다"
    return res
