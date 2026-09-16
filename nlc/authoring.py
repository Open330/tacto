"""Model-based flow authoring — the one path that spends model tokens.

When the agent meets an intent it has no recipe for, it observes the target app
and asks a model to translate "what the user wants" + "the current accessibility
tree" into a concrete NLC Flow (selectors + steps + postcondition). The Flow is
then saved, so every future run of that intent replays with zero tokens.

The model is reached through `muxa ask` (provider-agnostic: claude/codex/…), so
this works headless in the muxa fleet without wiring an API key here.
"""

from __future__ import annotations

import json
import subprocess

from .driver import Driver
from .flow import Flow, Step, Selector, Postcondition

_SYSTEM = """You convert a user's intent into a deterministic UI automation recipe.
You are given the app's accessibility tree (each line: "<index> <Role>  <attrs>").
Output ONLY JSON of this shape, no prose:
{"name": "<short_snake_case>", "target": "<bundle_id>",
 "steps": [
   {"action": "click|set_value|type_text|press_key|scroll",
    "selector": {"role": "Button", "identifier": "<AXIdentifier or null>",
                 "title": "<title or null>", "ancestor": "<container id or null>"},
    "params": {},                 // e.g. {"text":"..."} for type_text, {"key":"Return"}
    "intent_text": "<one short Korean phrase for the user>",
    "risk_class": "none|preapproved|confirm|handoff"},
   ...
 ],
 "postcondition": {"field_identifier": "<id to read>", "equals": "<expected>"}}
Rules: prefer identifier over title. Use ancestor to disambiguate repeated labels.
Sending/deleting/paying = confirm. Passwords = handoff. Reading = none.
Only reference indices/identifiers that appear in the tree."""


def _ask_model(prompt: str, provider: str = "claude", timeout=120) -> str:
    out = subprocess.run(
        ["muxa", "ask", "--agent", provider, "--timeout-secs", str(timeout), prompt],
        capture_output=True, text=True, timeout=timeout + 30)
    if out.returncode != 0:
        raise RuntimeError(f"muxa ask failed: {out.stderr[:300]}")
    return out.stdout.strip()


def _extract_json(text: str) -> dict:
    start = text.find("{")
    depth = 0
    for i in range(start, len(text)):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return json.loads(text[start:i + 1])
    raise ValueError("no JSON object in model output")


def _to_flow(spec: dict) -> Flow:
    steps = []
    for s in spec.get("steps", []):
        sel = None
        if s.get("selector"):
            sd = {k: v for k, v in s["selector"].items() if v is not None}
            sel = Selector(**sd) if sd else None
        steps.append(Step(action=s["action"], selector=sel,
                          params=s.get("params", {}),
                          intent_text=s.get("intent_text", ""),
                          risk_class=s.get("risk_class", "none")))
    if steps and spec.get("postcondition"):
        pc = spec["postcondition"]
        if pc.get("field_identifier"):
            steps[-1].postcondition = Postcondition(
                field=Selector(identifier=pc["field_identifier"]),
                equals=pc.get("equals"))
    return Flow(name=spec["name"], target=spec["target"], steps=steps)


def author(intent: str, driver: Driver, target: str, provider: str = "claude") -> Flow:
    """Observe `target`, ask the model for a recipe, return a validated Flow.

    Caller (Agent) saves it. This is the token-spending path; replay never is.
    """
    obs = driver.observe(target, "tree_full")
    if obs["type"] != "observation":
        raise RuntimeError(f"cannot observe {target}: {obs}")
    prompt = (f"{_SYSTEM}\n\n# 사용자 의도\n{intent}\n\n"
              f"# 대상 앱 bundle id\n{target}\n\n# 접근성 트리\n{obs['tree']}")
    spec = _extract_json(_ask_model(prompt, provider))
    flow = _to_flow(spec)
    # sanity: every selector must resolve uniquely against the tree we showed
    from .flow import TargetMissing, TargetAmbiguous
    for i, st in enumerate(flow.steps):
        if st.selector is not None:
            try:
                st.selector.resolve(obs["tree"])
            except (TargetMissing, TargetAmbiguous) as e:
                raise ValueError(f"authored step {i} selector invalid: {e}")
    return flow
