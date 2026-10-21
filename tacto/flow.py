"""Tacto deterministic replay — token-efficient computer-use emulation.

The insight behind PROTOCOL.md's token/determinism goal: a model is only needed
to *author* a flow once. After that, the flow is a fixed list of steps that
replay with zero model tokens.

A step does not store a volatile `element_index` (those are recomputed on every
observation). It stores a **stable Selector** — role plus identifier/title/
description — that is re-resolved against a fresh tree at replay time, and an
optional **expect** predicate that verifies the action landed (D7) before moving
on. Determinism comes from resolving selectors ourselves instead of asking a
model; token efficiency comes from spending zero tokens on replay and, during
authoring, from AX-tree diffs.
"""

from __future__ import annotations

import json
import re
import time
from copy import deepcopy
from dataclasses import dataclass, field, asdict, replace, fields, is_dataclass
from dataclasses import field as dataclass_field

from .flow_errors import FlowFieldError, step_error
from . import safety
from .contract import (SafetyContract, ContextBinding, policy,
                       validate, describe, ApprovalToken, issue_approval)
from .protocol import Driver, ACTION_PARAMS

# Steps the engine runs itself, with nothing sent to the app. Kept apart from
# ACTION_PARAMS, which is what a driver may be asked to do.
FLOW_ACTIONS = {"decide": frozenset({"question", "choices", "into", "branches", "goal",
                                     "extract_from", "compose"})}
HOST_FLOW_ACTIONS = {'visual': frozenset({'scope', 'max_screens', 'timeout_seconds'})}
# The same rule as a `{{name}}` template, or the answer could never be used.
_NAME = re.compile(r"[A-Za-z_][A-Za-z0-9_]{0,39}")


def visual_problem(params):
    if (not isinstance(params, dict) or set(params) - HOST_FLOW_ACTIONS['visual']
            or not isinstance(params.get('scope'), str) or not _NAME.fullmatch(params['scope'])):
        return 'visual requires a bounded host scope name'
    screens, timeout = params.get('max_screens', 2), params.get('timeout_seconds', 10)
    if type(screens) is not int or not 1 <= screens <= 20:
        return 'visual max_screens must be an integer from 1 to 20'
    import math
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 120:
        return 'visual timeout_seconds must be finite, positive, and at most 120'
    return None


def decide_problem(params: dict) -> str | None:
    """Why a decide step's parameters are unusable, or None."""
    q, choices, into = params.get("question"), params.get("choices"), params.get("into")
    if not isinstance(q, str) or not q.strip() or len(q) > 500:
        return "question must be a sentence"
    # An extraction answers with text rather than a choice — but only text
    # that is literally inside values this run read (`extract_from`), so the
    # answer is still checkable against something the model did not write.
    # Composing answers with new text, which nothing can check. What keeps it
    # safe is where it may go (replay: only into a typed value) and who sees
    # it before anything consequential (replay: `confirm`, with the text).
    if params.get("compose") is not None:
        if params["compose"] is not True:
            return "compose is true or absent"
        if any(k in params for k in ("choices", "branches", "goal", "extract_from")):
            return "a composition has no choices, branches or sources"
        if not isinstance(into, str) or not _NAME.fullmatch(into):
            return "into must be a name"
        return None
    if "extract_from" in params:
        names = params["extract_from"]
        if "choices" in params or "branches" in params or "goal" in params:
            return "an extraction has no choices or branches"
        if (not isinstance(names, list) or not 1 <= len(names) <= 5
                or not all(isinstance(n, str) and _NAME.fullmatch(n) for n in names)):
            return "extract_from must name 1-5 captured values"
        if not isinstance(into, str) or not _NAME.fullmatch(into):
            return "into must be a name"
        return None
    if (not isinstance(choices, list) or not 2 <= len(choices) <= 20
            or not all(isinstance(c, str) and c.strip() and len(c) <= 200 for c in choices)
            or len(set(choices)) != len(choices)):
        return "choices must be 2-20 distinct strings"
    if not isinstance(into, str) or not _NAME.fullmatch(into):
        return "into must be a name"
    # A decision whose answer changes everything after it carries the steps
    # for each answer it has seen; the ones it has not seen are walked.
    if "branches" in params:
        branches, goal = params["branches"], params.get("goal")
        if (not isinstance(branches, dict) or not set(branches) <= set(choices)
                or not all(isinstance(b, list) and b for b in branches.values())):
            return "branches must map known choices to steps"
        if not isinstance(goal, str) or not goal.strip():
            return "a branching decision needs the goal a walk would pursue"
    elif "goal" in params:
        return "goal belongs to a branching decision"
    return None
from .commit_evidence import make_event, evidence_error


def _norm(s: str) -> str:
    """Normalize a string for comparison: drop Unicode format chars (Cf: bidi
    marks etc.) and apply NFC. Applied to BOTH observed and expected values so
    they compare symmetrically (review finding: normalization was one-sided)."""
    import unicodedata
    stripped = "".join(c for c in s if unicodedata.category(c) != "Cf")
    return unicodedata.normalize("NFC", stripped)


def _element_block(tree: str, idx: int) -> str:
    """Return the line for element `idx` plus its subtree (more-indented lines)."""
    lines = tree.splitlines()
    start = next((k for k, l in enumerate(lines) if re.match(rf"\s*{idx}\s", l)), None)
    if start is None:
        return ""
    base = len(lines[start]) - len(lines[start].lstrip())
    out = [lines[start]]
    for l in lines[start + 1:]:
        if not l.strip():
            continue
        if (len(l) - len(l.lstrip())) <= base:
            break
        out.append(l)
    return "\n".join(out)


_TEMPLATE = re.compile(r"\{\{\s*([A-Za-z_][\w]*)\s*(?:\[\s*([A-Za-z_][\w]*)\s*\])?\s*\}\}")


@dataclass(frozen=True)
class _RuntimeReference:
    name: str
    key: str | None = None

    def text(self):
        return "{{" + self.name + ("[" + self.key + "]" if self.key is not None else "") + "}}"


class _RuntimeText(str):
    """A readable string retaining which spans are authored runtime references.

    Parameter/table values are literal spans, even if they contain {{...}}.
    This provenance is private to a bound in-memory Flow, never a new schema.
    """
    def __new__(cls, text, parts=()):
        value = super().__new__(cls, text)
        value.parts = tuple(parts) if parts else (str(text),)
        return value


def literal_text(value):
    """Mark template-looking external text as data before it enters a recipe."""
    if isinstance(value,str) and ('{{' in value or '}}' in value):
        return _RuntimeText(str(value), (str(value),))
    return value


def _render(value, ctx: dict):
    """Substitute authored references once; never parse inserted data as code."""
    if isinstance(value, dict):
        return {k: _render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, ctx) for v in value]
    if not isinstance(value, str):
        return value
    if ctx.get('__literal_data__') is True:
        return literal_text(value)

    def resolve(ref):
        name, key = ref.name, ref.key
        if key is None:
            if name not in ctx:
                raise KeyError(f"파라미터 {name!r} 가 없습니다")
            actual = ctx[name]
            return actual if isinstance(actual, _RuntimeReference) else str(actual)
        table = ctx.get("__tables__", {}).get(name)
        if table is None:
            raise KeyError(f"테이블 {name!r} 가 없습니다")
        actual_key = ctx.get(key, key)
        if isinstance(actual_key, _RuntimeReference):
            return ref
        k = str(actual_key)
        if k not in table:
            raise KeyError(f"테이블 {name!r} 에 {k!r} 항목이 없습니다")
        return str(table[k])

    if isinstance(value, _RuntimeText):
        parts = [resolve(part) if isinstance(part, _RuntimeReference) else part
                 for part in value.parts]
    else:
        matches = list(_TEMPLATE.finditer(value))
        if not matches:
            return value
        parts = []
        end = 0
        for match in matches:
            parts.extend((value[end:match.start()],
                          resolve(_RuntimeReference(match.group(1), match.group(2)))))
            end = match.end()
        parts.append(value[end:])
    text = "".join(part.text() if isinstance(part, _RuntimeReference) else part for part in parts)
    return _RuntimeText(text, parts)


def _arith(expr: str):
    """Evaluate a derived parameter: numbers and + - * / // % ** only.

    Recipes sometimes need a value computed from their inputs (a calculator's
    expected result from its operands). Parsing an AST and walking only the
    arithmetic nodes keeps that declarative — no eval, no names, no calls."""
    import ast
    ops = {ast.Add: lambda a, b: a + b, ast.Sub: lambda a, b: a - b,
           ast.Mult: lambda a, b: a * b, ast.Div: lambda a, b: a / b,
           ast.FloorDiv: lambda a, b: a // b, ast.Mod: lambda a, b: a % b,
           ast.Pow: lambda a, b: a ** b}

    def walk(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.UnaryOp) and isinstance(n.op, (ast.UAdd, ast.USub)):
            v = walk(n.operand)
            return v if isinstance(n.op, ast.UAdd) else -v
        if isinstance(n, ast.BinOp) and type(n.op) in ops:
            left, right = walk(n.left), walk(n.right)
            if isinstance(n.op, ast.Pow) and (abs(right) > 64 or abs(left) > 10**6):
                # A recipe's derived value is an expected result, not a workload:
                # cap exponentiation so a parameter cannot turn it into a DoS.
                raise ValueError("거듭제곱이 너무 큽니다")
            return ops[type(n.op)](left, right)
        raise ValueError(f"허용되지 않은 식: {ast.dump(n)[:60]}")

    v = walk(ast.parse(expr, mode="eval").body)
    return int(v) if isinstance(v, float) and v.is_integer() else v


def _same_role(token: str, want: str) -> bool:
    """Do these name the same role, `AX` prefix or not?

    The tree prints roles stripped ("Button"), the accessibility API spells them
    "AXButton", and a model writes whichever it saw last. The difference carries
    no meaning, so it should not be the reason a selector matches nothing —
    which is exactly how one escalation's answer was thrown away.
    """
    # A line whose element has no label but has secondary actions reads
    # "12 Cell, Secondary Actions: ShowMenu" — the role token is "Cell,". It
    # matched nothing, so click_ancestor ["Cell"] failed on a KakaoTalk chat
    # list (2026-10-10). The comma is separator, not role.
    a, b = token.rstrip(",").lower(), want.rstrip(",").lower()
    return a.removeprefix("ax") == b.removeprefix("ax")


def _alternatives(value) -> set[str]:
    """Accepted spellings of one selector field, normalized and lowercased."""
    items = [value] if isinstance(value, str) else list(value)
    return {_norm(v).lower() for v in items}


class TargetMissing(LookupError):
    """Zero elements matched the selector."""


class TargetAmbiguous(LookupError):
    """More than one element matched — never silently pick one (codex policy)."""


# A tree line that is an element: indent, index, role. Only such a line can be a
# container; a blank, a "… N개 더 있음" marker, or a fragment of a value that
# once carried a line break says nothing about where a match sits.
_NODE_LINE = re.compile(r"\s*\d+\s")


@dataclass
class Selector:
    """Stable way to re-find an element across observations.

    Matched against a tree line, most-specific field first. `identifier` (AX
    identifier, e.g. "Seven") is the strongest key when present; `title`/`desc`
    are the practical fallback for apps that expose no identifiers.

    Resolution requires exactly one match (codex resolver policy): zero raises
    TargetMissing, more than one raises TargetAmbiguous. `ancestor` scopes the
    search so a repeated label in another pane can't create ambiguity, and
    `allow_nth` is the *explicit* opt-in for a known-repeating element — there is
    no silent find()[0]. AXIdentifier is nullable and not guaranteed stable
    across app versions, so identifier is preferred but never assumed present.
    """
    role: str | None = None
    identifier: str | list[str] | None = None   # str, or any-of list for a
                                                 # state-dependent control (e.g.
                                                 # AllClear↔Clear) — still unique
    # Like `identifier`, these take a list when one control answers to more
    # than one name — a localized menu or a weekday heading the app writes in
    # the system's language, not the user's.
    title: str | list[str] | None = None
    desc: str | list[str] | None = None
    # Plenty of elements are labelled only by their value — a save panel's
    # sidebar entries, a Finder sidebar, a calendar's name — and without this
    # they could be read but never pointed at.
    value: str | list[str] | None = None
    ancestor: str | list[str] | None = None   # identifier/title/description of
                                               # a scoping container
    allow_nth: int | None = None      # explicit index into matches; None = require unique
    # Restrict the search to the window the app considers frontmost. Apps hold
    # several windows open — two TextEdit documents expose two identical
    # "First Text View" text areas — and almost every request means the one the
    # person is looking at. Without this, a second open document turns a working
    # recipe into TargetAmbiguous.
    in_focused_window: bool = False
    # After matching, act on the nearest enclosing element with one of these
    # roles. Lists label their rows with an inner text element while the row
    # itself is what responds to a click, so the identifiable part and the
    # actionable part are different elements — a save panel's sidebar and a
    # mail list both work this way.
    click_ancestor: str | list[str] | None = None
    subrole: str | None = None  # fixed macOS role detail, never templated

    def __post_init__(self):
        if self.subrole is not None and (type(self.subrole) is not str
                or re.fullmatch(r'AX[A-Za-z0-9_]{1,80}', self.subrole) is None):
            raise ValueError('subrole must be a fixed AX subrole identifier')

    # Known AX attribute keys as the driver formats them ("Key: value, Key2: …").
    # "Secondary Actions" is here so it TERMINATES the value before it, not
    # because a selector should match on it. Without it the key is unknown, a
    # value runs to end of line, and `Description: First Text View` silently
    # became `First Text View, Secondary Actions: ShowMenu` — every exact
    # selector on that element stopped matching the moment the observation
    # started naming actions.
    _ATTR_KEYS = ("Title", "Description", "Value", "Help", "Identifier",
                  "Placeholder", "Subrole", "Secondary Actions", "Document")

    # Suffixes after ';' that are known to be live state rather than identity.
    # An allowlist, not a general rule: treating everything after a ';' as
    # noise makes 'control;operation:preview' match 'control;operation:delete',
    # which is two different commands compared equal (advisor finding).
    _VOLATILE_SUFFIX = ("value:", "state:", "selected:", "index:", "count:")

    @classmethod
    def _stable_id(cls, v: str) -> str:
        """AXIdentifier can carry volatile state appended after ';' — a real
        device showed 'StandardInputView;value:408', where the identifier
        changes with every keystroke. Only a suffix whose key is known to be
        state is dropped; anything else is part of the name and stays, because
        an unrecognised suffix is as likely to BE the distinction as to hide it.
        """
        head, sep, tail = v.partition(";")
        if not sep:
            return v.strip()
        if tail.strip().lower().startswith(cls._VOLATILE_SUFFIX):
            return head.strip()
        return v.strip()

    @classmethod
    def _attr(cls, line: str, key: str) -> str | None:
        """Extract one attribute's EXACT value from a tree line (not substring).
        Value runs from 'Key: ' to the next ', <KnownKey>: ' or end / [disabled].
        Exact comparison prevents 'Save' matching 'SaveAs' (review finding).
        Unicode bidi/format marks (e.g. the LTR mark macOS Calculator wraps its
        digits in) are stripped so '\\u200e408' compares equal to '408'."""
        if key=='Document':
            from .document_label import document_attribute
            return document_attribute(line)
        from .document_label import window_attributes
        window=window_attributes(line)
        if window is not None:return _norm(window[key]) if key in window else None
        others = "|".join(k for k in cls._ATTR_KEYS if k != key)
        m = re.search(rf"{key}:[ \t]*(.*?)"
                      rf"(?:,\s*(?:{others}):|\s*\[(?:disabled|focused|selected)\]|$)", line)
        if m is None:return None
        return _norm(m.group(1).strip())

    def matches(self, line: str) -> bool:
        if self.subrole and self._attr(line, "Subrole") != self.subrole:
            return False
        if self.role:
            # role token is the 2nd field on the line: "<idx> <Role> ..."
            toks = line.strip().split()
            if len(toks) < 2 or not _same_role(toks[1], self.role):
                return False
        if self.identifier:
            ids = [self.identifier] if isinstance(self.identifier, str) else self.identifier
            val = self._attr(line, "Identifier")   # already normalized
            if val is None:
                return False
            want = {_norm(i).lower() for i in ids}
            want |= {self._stable_id(w) for w in want}
            if val.lower() not in want and self._stable_id(val.lower()) not in want:
                return False
        for key, want in (("Title", self.title), ("Description", self.desc),
                          ("Value", self.value)):
            if not want:
                continue
            val = self._attr(line, key)
            if val is None or val.lower() not in _alternatives(want):
                return False
        return True

    def _names_something(self) -> bool:
        """Does this selector name a particular element, rather than a kind?

        Required for acting: "click a Button" is not an instruction, and a
        selector that only says a role would happily resolve against whichever
        button happened to be alone on screen. Reading a list is the opposite
        case — see `resolve_all`, which asks for a scope instead.
        """
        return any([self.identifier, self.title, self.desc, self.value, self.subrole])

    def _in_scope(self, lines: list[str], line_no: int) -> bool:
        """True if the matched line sits under an ancestor container. The ancestor
        is matched on an exact attribute value (Identifier/Title), not a substring
        of the whole line, so ancestor='main' doesn't match 'Title: domain main
        settings' (review finding)."""
        if self.in_focused_window and not self._under_focused_window(lines, line_no):
            return False
        if not self.ancestor:
            return True
        want = _alternatives(self.ancestor)
        indent = len(lines[line_no]) - len(lines[line_no].lstrip())
        for j in range(line_no - 1, -1, -1):
            prev = lines[j]
            if not _NODE_LINE.match(prev):
                continue
            pind = len(prev) - len(prev.lstrip())
            if pind < indent:
                # Description as well as Identifier/Title: plenty of containers
                # carry no identifier and no title, and are labelled only by
                # what they are — Calendar's day columns, Finder's sidebar.
                for key in ("Identifier", "Title", "Description"):
                    v = self._attr(prev, key)
                    if v is not None and (v.lower() in want
                                          or self._stable_id(v.lower()) in want):
                        return True
                # A document window by the file it shows: two open documents
                # can share a title, never a path (an exact owned file,
                # 2026-10-10). Compared composed, as TextEdit reports NFD.
                document = self._attr(prev, "Document")
                if document is not None:
                    import unicodedata
                    paths=[self.ancestor] if isinstance(self.ancestor,str) else self.ancestor
                    if document in {unicodedata.normalize('NFC',path) for path in paths}:
                        return True
                indent = pind   # climb to the next shallower ancestor
        return False

    @staticmethod
    def _under_focused_window(lines: list[str], line_no: int) -> bool:
        """Is this line inside the window the driver marked `[focused]`?

        Climb to the outermost container above the line; that is the window it
        belongs to. A line at top level is its own window.
        """
        indent = len(lines[line_no]) - len(lines[line_no].lstrip())
        top = lines[line_no]
        for j in range(line_no - 1, -1, -1):
            prev = lines[j]
            if not _NODE_LINE.match(prev):
                continue
            pind = len(prev) - len(prev.lstrip())
            if pind < indent:
                indent, top = pind, prev
                if pind == 0:
                    break
        from .document_label import window_flag
        return window_flag(top,"focused")

    def describe(self) -> str:
        """The selector in the words a person would use.

        The error a beginner sees should say "'Cancel' 이라는 이름의 버튼", not a
        dataclass repr with eight None fields — this product exists for people
        who are uneasy with computers.
        """
        role = {"Button": "버튼", "TextField": "입력칸", "TextArea": "글 상자",
                "CheckBox": "체크박스", "MenuItem": "메뉴 항목",
                "Row": "항목", "Cell": "항목", "StaticText": "항목",
                "Window": "창", "PopUpButton": "선택 목록",
                "Outline": "목록", "List": "목록", "Table": "표"
                }.get(self.role or "", self.role or "항목")
        for label in (self.title, self.value, self.desc, self.identifier):
            if label:
                names = [label] if isinstance(label, str) else list(label)
                return f"'{' 또는 '.join(names)}' {role}"
        return role

    def resolve_all(self, tree: str) -> list[int]:
        """Every element matching this selector, in tree order.

        Acting still demands exactly one match — `resolve` below — but *reading*
        a list is a different job: a folder's contents, the senders of unread
        mail, the rows of a table. Those are plural by nature, and a selector
        that must match once cannot describe them.

        What acting gets from "name one element", reading gets from a scope: a
        role plus a container is a description ("every filename in the file
        list"), while a bare role would mean every TextField on the screen.
        """
        if not (self.ancestor or self.in_focused_window or self._names_something()):
            raise TargetMissing(f"{self} reads a list but names no scope — "
                                "add ancestor or in_focused_window")
        lines = tree.splitlines()
        out = []
        for i, line in enumerate(lines):
            m = re.match(r"\s*(\d+)\s", line)
            if m and self.matches(line) and self._in_scope(lines, i):
                out.append(int(m.group(1)))
        return out

    def resolve(self, tree: str) -> tuple[int, str]:
        """Return (element_index, resolver_trace) for a unique match in a fresh tree."""
        if not self._names_something():
            raise TargetMissing(f"{self} names no element — role alone is not a "
                                "selector for acting")
        lines = tree.splitlines()
        hits = []
        for i, line in enumerate(lines):
            m = re.match(r"\s*(\d+)\s", line)
            if m and self.matches(line) and self._in_scope(lines, i):
                hits.append(int(m.group(1)))
        strat = ("id" if self.identifier else "title" if self.title
                 else "value" if self.value else "desc")
        if self.ancestor:
            strat += f"@{self.ancestor}"
        if not hits:
            raise TargetMissing(f"화면에서 찾지 못했어요 — {self.describe()}")
        if self.allow_nth is not None:
            if not (0 <= self.allow_nth < len(hits)):   # reject negatives too
                raise TargetMissing(f"{self} allow_nth={self.allow_nth} out of "
                                    f"range 0..{len(hits)-1}")
            picked, how = hits[self.allow_nth], f"{strat}[nth={self.allow_nth}]"
            # nth chooses WHICH match; click_ancestor says what to act on. They
            # are independent, and returning early here handed back the leaf
            # StaticText for a selector that asked for its Row (advisor finding).
            if self.click_ancestor:
                return self._climb(lines, picked), how + "^" + str(self.click_ancestor)
            return picked, how
        if len(hits) > 1:
            raise TargetAmbiguous(f"어느 것인지 알 수 없어요 — {self.describe()} "
                                  f"{len(hits)}개가 화면에 있습니다")
        if self.click_ancestor:
            return self._climb(lines, hits[0]), strat + "^" + str(self.click_ancestor)
        return hits[0], strat

    def _climb(self, lines: list[str], idx: int) -> int:
        """Index of the nearest ancestor of `idx` whose role we want to act on."""
        want = _alternatives(self.click_ancestor)
        start = next(k for k, l in enumerate(lines) if re.match(rf"\s*{idx}\s", l))
        indent = len(lines[start]) - len(lines[start].lstrip())
        for j in range(start - 1, -1, -1):
            prev = lines[j]
            if not _NODE_LINE.match(prev):
                continue
            pind = len(prev) - len(prev.lstrip())
            if pind >= indent:
                continue
            indent = pind
            toks = prev.strip().split()
            if len(toks) >= 2 and any(_same_role(toks[1], w) for w in want):
                return int(toks[0])
        raise TargetMissing(f"{self}: no enclosing {self.click_ancestor} above "
                            f"element {idx}")


