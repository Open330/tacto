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
        """If this step types a single character via a table, return
        (table, char, field): which selector field names the key.

        A key is named either by its identifier (`Two`, looked up in the
        table) or by its label, which is the character itself (`2`). Recipes a
        person wrote use the first; a path an agent walked uses whatever the
        screen called the key, which is usually the second.
        """
        sel = step.get("selector") or {}
        ident = sel.get("identifier")
        if isinstance(ident, str):
            for tname, rev in reverse.items():
                if ident in rev:
                    return tname, rev[ident], "identifier"
        for key in ("desc", "title"):
            label = sel.get(key)
            if isinstance(label, str) and not ident:
                for tname, tbl in tables.items():
                    if label in tbl:
                        return tname, label, key
        return None, None, None

    # A multi-character value ("12") was authored as consecutive single-character
    # steps, so match the RUN of steps that spells it and fold it into one
    # for_each — matching a single literal per step would never find it.
    out_steps, i = [], 0
    steps = d["steps"]
    while i < len(steps):
        matched = False
        for literal, param in mapping.items():
            run, j, tname, named = [], i, None, None
            for ch in str(literal):
                if j >= len(steps):
                    break
                t, c, how = decoded(steps[j])
                if c != ch or (tname and (t, how) != (tname, named)):
                    break
                tname, named, run, j = t, how, run + [steps[j]], j + 1
            if len(run) == len(str(literal)) and run:
                folded = json.loads(json.dumps(run[0], ensure_ascii=False))
                # By identifier the item is looked up in the table; by label
                # the item IS the label.
                key = "{{%s[item]}}" % tname if named == "identifier" else "{{item}}"
                folded["selector"][named] = key
                folded["for_each"] = "{{%s}}" % param
                folded["intent_text"] = f"{param} 의 {{{{item}}}} 입력"
                # The key is an input now, so the binding pins the TEMPLATE,
                # which Flow.bind() renders to the concrete value. Clearing the
                # pin instead would drop the invariant entirely — the contract
                # must still describe whatever actually runs.
                if folded.get("binding"):
                    folded["binding"][named] = key
                out_steps.append(folded)
                i, matched = j, True
                break
        if matched:
            continue
        # not part of a parameterized run: substitute any literal appearing
        # verbatim (a typed string, an expected value)
        text = json.dumps(steps[i], ensure_ascii=False)
        for literal, param in mapping.items():
            text = text.replace(json.dumps(literal,ensure_ascii=False), json.dumps('{{%s}}' % param))
            # External literals may already be stored behind data parameters.
            # An explicit user mapping can parameterize these labels too.
            for stored_name,stored_value in {**d.get('params',{}),**d.get('literal_params',{})}.items():
                if stored_value == literal:
                    text = text.replace(json.dumps('{{%s}}' % stored_name),json.dumps('{{%s}}' % param))
        # Templates in a binding stay templates: bind() renders them to the
        # concrete value, so the invariant keeps describing the real action.
        out_steps.append(json.loads(text))
        i += 1

    collapsed = out_steps
    d["steps"] = collapsed
    new_params = {p: v for v, p in mapping.items()}
    if any(name in d.get('literal_params',{}) or (name in d.get('params',{}) and d['params'][name] != value)
           for name,value in new_params.items()):
        raise ValueError('parameter name already used; choose a different name')
    d["params"] = {**d.get('params',{}), **new_params}
    if tables:
        d["tables"] = {**d.get('tables',{}), **tables}
    if derived:
        d["derived"] = {**d.get('derived',{}), **derived}
    return Flow.from_json(json.dumps(d, ensure_ascii=False))


def digits_table() -> dict:
    """The common case: a keypad where each digit has its own button."""
    return {"digit": DIGITS}
