"""NLC deterministic replay — token-efficient computer-use emulation.

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
from dataclasses import dataclass, field, asdict, replace

from . import safety
from .contract import (SafetyContract, ContextBinding, policy,
                       validate, describe, ApprovalToken)
from .driver import Driver


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


def _render(value, ctx: dict):
    """Substitute {{name}} and {{table[key]}} inside a string (or, recursively,
    inside a dict/list). Non-strings pass through untouched."""
    if isinstance(value, dict):
        return {k: _render(v, ctx) for k, v in value.items()}
    if isinstance(value, list):
        return [_render(v, ctx) for v in value]
    if not isinstance(value, str):
        return value

    def sub(m):
        name, key = m.group(1), m.group(2)
        if key is None:
            if name not in ctx:
                raise KeyError(f"파라미터 {name!r} 가 없습니다")
            return str(ctx[name])
        table = ctx.get("__tables__", {}).get(name)
        if table is None:
            raise KeyError(f"테이블 {name!r} 가 없습니다")
        k = str(ctx.get(key, key))
        if k not in table:
            raise KeyError(f"테이블 {name!r} 에 {k!r} 항목이 없습니다")
        return str(table[k])

    return _TEMPLATE.sub(sub, value)


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
    a, b = token.lower(), want.lower()
    return a.removeprefix("ax") == b.removeprefix("ax")


def _alternatives(value) -> set[str]:
    """Accepted spellings of one selector field, normalized and lowercased."""
    items = [value] if isinstance(value, str) else list(value)
    return {_norm(v).lower() for v in items}


class TargetMissing(LookupError):
    """Zero elements matched the selector."""


class TargetAmbiguous(LookupError):
    """More than one element matched — never silently pick one (codex policy)."""


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

    # Known AX attribute keys as the driver formats them ("Key: value, Key2: …").
    _ATTR_KEYS = ("Title", "Description", "Value", "Help", "Identifier",
                  "Placeholder")

    @staticmethod
    def _stable_id(v: str) -> str:
        """AXIdentifier can carry volatile state appended after ';' — a real
        device showed 'StandardInputView;value:408', where the identifier
        changes with every keystroke. The part before the first ';' is the
        stable identity; the rest is state. Splitting on this structured
        separator does NOT reintroduce prefix matching ('Save' still never
        matches 'SaveAs'), so exactness is preserved where it matters."""
        return v.split(";", 1)[0].strip()

    @classmethod
    def _attr(cls, line: str, key: str) -> str | None:
        """Extract one attribute's EXACT value from a tree line (not substring).
        Value runs from 'Key: ' to the next ', <KnownKey>: ' or end / [disabled].
        Exact comparison prevents 'Save' matching 'SaveAs' (review finding).
        Unicode bidi/format marks (e.g. the LTR mark macOS Calculator wraps its
        digits in) are stripped so '\\u200e408' compares equal to '408'."""
        others = "|".join(k for k in cls._ATTR_KEYS if k != key)
        m = re.search(rf"{key}:\s*(.+?)"
                      rf"(?:,\s*(?:{others}):|\s*\[(?:disabled|focused)\]|$)", line)
        return _norm(m.group(1).strip()) if m else None

    def matches(self, line: str) -> bool:
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
        return any([self.identifier, self.title, self.desc, self.value])

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
            if not prev.strip():
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
            if not prev.strip():
                continue
            pind = len(prev) - len(prev.lstrip())
            if pind < indent:
                indent, top = pind, prev
                if pind == 0:
                    break
        return "[focused]" in top

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
            return hits[self.allow_nth], f"{strat}[nth={self.allow_nth}]"
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
            if not prev.strip():
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
    a, b = canon(expected), canon(observed)
    if a == b:
        return True
    # Decimal, not float: postconditions verify money and counts, and float()
    # makes 9007199254740992 and ...93 compare equal (advisor finding).
    from decimal import Decimal, InvalidOperation
    try:
        return Decimal(a) == Decimal(b)
    except (InvalidOperation, ValueError):
        return False


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

    def _attr_values(self, block: str):
        keys = [self.attribute] if self.attribute else Selector._ATTR_KEYS
        for ln in block.splitlines():
            for k in keys:
                v = Selector._attr(ln, k)
                if v is not None:
                    yield v

    def check(self, tree: str) -> bool:
        if self.absent is not None and self.absent in tree:
            return False
        if self.equals is None and self.contains is None:
            return self.absent is not None   # absence alone is a real check
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

    def bind(self, **values) -> "Flow":
        """Return a concrete, runnable Flow with parameters substituted and
        `for_each` steps expanded. The original stays a template."""
        ctx = {**self.params, **{k: v for k, v in values.items() if v is not None}}
        missing = [k for k, v in ctx.items() if v is None]
        if missing:
            raise ValueError(f"파라미터가 필요합니다: {', '.join(missing)}")
        for name, expr in self.derived.items():
            ctx[name] = _arith(_render(expr, {**ctx, "__tables__": self.tables}))
        ctx["__tables__"] = self.tables

        out: list[Step] = []
        for st in self.steps:
            items = [None]
            if st.for_each:
                items = list(_render(st.for_each, ctx))
            for it in items:
                c = dict(ctx) if it is None else {**ctx, "item": it}
                out.append(_render_step(st, c))
        # The target is rendered too: which app a recipe drives can itself be
        # an input, as it is for "close the dialog in <app>".
        return Flow(name=self.name, target=_render(self.target, ctx), steps=out,
                    schema=self.schema)

    def with_learned(self, learned: list) -> "Flow":
        """A copy of this recipe with the selectors an escalation worked out.

        This is what makes an escalation pay for itself: the judgment came back
        as a selector, so the recipe can keep it and never buy that answer
        again. A recipe gets cheaper the more it has been surprised.
        """
        steps = list(self.steps)
        for idx, sel in learned:
            if 0 <= idx < len(steps):
                steps[idx] = replace(steps[idx], selector=Selector(**sel))
        return replace(self, steps=steps)

    def to_json(self) -> str:
        d = {"name": self.name, "target": self.target, "schema": self.schema,
             "steps": [{**asdict(s)} for s in self.steps]}
        for k in ("params", "tables", "derived"):
            if getattr(self, k):
                d[k] = getattr(self, k)
        return json.dumps(d, ensure_ascii=False, indent=2)

    @staticmethod
    def from_json(text: str) -> "Flow":
        d = json.loads(text)
        steps = []
        for s in d["steps"]:
            sel = Selector(**s["selector"]) if s.get("selector") else None

            def _post(pc):
                if not pc:
                    return None
                pf = Selector(**pc["field"]) if pc.get("field") else None
                return Postcondition(field=pf, equals=pc.get("equals"),
                                     contains=pc.get("contains"),
                                     absent=pc.get("absent"),
                                     attribute=pc.get("attribute"))

            post = _post(s.get("postcondition"))
            skip_if = _post(s.get("skip_if"))
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
            steps.append(Step(action=s["action"], selector=sel,
                              params=s.get("params", {}),
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
                              on_uncertain=list(s.get("on_uncertain", []))))
        return Flow(name=d["name"], target=d["target"], steps=steps,
                    schema=d.get("schema", 1),   # older files predate contracts
                    params=d.get("params", {}), tables=d.get("tables", {}),
                    derived=d.get("derived", {}))


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
            field=Selector(**c["field"]) if c.get("field") else None,
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


def _candidates(selector: "Selector", tree: str, limit: int = 12) -> list[str]:
    """The things on screen that COULD have been meant, as a plain list.

    An escalation needs choices, not prose. Same source as the message a person
    reads on failure — what is there, named the way the screen names it."""
    # With no selector to go on — a menu step has none — the useful answer is
    # not "every label on screen" but "what can actually be pressed right now".
    # That is what tells a reader a dialog is in the way.
    role = selector.role if selector is not None and selector.role else "Button"
    out = []
    for line in tree.splitlines():
        toks = line.strip().split()
        if len(toks) < 2 or not _same_role(toks[1], role):
            continue
        if role == "Button" and "[disabled]" in line:
            continue
        for key in ("Title", "Description", "Value", "Identifier"):
            v = Selector._attr(line, key)
            if v:
                out.append(v)
                break
        if len(out) >= limit:
            break
    return out


def _run_fix(driver, flow, steps, narrate) -> bool:
    """Run the steps an escalation asked for, before retrying the real one.

    A nested replay on purpose: the inserted steps face the same contracts, the
    same gate and the same verification as any other. Nothing was pre-approved
    for them, so a step whose meaning needs consent simply gates and the fix
    fails — an escalation can clear a dialog out of the way, it cannot decide
    that something may be sent.
    """
    if not steps:
        return False
    patch = Flow(name=f"{flow.name}:fix", target=flow.target, steps=list(steps),
                 schema=flow.schema)
    out = replay(driver, patch, narrate=narrate)
    if not out.ok and narrate:
        bad = next((e for e in out.log if e.get("status") in ("error", "gated")), {})
        why = bad.get("error") or bad.get("gate") or "?"
        narrate(f"  (고치기가 실패했습니다: {why} {str(bad.get('detail'))[:90]})")
    return out.ok


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
                             absent=_render(p.absent, ctx), attribute=p.attribute)

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
            ancestor=_render(binding.ancestor, ctx),
            # Recomputed from the rendered payload: a parameterized type_text
            # changes what is typed, so a copied digest would invalidate every
            # concrete run (advisor finding).
            params_digest=(_payload_digest(_render(st.params, ctx))
                           if binding.params_digest is not None else None),
            resource_identity=_render(binding.resource_identity, ctx),
            destination_identity=_render(binding.destination_identity, ctx))
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
                on_uncertain=list(st.on_uncertain))


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
    live = ContextBinding(policy_version=rec.policy_version)

    if rec.bundle_id is not None:
        live.bundle_id = (driver.identity(flow.target)
                          if hasattr(driver, "identity") else flow.target)
    if rec.role is not None and step.selector is not None:
        live.role = step.selector.role
    if rec.identifier is not None and step.selector is not None:
        live.identifier = _first_id(step.selector)
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
    options: list[str] = field(default_factory=list)   # what IS on screen
    tree_diff: str = ""  # what changed since the previous observation


@dataclass
class Resolution:
    """The answer to an Uncertainty, expressed as IR rather than as prose.

    A model that says "click the second one" has not helped; a model that hands
    back a selector has. That is what makes an escalation something the recipe
    can LEARN from — the resolution is the same kind of thing the recipe is
    already made of.
    """
    kind: str                       # use_selector | insert_steps | give_up
    selector: "Selector | None" = None
    steps: list = field(default_factory=list)
    reason: str = ""


class _Budget:
    """How many escalations this run may spend. Zero means "never ask"."""

    def __init__(self, calls: int = 0):
        self.left = calls
        self.spent = 0

    def take(self) -> bool:
        if self.left <= 0:
            return False
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
    if not budget.take():
        return None
    if narrate:
        narrate(f"  ? 예상과 달라 판단이 필요합니다: {why.detail[:80]}")
    try:
        return escalate(why)
    except Exception:
        return None


class ReplayResult:
    def __init__(self):
        self.ok = True
        self.log: list[dict] = []

    @property
    def learned(self) -> list[tuple[int, dict]]:
        """Selector fixes this run discovered, as (step index, selector dict).

        Only `use_selector` resolutions appear here, and the distinction is the
        point. "The recipe named the wrong element, here is the right one" is
        durable knowledge about the app. "Something was in the way this time" is
        not — baking a Cancel step into a recipe would cancel dialogs that
        should not be cancelled. Situational recovery stays in the log.
        """
        return [(e["step"], e["learned"]["selector"]) for e in self.log
                if e.get("status") == "resolved"
                and isinstance(e.get("learned"), dict)
                and "selector" in e["learned"]]

    def record(self, **kw):
        self.log.append(kw)
        if kw.get("status") == "error":
            self.ok = False


def replay(driver: Driver, flow: Flow, approvals: dict[int, bool] | None = None,
           narrate=None, escalate=None, budget: int = 0) -> ReplayResult:
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
    """
    approvals = approvals or {}
    res = ReplayResult()
    spend = _Budget(budget if escalate is not None else 0)
    captures: dict = {}      # values read off the screen during this run

    obs = driver.observe(flow.target, "tree_full")
    if obs.get("error") == "no_window_content" and flow.steps \
            and flow.steps[0].action == "menu":
        # "Finder 열어줘" starts from an app with no window at all. Menus are
        # app-scoped, so such a recipe can legitimately begin here; anything
        # needing window content still fails honestly on its own step.
        obs = driver.observe(flow.target, "tree_full", require_window=False)
    if obs["type"] != "observation":
        res.record(status="error", stage="initial_observe", detail=obs)
        return res

    # A contract is the authorization mechanism, not an optional annotation.
    # Without this check a step that simply omits `safety` falls through to the
    # legacy label classifier — so "the model never declares risk" would be
    # false in practice (advisor finding). v1 recipes predate contracts and are
    # still honoured, but must opt in explicitly.
    if flow.schema >= 2:
        missing = [i for i, st in enumerate(flow.steps)
                   if st.action != "observe" and st.safety is None]
        if missing:
            res.record(status="error", error="contract_required",
                       detail=f"스텝 {missing} 에 safety 계약이 없습니다 "
                              "(schema 2는 모든 실행 스텝에 계약을 요구합니다)")
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

    for i, step in enumerate(flow.steps):
        # Values captured earlier in THIS run are only known now, so a step is
        # rendered against them just before it executes. Flow.bind() resolves
        # parameters ahead of time; this resolves what the screen told us since
        # (without it, "capture the total, then type it" could not work — the
        # promise the skill documents).
        if captures:
            step = _render_step(step, {**captures, "__tables__": flow.tables})
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

        action = {"name": step.action, **step.params}
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
                    # The judgment comes back as a selector, so it is the same
                    # kind of thing the recipe is made of — which is what lets
                    # the recipe keep it and stop paying for this next time.
                    step = replace(step, selector=fix.selector)
                    res.record(status="resolved", step=i, kind=kind,
                               learned={"selector": asdict(fix.selector)},
                               reason=fix.reason)
                    continue
                res.record(status="error", step=i, action=step.action,
                           error=kind, detail=f"{e}. {nearby}")
                return res
            idx = action["element_index"]
            target_line = next((l for l in obs["tree"].splitlines()
                                if re.match(rf"\s*{idx}\s", l)), "")
            # Precondition (TOCTOU/enabled): re-verify the resolved target on the
            # fresh observation right before acting.
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

        r = driver.act(obs["obs_id"], flow.target, action,
                       risk_class=risk, intent_text=gate_desc,
                       approved=_approved(approvals, i, step, action))

        if r["type"] in ("confirm_request", "handoff_request"):
            res.record(status="gated", step=i, gate=r["type"], risk=risk,
                       reason=reason, detail=r)
            return res
        if r["type"] == "error":
            fix = _escalate(escalate, spend, step, Uncertainty(
                kind="action_failed", step=i, intent=step.intent_text,
                detail=str(r.get("detail") or r.get("error")),
                options=_candidates(step.selector, obs.get("tree", ""))), narrate)
            if fix is not None and fix.kind == "insert_steps" and \
                    _run_fix(driver, flow, fix.steps, narrate):
                res.record(status="resolved", step=i, kind="action_failed",
                           learned={"insert_before": len(fix.steps)},
                           reason=fix.reason)
                # The screen moved, so everything derived from it is stale.
                obs = driver.observe(flow.target, "tree_full", require_window=False)
                if obs["type"] == "observation" and step.selector is not None:
                    try:
                        action["element_index"], trace = \
                            step.selector.resolve(obs["tree"])
                    except LookupError:
                        pass
                r = driver.act(obs["obs_id"], flow.target, action,
                               risk_class=risk, intent_text=gate_desc,
                               approved=_approved(approvals, i, step, action))
            if r["type"] == "error":
                res.record(status="error", step=i, action=step.action, detail=r)
                return res

        # D7: verify by polling to a monotonic deadline, not a blind sleep.
        post = step.postcondition
        if post is not None:
            obs = driver.wait_until(flow.target, post.check)
            if obs["type"] != "observation":
                seen = None
                last = obs.get("last_obs") or {}
                if post.field is not None and last.get("tree"):
                    try:
                        idx, _ = post.field.resolve(last["tree"])
                        seen = list(post._attr_values(_element_block(last["tree"], idx)))
                    except Exception:
                        seen = "field-not-found"
                fix = _escalate(escalate, spend, step, Uncertainty(
                    kind="postcondition_timeout", step=i,
                    intent=step.intent_text,
                    detail=f"기대값 {post.equals or post.contains!r}, "
                           f"화면에서 읽은 값 {seen!r}",
                    options=_candidates(post.field, last.get("tree", ""))),
                    narrate)
                if fix is not None and fix.kind == "insert_steps" and \
                        _run_fix(driver, flow, fix.steps, narrate):
                    # The step may have needed something done first — a
                    # selection, a focus, a dialog cleared — so do the whole
                    # thing again now that it has been. A fresh observation is
                    # required, not optional: the one in hand is the timeout,
                    # which carries no obs_id to act against.
                    fresh = driver.observe(flow.target, "tree_full")
                    if fresh["type"] == "observation":
                        retry = dict(action)
                        if step.selector is not None:
                            try:
                                retry["element_index"], _ = \
                                    step.selector.resolve(fresh["tree"])
                            except LookupError:
                                retry = None
                        if retry is not None:
                            driver.act(fresh["obs_id"], flow.target, retry,
                                       risk_class=risk, intent_text=gate_desc,
                                       approved=_approved(approvals, i, step,
                                                          retry))
                            obs = driver.wait_until(flow.target, post.check)
                            if obs["type"] == "observation":
                                res.record(status="resolved", step=i,
                                           kind="postcondition_timeout",
                                           learned={"insert_before":
                                                    len(fix.steps)},
                                           reason=fix.reason)
                if obs["type"] != "observation":
                    res.record(status="error", step=i, action=step.action,
                               error="postcondition_timeout",
                               expected=post.equals, observed=seen)
                    return res
        else:
            obs = driver.observe(flow.target, "tree_full")
            if obs["type"] != "observation":
                res.record(status="error", step=i, stage="verify_observe", detail=obs)
                return res
            if step.expect is not None and step.expect not in obs["tree"]:
                # settle a moment more, then re-check (transient render lag)
                obs = driver.wait_until(flow.target, lambda t: step.expect in t,
                                        timeout_ms=3000)
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
            shown = "[redacted]" if cap.sensitive else value
        res.record(status="ok", step=i, action=step.action, resolver=trace,
                   risk=risk,
                   verified=("already_true" if pre_satisfied
                             else step.expect or (post and "postcond")),
                   result=result_value, captured=shown)

    final = next((e.get("result") for e in reversed(res.log)
                  if e.get("status") == "ok" and e.get("result")), None)
    sensitive_names = {c.name for st in flow.steps for c in st.captures
                       if c.sensitive}
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