_GROUPING = ",_ \u00a0\u202f\u2009\u2019"    # digit-grouping marks, NOT the decimal point


def _same_number(expected: str, observed: str) -> bool:
    """Compare two numbers ignoring digit grouping: a calculator shows 5,535 for
    5535. Only grouping marks are stripped — '.' stays a decimal point, so 1.5
    still differs from 15."""
    def canon(v):
        t = "".join(c for c in str(v) if c not in _GROUPING).strip()
        return t
    # Grouping only — NOT numeric equality. Parsing both sides as numbers made
    # an account number '00123' verify against '123', and a postcondition is an
    # assertion about what the screen says, not about a quantity (advisor
    # finding). '5,535' still matches '5535' because those are the same
    # characters once the separator a locale inserted is removed.
    return canon(expected) == canon(observed)


class ReadyHint:
    """When the screen is ready after an unverified step — a hint, not proof.

    A step with no postcondition is followed by a fixed settle (up to one
    second), because nothing says when its effect has landed. A hint names one
    element and the exact value that, in THIS app, means the step has landed;
    when the element shows it, replay moves on. A screen value is not that by
    itself — an app may show the final value before its own state catches up —
    so hints are written for apps whose meaning is known, never learned from
    what a run happened to see. Unmet within the settle, the step settles as
    without a hint. It never counts as verification.
    """

    def __init__(self, field: "Selector", attribute: str, equals: str):
        self.field, self.attribute, self.equals = field, attribute, equals

    def __eq__(self, other):
        return isinstance(other, ReadyHint) and (self.field, self.attribute, self.equals) == \
            (other.field, other.attribute, other.equals)

    def read(self, tree: str | None):
        """The attribute's first value in the field's one element, else None.

        Read over the element's block, as a postcondition is: Calculator keeps
        its displayed Value on a StaticText inside the ScrollArea named.
        """
        if not tree:
            return None
        try:
            index, _ = self.field.resolve(tree)
        except (TargetAmbiguous, TargetMissing):
            return None
        return _block_value(_element_block(tree, index), self.attribute)

    def usable(self, before):
        # Already true before the action would say nothing about the action.
        return before != self.equals

    def met(self, tree: str) -> bool:
        return self.read(tree) == self.equals

    def to_dict(self):
        # Only None and a False flag are defaults; allow_nth=0 names the first.
        return {"field": {k: v for k, v in vars(self.field).items() if v is not None and v is not False},
                "attribute": self.attribute, "equals": self.equals}


READY_HINT_MS = 1000


@dataclass
class Postcondition:
    """Structured verification that the action landed (D7).

    `equals` asserts the field's value EXACTLY (an attribute value equals the
    expected, so '408' does not match a field showing '1408' — review finding).
    `contains` is an explicit substring test. A postcondition with neither is
    unverifiable and checks False rather than silently passing (review finding)."""
    field: Selector | None = None     # None = assert against the whole tree
    equals: str | None = None         # exact attribute-value match
    contains: str | None = None       # explicit substring (field block, or tree)
    # Some actions are verified by something GOING AWAY — a dialog closes, a
    # spinner stops, a row is removed. Without this the only expressible
    # verification was "something is there", so those steps had nothing to check.
    absent: str | None = None
    attribute: str | None = None      # narrow `equals` to one attribute (e.g.
                                      # "Value"); None = any of _ATTR_KEYS
    absent_role: str | None = None    # exact observed role token, never a label substring

    all_of: list["Postcondition"] = dataclass_field(default_factory=list)

    def primary_check(self, tree: str) -> bool:
        """Commit guards still test their original result, independent of extras."""
        return replace(self, all_of=[]).check(tree)

    def _attr_values(self, block: str):
        keys = [self.attribute] if self.attribute else Selector._ATTR_KEYS
        for ln in block.splitlines():
            for k in keys:
                v = Selector._attr(ln, k)
                if v is not None:
                    yield v

    def check(self, tree: str) -> bool:
        if not all(condition.check(tree) for condition in self.all_of):
            return False
        if self.absent_role is not None:
            if not isinstance(self.absent_role,str) or not self.absent_role.strip():
                return False
            for line in tree.splitlines():
                node = re.match(r'^\s*\d+\s+(\S+)(?:\s|$)',line)
                if node and _same_role(node.group(1),self.absent_role):
                    return False
        if self.absent is not None and self.absent in tree:
            return False
        if self.equals is None and self.contains is None:
            return self.absent is not None or self.absent_role is not None or bool(self.all_of)
        want = _norm(self.equals) if self.equals is not None else None
        if self.field is not None:
            try:
                idx, _ = self.field.resolve(tree)
            except LookupError:
                return False
            # Read the field's own line plus its subtree (deeper indent): AX
            # values commonly live on a child StaticText, not the container line.
            block = _element_block(tree, idx)
            if want is not None and not any(
                    v == want or _same_number(want, v)
                    for v in self._attr_values(block)):
                return False
            if self.contains is not None and self.contains not in block:
                return False
            return True
        # whole-tree assertion. `equals` is still EXACT: some element's attribute
        # value equals it (not a substring of the raw tree — '408' must not match
        # '1408' just because the digits appear somewhere; review finding).
        if want is not None and not any(
                v == want or _same_number(want, v)
                for v in self._attr_values(tree)):
            return False
        if self.contains is not None and self.contains not in tree:
            return False
        return True


@dataclass
class Capture:
    """Read one value from the screen into `name` for later steps to use."""
    name: str = "value"
    field: Selector | None = None      # element to read (None = whole tree)
    attribute: str = "Value"           # which attribute holds the value
    sensitive: bool = False            # keep the value out of logs and traces
    # Missing is an answer too. Finder exposes a folder's filenames for
    # Documents and Applications but not for Downloads, so a recipe that reads
    # both a count and the names should report what it could see rather than
    # failing outright.
    optional: bool = False
    # Is "nothing matched" an answer, or a failure to see? For a folder listing
    # it is a failure — Finder shows 21 items and exposes none of their names.
    # For today's calendar it is the answer: the recipe's postcondition already
    # proved the day column is on screen, so an empty one means a free day.
    empty_ok: bool = False
    # Read the LAST matches rather than the first. Finder's column view shows
    # every parent folder alongside the current one, so taking the first few
    # names answered with drives and ancestors — names that are not in the
    # folder at all. The rightmost column is the folder, so reading from the end
    # is never wrong, only sometimes partial.
    tail: bool = False
    # Read every match rather than requiring exactly one. "무슨 파일 있어?" and
    # "안 읽은 메일 누구한테서 왔어?" are questions about a list, and the
    # single-value form could only ever answer them with a count.
    many: bool = False
    limit: int = 20                    # how many, at most, to read back

    def _one(self, tree: str, idx: int) -> str | None:
        for ln in _element_block(tree, idx).splitlines():
            v = Selector._attr(ln, self.attribute)
            if v:
                return v
        return None

    def read(self, tree: str):
        if self.field is None:
            return None
        if self.many:
            found = self.field.resolve_all(tree)
            found = found[-self.limit:] if self.tail else found[:self.limit]
            vals = [self._one(tree, i) for i in found]
            vals = [v for v in vals if v]
            return vals if (vals or self.empty_ok) else None
        try:
            idx, _ = self.field.resolve(tree)
        except LookupError:
            return None
        return self._one(tree, idx)


@dataclass
class Step:
    action: str                       # click | set_value | type_text | press_key | scroll | secondary_action | menu
    selector: Selector | None = None  # None for target-less actions (type_text, press_key, menu)
    params: dict = field(default_factory=dict)   # value/text/key/direction/pages/action/path
    risk_class: str = "none"
    intent_text: str = ""
    expect: str | None = None         # P0 substring postcondition (kept for simple flows)
    postcondition: Postcondition | None = None   # P1 structured postcondition
    require_enabled: bool = True       # precondition: target must not be [disabled]
    is_submit: bool = False            # legacy classifier context
    into_sensitive_field: bool = False # legacy classifier context
    # Authored-once semantics (the model states meaning, not risk) and the facts
    # that judgment rested on. When present these supersede `risk_class` and the
    # legacy keyword classifier entirely.
    safety: "SafetyContract | None" = None
    binding: "ContextBinding | None" = None
    # Synthesized input (type_text/press_key) can be silently ignored by the
    # target app — the event posting "succeeding" proves nothing (advisor
    # finding). Such steps must therefore verify their effect; this flag is the
    # explicit, auditable opt-out for genuinely unverifiable cases.
    unverified_ok: bool = False
    # Repeat this step once per character of the rendered value, binding
    # {{item}} each time — "click each digit of 12" without unrolling the
    # recipe for one specific number.
    for_each: str | None = None
    # Read a value OFF the screen into the run's context, so later steps can use
    # it: "open the invoice, read the total, type it into the form". Without
    # this a recipe can only push data in, never carry it forward — which is
    # most real interactions, not just a calculator.
    # A step may read several things off the screen at once — a folder's item
    # count AND its filenames — so this is a list. One of them being
    # unavailable does not have to sink the other.
    captures: list["Capture"] = field(default_factory=list)
    # Skip this step when the world already satisfies it. Many real commands are
    # toggles, not settings — "make it bold" runs Format > Font > Bold, and
    # running that twice UN-bolds the document. A replay engine whose reruns
    # oscillate is not deterministic in any useful sense, so a step may state the
    # condition it exists to bring about and stand down when it already holds.
    skip_if: "Postcondition | None" = None
    # Kinds of surprise this step is allowed to hand back for judgment instead
    # of failing on. Empty — the default — keeps replay strictly deterministic:
    # a run that may spend tokens has to say so, step by step.
    on_uncertain: list[str] = field(default_factory=list)
    # When the screen is ready after this step, if it has no postcondition
    # (ReadyHint). Older engines ignore the key and settle as before.
    ready_when: "ReadyHint | None" = None
    # Is the value this step verifies the ANSWER to the request, or just proof
    # that the step landed? A calculator's 408 is an answer; the bold checkbox's
    # "1" is not, and reporting it made "굵게 해줘" reply "결과는 1 입니다".
    answers: bool = False


