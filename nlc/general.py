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
from dataclasses import dataclass, field

import re

from . import safety
from .flow import Selector, TargetAmbiguous, _same_role

# What can be done to a thing depends on what it is, and saying so is what
# stops "read the answer" from being offered as "click the answer". The first
# run of this loop pressed all seven calculator keys from one question and then
# had nothing to read the result from, because a display is not a button.
_CAN = {
    "Button": ("click",), "MenuItem": ("click",), "CheckBox": ("click",),
    "RadioButton": ("click",), "PopUpButton": ("click",), "Link": ("click",),
    "MenuButton": ("click",), "Row": ("click",), "Cell": ("click",),
    "TextField": ("click", "set_value", "read"),
    "TextArea": ("click", "set_value", "read"),
    "StaticText": ("read",), "ScrollArea": ("read",), "Image": ("read",),
}
ACTIONABLE = tuple(_CAN)

MAX_CANDIDATES = 60
# Read-only nodes are the numerous ones — a mail list is hundreds of
# StaticTexts — so they get their own, smaller share of the list.
MAX_READABLE = 20


def candidates(tree: str) -> list[dict]:
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
        bucket = act if "click" in can else read
        cap = MAX_CANDIDATES if bucket is act else MAX_READABLE
        if len(bucket) >= cap:
            continue
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
        bucket.append({"element_index": int(toks[0]), "role": toks[1],
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
            container = name
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
        if len(toks) >= 2 and _same_role(toks[1], "Sheet"):
            got = _pick(line, ("Title", "Description", "Identifier"))
            return got[1] if got else "(이름 없는 대화상자)"
    return ""


_ATTR_FIELD = {"Title": "title", "Description": "desc",
               "Value": "value", "Identifier": "identifier"}


def _line_for(tree: str, idx: int) -> str:
    return next((l for l in tree.splitlines()
                 if re.match(rf"\s*{idx}\s", l)), "")


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
        idx, _ = Selector(role=c["role"], **{field: c["match"]}).resolve(tree)
        return idx
    except TargetAmbiguous:
        # Two things of the same name is not a dead end if they are in
        # different places, and where it was is already part of the candidate
        # the question was asked about.
        if not c.get("container"):
            return None
        try:
            idx, _ = Selector(role=c["role"], ancestor=c["container"],
                              **{field: c["match"]}).resolve(tree)
            return idx
        except LookupError:
            return None
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
                   for c in cands if "click" in c.get("can", ()))
    return hashlib.sha1("|".join(names).encode()).hexdigest()[:12]


def _value_of(c: dict) -> str:
    """What a read actually answers with.

    The label is how a thing is NAMED, which for a display is often its
    identifier — and the calculator writes its running total into that
    (`StandardInputView;value:408`). A person asked for 408.
    """
    v = Selector._attr(c["line"], "Value")
    if v:
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
    asked: int = 0                                  # model calls spent

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
        approve=None, narrate=None) -> GeneralResult:
    """Drive `target` toward `goal`, one question per screen.

    `ask_here(goal, candidates, done_so_far)` returns a checked answer or None.
    `approve(what)` is consulted for any action the classifier does not call
    free; without it such an action stops the run rather than proceeding.
    """
    say = narrate or (lambda _s: None)
    res = GeneralResult()
    done_so_far: list[str] = []
    seen: dict[str, tuple] = {}      # screen -> the plan last run on it

    for _ in range(max_screens):
        obs = driver.observe(target, "tree_full", require_window=False)
        if obs["type"] != "observation":
            res.why = f"화면을 볼 수 없습니다: {obs.get('error')}"
            return res
        cands = candidates(obs["tree"])
        if not cands:
            res.why = "화면에 조작할 수 있는 것이 없습니다"
            return res
        key = screen_key(cands)

        # Standing on the same screen is not being stuck: a calculator's
        # keypad never changes, so counting visits ended every arithmetic run
        # at the third question — including ones that had already worked out
        # the answer. Doing the SAME THING on the same screen is being stuck.

        answer = ask_here(goal, cands, done_so_far)
        res.asked += 1
        if answer is None:
            res.why = "다음에 할 일을 정하지 못했습니다"
            return res
        if answer.get("done"):
            res.ok = True
            res.why = "목표에 도달했습니다"
            return res
        acts = answer.get("actions") or []
        if not acts:
            res.why = "이 화면에서 할 수 있는 일이 없습니다"
            return res

        plan = tuple((a["do"], a["candidate"]["match"]) for a in acts)
        if seen.get(key) == plan:
            res.why = "같은 화면에서 같은 일을 되풀이하고 있습니다"
            return res
        seen[key] = plan

        # A sheet that was already up when the plan was made is what the plan
        # is about; one that appears partway through is not.
        planned_over = bool(blocking_sheet(obs["tree"]))
        visit = Visit(screen=key, expect=answer.get("expect"))
        moved = False
        for n, a in enumerate(acts):
            c = a["candidate"]
            if c["disabled"]:
                res.why = f"'{c['label']}' 은 지금 누를 수 없습니다"
                res.visits.append(visit)
                return res
            if n:
                # Everything after the first action happens on a screen we have
                # not been asked about. Find the element by name; if it is no
                # longer exactly one thing, the rest of this batch is about a
                # screen that no longer exists.
                fresh = driver.observe(target, "tree_full", require_window=False)
                if fresh["type"] != "observation":
                    res.why = f"화면을 볼 수 없습니다: {fresh.get('error')}"
                    res.visits.append(visit)
                    return res
                obs = fresh
                sheet = blocking_sheet(obs["tree"])
                if sheet and not planned_over:
                    say(f"  ('{sheet}' 가 열려서 '{c['label']}' 부터는 중단하고 "
                        f"다시 봅니다)")
                    visit.cut_short = f"{c['label']} 이후 — {sheet}"
                    moved = True
                    break
                where = reacquire(c, obs["tree"])
                if where is None:
                    say(f"  (화면이 바뀌어 '{c['label']}' 부터는 중단하고 "
                        f"다시 봅니다)")
                    visit.cut_short = f"{c['label']} 이후"
                    moved = True
                    break
                # The line too, not just the index. A read takes its answer
                # from the element's line, and the one captured at observation
                # time is what the screen said BEFORE the batch ran — which is
                # how "7 + 8" was read back as 0.
                c = {**c, "element_index": where,
                     "line": _line_for(obs["tree"], where) or c["line"]}
            action = {"name": a["do"], "element_index": c["element_index"]}
            if a["do"] == "set_value":
                action["value"] = a["value"]
            # The control decides the risk, not the model's account of it.
            risk, reason = safety.classify(action, c["line"])
            what = f"{c['label']} — {a.get('why') or goal}"
            approved = False
            if risk != "none":
                if approve is None or not approve(what):
                    res.why = f"{what} — 진행하지 않았습니다"
                    res.visits.append(visit)
                    return res
                approved = True
            say(f"  {c['label']}")
            r = driver.act(obs["obs_id"], target, action, risk_class=risk,
                           intent_text=what, approved=approved)
            if r["type"] != "acted":
                res.why = f"'{c['label']}' 실행 실패: {r.get('error')}"
                visit.actions.append({**a, "result": r.get("error")})
                res.visits.append(visit)
                return res
            visit.actions.append({"do": a["do"], "label": c["label"]})
            done_so_far.append(f"{c['label']} ({a['do']})")
            if a["do"] == "read":
                res.said.append(_value_of(c))

        if moved:
            # Not a failure: the screen advanced, which is often exactly what
            # the batch was for. Look again and ask about what is there now.
            after = driver.observe(target, "tree_full", require_window=False)
            visit.next_screen = (screen_key(candidates(after["tree"]))
                                 if after["type"] == "observation" else "")
            res.visits.append(visit)
            continue

        if visit.expect:
            got = driver.wait_until(target, lambda t: visit.expect in t)
            visit.verified = got["type"] == "observation"
            if not visit.verified:
                say(f"  ({visit.expect!r} 가 나타나지 않았습니다)")
        after = driver.observe(target, "tree_full", require_window=False)
        visit.next_screen = (screen_key(candidates(after["tree"]))
                             if after["type"] == "observation" else "")
        res.visits.append(visit)

    res.why = f"{max_screens}개 화면을 지나도 끝나지 않았습니다"
    return res
