"""Turn a concrete recipe into a parameterized one — without re-learning it.

Authoring produces a recipe for the example it was given: "12 x 34". The
procedure, though, is the same for any pair of numbers, and re-running a model
to learn "9 x 4" would be waste. This module rewrites an existing Flow so that
chosen literals become parameters, which is a text-level transformation on the
IR — no model, no app, no screen.

It is deliberately explicit: the caller says which literal becomes which
parameter. Guessing which parts of a recipe are "the variable bits" is exactly
the kind of inference that should happen once, with a model, at compile time —
not silently on every edit.
"""

from __future__ import annotations

import json
from dataclasses import asdict

from .flow import Flow, DIGITS


def parameterize(flow: Flow, mapping: dict[str, str], *,
                 tables: dict | None = None,
                 derived: dict | None = None) -> Flow:
    """Replace literals with `{{param}}` templates throughout a flow.

    mapping: {literal_or_identifier: param_name}. A step whose selector names a
    mapped identifier becomes a `{{digit[item]}}`-style lookup when a table is
    supplied for it; other occurrences are substituted as plain templates.
    """
    d = json.loads(flow.to_json())
    tables = tables or {}
    derived = derived or {}

    # Reverse a value table (button name -> the character it types) so a
    # concrete step like "click One" can become "click {{digit[item]}}".
    reverse = {tname: {v: k for k, v in tbl.items()} for tname, tbl in tables.items()}

    def decoded(step):
        """If this step types a single character via a table, return (table, char)."""
        sel = step.get("selector") or {}
        ident = sel.get("identifier")
        if isinstance(ident, str):
            for tname, rev in reverse.items():
                if ident in rev:
                    return tname, rev[ident]
        return None, None

    # A multi-character value ("12") was authored as consecutive single-character
    # steps, so match the RUN of steps that spells it and fold it into one
    # for_each — matching a single literal per step would never find it.
    out_steps, i = [], 0
    steps = d["steps"]
    while i < len(steps):
        matched = False
        for literal, param in mapping.items():
            run, j, tname = [], i, None
            for ch in str(literal):
                if j >= len(steps):
                    break
                t, c = decoded(steps[j])
                if c != ch or (tname and t != tname):
                    break
                tname, run, j = t, run + [steps[j]], j + 1
            if len(run) == len(str(literal)) and run:
                folded = json.loads(json.dumps(run[0], ensure_ascii=False))
                folded["selector"]["identifier"] = "{{%s[item]}}" % tname
                folded["for_each"] = "{{%s}}" % param
                folded["intent_text"] = f"{param} 의 {{{{item}}}} 입력"
                # The identifier is an input now, so the binding pins the
                # TEMPLATE, which Flow.bind() renders to the concrete value.
                # Clearing the pin instead would drop the invariant entirely —
                # the contract must still describe whatever actually runs.
                if folded.get("binding"):
                    folded["binding"]["identifier"] = "{{%s[item]}}" % tname
                out_steps.append(folded)
                i, matched = j, True
                break
        if matched:
            continue
        # not part of a parameterized run: substitute any literal appearing
        # verbatim (a typed string, an expected value)
        text = json.dumps(steps[i], ensure_ascii=False)
        for literal, param in mapping.items():
            text = text.replace(f'"{literal}"', '"{{%s}}"' % param)
        # Templates in a binding stay templates: bind() renders them to the
        # concrete value, so the invariant keeps describing the real action.
        out_steps.append(json.loads(text))
        i += 1

    collapsed = out_steps
    d["steps"] = collapsed
    d["params"] = {p: v for v, p in mapping.items()}
    if tables:
        d["tables"] = tables
    if derived:
        d["derived"] = derived
    return Flow.from_json(json.dumps(d, ensure_ascii=False))


def digits_table() -> dict:
    """The common case: a keypad where each digit has its own button."""
    return {"digit": DIGITS}