@dataclass
class Flow:
    """A recipe. `params` make it reusable: the digits of a calculation are
    inputs, not part of the procedure, so one `calc_multiply` serves every pair
    of numbers instead of a separate recipe per number."""
    name: str
    target: str
    steps: list[Step] = field(default_factory=list)
    schema: int = 2          # v2 requires a SafetyContract on every acting step
    params: dict = field(default_factory=dict)    # name -> default value
    tables: dict = field(default_factory=dict)    # name -> {key: value} lookups
    derived: dict = field(default_factory=dict)   # name -> arithmetic over params
    # The sentences this recipe answers, with its inputs as `{{name}}`:
    # "계산기에서 {{n}}의 제곱근 구해줘". What lets a request find a recipe that
    # a run promoted, rather than only one somebody wrote a rule for.
    intents: list = field(default_factory=list)
    # Schema 4: observed literal data is not caller-overridable task input.
    literal_params: dict = field(default_factory=dict)

    def bind(self, **values) -> "Flow":
        """Return a concrete, runnable Flow with parameters substituted and
        `for_each` steps expanded. The original stays a template."""
        if set(self.literal_params) & (set(self.params) | set(self.tables) | set(self.derived) | self.runtime_names()):
            raise ValueError('literal data names conflict with authored inputs')
        if set(values) & set(self.literal_params):
            raise ValueError('observed literal data cannot be overridden; choose a task parameter')
        ctx = {**self.params, **self.literal_params, **{k: v for k, v in values.items() if v is not None}}
        missing = [k for k, v in ctx.items() if v is None]
        if missing:
            raise ValueError(f"파라미터가 필요합니다: {', '.join(missing)}")
        for name, expr in self.derived.items():
            ctx[name] = _arith(_render(expr, {**ctx, "__tables__": self.tables}))
        # Names only the run can fill — a value read off the screen, a choice
        # made mid-run — stay templates here and are rendered just before the
        # step that uses them executes.
        for name in self.runtime_names():
            ctx.setdefault(name, _RuntimeReference(name))
        ctx["__tables__"] = self.tables

        # The target is rendered too: which app a recipe drives can itself be
        # an input, as it is for "close the dialog in <app>".
        return Flow(name=self.name, target=_render(self.target, ctx),
                    steps=_bind_steps(self.steps, ctx), schema=self.schema,
                    tables=deepcopy(self.tables))

    def runtime_names(self) -> set:
        """Values produced during a run: captures and decisions, at any depth."""
        steps = every_step(self.steps)
        out = {c.name for st in steps for c in st.captures}
        out |= {st.params.get("into") for st in steps
                if st.action == "decide" and isinstance(st.params.get("into"), str)}
        return out

    def to_json(self, *, compact: bool = False) -> str:
        """Serialize expanded meaning; compact omits only parser defaults.

        Dictionaries such as action params and evidence are opaque: preserve
        their null/false/empty values. Typed optional fields alone lose nulls.
        """
        # Preserve literal/reference meaning using ordinary data parameters.
        # Serialized literals require schema 4 so older runtimes refuse them:
        # external template-looking text becomes a data parameter, not code.
        used=set(self.params) | set(self.literal_params) | set(self.tables) | set(self.derived) | self.runtime_names()
        def reserve(value):
            if isinstance(value,str):
                used.update(m.group(1) for m in _TEMPLATE.finditer(value))
            elif is_dataclass(value):
                for field_info in fields(value):reserve(getattr(value,field_info.name))
            elif isinstance(value,dict):
                used.update(value)
                for item in value.values():reserve(item)
            elif isinstance(value,(list,tuple)):
                for item in value:reserve(item)
        reserve(self)
        literal_params={}
        literal_names={}
        def literal_ref(text):
            if text not in literal_names:
                n=len(literal_names)+1
                while f'_tacto_data_{n}' in used:n+=1
                name=f'_tacto_data_{n}'
                used.add(name);literal_names[text]=name;literal_params[name]=text
            return '{{'+literal_names[text]+'}}'
        def encode_text(value):
            out=[];pending=[]
            def flush():
                if not pending:return
                text=''.join(pending);pending.clear()
                out.append(literal_ref(text) if ('{{' in text or '}}' in text) else text)
            for part in value.parts:
                if isinstance(part,_RuntimeReference):
                    flush();out.append(part.text())
                else:pending.append(part)
            flush()
            return ''.join(out)
        def encode(value):
            if isinstance(value,_RuntimeText):
                return encode_text(value)
            if is_dataclass(value):
                out = {}
                step_defaults = {"params": {}, "risk_class": "none", "intent_text": "",
                    "require_enabled": True, "is_submit": False,
                    "into_sensitive_field": False, "unverified_ok": False,
                    "captures": [], "answers": False, "on_uncertain": []}
                for f in fields(value):
                    item = getattr(value, f.name)
                    if isinstance(value, Step) and f.name == "ready_when":
                        if item is not None:
                            out[f.name] = item.to_dict()
                        continue
                    if isinstance(value, (Selector, ContextBinding)) and f.name == "subrole" and item is None:
                        continue  # unchanged recipes remain readable by older engines
                    if compact and item is None:
                        continue
                    if compact and isinstance(value, Step) and f.name in step_defaults and item == step_defaults[f.name]:
                        continue
                    if compact and isinstance(value, Postcondition) and f.name == "all_of" and not item:
                        continue
                    out[f.name] = encode(item)
                return out
            if isinstance(value, dict):
                return {k: encode(v) for k, v in value.items()}
            if isinstance(value, list):
                return [encode(v) for v in value]
            return value
        d = {"name": self.name, "target": encode(self.target), "schema": max(self.schema,3) if any(
            p is not None and p.all_of for st in every_step(self.steps)
            for p in (st.postcondition, st.skip_if)) else self.schema,
             "steps": [encode(s) for s in self.steps]}
        for k in ("params", "tables", "derived", "intents"):
            if getattr(self, k):
                d[k] = encode(getattr(self,k)) if k=='intents' else getattr(self,k)
        if self.literal_params or literal_params:
            d['literal_params']={**self.literal_params,**literal_params}
            d['schema']=max(d['schema'],4)
        return json.dumps(d, ensure_ascii=False, indent=None if compact else 2,
                          separators=(',', ':') if compact else None)

    @staticmethod
    def from_json(text: str, *, allow_legacy: bool = False, strict_checks: bool = False) -> "Flow":
        # New session input can reject impossible checks before input. Stored
        # recipes retain their existing load/show/repair and skip_if semantics.
        d = json.loads(text)
        if not isinstance(d, dict):
            raise ValueError("Flow must be a JSON object")
        schema = d.get("schema", 2)
        if type(schema) is not int or schema not in (1, 2, 3, 4):
            raise ValueError(f"unsupported Flow schema: {schema!r}")
        if schema == 1 and not allow_legacy:
            raise ValueError("schema 1 requires explicit migration to schema 2")
        if not isinstance(d.get("steps"), list):
            raise ValueError("Flow.steps must be a list")
        if not all(isinstance(d.get(k), str) and d[k] for k in ("name", "target")):
            raise ValueError("Flow requires name and target")
        for key in ("params", "tables", "derived", "literal_params"):
            if key in d and not isinstance(d[key], dict):
                raise FlowFieldError(f"Flow.{key} must be an object", key, 'Use an object; params example: {"amount":"11"}, with {{amount}} in step values.')
        if d.get('literal_params') and schema < 4:
            raise ValueError('literal data requires Flow schema 4')
        for name,value in d.get('literal_params',{}).items():
            if not isinstance(name,str) or not _NAME.fullmatch(name) or not isinstance(value,str):
                raise ValueError('literal data requires named string values')
        for name, table in d.get("tables", {}).items():
            if not isinstance(table, dict):
                raise ValueError(f"Flow.tables.{name} must be an object")
        defaults = d.get("safety_defaults", {})
        if not isinstance(defaults, dict):
            raise ValueError("Flow.safety_defaults must be an object")
        # Defaults compress authored meaning, never consent. Evidence and the
        # commit point remain local to the step that actually commits.
        allowed_defaults = {"effects", "data_classes", "resource", "destination",
                            "reversibility", "unknowns"}
        if set(defaults) - allowed_defaults:
            raise ValueError("Flow.safety_defaults has unsupported fields; "
                             "evidence and commit_point belong on individual steps")
        for key in ("resource", "destination"):
            if key in defaults and not isinstance(defaults[key], dict):
                raise ValueError(f"Flow.safety_defaults.{key} must be an object")
        for key in ("effects", "data_classes", "unknowns"):
            if key in defaults and (not isinstance(defaults[key], list)
                    or not all(isinstance(v, str) for v in defaults[key])):
                raise ValueError(f"Flow.safety_defaults.{key} must be a list of strings")
        if "reversibility" in defaults and not isinstance(defaults["reversibility"], str):
            raise ValueError("Flow.safety_defaults.reversibility must be text")
        selector_defaults = d.get("selector_defaults", {})
        check_defaults = d.get("check_defaults", {})
        if not isinstance(selector_defaults, dict):
            raise ValueError("Flow.selector_defaults must be an object")
        if set(selector_defaults) - set(Selector.__dataclass_fields__):
            raise ValueError("Flow.selector_defaults has unknown selector fields")
        if not isinstance(check_defaults, dict) or set(check_defaults) - {"field", "attribute"}:
            raise ValueError("Flow.check_defaults accepts only field and attribute")
        if "field" in check_defaults and (not isinstance(check_defaults["field"], dict)
                or not check_defaults["field"]
                or set(check_defaults["field"]) - set(Selector.__dataclass_fields__)):
            raise ValueError("Flow.check_defaults.field must be a nonempty selector object")
        if "attribute" in check_defaults and not isinstance(check_defaults["attribute"], str):
            raise ValueError("Flow.check_defaults.attribute must be text")
        def parse(s, path):
            try:
                return parse_step(s, path)
            except FlowFieldError:
                raise
            except (ValueError, TypeError, KeyError) as exc:
                raise step_error(exc, path) from exc

        def parse_step(s, path):
            # Compact click records expand before the existing parser/policy.
            # No target, action meaning, or result predicate is inferred.
            if isinstance(s, dict) and any(k in s for k in ('click', 'equals', 'skip_equals')):
                s = deepcopy(s)
                identifier = s.pop('click', None)
                valid_id = (isinstance(identifier, str) and bool(identifier.strip())
                            or isinstance(identifier, list) and bool(identifier)
                            and all(isinstance(v, str) and v.strip() for v in identifier))
                if not valid_id or 'action' in s or 'selector' in s:
                    raise ValueError('compact click requires an identifier and no action/selector')
                s['action'] = 'click'
                s['selector'] = {'identifier': identifier}
                for alias, key in (('equals', 'postcondition'), ('skip_equals', 'skip_if')):
                    if alias in s:
                        if key in s or not isinstance(s[alias], str) or not check_defaults.get('field'):
                            raise ValueError('compact check requires check_defaults.field and no duplicate check')
                        s[key] = {'equals': s.pop(alias)}
            known = {**ACTION_PARAMS, **FLOW_ACTIONS, **HOST_FLOW_ACTIONS}
            if (not isinstance(s, dict) or not isinstance(s.get("action"), str)
                    or s["action"] not in known):
                raise ValueError("unknown Flow action")
            s = dict(s)
            if isinstance(s.get("selector"), dict) and s["selector"]:
                s["selector"] = {**deepcopy(selector_defaults), **deepcopy(s["selector"])}
            for check in ("postcondition", "skip_if"):
                if isinstance(s.get(check), dict) and s[check]:
                    # An explicitly supplied field replaces the entire shared
                    # field. Defaults never add a predicate to an absent check.
                    s[check] = {**deepcopy(check_defaults), **deepcopy(s[check])}
            if defaults and s["action"] not in FLOW_ACTIONS:
                # Explicit null disables inheritance; replay then refuses the
                # missing contract. Lists replace, resource/destination merge
                # one level, and every step gets an independent copy.
                if "safety" not in s or isinstance(s["safety"], dict):
                    override = s.get("safety", {})
                    merged = deepcopy(defaults)
                    for key, value in override.items():
                        if key in ("resource", "destination") and isinstance(value, dict):
                            merged[key] = {**merged.get(key, {}), **deepcopy(value)}
                        else:
                            merged[key] = deepcopy(value)
                    s = {**s, "safety": merged}
            for key in ("selector", "safety", "binding"):
                if s.get(key) is not None and not isinstance(s[key], dict):
                    raise ValueError(f"step.{key} must be an object")
            sf = s.get("safety") or {}
            for key in ("resource", "destination"):
                if sf.get(key) is not None and not isinstance(sf[key], dict):
                    raise ValueError(f"step.safety.{key} must be an object")
            if "evidence" in sf and (not isinstance(sf["evidence"], list)
                    or not all(isinstance(e, dict) for e in sf["evidence"])):
                raise ValueError("step.safety.evidence must be a list of objects")
            payload = s.get("params", {})
            if not isinstance(payload, dict) or set(payload) - known[s["action"]]:
                raise ValueError("invalid action parameters; names and indices are not parameters")
            if s['action']=='drag':
                from .native_drag import drag_problem
                problem=drag_problem(payload)
                if problem:raise ValueError(problem)
            if s['action']=='open_select':
                from .protocol import native_opening_parameters_problem
                problem=native_opening_parameters_problem(payload)
                if problem:raise ValueError(problem)
            if s['action'] == 'visual' and visual_problem(payload):
                raise ValueError(visual_problem(payload))
            if s["action"] in FLOW_ACTIONS:
                if s.get("selector") or s.get("safety"):
                    raise ValueError("a decide step touches nothing on screen")
                problem = decide_problem(payload)
                if problem:
                    raise ValueError(f"decide: {problem}")
                if "branches" in payload:
                    # Each branch is steps of its own, parsed by the same rules
                    # — a branch is not a way around any of them.
                    payload = {**payload, "branches": {
                        choice: [parse(x, path + '.params.branches[*][' + str(j) + ']') for j, x in enumerate(steps)]
                        for choice, steps in payload["branches"].items()}}
                    # A branch may decide again; each decision needs its own
                    # name, or what one answered would stand for another.
                    names = [st.params.get("into") for st in every_step(
                        [x for b in payload["branches"].values() for x in b])
                        if st.action in FLOW_ACTIONS]
                    if payload["into"] in names or len(set(names)) != len(names):
                        raise ValueError("decide: every decision needs its own name")
            if s["action"] in ("select_option", "select_custom_option") and not isinstance(payload.get("option_label"), str):
                raise ValueError("selection requires option_label for effect classification")
            for key in ("require_enabled", "is_submit", "into_sensitive_field",
                        "unverified_ok", "answers"):
                if key in s and type(s[key]) is not bool:
                    raise ValueError(f"{key} must be boolean")
            sel = _optional_selector(s, "selector", "selector")

            def _post(pc, where, depth=0):
                if pc is None or pc == {}:
                    return None
                # Any other non-object — a string, a list, 0, [] — is a mistake,
                # not "no postcondition" (Codex review of 4a79c11).
                if not isinstance(pc, dict):
                    raise ValueError(f"{where} must be an object, not {type(pc).__name__}")
                if pc.get("field") is not None and not isinstance(pc["field"], dict):
                    raise ValueError(f"{where}.field must be an object, not "
                                     f"{type(pc['field']).__name__}")
                # Checked here, not where it is used: an object under `absent`
                # once reached `in tree` mid-replay and surfaced as a traceback.
                for key in ("equals", "contains", "absent", "attribute", "absent_role"):
                    if pc.get(key) is not None and not isinstance(pc[key], str):
                        raise ValueError(
                            f"{where}.{key} must be text, not "
                            f"{type(pc[key]).__name__}"
                            + (" — absent is text that must not appear in the "
                               "observed tree, e.g. an identifier like "
                               "'GoToWindow'" if key == "absent" else ""))
                group=pc.get('all_of',[])
                if (not isinstance(group,list) or len(group)>32 or depth>8
                        or any(not isinstance(item,dict) or not item for item in group)):
                    raise ValueError(f'{where}.all_of must contain at most 32 nonempty check objects, depth at most 8')
                checks=[_post(item,f'{where}.all_of[{j}]',depth+1) for j,item in enumerate(group)]
                if strict_checks and not checks and all(pc.get(key) is None for key in
                        ('equals','contains','absent','absent_role')):
                    raise FlowFieldError('check requires a predicate', path+'.'+where,
                        'Use equals:"text" (not value), contains, absent, absent_role or nonempty all_of. '
                        'For a field value use field:{"identifier":"observed-id"}, equals:"text", attribute:"Value".')
                pf = _optional_selector(pc, "field", f"{where}.field")
                if pc.get('absent_role') is not None and not pc['absent_role'].strip():
                    raise ValueError(f'{where}.absent_role must name a role')
                return Postcondition(field=pf, equals=pc.get("equals"),
                                     contains=pc.get("contains"),
                                     absent=pc.get("absent"),
                                     absent_role=pc.get("absent_role"),
                                     attribute=pc.get("attribute"), all_of=checks)

            post = _post(s.get("postcondition"), "postcondition")
            skip_if = _post(s.get("skip_if"), "skip_if")
            contract = None
            if s.get("safety"):
                from .contract import Resource, Destination, Evidence
                sf = s["safety"]
                r, dd = sf.get("resource") or {}, sf.get("destination") or {}
                contract = SafetyContract(
                    effects=sf.get("effects", []),
                    data_classes=sf.get("data_classes", ["none"]),
                    resource=Resource(**r) if r else Resource(),
                    destination=Destination(**dd) if dd else Destination(),
                    reversibility=sf.get("reversibility", "unknown"),
                    commit_point=sf.get("commit_point"),
                    evidence=[Evidence(**e) for e in sf.get("evidence", [])],
                    unknowns=sf.get("unknowns", []))
            binding = None
            if s.get("binding"):
                binding = ContextBinding(**s["binding"])
            return Step(action=s["action"], selector=sel,
                              params=payload,
                              risk_class=s.get("risk_class", "none"),
                              intent_text=s.get("intent_text", ""),
                              expect=s.get("expect"), postcondition=post,
                              require_enabled=s.get("require_enabled", True),
                              is_submit=s.get("is_submit", False),
                              into_sensitive_field=s.get("into_sensitive_field", False),
                              safety=contract, binding=binding,
                              unverified_ok=s.get("unverified_ok", False),
                              for_each=s.get("for_each"),
                              captures=_captures_from(s),
                              skip_if=skip_if, answers=s.get("answers", False),
                              on_uncertain=list(s.get("on_uncertain", [])),
                              ready_when=_ready_hint(s.get("ready_when")))
        steps = [parse(s, f"steps[{i}]") for i, s in enumerate(d["steps"])]
        if any(p is not None and p.all_of for st in every_step(steps)
               for p in (st.postcondition, st.skip_if)):
            schema=max(schema,3)
        return Flow(name=d["name"], target=d["target"], steps=steps,
                    schema=schema,
                    params=d.get("params", {}), tables=d.get("tables", {}),
                    derived=d.get("derived", {}), intents=_intents_from(d),
                    literal_params=d.get('literal_params',{}))


# Endings that make a word the sentence's predicate — how the thing is asked
# for (구해줘, 알려줄래, 계산해 주세요), not what is asked for.
_PREDICATE = re.compile(r"(줘|줘요|주세요|줄래|줄래요|줄수있어|해|해요|해라|하기|한다|하다|"
                        r"할래|할까|해줄래|봐|봐요|줄게|줄까)[?.!。]*$")
