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

from . import safety
from .flow import Selector, _same_role, _where

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
        label = (Selector._attr(line, "Title")
                 or Selector._attr(line, "Description")
                 or Selector._attr(line, "Value")
                 or Selector._attr(line, "Identifier"))
        if not label:
            continue
        bucket.append({"element_index": int(toks[0]), "role": toks[1],
                       "label": label[:80], "where": _where(lines, i),
                       "can": list(can), "disabled": "[disabled]" in line,
                       "line": line})
    return act + read


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
    seen: dict[str, int] = {}

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

        # The same screen twice means the last batch changed nothing that
        # matters. Asking again gets the same answer and the same nothing.
        seen[key] = seen.get(key, 0) + 1
        if seen[key] > 2:
            res.why = "같은 화면에서 진전이 없습니다"
            return res

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

        visit = Visit(screen=key, expect=answer.get("expect"))
        for a in acts:
            c = a["candidate"]
            if c["disabled"]:
                res.why = f"'{c['label']}' 은 지금 누를 수 없습니다"
                res.visits.append(visit)
                return res
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
            # Each action is derived from the observation we are holding, and
            # acting invalidates it. Re-observe rather than reuse indices that
            # the app has already moved.
            obs2 = driver.observe(target, "tree_full", require_window=False)
            if obs2["type"] == "observation":
                obs = obs2

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
