"""Turning a walked path into a recipe — and refusing to, most of the time.

The screen loop leaves a trail: which screen, what was done on it, which screen
that led to. A path walked successfully is the raw material for a recipe that
would replay for nothing. Converting one is easy; the reason this module is
mostly refusals is that a wrong recipe is **silent** — it replays with no model
call, so nobody is looking, and it keeps being wrong every time.

That asymmetry sets the whole design:

  * A run produces a CANDIDATE, never a recipe. Candidates live apart from the
    store and nothing replays them.
  * Deploying one is a gate with reasons, not a counter. "It worked twice" is
    not a property of the recipe; it is a property of two afternoons.
  * What generalises is decided by PROVENANCE, not by comparing values across
    runs. A literal that came from the user's request is a parameter; one that
    came off the screen is this run's data and has no business in a template.
    Diffing two runs would call 12 and 34 "the variable parts" of any recipe
    that happened to use them twice.
  * The gate's last question is whether a broken version of the recipe would
    still pass it. If it would, the gate is measuring nothing.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

from . import safety
from .contract import ContextBinding, Envelope, policy
from .flow import Capture, Flow, Postcondition, Selector, Step, pin_params


@dataclass
class Candidate:
    """A path that worked once, written down. Not a recipe.

    Kept whole rather than as a Flow so that what it came from stays legible:
    a candidate that cannot be explained is one nobody can judge.
    """
    goal: str
    target: str
    quote: str = ""                         # the user's words it rests on
    screens: list = field(default_factory=list)
    actions: list = field(default_factory=list)
    envelope: Envelope | None = None
    verified: bool = False
    target_identity: str | None = None

    def same_path_as(self, other: "Candidate") -> bool:
        """Two runs did the same job.

        Screens alone are not enough — the same screen accepts many different
        things being done to it. Actions alone are not enough either — the same
        clicks mean different things from different starting points. Both, or
        it is a different path that happens to rhyme.
        """
        return (self.target == other.target
                and self.target_identity == other.target_identity
                and self.screens == other.screens
                and [(a["do"], a["match"]) for a in self.actions]
                    == [(b["do"], b["match"]) for b in other.actions])


def candidate_from(result, target: str, goal: str, quote: str = "",
                   envelope: Envelope | None = None,
                   target_identity: str | None = None) -> Candidate | None:
    """What a finished general run proposes, if it proposes anything.

    A run that did not reach a verified end proposes nothing: the first thing a
    recipe has to be is a path that is known to arrive.
    """
    if not (result.ok and result.verified) or getattr(result, "identity_changed", False):
        return None
    actions = [a for v in result.visits for a in v.actions if a.get("match")]
    # Pure menu discovery is not an executable replay action.
    if not actions or any(a.get("do") == "inspect_menu" for a in actions):
        return None
    return Candidate(goal=goal, target=target, quote=quote,
                     screens=list(result.path), actions=actions,
                     envelope=envelope, verified=True,
                     target_identity=getattr(result, "target_identity", None) or target_identity)


# ------------------------------------------------------------- provenance

_TOKEN = re.compile(r"[0-9]+|[A-Za-z]+|[가-힣]+")


def provenance(literal: str, request: str) -> str:
    """Where a literal in a run came from: the request, or the screen.

    This is the whole of how a recipe generalises. A value the user supplied is
    what changes next time — it is a parameter. A value read off the screen is
    this run's data, and writing it into a template is how a recipe meant to be
    reusable ends up hard-coding one afternoon (which is why write-back was
    withdrawn rather than fixed).

    Comparing two runs to see what differs cannot tell these apart: it would
    call any value that happened to repeat a constant, and any value that
    happened to differ a parameter, neither of which is about the user.
    """
    if not literal:
        return "screen"
    if literal in request:
        return "request"
    # A run of digits typed one key at a time is still the number the user
    # said; the keys are separate literals but the number is one.
    if literal.isdigit() and any(literal in t for t in _TOKEN.findall(request)):
        return "request"
    return "screen"


def parameters(c: Candidate) -> dict:
    """Which of this run's literals came from the request, with where they sit.

    Reported, not applied. Naming a parameter is a claim about what the recipe
    is FOR, and this module does not get to make that claim on its own.
    """
    out: dict = {}
    for i, a in enumerate(c.actions):
        # What the control SAYS, not what it is called internally. A calculator
        # key is identified as `One` and labelled `1`, and it is the 1 that the
        # user said — matching on the identifier found nothing at all.
        lit = str(a.get("value") or a.get("label") or "")
        if provenance(lit, c.goal) == "request":
            out.setdefault(lit, []).append(i)
    return out


# ------------------------------------------------------------------ flow

def to_flow(c: Candidate, name: str, *, menu_postconditions: dict[int, Postcondition] | None = None,
            preparation_postconditions: dict[int, Postcondition] | None = None) -> Flow:
    """The candidate as a concrete Flow — every step contracted and bound.

    Concrete on purpose. A template is a claim that some of this varies, and
    `parameters()` only reports which literals COULD; turning that into
    `{{a}} × {{b}}` is a decision about what the recipe means, which is the
    user's to make and not a byproduct of one successful afternoon.
    """
    env = c.envelope or Envelope.minimal()
    steps = []
    for index, a in enumerate(c.actions):
        if a["do"] == "menu":
            path = a.get("menu_path")
            if (not isinstance(path,list) or not 1 <= len(path) <= 6
                    or any(not isinstance(title,str) or not title or len(title)>200
                           or any(ord(char)<32 or ord(char)==127 for char in title) for title in path)):
                raise ValueError("menu candidate has no observed path")
            params = {"path":list(path)}
            effects,_ = safety.effects_of({"name":"menu", **params},a.get("line", ""),target=c.target)
            steps.append(Step("menu", None, params=params,
                intent_text=a.get("label", " > ".join(path)),
                safety=env.contract_for(effects,a.get("label", " > ".join(path))),
                binding=ContextBinding(bundle_id=c.target_identity or c.target,
                                       operation="menu",params_digest=pin_params(params)),
                postcondition=(menu_postconditions or {}).get(index)))
            continue
        sel = Selector(role=a["role"], **{
            {"Title": "title", "Description": "desc",
             "Value": "value", "Identifier": "identifier"}[a["attr"]]: a["match"]},
            ancestor=a.get("container") or None)
        from .protocol import ACTION_PARAMS
        params = {k: a[k] for k in ACTION_PARAMS[a["do"]] if k in a}
        action = {"name": a["do"], **params}
        effects, why = safety.effects_of(action, a.get("line", ""), target=c.target)
        # A read is what the recipe ANSWERS with, so it becomes a capture — a
        # named value the run reports rather than a click nobody sees. Without
        # this a promoted recipe would replay perfectly and say nothing.
        captures = ([Capture(name="결과", field=sel, attribute=a.get("read_attribute", "Value"))]
                    if a["do"] == "read" else [])
        steps.append(Step(
            a["do"], sel,
            postcondition=(preparation_postconditions or {}).get(index) if a["do"] == "prepare_window" else None,
            params=params,
            intent_text=a.get("label", a["match"]),
            safety=env.contract_for(effects, a.get("label", a["match"])),
            binding=ContextBinding(bundle_id=c.target_identity or c.target, role=a["role"],
                                   identifier=a["match"] if a["attr"] == "Identifier" else None,
                                   title=a["match"] if a["attr"] == "Title" else None,
                                   desc=a["match"] if a["attr"] == "Description" else None,
                                   value=a["match"] if a["attr"] == "Value" else None,
                                   operation=a["do"],
                                   params_digest=pin_params(params),
                                   ancestor=a.get("container") or None),
            captures=captures, answers=bool(captures),
            unverified_ok=(a["do"] == "read")))
    return Flow(name=name, target=c.target, steps=steps, schema=2)


# ------------------------------------------------------------------ gate

def gate(c: Candidate, flow: Flow, request: str) -> list[str]:
    """Every reason this may not be deployed. Empty means it may.

    Reasons rather than a score, because "8/10" does not tell anyone what to
    fix — and because a recipe that replays with no model call has nobody
    watching it, so the list has to be worth trusting on its own.
    """
    why: list[str] = []
    if not c.verified:
        why.append("결과를 확인한 실행이 아닙니다")
    if not flow.steps:
        why.append("단계가 없습니다")
    env = c.envelope or Envelope.minimal()

    verifies = False
    for i, st in enumerate(flow.steps):
        if st.action == "prepare_window":
            post=st.postcondition
            if (st.selector is None or not isinstance(post,Postcondition) or post.field is None
                    or post.equals is None or post.attribute is None or st.unverified_ok
                    or st.safety is None or set(st.safety.effects) != {"write_ephemeral"}):
                why.append(f"{i}번 창 준비에 관측 대상과 정확한 결과 조건이 필요합니다")
        if st.action == "menu":
            post = st.postcondition
            if (not isinstance(post,Postcondition) or post.field is None
                    or post.equals is None or post.attribute is None or st.unverified_ok):
                why.append(f"{i}번 메뉴에 직접 읽을 필드와 정확한 값의 결과 조건이 필요합니다")
            if (st.selector is not None or st.binding is None or st.binding.operation != "menu"
                    or st.binding.params_digest != pin_params(st.params)):
                why.append(f"{i}번 메뉴 경로의 실행 계약이 달라졌습니다")
            recorded = c.actions[i] if i < len(c.actions) else {}
            if recorded.get("do") != "menu" or st.params.get("path") != recorded.get("menu_path"):
                why.append(f"{i}번 메뉴 경로가 관측된 후보와 다릅니다")
            actual,_ = safety.effects_of({"name":"menu", **st.params},
                " > ".join(x for x in st.params.get("path", []) if isinstance(x,str)),target=c.target)
            if st.safety is not None and not actual <= set(st.safety.effects):
                why.append(f"{i}번 메뉴의 실제 효과가 계약에 포함되지 않았습니다")
        if st.action == "press_key" and (st.selector is not None or
                                         (st.postcondition is None and st.expect is None)):
            why.append(f"{i}번 탐색 키의 포커스와 실행 결과를 재생 계약에 고정하지 못했습니다")
        if st.safety is None:
            why.append(f"{i}번 단계에 계약이 없습니다")
            continue
        effects = set(st.safety.effects)
        if not env.admits(effects):
            why.append(f"{i}번 단계가 부탁받은 범위를 벗어납니다 "
                       f"({', '.join(sorted(effects))})")
        risk = policy(st.safety).risk
        if risk in ("handoff", "blocked"):
            why.append(f"{i}번 단계는 무인 재생 대상이 아닙니다 ({risk})")
        if st.binding is None:
            why.append(f"{i}번 단계에 고정된 설명이 없습니다")
        if effects - {"read", "write_ephemeral"}:
            # A general run's witness is not yet a durable selector binding.
            # Keep the candidate reviewable, but never publish an unbound send.
            if st.binding is None or not (st.binding.resource_identity or st.binding.destination_identity):
                why.append(f"{i}번 단계의 실행 전 대상 검증을 레시피에 고정하지 못했습니다")
        if st.postcondition is not None or st.expect or st.captures:
            verifies = True
        # This run's data must not become the template's. A literal that came
        # off the screen is what THIS screen said, and a recipe that replays it
        # is asserting the screen will say it again.
        for parameter in ('value', 'option_label'):
            lit = str(st.params.get(parameter) or "")
            if lit and provenance(lit, request) == "screen":
                why.append(f"{i}번 단계가 이번 실행에서 읽은 값을 그대로 담고 있습니다"
                           f" ({lit[:24]!r})")
    if not verifies:
        why.append("성공했는지 확인하는 단계가 하나도 없습니다")
    return why


# --------------------------------------------------------- counterexamples

def counterexamples(c: Candidate, flow: Flow, request: str) -> list[tuple]:
    """Broken versions of this recipe that the gate had better refuse.

    A gate nobody has tried to get past is a gate of unknown height. These are
    the ways a promotion goes wrong that we know of: a step that no longer
    verifies anything, a contract that claims less than the control does, and a
    value from this run smuggled into the template.
    """
    from dataclasses import replace
    out = []

    # 1. Nothing checks the result any more.
    blind = replace(flow, steps=[replace(s, postcondition=None, expect=None,
                                         captures=[]) for s in flow.steps])
    out.append(("확인 단계를 모두 없앤 판", blind, request))

    # 2. A step that carries what this run happened to read.
    if flow.steps:
        smuggled = list(flow.steps)
        smuggled[0] = replace(smuggled[0],
                              params={"value": "이번 실행에서만 나온 값"})
        out.append(("이번 실행의 값을 담은 판",
                    replace(flow, steps=smuggled), request))

    # 3. An effect the request never covered.
    if flow.steps and flow.steps[0].safety is not None:
        env = c.envelope or Envelope.minimal()
        over = list(flow.steps)
        over[0] = replace(over[0], safety=env.contract_for({"transmit"}, "무언가"))
        out.append(("부탁받지 않은 효과를 가진 판",
                    replace(flow, steps=over), request))
    return out


def deployable(c: Candidate, name: str, request: str, *,
               menu_postconditions: dict[int, Postcondition] | None = None,
               preparation_postconditions: dict[int, Postcondition] | None = None) -> tuple[Flow | None, list]:
    """The whole decision: a recipe, or every reason there is not one.

    The counterexamples run against the same gate. If a knowingly broken
    version passes, the gate measured nothing and nothing is deployed —
    including the version that looked fine.
    """
    flow = to_flow(c, name, menu_postconditions=menu_postconditions, preparation_postconditions=preparation_postconditions)
    why = gate(c, flow, request)
    if why:
        return None, why
    for label, broken, req in counterexamples(c, flow, request):
        if not gate(c, broken, req):
            return None, [f"게이트가 '{label}' 을 통과시킵니다 — "
                          f"검사가 아무것도 재고 있지 않습니다"]
    return flow, []


# ------------------------------------------------------------------ store

class CandidateStore:
    """Where candidates live, which is deliberately not where recipes live.

    Nothing replays from here. A candidate is a proposal with a verdict
    attached, kept so a person can look at what the machine thinks it learned
    before anything starts running unattended.
    """

    def __init__(self, path: str = "flows/_candidates"):
        import os
        self.path = path
        os.makedirs(path, exist_ok=True)

    def _file(self, name: str) -> str:
        import os
        safe = "".join(ch for ch in name if ch.isalnum() or ch in "_-")
        return os.path.join(self.path, f"{safe}.json")

    def record(self, c: Candidate, request: str, name: str) -> dict:
        """File a candidate, merging with one that walked the same path.

        The repeat count is recorded and NOT gated on. That a path worked twice
        says something about two afternoons, not about the recipe — what
        decides deployment is whether its claims are checkable, which does not
        change with the tally (advisor finding).
        """
        import json
        import os
        prior = self.get(name)
        runs = 1
        if prior and Candidate(**{k: v for k, v in prior["candidate"].items()
                                  if k != "envelope"},
                               envelope=c.envelope).same_path_as(c):
            runs = prior.get("runs", 1) + 1
        flow, why = deployable(c, name, request)
        rec = {
            "name": name, "runs": runs, "request": request,
            "candidate": {"goal": c.goal, "target": c.target, "quote": c.quote,
                          "target_identity": c.target_identity,
                          "screens": c.screens, "actions": c.actions,
                          "verified": c.verified},
            "parameters": parameters(c),
            "blocked_by": why,
            "flow": json.loads(flow.to_json()) if flow else None,
        }
        from .storage import write_private
        write_private(self._file(name), json.dumps(rec, ensure_ascii=False, indent=2))
        return rec

    def get(self, name: str) -> dict | None:
        import json
        import os
        p = self._file(name)
        if not os.path.exists(p):
            return None
        try:
            with open(p, encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return None

    def list(self) -> list:
        import os
        return sorted(f[:-5] for f in os.listdir(self.path)
                      if f.endswith(".json"))