# Particles that attach to a noun without changing which noun it is.
_PARTICLES = sorted(["에서", "으로", "로", "을", "를", "이", "가", "은", "는", "의", "에",
                     "와", "과", "도", "만", "까지", "부터", "한테", "에게"], key=len, reverse=True)
# Words that soften a request without adding to it.
_FILLERS = {"좀", "한번", "한", "번", "다시", "지금", "그냥", "빨리", "please", "나", "혹시"}
_NUMBER = re.compile(r"^[0-9]+(?:\.[0-9]+)?$")


def _content(sentence: str) -> tuple[list, list, list]:
    """(content words, numbers, each number's particle) of a sentence, in order.

    The predicate at the end and fillers anywhere are dropped, particles are
    cut off nouns, and numbers are kept apart. What is left is WHAT was asked,
    not how politely or in which verb.
    """
    words, numbers, roles = [], [], []
    tokens = [t for t in re.split(r"\s+", sentence.strip()) if t]
    # The verb at the end says WHAT to do as much as the nouns do — "곱해줘"
    # and "나눠줘" differ by nothing else. Only its ending is dropped (줘,
    # 주세요, 줄래…), and only a verb that says nothing beyond "work it out"
    # or "tell me" (구해, 계산해, 알려) is dropped with it.
    verb = None
    if tokens and tokens[-1].strip("?.!。") in _AUXILIARY:
        tokens.pop()                     # "나눠 줘", "계산해 주세요": helper apart
        verb = tokens.pop() if tokens else None
    elif tokens and _PREDICATE.search(tokens[-1]):
        verb = tokens.pop()
    if verb is not None:
        stem = _ENDING.sub("", verb.strip("?.!。"))
        stem = re.sub(r"하기$", "해", stem)
        if stem and stem not in _GENERIC_VERBS:
            tokens.append("~" + stem)    # marked, so it never equals a noun
    for t in tokens:
        t, role = t.strip("?.!,。"), ""
        for p in _PARTICLES:
            if t.endswith(p) and len(t) > len(p):
                t, role = t[: -len(p)], _ROLE.get(p, p)
                break
        if not t or t in _FILLERS:
            continue
        if _NUMBER.match(t) or re.fullmatch(r"\{\{\s*\w+\s*\}\}", t):
            numbers.append(t)
            roles.append(role)
        else:
            words.append(t)
    return words, numbers, roles


_ENDING = re.compile(r"(줘요|주세요|줄래요|줄수있어|줄래|줄게|줄까|줘|봐요|봐|요)$")
_GENERIC_VERBS = {"구해", "계산해", "알려", "해", "말해", "보여", "구하", "계산하"}
# A helper verb written apart from the verb it helps.
_AUXILIARY = {"줘", "줘요", "주세요", "줄래", "줄래요", "줄수있어", "줄게", "줄까", "봐", "봐요"}
# Particles that mark the same role, spelled after a vowel or a consonant.
_ROLE = {"를": "을", "로": "으로", "가": "이", "는": "은", "와": "과"}


def content_match(template: str, params: dict, sentence: str) -> dict | None:
    """The inputs, if `sentence` asks for exactly what `template` does.

    The zero-token middle ground between the literal sentence and a model's
    reading: the same content words — no fewer, and no more, because an extra
    one ("두 배", "말고 제곱") is a different request — and one number for each
    numeric input, in order. Inputs that are not numbers cannot be told from
    the words around them this way, so such recipes are left to the reading.
    """
    want_words, slots, want_roles = _content(template)
    got_words, numbers, got_roles = _content(sentence)
    names = [re.sub(r"[{}\s]", "", s) for s in slots]
    if (not want_words or sorted(want_words) != sorted(got_words)
            or len(numbers) != len(names) or len(set(names)) != len(names)
            # Two numbers are told apart by their particles, not their order:
            # "9를 3으로 나눠" and "3으로 9를 나눠" are the same request.
            or (len(names) > 1 and (sorted(want_roles) != sorted(got_roles)
                                    or len(set(want_roles)) != len(want_roles)))
            or any(not re.fullmatch(r"[0-9.]+", str(params.get(n, ""))) for n in names)
            or any(not _NUMBER.match(x) for x in numbers)):
        return None
    if len(names) == 1:
        return {names[0]: numbers[0]}
    by_role = dict(zip(got_roles, numbers))
    return {name: by_role[role] for name, role in zip(names, want_roles)}


def every_step(steps) -> list:
    """Every step, including those inside decisions' branches, in order."""
    out = []
    for st in steps:
        out.append(st)
        if st.action == "decide":
            for branch in (st.params.get("branches") or {}).values():
                out.extend(every_step(branch))
    return out


def _bind_steps(steps, ctx) -> list:
    """Steps with inputs filled and loops expanded — branches included.

    Branches used to be left as they were, so a learned branch in a recipe
    with inputs would have replayed "{{n}}" to the screen.
    """
    out = []
    for st in steps:
        items = [None]
        if st.for_each:
            items = list(_render(st.for_each, ctx))
        for k, it in enumerate(items):
            # `items_so_far`: what the loop has entered up to and including
            # this item ("1", then "12"), for a hint that names exactly what an
            # app shows after each one.
            c = dict(ctx) if it is None else {**ctx, "item": it,
                                              "items_so_far": "".join(str(x) for x in items[:k + 1])}
            bound = _render_step(st, c)
            if bound.action == "decide" and "branches" in bound.params:
                bound = replace(bound, params={**bound.params, "branches": {
                    k: _bind_steps(v, ctx) for k, v in bound.params["branches"].items()}})
            out.append(bound)
    return out


def _intents_from(d: dict) -> list:
    raw = d.get("intents", [])
    if not isinstance(raw, list) or not all(isinstance(x, str) and x.strip() for x in raw):
        raise ValueError("Flow.intents must be a list of sentences")
    return list(raw)


def intent_pattern(template: str, params: dict):
    """A recipe's intent sentence as a pattern its inputs can be read out of.

    Deliberately literal: the words around an input must be the words said,
    give or take spacing and a closing mark. Anything looser is a guess about
    what someone meant, and a guess that picks a recipe runs it unattended.
    A person who says it differently goes through the reading step, whose
    cleaned-up goal is matched the same way.
    """
    parts = re.split(r"\{\{\s*(\w+)\s*\}\}", template.strip())
    out = []
    for i, part in enumerate(parts):
        if i % 2:
            default = str(params.get(part, ""))
            value = r"[0-9]+(?:\.[0-9]+)?" if re.fullmatch(r"[0-9.]+", default) else r".+?"
            out.append(f"(?P<{part}>{value})")
        else:
            out.append(r"\s*".join(re.escape(w) for w in part.split()) if part.strip() else "")
            if part[:1].isspace():
                out[-1] = r"\s*" + out[-1]
            if part[-1:].isspace():
                out[-1] += r"\s*"
    return re.compile(r"\s*" + "".join(out) + r"\s*[.!?。]*\s*")


def _as_labelled(selector: "Selector", tree: str) -> "Selector":
    """A re-aimed selector, named the way the element is actually named.

    The judge is shown labels — "All Clear" — not which attribute carries
    them, and Calculator carries that one as a Description. A model that wrote
    it as a title named the right element in the wrong slot, and the re-aim
    failed on the very target it had just picked. The other naming slot is
    tried only if the given one finds nothing, and only a unique match counts.
    """
    try:
        selector.resolve(tree)
        return selector
    except LookupError:
        pass
    for have, other in (("title", "desc"), ("desc", "title")):
        label = getattr(selector, have)
        if label and not getattr(selector, other):
            swapped = replace(selector, **{have: None, other: label})
            try:
                swapped.resolve(tree)
                return swapped
            except LookupError:
                pass
    return selector


def _captures_from(step: dict) -> list["Capture"]:
    """Read a step's captures, accepting the older single-`capture` spelling."""
    raw = step.get("captures")
    if raw is None:
        one = step.get("capture")
        raw = [one] if one else []
    out = []
    for c in raw:
        out.append(Capture(
            name=c.get("name", "value"),
            field=_optional_selector(c, "field", "captures.field"),
            attribute=c.get("attribute", "Value"),
            sensitive=bool(c.get("sensitive")),
            many=bool(c.get("many")),
            limit=int(c.get("limit", 20)),
            optional=bool(c.get("optional")),
            empty_ok=bool(c.get("empty_ok")),
            tail=bool(c.get("tail"))))
    return out


def _nearby(selector: "Selector", tree: str, limit: int = 8) -> str:
    """What IS on screen where the selector expected something.

    "matched nothing" leaves a person with nowhere to go. The save panel offers
    only recent and favourite locations, so asking it for Documents fails — and
    the useful reply is the list it does offer, not the fact of the failure.
    """
    if not selector or not selector.role:
        return ""
    names = []
    for line in tree.splitlines():
        toks = line.strip().split()
        if len(toks) < 2 or not _same_role(toks[1], selector.role):
            continue
        for key in ("Title", "Description", "Value", "Identifier"):
            v = Selector._attr(line, key)
            if v:
                names.append(v)
                break
        if len(names) >= limit:
            break
    return f"화면에 있는 것: {', '.join(names)}" if names else ""


def _where(lines: list[str], i: int) -> str:
    """The enclosing window and nearest named container of line `i`.

    Two Save buttons in two windows described themselves as ["Save", "Save"],
    and no answer to "which one did you mean" can be given from that (advisor
    finding). A candidate is only a choice if it can be told apart.
    """
    indent = len(lines[i]) - len(lines[i].lstrip())
    window = container = ""
    for j in range(i - 1, -1, -1):
        prev = lines[j]
        if not _NODE_LINE.match(prev):
            continue
        pind = len(prev) - len(prev.lstrip())
        if pind >= indent:
            continue
        indent = pind
        toks = prev.strip().split()
        role = toks[1].rstrip(",") if len(toks) >= 2 else ""
        name = (Selector._attr(prev, "Identifier")
                or Selector._attr(prev, "Title")
                or Selector._attr(prev, "Description") or "")
        if _same_role(role, "Window") and not window:
            window = name or "(제목 없음)"
            break
        if name and not container:
            container = name
    parts = []
    if container:
        parts.append(f"안: {container}")
    if window:
        parts.append(f"창: {window}")
    return " / ".join(parts)


def _candidates(selector: "Selector", tree: str, limit: int = 12) -> list[str]:
    """The things on screen that COULD have been meant, each with where it is.

    An escalation needs choices, not prose — and a choice that cannot be told
    from another choice is not one. Scoped the same way the selector was: if it
    asked inside a container or the front window, offering what is outside that
    scope offers something the selector never could have matched.
    """
    # With no selector to go on — a menu step has none — the useful answer is
    # not "every label on screen" but "what can actually be pressed right now".
    # That is what tells a reader a dialog is in the way.
    role = selector.role if selector is not None and selector.role else "Button"
    lines = tree.splitlines()
    out = []
    for i, line in enumerate(lines):
        toks = line.strip().split()
        if len(toks) < 2 or not _same_role(toks[1], role):
            continue
        if role == "Button" and "[disabled]" in line:
            continue
        if selector is not None:
            try:
                if not selector._in_scope(lines, i):
                    continue
            except Exception:
                pass
        for key in ("Title", "Description", "Value", "Identifier"):
            v = Selector._attr(line, key)
            if v:
                where = _where(lines, i)
                out.append(f"{v} ({where})"[:120] if where else v[:120])
                break
        if len(out) >= limit:
            break
    return out


def _run_fix(driver, flow, recovery: str, narrate, *, result=None, parent_step=None) -> bool:
    """Run one of OUR recovery recipes, before retrying the step that failed.

    Named, not described: the model picks a key of RECOVERIES, and what that key
    does was written and verified here. Schema 2 regardless of the parent, so a
    legacy recipe cannot lend a recovery its looser contract rules.
    """
    build = RECOVERIES.get(recovery)
    if build is None:
        return False
    patch = build(flow)
    patch = replace(patch, name=f"{flow.name}:{recovery}", schema=2)
    child_narrate = (lambda text: narrate(f"(복구 {recovery}) {text}")) if narrate else None
    out = replay(driver, patch, narrate=child_narrate)
    uncertain = any(event.get('state') == 'unknown' for event in out.events)
    if result is not None:
        # A recovery is an actual child execution, including when it fails
        # after dispatch. Preserve its IDs and evidence rather than returning
        # only a boolean that loses effects and the foreground-input latch.
        for secret in out._secrets:
            result.protect(secret)
        result.record(status='ok' if out.ok and not uncertain else 'error',
                      step=parent_step, stage='recovery', recovery=recovery,
                      **({'error': 'recovery_unverified'} if uncertain else {}),
                      detail={'ok': out.ok, 'log': [
                          {**{k:v for k,v in entry.items() if k != 'step'},
                           'recovery_step': entry.get('step')} for entry in out.log[:64]],
                              'log_total': len(out.log), 'log_truncated': len(out.log) > 64})
        result.events.extend({**event, 'recovery': recovery, 'parent_step': parent_step}
                             for event in out.events)
        result.commit_evidence.update(out.commit_evidence)
        result.visual_used |= out.visual_used
        result.visual_history.extend(out.visual_history)
        result.provider_calls.extend(out.provider_calls)
        result.preparation_diagnostics.extend(out.preparation_diagnostics[
            :max(0, 64 - len(result.preparation_diagnostics))])
        result.ok = result.ok and out.ok
    if not out.ok and narrate:
        bad = next((e for e in out.log if e.get("status") in ("error", "gated")), {})
        why = bad.get("error") or bad.get("gate") or "?"
        narrate(f"  (고치기가 실패했습니다: {why} {str(bad.get('detail'))[:90]})")
    return out.ok and not uncertain


def _payload_digest(params: dict) -> str:
    import hashlib, json as _json
    payload = {k: v for k, v in params.items() if k != "element_index"}
    return hashlib.sha256(
        _json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()[:32]


def pin_params(params: dict) -> str:
    """Pin a step's parameters into its ContextBinding while authoring it.

    Use this when the step carries arguments whose change would change what the
    action MEANS — a menu path most of all, since "File > Save" and
    "File > Move to Trash" are the same `menu` operation on the same app. A
    parameterized step's pin is recomputed per run against the rendered values,
    so this records the template; an unparameterized one keeps exactly this.
    """
    return _payload_digest(params)


def _render_step(st: "Step", ctx: dict) -> "Step":
    """A copy of `st` with every template in its selector/params/verification
    resolved against `ctx`."""
    # `replace` rather than a field-by-field rebuild: listing the fields meant
    # every new one had to be remembered here too, and the first one added
    # (in_focused_window) was silently dropped on every bind.
    def render_sel(s):
        return None if s is None else replace(
            s, identifier=_render(s.identifier, ctx), title=_render(s.title, ctx),
            desc=_render(s.desc, ctx), value=_render(s.value, ctx),
            ancestor=_render(s.ancestor, ctx))   # click_ancestor is a role, never templated

    sel = render_sel(st.selector)
    def render_post(p):
        if p is None:
            return None
        return Postcondition(field=render_sel(p.field), equals=_render(p.equals, ctx),
                             contains=_render(p.contains, ctx),
                             absent=_render(p.absent, ctx), attribute=p.attribute,
                             absent_role=_render(p.absent_role,ctx),
                             all_of=[render_post(child) for child in p.all_of])

    post = render_post(st.postcondition)
    # The contract and its binding describe THIS concrete action, so they are
    # rendered too. Leaving them templated would let a confirmation and an
    # ApprovalToken bind to "{{recipient}}" while the action goes to a real
    # address (advisor finding) — the description the user approves must be the
    # thing that runs.
    # Rendered with `replace`, so a field added later is carried through instead
    # of quietly dropped. Both of these were rebuilt field-by-field before, and
    # both lost the first field added to them — the binding losing
    # resource_identity_from turned every witnessed pin into "unverifiable".
    safety = st.safety
    if safety is not None:
        safety = replace(
            safety,
            # copied, not shared: `replace` would hand the rendered step the
            # template's own lists
            effects=list(safety.effects), data_classes=list(safety.data_classes),
            evidence=list(safety.evidence), unknowns=list(safety.unknowns),
            resource=replace(safety.resource,
                             object_type=_render(safety.resource.object_type, ctx),
                             object_identity=_render(safety.resource.object_identity, ctx)),
            destination=replace(safety.destination,
                                identity=_render(safety.destination.identity, ctx),
                                account=_render(safety.destination.account, ctx)))
    binding = st.binding
    if binding is not None:
        binding = replace(
            binding,
            # a templated pin becomes the concrete value it renders to
            identifier=_render(binding.identifier, ctx),
            title=_render(binding.title, ctx), desc=_render(binding.desc, ctx),
            value=_render(binding.value, ctx),
            ancestor=_render(binding.ancestor, ctx),
            # Recomputed from the rendered payload: a parameterized type_text
            # changes what is typed, so a copied digest would invalidate every
            # concrete run (advisor finding).
            params_digest=(_payload_digest(_render(st.params, ctx))
                           if binding.params_digest is not None else None),
            resource_identity=_render(binding.resource_identity, ctx),
            destination_identity=_render(binding.destination_identity, ctx),
            # The witness that re-reads an identity may name it too (textedit_save:
            # the focused window titled {{name}}), so it is rendered as well.
            resource_identity_from=_render(binding.resource_identity_from, ctx),
            destination_identity_from=_render(binding.destination_identity_from, ctx))
    return Step(action=st.action, selector=sel, params=_render(st.params, ctx),
                risk_class=st.risk_class, intent_text=_render(st.intent_text, ctx),
                expect=_render(st.expect, ctx), postcondition=post,
                require_enabled=st.require_enabled, is_submit=st.is_submit,
                into_sensitive_field=st.into_sensitive_field,
                safety=safety, binding=binding,
                unverified_ok=st.unverified_ok,
                # Captures carry selectors too, and those were the one place
                # templates were left unrendered — a capture scoped to
                # "{{weekday_en}}" matched nothing and read as "no events".
                captures=[replace(c, field=render_sel(c.field))
                          for c in st.captures],
                skip_if=render_post(st.skip_if), answers=st.answers,
                on_uncertain=list(st.on_uncertain),
                ready_when=None if st.ready_when is None else ReadyHint(
                    render_sel(st.ready_when.field), st.ready_when.attribute,
                    _render(st.ready_when.equals, ctx)))


def literal_step(step):
    """A concrete observed step: all rendered text fields are external data."""
    return _render_step(step, {'__literal_data__':True})


def _receipt_public_codes() -> frozenset:
    """Every receipt's closed refusal codes — what replay may show of one."""
    import importlib
    codes = set()
    for module in ("existing_file_receipt", "transcript_receipt", "delivery_receipt", "producer_file_receipt", "receipt_clock"):
        try:
            codes |= set(getattr(importlib.import_module(f".{module}", __package__),
                                 "PUBLIC_CODES", ()))
        except ImportError:
            continue
    return frozenset(codes)


def _live_binding(driver, flow, step, action, target_line,
                  tree: str = "") -> ContextBinding:
    """Re-derive, from the live world, the facts the contract was judged on.

    Only fields we can actually re-observe are filled. Copying a recorded value
    into the "live" side would make that field incapable of ever mismatching —
    which is how resource/destination invariants were silently passing before
    (advisor finding). A field we cannot re-derive is left None and reported, so
    an unverifiable invariant is visible rather than fake.
    """
    import hashlib, json as _json
    rec = step.binding
    # Same payload the recorded digest was taken over (_payload_digest): the
    # step's parameters, without the resolved index and without the action name.
    # The name is pinned separately as `operation`; counting it here too made
    # every params_digest mismatch on principle — invisible while no recipe used
    # the field, and an instant contract_invalidated for the first one that did.
    payload = {k: v for k, v in action.items()
               if k not in ("element_index", "name")}
    live = ContextBinding()

    if rec.bundle_id is not None:
        live.bundle_id = (driver.identity(flow.target)
                          if hasattr(driver, "identity") else flow.target)
    if step.selector is not None and (rec.subrole is not None or step.selector.subrole is not None):
        live.subrole = Selector._attr(target_line, "Subrole")
    if rec.role is not None and step.selector is not None:
        live.role = step.selector.role
    if rec.identifier is not None and step.selector is not None:
        live.identifier = _first_id(step.selector)
    for key in ("title", "desc", "value"):
        if getattr(rec, key) is not None and step.selector is not None:
            setattr(live, key, getattr(step.selector, key))
    if rec.ancestor is not None and step.selector is not None:
        live.ancestor = step.selector.ancestor
    if rec.operation is not None:
        live.operation = step.action
    if rec.params_digest is not None:
        live.params_digest = hashlib.sha256(
            _json.dumps(payload, sort_keys=True, default=str).encode()
        ).hexdigest()[:32]
    if rec.label_fingerprint is not None:
        live.label_fingerprint = hashlib.sha256(target_line.encode()).hexdigest()[:16]

    # An identity the recipe said where to find is re-read from the screen, not
    # copied across. This is what lets consent be about WHICH document is being
    # saved rather than only which button is being pressed: the name field still
    # has to say what the person agreed to, right before the click.
    for fname in ("resource_identity", "destination_identity"):
        spec = getattr(rec, f"{fname}_from", None)
        if getattr(rec, fname) is None or not spec or not tree:
            continue
        spec = dict(spec)
        witness = Capture(field=Selector(**{k: v for k, v in spec.items()
                                            if k != "attribute"}),
                          attribute=spec.get("attribute", "Value"))
        setattr(live, fname, witness.read(tree))

    # Fields with no live source. Carrying the recorded value forward would
    # assert they are unchanged when nothing checked; carry None and let
    # validate() flag them as unverifiable instead.
    for fname in ("team_id", "resource_identity", "selection_count",
                  "destination_identity"):
        if getattr(rec, fname) is not None and getattr(live, fname) is None:
            setattr(live, fname, None)
    return live


def _first_id(sel) -> str | None:
    i = getattr(sel, "identifier", None)
    return i[0] if isinstance(i, list) and i else i


def _approved(approvals, i, step, action) -> bool:
    """Accept either a bound ApprovalToken (preferred) or a legacy {index: True}.

    A token is checked against the step's binding and contract, so consent given
    for one described action cannot silently authorize a different one."""
    if not approvals:
        return False
    tok = approvals.get(i, approvals.get(str(i)))
    if isinstance(tok, ApprovalToken):
        if step.safety is None or step.binding is None:
            return False
        ok, _why = tok.authorizes(step.binding, step.safety)
        if ok:
            tok.spend()
        return ok
    # A bare True approves "step 3" — it would still read as approved if step 3
    # came to mean something else. Consent must be bound to the described
    # action, so a raw flag is only honoured for contract-less v1 recipes
    # (advisor finding: the token was implemented but never required).
    if step.safety is not None:
        # A contract-bearing step needs consent bound to the described action.
        # Requiring a binding too left a gap exactly where authoring lands:
        # contract present, binding absent, bare True still approving.
        return False
    return bool(tok)


@dataclass
class Uncertainty:
    """What the recipe could not determine, and what the screen showed instead.

    This is the whole context an escalation gets. Deliberately small: a diff and
    a list of what is actually there, not the whole tree. Looking again has to
    stay cheap, or "ask when the screen surprises you" becomes "pay every run".
    """
    kind: str            # target_ambiguous | target_missing | action_failed | postcondition_timeout
    step: int
    intent: str          # what the step was trying to do, in the user's words
    detail: str          # the engine's own account of the failure
    # Whether the action may ALREADY have happened. A postcondition that is
    # false at the deadline does not prove the effect did not land — the send
    # can complete while the screen is still catching up. So the two questions
    # an escalation can be about are kept apart: "what should I act on?" is
    # asked before anything ran, "did it already happen?" after (advisor
    # finding). Only the first may re-aim, and only the second forbids a re-run.
    effect: str = "not_started"      # not_started | unknown
    options: list[str] = field(default_factory=list)   # what IS on screen
    tree_diff: str = ""  # what changed since the previous observation


@dataclass
class Resolution:
    """The answer to an Uncertainty, expressed as IR rather than as prose.

    Deliberately narrow. An earlier version let the answer be arbitrary steps
    with their own contracts, and the safety argument for that — "the nested
    replay gates them" — was wrong: the gate computes `policy(what the model
    said this step means)`, so a step that clicks Send while declaring
    `effects: ["read"]` passes (advisor finding). Nothing in the engine can
    check a semantic claim against what the control actually does.

    So a recovery is chosen from a vocabulary this code owns, by name. The model
    decides WHICH recovery is called for; what each one means is not its to
    state.
    """
    kind: str                       # use_selector | recover | give_up
    selector: "Selector | None" = None
    recovery: str = ""              # a key of RECOVERIES
    reason: str = ""


def _recovery_dismiss_dialog(flow: "Flow") -> "Flow":
    from .library import close_dialog
    return close_dialog().bind(app=flow.target)


# Recoveries are code, not model output. Each is a whole recipe with its own
# contract, written and verified here — which is the difference between "the
# model may clear a dialog out of the way" and "the model may run whatever it
# describes as harmless".
RECOVERIES = {
    "dismiss_dialog": _recovery_dismiss_dialog,
}


class _Budget:
    """How many escalations this run may spend. Zero means "never ask".

    Spent BEFORE the model is called, never after, so a timeout, unparseable
    answer or `give_up` costs the same as a useful one — otherwise a provider
    that always fails is a provider that asks forever (advisor finding).

    Capped per (step, kind) as well as per run: without that, one step that
    keeps getting the same wrong selector back can burn the whole budget
    oscillating, and the run still ends in the same failure it started with.
    """

    def __init__(self, calls: int = 0, per_step: int = 1):
        self.left = calls
        self.spent = 0
        self.per_step = per_step
        self._asked: dict = {}

    def take(self, step: int = -1, kind: str = "") -> bool:
        if self.left <= 0:
            return False
        key = (step, kind)
        if self._asked.get(key, 0) >= self.per_step:
            return False
        self._asked[key] = self._asked.get(key, 0) + 1
        self.left -= 1
        self.spent += 1
        return True


def _escalate(escalate, budget: "_Budget", step: "Step", why: Uncertainty,
              narrate) -> "Resolution | None":
    """Hand one surprise back to a caller that can look and decide.

    Refused unless the step said it expects this kind of surprise. A recipe that
    escalates on anything is a recipe that never fails, which is worse than one
    that fails honestly — the point is to name where judgment is genuinely
    needed, not to paper over every mistake.
    """
    if escalate is None or why.kind not in (step.on_uncertain or ()):
        return None
    if not budget.take(why.step, why.kind):
        return None
    if narrate:
        narrate(f"  ? 예상과 달라 판단이 필요합니다: {why.detail[:80]}")
    try:
        fix = escalate(why)
    except Exception:
        return None
    if fix is None:
        return None
    # Re-aiming is a question about what to act on, which only exists before
    # anything ran. After that, pointing the step at a different element does
    # not correct the first action — it adds a second one.
    if fix.kind == "use_selector" and why.effect != "not_started":
        if narrate:
            narrate("  (이미 실행됐을 수 있어 대상 변경은 받지 않습니다)")
        return None
    return fix


# Actions that mean the same thing done twice as done once. A second AXPress is
# a second press; setting a field to the same value again is not a second edit.
# This is a property of the action, not of what the model believes happened —
# after an unclear outcome it is the only thing that can license a re-run.
_IDEMPOTENT = ("read", "set_value")


def _may_rerun(step: "Step") -> bool:
    if step.action not in _IDEMPOTENT:
        return False
    if step.safety is None:
        return False
    return set(step.safety.effects) <= {"read", "write_ephemeral"}


class ReplayResult:
    def __init__(self):
        self.ok = True
        self.log: list[dict] = []
        self.events: list[dict] = []
        self.commit_evidence: dict = {}
        self.visual_used = False
        self.visual_history: list[dict] = []
        self.provider_calls: list[dict] = []
        self.preparation_diagnostics: list[dict] = []
        self.repair_context = None
        self.repair_observation = None
        self._continuation = None
        self._secrets: set[str] = set()
        # Steps an agent walked for a decision's answer that had none:
        # {(decision name, answer): [steps]}. Reported, never saved here.
        self.learned: dict = {}

    def protect(self, value):
        if isinstance(value, str) and value:
            self._secrets.add(value)
        elif isinstance(value, list):
            for item in value:
                self.protect(item)
        self.log = [self._redact(e) for e in self.log]

    def _redact(self, value):
        if isinstance(value, str):
            for secret in sorted(self._secrets, key=len, reverse=True):
                value = value.replace(secret, "[redacted]")
        elif isinstance(value, dict):
            value = {k: self._redact(v) for k, v in value.items()}
        elif isinstance(value, list):
            value = [self._redact(v) for v in value]
        return value

    @property
    def resolved(self) -> list[dict]:
        """What this run had to ask about, for the caller to report.

        NOT written back into the recipe. Persisting a fix looked like the way
        to make escalation pay for itself, and it was wrong twice over: the flow
        in hand is the BOUND one, so saving it would bake this run's filename,
        recipient and text into the template — the feature meant to stop a model
        hard-coding a window title would have hard-coded the entire run. And
        whether a fix is durable is a property of the CONDITION it holds under,
        not of its IR shape: a selector can name today's date, while "open that
        panel first" can be structural (advisor finding, both). Learning needs a
        validity condition the IR has nowhere to put yet.
        """
        return [e for e in self.log if e.get("status") == "resolved"]

    def record(self, **kw):
        self.log.append(self._redact(kw))
        if kw.get("status") in ("error", "gated", "unverified"):
            self.ok = False


def _decide(decide, step: "Step", i: int, captures: dict, flow: "Flow",
            res: "ReplayResult", narrate, sensitive_names=frozenset()) -> bool:
    """Run one decide step. False means the run stops here."""
    q, into = step.params["question"], step.params["into"]
    choices = step.params.get("choices")
    sources = step.params.get("extract_from")
    if step.params.get("compose"):
        # Only a decider that says it can write may be asked to.
        decide = getattr(decide, "compose", None)
    if decide is None:
        res.record(status="error", step=i, action="decide", error="decision_unavailable",
                   detail=f"이 단계는 판단이 필요합니다 — '{q[:80]}' 에 답할 모델이 연결되지 않았습니다")
        return False
    # What the run has seen so far, minus anything marked sensitive: a
    # password read off the screen is not something to hand a model.
    sensitive = set(sensitive_names) | {c.name for st in every_step(flow.steps)
                                       for c in st.captures if c.sensitive}
    if res._redact(q) != q:
        res.record(status="error", step=i, action="decide", error="decision_invalid",
                   detail="the decision question contains a protected captured value")
        return False
    inputs = {k: v for k, v in captures.items()
              if k not in sensitive and res._redact(v) == v}
    if sources is not None:
        # Extracting from a value the model may not see would be extracting
        # from nothing; and from a value never read, the same.
        missing = [n for n in sources if n not in inputs]
        if missing:
            res.record(status="error", step=i, action="decide", error="decision_invalid",
                       detail="판단의 재료가 읽히지 않았거나 보여줄 수 없는 값입니다: "
                              + ", ".join(missing))
            return False
    if narrate:
        narrate(f"판단이 필요한 단계입니다: {q}")
    try:
        # `None` for choices is how a decider is told to extract, not choose;
        # a composer is its own callable and gets no choices at all.
        answer = (decide(q, dict(inputs)) if step.params.get("compose")
                  else decide(q, None if sources is not None else list(choices), dict(inputs)))
    except Exception as exc:
        res.record(status="error", step=i, action="decide", error="decision_failed",
                   detail=f"판단을 받지 못했습니다: {type(exc).__name__}")
        return False
    if step.params.get("compose"):
        if not isinstance(answer, str) or not answer.strip() or len(answer) > 2000:
            res.record(status="error", step=i, action="decide", error="decision_invalid",
                       detail="쓸 글을 받지 못했습니다")
            return False
    elif sources is not None:
        pool = [str(inputs[n]) for n in sources]
        if (not isinstance(answer, str) or not answer.strip() or len(answer) > 500
                or not any(answer in text for text in pool)):
            res.record(status="error", step=i, action="decide", error="decision_invalid",
                       detail="읽은 글 안에 그대로 있는 답이 아닙니다")
            return False
    elif answer not in choices:
        res.record(status="error", step=i, action="decide", error="decision_invalid",
                   detail="고를 수 있는 것 밖의 답이 왔습니다")
        return False
    captures[into] = answer
    if into in sensitive:
        res.protect(answer)
    res.record(status="decided", step=i, action="decide", into=into, value=answer)
    if narrate:
        narrate(f"  → {answer}")
    return True


def _follow_branch(driver, flow: "Flow", step: "Step", i: int, captures: dict,
                   res: "ReplayResult", *, narrate, escalate, budget, decide, walk,
                   prepare_commit, after_commit, confirm=None, visual_scopes=None,
                   sensitive_names=frozenset()) -> "ReplayResult":
    """Run the steps for the answer given — or walk them, if there are none yet."""
    into = step.params["into"]
    choice = captures[into]
    branch = step.params["branches"].get(choice)
    outer = ({s.params.get("into") for s in every_step(flow.steps) if s.action == "decide"},
             {s.params.get("into") for s in every_step(flow.steps)
              if s.action == "decide" and s.params.get("compose")})
    if branch:
        # Only steps that need no consent live in a branch (the caller checks
        # that before saving one), so no approval is carried in.
        sub = replay(driver, Flow(flow.name, flow.target, steps=list(branch),
                                  tables=flow.tables),
                     narrate=narrate, escalate=escalate, budget=budget, decide=decide,
                     walk=walk, prepare_commit=prepare_commit, after_commit=after_commit,
                     confirm=confirm, visual_scopes=visual_scopes, _seed=captures,
                     _seed_secrets=tuple(res._secrets), _seed_sensitive_names=frozenset(sensitive_names),
                     _outer=outer)
        for secret in sub._secrets:
            res.protect(secret)
        for entry in sub.log:
            res.log.append(res._redact({**entry, "branch": choice}))
        res.ok = res.ok and sub.ok
        res.events.extend(sub.events)
        res.commit_evidence.update(sub.commit_evidence)
        res.visual_used |= sub.visual_used
        res.visual_history.extend(sub.visual_history)
        res.provider_calls.extend(sub.provider_calls)
        res.learned.update(sub.learned)
        return res
    if walk is None:
        res.record(status="error", step=i, action="decide", error="branch_unwalked",
                   detail=f"'{choice}' 인 경우는 아직 해 본 적이 없습니다")
        return res
    if narrate:
        narrate(f"'{choice}' 인 경우는 처음이라 화면을 보면서 이어서 할게요")
    try:
        walked = walk(step.params["goal"], step.params["question"], choice) or {}
    except Exception as exc:
        walked = {"ok": False, "why": type(exc).__name__}
    if not walked.get("ok"):
        res.record(status="error", step=i, action="decide", error="branch_walk_failed",
                   detail=str(walked.get("why") or "이어서 하지 못했습니다")[:200])
        return res
    said = walked.get("said") or []
    if walked.get("steps"):
        res.learned[(into, choice)] = walked["steps"]
    res.record(status="done", steps=len(flow.steps), result=said[-1] if said else None,
               captured={"결과": said[-1]} if said else None, walked=choice)
    return res


def _replay_exception(exc):
    name = type(exc).__name__
    # Closed names aid diagnosis without copying arbitrary exception
    # messages or user-defined class names into an execution record.
    public = {"HelperUnavailable", "TimeoutError", "OSError", "ValueError",
              "AttributeError", "TypeError", "RuntimeError", "AssertionError",
              "KeyError", "IndexError"}
    return {"type": "error", "error": "observation_unavailable",
            "exception_class": name if name in public else "Exception"}


def _ready_hint(raw):
    if raw is None:
        return None
    if (not isinstance(raw, dict) or set(raw) != {"field", "attribute", "equals"}
            or not isinstance(raw["field"], dict) or not raw["field"]
            or not isinstance(raw["attribute"], str) or not raw["attribute"]
            or not isinstance(raw["equals"], str)):
        raise ValueError("ready_when needs a field selector, an attribute and the exact value to wait for")
    return ReadyHint(_selector_from(raw["field"], "ready_when.field"), raw["attribute"], raw["equals"])


_TEXT_FIELDS = ("identifier", "title", "desc", "value", "ancestor", "click_ancestor")


def _optional_selector(container, key, where):
    """Absent, null or {} means no selector, as before; anything else is checked.

    Branching on truthiness let 0, false, [] and "" through as "none".
    """
    value = container.get(key)
    if value is None or value == {}:
        return None
    return _selector_from(value, where)


def _selector_from(raw, where="selector"):
    """A selector from a recipe, checked when the recipe is read.

    Unchecked, a wrong type surfaced only when the selector was matched — for
    a postcondition or hint, after the step had already acted (`identifier: 42`
    raised mid-run). Every stored and built-in recipe already conforms.
    """
    def text(value, many):
        if isinstance(value, str):
            return bool(value.strip())
        return many and isinstance(value, list) and bool(value) and all(
            isinstance(v, str) and v.strip() for v in value)
    known = {f.name for f in fields(Selector)}
    if not isinstance(raw, dict):
        raise ValueError(f"{where} must be an object")
    if set(raw) - known or not any(raw.get(k) is not None for k in ("role", *_TEXT_FIELDS)):
        raise ValueError(f"{where} needs known selector fields that name an element")
    for key, value in raw.items():
        ok = (value is None or (key == "role" and text(value, False))
              or (key in _TEXT_FIELDS and text(value, True))
              or (key == "allow_nth" and type(value) is int and value >= 0)
              or (key == "in_focused_window" and type(value) is bool)
              or (key == "subrole" and isinstance(value, str)))
        if not ok:
            raise ValueError(f"{where}.{key} has the wrong type")
    return Selector(**raw)


def _block_value(block, attribute):
    for line in block.splitlines():
        value = Selector._attr(line, attribute)
        if value is not None:
            return value
    return None


def _replay_observation(method, *args, **kwargs):
    """Keep transport read failures inside the run's existing evidence record.

    A read failure cannot justify a retry of an input or erase its receipt.
    Interrupts still propagate; raw exception text may contain observed data.
    """
    try:
        value = method(*args, **kwargs)
    except Exception as exc:
        return _replay_exception(exc)
    if (not isinstance(value, dict) or value.get("type") not in ("observation", "timeout", "error")
            or (value.get("type") == "observation" and not isinstance(value.get("tree"), str))
            or (value.get("type") == "timeout" and value.get("last_obs") is not None
                and (not isinstance(value["last_obs"], dict)
                     or ("tree" in value["last_obs"] and not isinstance(value["last_obs"]["tree"], str))))):
        return {"type": "error", "error": "observation_unavailable", "reason": "invalid_response"}
    if value.get("type") == "timeout" and isinstance(value.get("last_obs"), dict) \
            and value["last_obs"].get("type") == "error":
        # Legacy/custom waiters may wrap a terminal error as a timeout.
        # Such a reply supplies no false-condition observation to recover from.
        return value["last_obs"]
    return value


def replay(driver: Driver, flow: Flow, approvals: dict[int, bool] | None = None,
           narrate=None, escalate=None, budget: int = 0, *, prepare_commit=None,
           after_commit=None, commit_expected: dict | None = None,
           decide=None, walk=None, confirm=None, visual_scopes=None,
           _seed: dict | None = None, _seed_secrets: tuple = (), _seed_sensitive_names: frozenset = frozenset(), _retain_continuation: bool = False, _outer: tuple = (frozenset(), frozenset())) -> ReplayResult:
    """Run a flow deterministically. Zero model tokens — unless asked otherwise.

    approvals: {step_index: True} pre-authorizes confirm-class steps.
    narrate:   optional callback(str) for the L4 user-facing narration.
    escalate:  callback(Uncertainty) -> Resolution|None, for the moments the
               recipe cannot decide by itself. `budget` caps how many times it
               may be called, and both default to "never": replay stays free and
               deterministic unless a caller deliberately buys judgment.

    The escalation points are the places this engine ALREADY knows its
    prediction broke — an ambiguous selector, an action the app refused, an
    outcome that did not arrive. Today those end the run; given a budget they
    become a question instead. Screen-granularity is not "call a model every
    screen", it is "call one when the screen disagrees with the recipe".

    decide:    callback(question, choices, inputs) -> one of choices, for
               `decide` steps — the judgment a recipe PLANS to need, as opposed
               to `escalate`, which is for the judgment it did not expect. Each
               decide step asks once; without a callback it fails, never guesses.
    visual_scopes: host registry for explicit image steps; these may invoke a
               planner and require an independent receiving-result callback.
    confirm:   callback(description) -> bool, for a step that needs consent
               and uses a value decided during this run. Such a step cannot be
               approved before the run — nobody knew the value — so it is asked
               here, with the value in the question. Without this callback such
               a recipe is refused before it starts.
    walk:      callback(goal, question, choice) -> dict, for a branching
               decision whose answer has no steps yet: the agent carries on
               from the screen as it is. What it returns ({"ok", "said",
               "steps"}) is reported in `res.learned`, never written here —
               saving a new branch is the caller's decision.

    Consequential commits require typed host evidence, even if a UI
    postcondition succeeds. Hooks have the same shape as general.run and may
    come from the driver. commit_expected contains host expectations fixed
    before execution; a receipt must never be reused as its own expectation.
    """
    approvals = approvals or {}
    # Host registration is fixed for this replay and its selected branches.
    visual_scopes = dict(visual_scopes) if isinstance(visual_scopes, dict) else visual_scopes
    res = ReplayResult()
    if narrate is not None:
        original_narrate = narrate
        narrate = lambda text: original_narrate(res._redact(text))
    for secret in _seed_secrets:
        res.protect(secret)
        if hasattr(driver, 'protect_value'):
            driver.protect_value(secret)
    sensitive_names = set(_seed_sensitive_names) | {
        c.name for st in every_step(flow.steps) for c in st.captures if c.sensitive}
    for name in sensitive_names:
        if name in (_seed or {}):
            res.protect(_seed[name])
            if hasattr(driver, 'protect_value'):
                driver.protect_value(_seed[name])
    last_checkpoint_index = None
    last_checkpoint = None
    if type(flow.schema) is not int or flow.schema not in (2, 3, 4):
        res.record(status="error", error="unsupported_schema",
                   detail="migrate the flow to schema 2 before execution")
        return res
    spend = _Budget(budget if escalate is not None else 0)
    captures: dict = dict(_seed or {})   # values read off the screen during this run

    # A decision is a model's answer, and a model's answer is never consent.
    # A step that needs approval is approved as DESCRIBED, before the run; a
    # value chosen mid-run would change what it does after the person agreed.
    # Decisions made here and — for a branch — above it: a step deep in a
    # branch that uses an answer given earlier is held to the same rules.
    decided = {st.params.get("into") for st in every_step(flow.steps)
               if st.action == "decide"} | set(_outer[0])
    composed = {st.params.get("into") for st in every_step(flow.steps)
                if st.action == "decide" and st.params.get("compose")} | set(_outer[1])
    ask_then = {}                        # step -> decided names it uses
    for i, st in enumerate(flow.steps):
        if st.action == "decide" or not composed:
            continue
        # Text a model wrote may be typed; it may never say what to act on.
        aimed = json.dumps([asdict(st.selector) if st.selector else None,
                            {k: v for k, v in st.params.items() if k not in ("value", "text")},
                            st.for_each], ensure_ascii=False, default=str)
        if any("{{%s}}" % name in aimed for name in composed):
            res.record(status="error", step=i, error="composed_text_aims",
                       detail="모델이 쓴 글은 입력할 내용으로만 쓸 수 있습니다")
            return res
    for i, st in enumerate(flow.steps):
        if st.action == "decide" or not decided:
            continue
        used = json.dumps([asdict(st.selector) if st.selector else None, st.params],
                          ensure_ascii=False, default=str)
        names = [name for name in decided if "{{%s}}" % name in used]
        if names and (st.safety is None or policy(st.safety).risk != "none"):
            # Asked at the moment the value exists, or not at all. A contract
            # and a binding are what the consent is bound to.
            if confirm is None or st.safety is None or st.binding is None \
                    or policy(st.safety).risk not in ("confirm",):
                res.record(status="error", step=i, error="decision_drives_consent",
                           detail="판단으로 정한 값은 승인이 필요한 단계에 쓸 수 없습니다")
                return res
            ask_then[i] = names

    for i, step in enumerate(flow.steps):
        if step.action == 'visual':
            from .visual_replay import scope_problem
            problem = scope_problem(step, visual_scopes, driver)
            if problem:
                res.record(status='error', step=i, error='visual_scope_required', detail=problem)
                return res
            continue
        if step.action in FLOW_ACTIONS:
            problem = (decide_problem(step.params) if isinstance(step.params, dict)
                       else "params must be an object")
            if problem or step.selector is not None or step.safety is not None:
                res.record(status="error", step=i, error="invalid_action",
                           detail=problem or "a decide step touches nothing on screen")
                return res
            continue
        if step.action not in ACTION_PARAMS or not isinstance(step.params, dict) \
                or set(step.params) - ACTION_PARAMS.get(step.action, frozenset()):
            res.record(status="error", step=i, error="invalid_action")
            return res
        if step.action=='drag':
            from .native_drag import drag_problem
            problem=drag_problem(step.params)
            if (problem or not {'action.drag','drag.window_bound'} <= set(getattr(driver,'capabilities',()))
                    or step.selector is None or step.selector.role != 'Window'
                    or step.postcondition is None or step.unverified_ok):
                res.record(status='error',step=i,error='drag_unavailable',effect='not_started',detail=problem)
                return res
        if step.action=='open_select' and 'opening_action' in step.params:
            from .protocol import native_opening_parameters_problem
            problem=native_opening_parameters_problem(step.params)
            if problem or 'action.open_select.explicit_ax' not in getattr(driver,'capabilities',()):
                res.record(status='error',step=i,error='explicit_opening_contract_required',effect='not_started')
                return res
        if step.action == 'upload':
            from .browser_upload import upload_params_valid
            if ('action.upload' not in getattr(driver, 'capabilities', ())
                    or not upload_params_valid(step.params) or step.selector is None
                    or step.safety is None or set(step.safety.effects) != {'transmit'}
                    or step.binding is None or step.binding.operation != 'upload'
                    or step.binding.params_digest != pin_params(step.params)
                    or step.unverified_ok):
                res.record(status='error', step=i, error='upload_contract_required',
                           effect='not_started')
                return res
        if step.action == "prepare_window":
            if ("action.prepare_window" not in getattr(driver,"capabilities",())
                    or step.selector is None or step.postcondition is None
                    or step.postcondition.field is None or step.postcondition.equals is None
                    or step.postcondition.attribute is None or step.unverified_ok
                    or step.safety is None or set(step.safety.effects) != {"write_ephemeral"}):
                res.record(status="error", step=i, error="window_preparation_contract_required",
                           detail="prepare_window needs capability, window selector and exact observed result")
                return res
        if (step.action == "menu" and isinstance(step.params, dict)
                and "focused_document" in step.params
                and "action.menu.focused_document" not in getattr(driver, "capabilities", ())):
            # A backend that does not check the focused file would ignore the
            # field and press; refused before any input (Codex review of 31955f0).
            res.record(status="error", step=i, error="focused_document_guard_unavailable",
                       effect="not_started")
            return res
        if step.action == "press_key" and "press_key.element_bound" in getattr(driver, "capabilities", ()):
            from .protocol import NAVIGATION_KEYS, RETURN_BOUND_CAPABILITY, RETURN_CAPABILITY, SUBMIT_KEYS
            key = step.params.get('key')
            # Return commits: only where the backend says it can scope it, in
            # a step that says it submits, verified by an exact value (never
            # `contains`, which a sheet that merely closed could satisfy).
            submit_ok = (type(key) is str and key in SUBMIT_KEYS
                         and RETURN_CAPABILITY in getattr(driver, "capabilities", ())
                         and step.is_submit
                         and step.postcondition is not None
                         and step.postcondition.equals is not None)
            # The general form (2026-10-10: open a chat from its row, send the
            # message in its field): a declared submit, bound, with a field
            # postcondition that must NOT hold before the key (checked at
            # dispatch). What it commits is the step's declared effect; a
            # consequential one goes through approval and its receipt.
            bound_submit = (type(key) is str and key in SUBMIT_KEYS
                            and RETURN_BOUND_CAPABILITY in getattr(driver, "capabilities", ())
                            and step.is_submit and step.safety is not None
                            and "credential" not in step.safety.effects)
            effects_ok = step.safety is not None and (
                set(step.safety.effects) == {"write_ephemeral"}
                or (bound_submit and not submit_ok))
            if ("action.press_key" not in getattr(driver, "capabilities", ())
                    or type(key) is not str
                    or (key not in NAVIGATION_KEYS - {'Escape'} and not (submit_ok or bound_submit))
                    or step.binding is None or step.binding.operation != 'press_key'
                    or step.binding.params_digest != pin_params(step.params)
                    or step.selector is None or step.postcondition is None or step.postcondition.field is None
                    or step.postcondition.attribute is None
                    or (step.postcondition.equals is None and step.postcondition.contains is None)
                    or step.unverified_ok or not effects_ok):
                res.record(status="error", step=i, error="navigation_contract_required",
                           detail="window navigation needs an input selector and explicit observed result")
                return res
        if step.action in ("select_option", "select_custom_option") and not isinstance(step.params.get("option_label"), str):
            res.record(status="error", step=i, error="selection_label_required")
            return res

    # A decide step acts on nothing, so it has nothing to contract for; what
    # its answer may reach is checked above.
    missing = [i for i, st in enumerate(flow.steps)
               if st.safety is None and st.action not in FLOW_ACTIONS]
    if missing:
        res.record(status="error", error="contract_required",
                   detail=f"steps {missing} require a safety contract")
        return res
    prepare_commit = prepare_commit or getattr(driver, "prepare_commit", None)
    after_commit = after_commit or getattr(driver, "after_commit", None)
    configured_expected = (commit_expected if commit_expected is not None
                           else getattr(driver, "commit_expected", None))
    if configured_expected is not None and not isinstance(configured_expected, dict):
        raise ValueError("commit_expected must be a host-supplied dictionary")
    host_expected = deepcopy(configured_expected or {})
    if hasattr(driver, "protect_captures"):
        driver.protect_captures([c for s in flow.steps for c in s.captures if c.sensitive])
    obs = _replay_observation(driver.observe, flow.target, "tree_full")
    if obs.get("error") == "no_window_content" and flow.steps \
            and flow.steps[0].action in ("menu", "open_folder", "reopen_window", "close_menu"):
        # "Finder 열어줘" starts from an app with no window at all. Menus are
        # app-scoped, and open_folder is what makes the window, so such a
        # recipe can legitimately begin here (open_folder: seen on june-mba,
        # 2026-10-09, Finder with no window); anything needing window content
        # still fails honestly on its own step.
        obs = _replay_observation(driver.observe, flow.target, "tree_full", require_window=False)
    if obs["type"] != "observation":
        res.record(status="error", stage="initial_observe", detail=obs)
        return res

    # Steps whose success is not self-evident must be verifiable: delivery of a
    # keyboard event is not evidence the app accepted it, so an unverified
    # type/key step could report success while nothing happened (or worse,
    # happened elsewhere). `menu` belongs to the same family for a reason we
    # measured rather than assumed: on this machine, pressing Format > Font >
    # Bold with no selection returns success and changes nothing at all.
    for i, step in enumerate(flow.steps):
        if step.action in ("type_text", "press_key", "menu") and \
                step.postcondition is None and step.expect is None and \
                not step.unverified_ok:
            res.record(status="error", step=i, action=step.action,
                       error="unverifiable_input_step",
                       detail=f"step {i} ({step.action}) has no postcondition/"
                              "expect; add one or set unverified_ok=True explicitly")
            return res

    def seen_in_front():
        # One latch for every path that could send more: the next step, and
        # this step's own recovery and retry (Codex review of 74f33e2 — a
        # postcondition_timeout reached dismiss_dialog after the flag).
        return any(e.get("activated_target") for e in res.events)

    for i, step in enumerate(flow.steps):
        # The target was seen in front after an earlier step: the person's
        # focus may be on it now, so nothing more is sent to it in this run —
        # only reads continue (Codex review of b8a1113). What that step did
        # stays done; this stops what would come next.
        if seen_in_front() and step.action != "read":
            res.record(status="error", step=i, action=step.action, error="target_seen_in_front",
                       effect="not_started",
                       detail="앞 단계 뒤에 대상 앱이 맨 앞에 있는 것이 관찰되어, 이 단계부터는 보내지 않았습니다")
            return res
        # Values captured earlier in THIS run are only known now, so a step is
        # rendered against them just before it executes. Flow.bind() resolves
        # parameters ahead of time; this resolves what the screen told us since
        # (without it, "capture the total, then type it" could not work — the
        # promise the skill documents).
        try:
            step = _render_step(step, {**captures, "__tables__": flow.tables})
        except KeyError:
            # A skipped/optional capture is absent, never the literal template
            # text. Refuse before observations or dispatch for this step.
            res.record(status="error", step=i, action=step.action,
                       error="runtime_value_missing", effect="not_started",
                       detail="a required parameter, capture, or table value is unavailable")
            return res
        if i in ask_then:
            # The whole of anything a model wrote; consent to part of a
            # message is not consent to the message.
            shown = ", ".join(f"{n}: {str(captures.get(n))[:2000 if n in composed else 120]!r}"
                              for n in ask_then[i])
            question = f"{describe(step.safety, policy(step.safety))} — {shown}"
            try:
                agreed = bool(confirm(question))
            except Exception:
                agreed = False
            if not agreed:
                res.record(status="gated", step=i, gate="declined",
                           detail="판단으로 정한 값으로 진행하지 않았습니다")
                res.ok = False
                return res
            approvals = {**approvals, i: issue_approval(step.binding, step.safety)}
        if step.action in FLOW_ACTIONS:
            if not _decide(decide, step, i, captures, flow, res, narrate, sensitive_names):
                return res
            if "branches" in step.params:
                return _follow_branch(driver, flow, step, i, captures, res, narrate=narrate,
                                      escalate=escalate, budget=spend.left, decide=decide,
                                      walk=walk, prepare_commit=prepare_commit,
                                      after_commit=after_commit, confirm=confirm, visual_scopes=visual_scopes,
                                      sensitive_names=sensitive_names)
            continue
        if step.action == 'visual':
            from .visual_replay import run_step
            try:
                passed = run_step(driver, flow.target, step, i, res, visual_scopes)
            except Exception as exc:
                res.record(status='error', step=i, stage='visual_step',
                           error='observation_unavailable', detail=_replay_exception(exc))
                return res
            if not passed:
                return res
            if i + 1 < len(flow.steps):
                obs = _replay_observation(driver.observe, flow.target, 'tree_full')
                if obs.get('type') != 'observation':
                    res.record(status='error', step=i, error='visual_reobserve_failed', detail=obs)
                    return res
            continue
        # Idempotency, decided before anything is narrated, authorized or
        # performed: a step that exists to reach a state has nothing to do once
        # that state holds, and pressing a toggle anyway would undo it.
        if step.skip_if is not None and step.skip_if.check(obs["tree"]):
            if narrate:
                narrate(f"이미 되어 있어서 건너뜁니다: {step.intent_text}")
            res.record(status="skipped", step=i, action=step.action,
                       reason="skip_if already satisfied")
            continue
        if narrate and step.intent_text:
            narrate(step.intent_text)

        # Was the outcome already true before we acted? Then verifying it
        # afterwards proves nothing. A model-authored recipe passed this way
        # once — it clicked a control that does not respond, and the checkbox it
        # checked had been on the whole time. Recorded, not refused: setting a
        # value that already matches is legitimate, and a toggle should say so
        # with skip_if instead.
        pre_satisfied = bool(step.postcondition is not None
                             and obs.get("tree")
                             and step.postcondition.check(obs["tree"]))
        # For Return, an outcome already true means the press could prove
        # nothing and might commit something else — refused before input.
        if (step.action == "press_key" and step.params.get("key") == "Return"
                and step.postcondition is not None and step.postcondition.primary_check(obs["tree"])):
            res.record(status="error", step=i, action=step.action,
                       error="postcondition_already_true", effect="not_started",
                       detail="Return 의 결과가 누르기 전부터 참입니다 — 누르지 않았습니다")
            return res

        action = {"name": step.action, **step.params}
        acted_on_tree = None
        trace = None
        target_line = ""
        if step.action == "menu":
            # A menu step has no element in the tree, so give the contract
            # something real to fingerprint: the command's own title path. Left
            # empty, every menu step would fingerprint "" and the label
            # invariant would be incapable of ever noticing a change.
            target_line = "menu: " + " > ".join(
                p if isinstance(p, str) else "|".join(p)
                for p in step.params.get("path", []))
        while step.selector is not None:
            try:
                action["element_index"], trace = step.selector.resolve(obs["tree"])
                break
            except (TargetAmbiguous, TargetMissing) as e:
                kind = ("target_ambiguous" if isinstance(e, TargetAmbiguous)
                        else "target_missing")
                nearby = _nearby(step.selector, obs["tree"])
                fix = _escalate(escalate, spend, step, Uncertainty(
                    kind=kind, step=i, intent=step.intent_text,
                    detail=f"{e}. {nearby}",
                    options=_candidates(step.selector, obs["tree"])), narrate)
                if fix is not None and fix.kind == "use_selector" and fix.selector:
                    # Only where the step needs no approval. Re-aiming a step
                    # that does would point the consent — already given, for a
                    # described action — at something else, and the description
                    # the person agreed to must be the thing that runs.
                    if step.safety is not None and policy(step.safety).risk != "none":
                        res.record(status="error", step=i, action=step.action,
                                   error=kind,
                                   detail="승인이 필요한 단계의 대상은 바꿀 수 없습니다")
                        return res
                    aimed = _as_labelled(fix.selector, obs["tree"])
                    # The pins that described the OLD element would refuse the
                    # new one on sight. Only a step that needs no consent gets
                    # here, so nothing was agreed to about the old element; the
                    # identity is re-pinned to what was chosen, and the app,
                    # operation and parameter pins stay exactly as they were.
                    binding = step.binding
                    if binding is not None:
                        binding = replace(binding, **{
                            f: (getattr(aimed, f) if isinstance(getattr(aimed, f), str) else None)
                            for f in ("role", "identifier", "title", "desc", "value", "ancestor")})
                    step = replace(step, selector=aimed, binding=binding)
                    res.record(status="resolved", step=i, kind=kind,
                               reason=fix.reason)
                    continue
                from .repair_packet import selector_candidates
                res.repair_observation = obs
                if _retain_continuation:
                    from .continuation_state import ReplayContinuation
                    res._continuation = ReplayContinuation(step_index=i, captures=captures,
                        secrets=res._secrets, sensitive_names=sensitive_names,
                        checkpoint=last_checkpoint if last_checkpoint_index==i-1 else None,
                        outcome=step.postcondition)
                res.repair_context = res._redact({
                    "step_index": i, "action": step.action,
                    "selector": asdict(step.selector),
                    "candidates": selector_candidates(step.selector, obs["tree"]),
                    "effect": "not_started", "error": kind,
                })
                res.record(status="error", step=i, action=step.action,
                           error=kind, detail=f"{e}. {nearby}")
                return res
        if step.selector is not None:
            idx = action["element_index"]
            target_line = next((l for l in obs["tree"].splitlines()
                                if re.match(rf"\s*{idx}\s", l)), "")
            if step.action == "prepare_window":
                parts = target_line.strip().split()
                if len(parts)<2 or not _same_role(parts[1], "Window"):
                    res.record(status="error", step=i, error="window_preparation_target_required")
                    return res
            if step.require_enabled and "[disabled]" in target_line:
                res.record(status="error", step=i, action=step.action,
                           error="precondition_failed",
                           detail=f"target disabled: {target_line.strip()}")
                return res

        # Authorization. When the step carries an authored SafetyContract, the
        # decision comes from the deterministic policy over that contract, and
        # replay only re-validates the facts the judgment rested on — it never
        # re-infers meaning, and no keyword is consulted. Steps without a
        # contract fall back to the legacy label classifier.
        gate_desc = step.intent_text
        if step.safety is not None:
            if step.binding is not None:
                live = _live_binding(driver, flow, step, action, target_line,
                                     obs.get("tree", ""))
                v = validate(step.binding, live)
                if v.unverifiable:
                    res.record(status="error", step=i, action=step.action,
                               error="invariant_unverifiable",
                               detail="재검증할 수 없는 고정 항목: "
                                      + ", ".join(v.unverifiable))
                    return res
                if not v.ok:
                    # The recorded meaning no longer applies. This is NOT a
                    # confirm — the user cannot consent to something whose
                    # description has gone stale; it needs re-authoring.
                    res.record(status="error", step=i, action=step.action,
                               error="contract_invalidated", detail="; ".join(v.hard))
                    return res
                if v.drift:
                    res.record(status="drift", step=i, detail="; ".join(v.drift))
            decision = policy(step.safety)
            risk, reason = decision.risk, decision.reason
            gate_desc = describe(step.safety, decision)
            if risk == "blocked":
                res.record(status="error", step=i, action=step.action,
                           error="unverifiable_action", detail=reason)
                return res
        else:
            # schema 1 only: label heuristics, kept for recipes authored before
            # contracts existed.
            detected, reason = safety.classify(
                action, target_line, is_submit=step.is_submit,
                into_sensitive_field=step.into_sensitive_field)
            risk = safety.merge(step.risk_class, detected)

        def dispatch(current, payload):
            if current.get("type") != "observation":
                return {"type": "error", "error": "observation_required", "effect": "not_started"}
            idx = payload.get("element_index")
            line = next((ln for ln in current["tree"].splitlines()
                         if idx is not None and re.match(rf"\s*{idx}\s", ln)), target_line)
            if step.binding is not None:
                live = _live_binding(driver, flow, step, payload, line, current["tree"])
                valid = validate(step.binding, live)
                if not valid.ok:
                    return {"type": "error", "error": "contract_invalidated",
                            "effect": "not_started", "detail": valid.hard + valid.unverifiable}
            claim = {}
            if idx is not None:
                parts = line.strip().split()
                if len(parts) >= 2:
                    claim["role"] = parts[1]
                for key in ("Identifier", "Title", "Description", "Value"):
                    label = Selector._attr(line, key)
                    if label is not None:
                        claim.update(attribute=key, label=label)
                        break
                if step.require_enabled and step.action != "prepare_window":
                    claim["enabled"] = True
            effects = set(step.safety.effects) if step.safety is not None else safety.effects_of(payload, line, target=flow.target)[0]
            observed_effects, observed_reason = safety.effects_of(payload, line, target=flow.target)
            # Element/option signals come from the resolved target. A menu's
            # synthesized path is authored text, not a live element observation
            # (Save As… can merely open a panel), so it is not this check's input.
            observed_consequential = (observed_effects - {"read", "write_ephemeral"}
                                      if idx is not None else safety.document_edit_effects(
                                          payload, line, target=flow.target))
            if observed_consequential - effects:
                return {"type": "error", "error": "contract_effect_mismatch",
                        "effect": "not_started", "detail": sorted(observed_consequential - effects)}
            effects |= observed_consequential
            calculator_clear = (
                flow.target == "com.apple.calculator" and effects == {"delete"}
                and risk == "none" and step.safety is not None
                and step.safety.resource.scope == "entry"
                and step.safety.reversibility == "reversible"
                and observed_effects == {"write_ephemeral"}
                and observed_reason == "입력 지우기")
            consequential = bool(effects - {"read", "write_ephemeral"}) and not calculator_clear
            expected = {}
            if consequential and "write_persistent" in effects and step.binding is not None:
                if step.binding.resource_identity:
                    expected["저장한 이름"] = step.binding.resource_identity
                if step.binding.destination_identity:
                    expected["저장 위치"] = step.binding.destination_identity
            # Explicit host expectations may resolve a logical folder label
            # (e.g. Downloads) to the canonical path before execution.
            expected.update(host_expected)
            event = make_event(payload, effects, flow.target, obs_id=current['obs_id'])
            context = None
            if consequential and prepare_commit is not None:
                try:
                    context = prepare_commit(deepcopy(event))
                except Exception as exc:
                    # Said, not swallowed — but only what a receipt chose to
                    # say: its own error messages are fixed strings; anything
                    # else may carry document text, so only its class is shown
                    # (Codex review of 4d04b80).
                    # Only a closed public code is shown; never an exception's
                    # message (any subclass could carry document text).
                    code = getattr(exc, "public_code", None)
                    # A private refusal class shows as its public base.
                    name = next(c.__name__ for c in type(exc).__mro__
                                if not c.__name__.startswith("_"))
                    shown = (f"{name}: {code}"
                             if type(code) is str and code in _receipt_public_codes()
                             else name)
                    return {"type": "error", "error": "host_evidence_preparation_failed",
                            "effect": "not_started", "detail": shown}
            from .commit_evidence import save_panel_dispatch_parameters
            from .protocol import RETURN_BOUND_CAPABILITY, SUBMIT_KEYS
            caps_now = getattr(driver, 'capabilities', ())
            # The helper still takes the Go to Folder path first when it applies.
            bound_return = (payload.get("name") == "press_key" and payload.get("key") in SUBMIT_KEYS
                            and step.is_submit and RETURN_BOUND_CAPABILITY in caps_now)
            if bound_return and (step.postcondition is None or step.postcondition.primary_check(current["tree"])):
                # Already true before the key: nothing could show the key did it.
                return {"type": "error", "error": "postcondition_already_true",
                        "effect": "not_started"}
            try:
                dispatch_parameters = save_panel_dispatch_parameters(
                    event, context, caps_now)
                # Validate the attachment before consuming a one-shot approval.
                # Reuse the decision; authorizes/spend must run only once.
                dispatch_approved = _approved(approvals, i, step, payload)
                if dispatch_parameters and (risk not in ('confirm', 'preapproved')
                        or dispatch_approved is not True):
                    raise ValueError('save-panel dispatch requires explicit approval')
                if bound_return:
                    # Not an approval: the host checked the submit contract.
                    # A consequential submit is still gated by `risk` below.
                    dispatch_parameters = {**dispatch_parameters, "submit_authorized": True}
            except Exception:
                return {"type":"error", "error":"host_evidence_preparation_failed", "effect":"not_started"}
            try:
                # A receipt that re-read what is about to happen (a send's
                # recipient, from the window itself) adds it to the question.
                confirmation = getattr(context, "confirmation", None)
                intent = (f"{gate_desc} — {confirmation}"
                          if isinstance(confirmation, str) and confirmation else gate_desc)
                input_risk = risk
                if step.action == 'drag' and risk == 'none' and effects <= {'read','write_ephemeral'}:
                    # The existing task contract already authorizes this local
                    # gesture. Coordinate transport needs the host flag, not a
                    # second permission prompt for the same requested action.
                    input_risk, dispatch_approved = 'preapproved', True
                response = driver.act(current["obs_id"], flow.target, payload,
                                      **({"expect": claim} if claim else {}),
                                      risk_class=input_risk, intent_text=intent,
                                      approved=dispatch_approved, **dispatch_parameters)
            except Exception as exc:
                response = {"type": "error", "error": "driver_exception",
                            "effect": "unknown", "detail": str(exc)}
            from .protocol import normalize_action_response
            response = normalize_action_response(response)
            from .preparation_diagnostic import preparation_diagnostic
            diagnostic = preparation_diagnostic(response.get('preparation_diagnostic'))
            if diagnostic is not None and len(res.preparation_diagnostics) < 64:
                res.preparation_diagnostics.append(diagnostic)
            dispatched = response.get("type") == "acted" or (
                response.get("type") == "error" and response.get("effect", "unknown") != "not_started")
            if not dispatched:
                return response
            event["state"] = ("unknown" if response.get("type") != "acted" or response.get("unverified")
                              else "confirmed" if effects == {"read"} else "dispatched")
            # The target was observed in front shortly after this step (seen
            # with Finder's Go > Go to Folder…, 2026-10-08). An observation,
            # not a proven cause — and its absence proves nothing either.
            if response.get("activated_target") is True:
                event["activated_target"] = True
                if narrate:
                    narrate("⚠ 이 단계 실행 후 대상 앱이 맨 앞에 있는 것이 관찰되었습니다 — 이후 입력은 보내지 않습니다")
            res.events.append(event)
            preparation_observation = (
                step.action == "prepare_window" and response.get("type") == "acted"
                and response.get("input_route") == "window_prepare"
                and response.get("unverified") == "observe_effect"
                and response.get("reobserve_required") is True
                and effects <= {"read", "write_ephemeral"})
            from .protocol import (double_click_reobservation, element_click_reobservation,
                                   window_key_reobservation, drag_reobservation)
            caps = getattr(driver, "capabilities", ())
            navigation_observation = (
                (window_key_reobservation(payload, response, caps)
                 or double_click_reobservation(payload, response, caps)
                 or element_click_reobservation(payload, response, caps)
                 or drag_reobservation(payload, response, caps))
                and effects <= {"read", "write_ephemeral"})
            if step.action == "prepare_window" and not preparation_observation:
                event["state"] = "unknown"
                return {"type":"error", "error":"action_unverified", "effect":"unknown",
                        "event_id":event["event_id"], "event_state":"unknown",
                        "detail":"window preparation response has no reobservation contract"}
            if preparation_observation or navigation_observation:
                event["observation_required"] = True
            if (response.get("unverified") and not consequential
                    and not (preparation_observation or navigation_observation)):
                return {"type": "error", "error": "action_unverified", "effect": "unknown",
                        "event_id": event["event_id"], "event_state": "unknown",
                        "detail": response["unverified"]}
            if consequential:
                proof = None
                if after_commit is not None:
                    try:
                        proof = after_commit(deepcopy(event), deepcopy(response), context)
                    except Exception as exc:
                        code = getattr(exc, "public_code", None)
                        event["evidence_error"] = (code if type(code) is str and code in _receipt_public_codes()
                                                   else "host_commit_evidence_failed")
                problem = evidence_error({**event, "commit_evidence": proof}, expected)
                if problem is not None:
                    event["evidence_error"] = event.get("evidence_error", problem)
                    return {"type": "error", "error": "commit_unverified",
                            "effect": response.get("effect", "unknown"),
                            "event_id": event["event_id"], "event_state": event["state"],
                            "detail": event["evidence_error"]}
                res.commit_evidence[event["event_id"]] = proof
                # What the proof is OF, kept with the event: a sender's own
                # window is not the recipient's inbox (Codex review of a2207a2).
                from .commit_evidence import TranscriptEvidence, SavedFileStateEvidence
                if type(proof.evidence) is SavedFileStateEvidence:
                    event['commit_evidence_kind'] = 'saved_file_state'
                    event['saved_state_verified'] = True
                    acknowledged = response.get('type')=='acted' and not response.get('unverified')
                    event['state'] = 'state_verified' if acknowledged else 'unknown'
                    if not acknowledged:
                        event['dispatch_response'] = {'type':response.get('type'),'effect':'unknown'}
                        if response.get('unverified'):
                            event['dispatch_response']['unverified'] = True
                        return {'type':'error','error':'commit_unverified','effect':'unknown',
                                'event_id':event['event_id'],'event_state':'unknown',
                                'detail':'saved_state_does_not_confirm_dispatch'}
                    return response
                event["commit_evidence_kind"] = ("sender_transcript"
                    if type(proof.evidence) is TranscriptEvidence else type(proof.evidence).__name__)
                event["state"] = "confirmed"
                if response.get("type") == "error":
                    # The host receipt resolved an uncertain dispatch. Keep
                    # that transport uncertainty visible without resending a
                    # confirmed operation or treating it as an input failure.
                    event["dispatch_response"] = {"type": "error", "effect": "unknown"}
                    return {"type": "acted", "action": payload["name"],
                            "obs_id": current["obs_id"], "commit_confirmed": True,
                            "dispatch_response_unavailable": True}
            return response

        acted_on_tree = obs.get("tree")
        r = dispatch(obs, action)

        if r.get("error") in ("commit_unverified", "action_unverified"):
            res.record(status="unverified", step=i, action=step.action,
                       error=r["error"], effect=r["effect"],
                       event_id=r["event_id"], event_state=r["event_state"], detail=r["detail"])
            return res

        if r["type"] in ("confirm_request", "handoff_request"):
            res.record(status="gated", step=i, gate=r["type"], risk=risk,
                       reason=reason, detail=r)
            return res
        if r["type"] == "error":
            # "The driver refused" and "the driver threw while acting" are not
            # the same failure: the first is provably before any effect, the
            # second is not. The driver says which, and only the first may be
            # re-run without a witness.
            effect = r.get("effect", "unknown")
            fix = None if seen_in_front() else _escalate(escalate, spend, step, Uncertainty(
                kind="action_failed", step=i, intent=step.intent_text,
                effect=effect,
                detail=str(r.get("detail") or r.get("error")),
                options=_candidates(step.selector, obs.get("tree", ""))), narrate)
            if effect != "not_started" and not _may_rerun(step):
                if fix is not None and narrate:
                    narrate("  (실행 여부가 불확실해 다시 실행하지 않습니다)")
                fix = None
            if fix is not None and fix.kind == "recover" and \
                    _run_fix(driver, flow, fix.recovery, narrate, result=res, parent_step=i):
                if seen_in_front():
                    res.record(status="error", step=i, action=step.action,
                               error="target_seen_in_front", effect="not_started",
                               detail="복구 뒤 대상 앱이 맨 앞에 있는 것이 관찰되어 재입력을 멈췄습니다")
                    return res
                res.record(status="resolved", step=i, kind="action_failed",
                           recovery=fix.recovery, reason=fix.reason)
                # The screen moved, so everything derived from it is stale.
                obs = _replay_observation(driver.observe, flow.target, "tree_full", require_window=False)
                if obs["type"] != "observation":
                    res.record(status="error", step=i, stage="recovery_observe", detail=obs)
                    return res
                if obs["type"] == "observation" and step.selector is not None:
                    try:
                        action["element_index"], trace = \
                            step.selector.resolve(obs["tree"])
                    except LookupError as exc:
                        res.record(status="error", step=i, error="target_missing", detail=str(exc))
                        return res
                acted_on_tree = obs.get("tree")
                r = dispatch(obs, action)
                if r.get("error") in ("commit_unverified", "action_unverified"):
                    res.record(status="unverified", step=i, action=step.action,
                               error=r["error"], effect=r["effect"],
                               event_id=r["event_id"], event_state=r["event_state"], detail=r["detail"])
                    return res
                if r["type"] in ("confirm_request", "handoff_request"):
                    res.record(status="gated", step=i, gate=r["type"], detail=r)
                    return res
            if r["type"] == "error":
                res.record(status="error", step=i, action=step.action, detail=r)
                return res

        # D7: verify by polling to a monotonic deadline, not a blind sleep.
        post = step.postcondition
        if post is not None:
            # Keep settling when a non-idempotent action could leave a stale,
            # already-true pre-action result visible until its delayed effect.
            obs = _replay_observation(driver.observe, flow.target, "tree_full") if pre_satisfied and not _may_rerun(step) else None
            if obs is None or (obs.get("type") == "observation" and not post.check(obs["tree"])):
                obs = _replay_observation(driver.wait_until, flow.target, post.check)
            if obs.get('type') == 'error':
                from .window_absence import reobserve_closed_window
                obs = reobserve_closed_window(
                    lambda *args, **kwargs: _replay_observation(driver.observe, *args, **kwargs),
                    flow.target, post, obs)
            if obs.get("type") == "error":
                res.record(status="error", step=i, action=step.action, stage="verify_observe",
                           error=obs.get("error", "observation_unavailable"), detail=obs)
                return res
            if obs["type"] != "observation":
                seen = None
                last = obs.get("last_obs") or {}
                if post.field is not None and last.get("tree"):
                    try:
                        idx, _ = post.field.resolve(last["tree"])
                        seen = list(post._attr_values(_element_block(last["tree"], idx)))
                    except Exception:
                        seen = "field-not-found"
                # The action was dispatched and returned without error. The
                # only thing that failed is SEEING the result, so the effect is
                # unknown, not absent.
                fix = None if seen_in_front() else _escalate(escalate, spend, step, Uncertainty(
                    kind="postcondition_timeout", step=i, effect="unknown",
                    intent=step.intent_text,
                    detail=f"기대값 {post.equals or post.contains!r}, "
                           f"화면에서 읽은 값 {seen!r}",
                    options=_candidates(post.field, last.get("tree", ""))),
                    narrate)
                if fix is not None and fix.kind == "recover" and \
                        _run_fix(driver, flow, fix.recovery, narrate, result=res, parent_step=i):
                    if seen_in_front():
                        res.record(status="error", step=i, action=step.action,
                                   error="target_seen_in_front", effect="not_started",
                                   detail="복구 뒤 대상 앱이 맨 앞에 있는 것이 관찰되어 재입력을 멈췄습니다")
                        return res
                    # Look again FIRST. A dialog in the way, or a screen that
                    # had not caught up, is the common reason a postcondition
                    # times out on an action that did land — and looking costs
                    # nothing and cannot double an effect.
                    fresh = _replay_observation(driver.observe, flow.target, "tree_full")
                    if fresh.get("type") == "error":
                        res.record(status="error", step=i, stage="recovery_observe",
                                   error=fresh.get("error", "observation_unavailable"), detail=fresh)
                        return res
                    if fresh["type"] == "observation" and post.check(fresh["tree"]):
                        obs = fresh
                        res.record(status="resolved", step=i,
                                   kind="postcondition_timeout",
                                   recovery=fix.recovery, reason=fix.reason,
                                   detail="복구 후 다시 보니 이미 되어 있었습니다")
                    elif fresh["type"] == "observation" and _may_rerun(step):
                        # Only now, and only for an action that means the same
                        # thing done twice as done once. Clicking Send again
                        # because the screen was slow is how a message is sent
                        # twice (advisor finding).
                        retry = dict(action)
                        if step.selector is not None:
                            try:
                                retry["element_index"], _ = \
                                    step.selector.resolve(fresh["tree"])
                            except LookupError:
                                retry = None
                        if retry is not None:
                            retried = dispatch(fresh, retry)
                            if retried.get("type") != "acted":
                                res.record(status="error", step=i, error="retry_refused", detail=retried)
                                return res
                            obs = _replay_observation(driver.wait_until, flow.target, post.check)
                            if obs.get("type") == "error":
                                res.record(status="error", step=i, stage="verify_observe",
                                           error=obs.get("error", "observation_unavailable"), detail=obs)
                                return res
                            if obs["type"] == "observation":
                                res.record(status="resolved", step=i,
                                           kind="postcondition_timeout",
                                           recovery=fix.recovery,
                                           reason=fix.reason)
                if obs["type"] != "observation":
                    res.record(status="error", step=i, action=step.action,
                               error="postcondition_timeout",
                               effect="unknown",
                               detail="실행은 됐지만 결과를 확인하지 못했습니다 — "
                                      "같은 동작을 다시 실행하지 않았습니다",
                               expected=post.equals, observed=seen)
                    return res
        else:
            ready = None
            hint = step.ready_when if step.expect is None else None
            if hint is not None:
                before = hint.read(acted_on_tree)
                if hint.usable(before):
                    seen = _replay_observation(driver.wait_until, flow.target, hint.met,
                                               timeout_ms=READY_HINT_MS)
                    if seen.get("type") == "observation":
                        obs, ready = seen, "met"
                    elif seen.get("type") == "timeout":
                        ready = "timeout"   # unmet: settle exactly as without a hint
                    else:
                        # Stopped, locked, unreadable: never carry on from the
                        # tree the step acted on.
                        res.record(status="error", step=i, stage="ready_wait",
                                   error=seen.get("error", "observation_unavailable"), detail=seen)
                        return res
            if ready != "met":
                obs = _replay_observation(driver.observe, flow.target, "tree_full")
            if obs["type"] != "observation":
                res.record(status="error", step=i, stage="verify_observe",
                           error=obs.get("error", "observation_failed"), detail=obs)
                return res
            if ready is not None:
                res.log.append({"status": "ready_hint", "step": i, "ready": ready})
            if step.expect is not None and step.expect not in obs["tree"]:
                # settle a moment more, then re-check (transient render lag)
                obs = _replay_observation(driver.wait_until, flow.target,
                                          lambda t: step.expect in t, timeout_ms=3000)
                if obs.get("type") == "error":
                    res.record(status="error", step=i, stage="verify_observe",
                               error=obs.get("error", "observation_unavailable"), detail=obs)
                    return res
                if obs["type"] != "observation":
                    res.record(status="error", step=i, action=step.action,
                               error="expect_not_met",
                               detail=f"expect {step.expect!r} not in tree")
                    return res
        # Capture the value the verification actually read, so the agent can
        # tell the user the ANSWER — not just that something finished. A
        # calculator run that reports "done" without saying 408 has not really
        # answered the question that was asked.
        # Only a step that says it answers publishes a value. Everything is
        # still verified either way; this decides what gets said out loud.
        result_value = None
        if step.answers and post is not None and post.field is not None \
                and obs.get("tree"):
            try:
                fidx, _ = post.field.resolve(obs["tree"])
                block = _element_block(obs["tree"], fidx)
                if post.attribute:
                    vals = list(post._attr_values(block))
                else:
                    # The ANSWER lives in Value; Description/Help/Identifier are
                    # labels for the field, not its content. A field with no
                    # Value simply has no answer to report — falling back to any
                    # attribute is how "새 문서 만들어줘" came back as
                    # "결과는 First Text View 입니다".
                    vals = [Selector._attr(ln, "Value") for ln in block.splitlines()]
                    vals = [v for v in vals if v]
                result_value = vals[0] if vals else None
            except Exception:
                result_value = None
        shown = None
        for cap in step.captures:
            if not obs.get("tree"):
                break
            value = cap.read(obs["tree"])
            if value is None:
                if cap.optional:
                    # Not every app exposes everything. Finder names a folder's
                    # files for Documents and Applications but not for Downloads,
                    # so a recipe that reads both a count and the names should
                    # still answer with what it could see.
                    continue
                res.record(status="error", step=i, action=step.action,
                           error="capture_failed",
                           detail=f"{cap.name!r} 값을 화면에서 읽지 못했습니다")
                return res
            # The run needs the real value; the log and any trace must not have
            # it. Keep the two apart rather than masking at the source, which
            # would hand "[redacted]" to the next step (advisor finding).
            captures[cap.name] = value
            if cap.name in sensitive_names:
                res.protect(value)
            if cap.name in sensitive_names and hasattr(driver, "protect_value"):
                driver.protect_value(value)
            shown = "[redacted]" if cap.name in sensitive_names else value
        # Keep the effective predicate, not a template re-rendered after this
        # step may have overwritten a capture with the same name.
        if _retain_continuation:
            last_checkpoint_index=i
            last_checkpoint=deepcopy(post)
        res.record(status="ok", step=i, action=step.action, resolver=trace,
                   risk=risk,
                   verified=("already_true" if pre_satisfied
                             else step.expect or (post and "postcond")),
                   result=result_value, captured=shown,
                   **({'verification_evidence': 'ax_window_list_empty'}
                      if obs.get('verification_evidence') == 'ax_window_list_empty' else {}))

    final = next((e.get("result") for e in reversed(res.log)
                  if e.get("status") == "ok" and e.get("result")), None)
    shown_captures = {k: ("[redacted]" if k in sensitive_names else v)
                      for k, v in captures.items()}
    res.record(status="done", steps=len(flow.steps), result=final,
               captured=shown_captures or None)
    return res


# ------------------------------------------------------------ example flow

DIGITS = {"0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four",
          "5": "Five", "6": "Six", "7": "Seven", "8": "Eight", "9": "Nine"}


def calculator_multiply() -> Flow:
    """A parameterized recipe: the numbers are inputs, not part of the procedure.

    `for_each` walks the digits of each operand, `tables` maps a digit to the
    button that types it, and `derived` computes the answer so the run can still
    verify its own result (D7) for operands nobody hard-coded."""
    from .contract import Resource
    KP = "CalculatorKeypadView"
    ENTRY = Resource(object_type="계산기 입력", scope="entry")
    keypad = SafetyContract(effects=["write_ephemeral"], resource=ENTRY,
                            reversibility="reversible")
    ac = SafetyContract(effects=["delete"], resource=ENTRY, reversibility="reversible")

    def digits_of(param, label):
        return Step("click",
                    Selector(role="Button", identifier="{{digit[item]}}", ancestor=KP),
                    intent_text=f"{label} {{{{item}}}} 입력", safety=keypad,
                    for_each=f"{{{{{param}}}}}")

    return Flow(
        name="calc_multiply", target="com.apple.calculator",
        params={"a": "12", "b": "34"},
        tables={"digit": DIGITS},
        derived={"result": "{{a}} * {{b}}"},
        steps=[
            Step("click", Selector(role="Button", identifier=["AllClear", "Clear"],
                                   ancestor=KP), intent_text="초기화", safety=ac),
            digits_of("a", "첫째 수"),
            Step("click", Selector(role="Button", identifier="Multiply", ancestor=KP),
                 intent_text="곱하기", safety=keypad),
            digits_of("b", "둘째 수"),
            Step("click", Selector(role="Button", identifier="Equals", ancestor=KP),
                 intent_text="계산", safety=keypad, answers=True,
                 postcondition=Postcondition(
                     field=Selector(role="ScrollArea", identifier="StandardInputView"),
                     equals="{{result}}", attribute="Value")),
        ])


def calculator_12x34() -> Flow:
    """Hand-authored flow — the codex demo as a deterministic, tokenless replay."""
    from .contract import Resource
    KP = "CalculatorKeypadView"   # ancestor scope: keeps repeated labels unambiguous
    # Contracts state MEANING; the deterministic policy derives authorization.
    # No keyword is consulted anywhere on this path.
    ENTRY = Resource(object_type="계산기 입력", scope="entry")
    keypad = SafetyContract(effects=["write_ephemeral"], resource=ENTRY,
                            reversibility="reversible")
    ac = SafetyContract(effects=["delete"], resource=ENTRY,
                        reversibility="reversible")   # clears the ENTRY -> none
    def bind(ident):
        return ContextBinding(bundle_id="com.apple.calculator", role="Button",
                              identifier=ident, ancestor=KP, operation="click")
    def digit(name, d):
        return Step("click", Selector(role="Button", identifier=name, ancestor=KP),
                    intent_text=f"{d} 버튼", safety=keypad, binding=bind(name))
    return Flow(name="calc_12x34", target="com.apple.calculator", steps=[
        Step("click", Selector(role="Button", identifier=["AllClear", "Clear"], ancestor=KP),
             intent_text="초기화", safety=ac, binding=bind("AllClear")),
        digit("One", "1"), digit("Two", "2"),
        Step("click", Selector(role="Button", identifier="Multiply", ancestor=KP),
             intent_text="곱하기", safety=keypad, binding=bind("Multiply")),
        digit("Three", "3"), digit("Four", "4"),
        Step("click", Selector(role="Button", identifier="Equals", ancestor=KP),
             intent_text="계산", safety=keypad, binding=bind("Equals"),
             answers=True,
             # P1 structured postcondition: read the result field, poll until 408.
             postcondition=Postcondition(
                 field=Selector(role="ScrollArea", identifier="StandardInputView"),
                 equals="408", attribute="Value")),
    ])


if __name__ == "__main__":
    d = Driver()
    flow = calculator_12x34()
    print(flow.to_json())
    r = replay(d, flow, narrate=lambda s: print(f"  … {s}"))
    print("\nreplay ok:", r.ok)
    for e in r.log:
        print(" ", e)
