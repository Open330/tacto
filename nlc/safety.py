"""NLC safety rule engine — the §1.5 confirmations policy as code (D6).

Maps an action + its resolved target's context to a risk_class. This is a
deliberately conservative, auditable rule set: when a signal is ambiguous it
escalates rather than waves through. The classifier is deterministic and takes
no model tokens; it runs on *every* replay execution, not just authoring.

risk_class levels (rising):
  none        — observation / non-mutating, always allowed
  preapproved — allowed only if the original request named it (login, upload…)
  confirm     — blocking confirm right before the action, even if pre-approved
  handoff     — the user must do it themselves (password submit, safety bypass)
"""

from __future__ import annotations

import re

# Signals matched against the target's tree line (label/identifier/description),
# lowercased. Word-ish boundaries keep "clear" from firing on "nuclear".
_HANDOFF = [
    r"\bchange password\b", r"\bnew password\b", r"\bconfirm password\b",
    r"\bnot secure\b", r"\bproceed anyway\b", r"\bbypass\b",
]
_CONFIRM = [
    r"\bdelete\b", r"\bremove\b", r"\btrash\b", r"\bempty trash\b",
    r"\bpay\b", r"\bpurchase\b", r"\bbuy now\b", r"\bplace order\b",
    r"\bcheckout\b", r"\btransfer\b", r"\bsend\b", r"\bpost\b", r"\bpublish\b",
    r"\bsubmit\b", r"\bconfirm\b", r"\bunsubscribe\b", r"\bsubscribe\b",
    r"\bcreate account\b", r"\bsign up\b", r"\bapi key\b",
    r"\bcaptcha\b", r"\bcaptcha\b",
]
_PREAPPROVED = [
    r"\blog ?in\b", r"\bsign ?in\b", r"\bupload\b", r"\bchoose file\b",
    r"\bmove\b", r"\brename\b", r"\ballow\b", r"\bage verif",
]

_H = [re.compile(p) for p in _HANDOFF]
_C = [re.compile(p) for p in _CONFIRM]
_P = [re.compile(p) for p in _PREAPPROVED]

# Korean signals. \b word boundaries don't work across Hangul, so these are
# plain substrings (the product is Korean-first — English-only regexes would
# silently pass 결제/삭제/전송 as risk-free, a critical false negative).
_HANDOFF_KO = ["비밀번호 변경", "비밀번호 재설정", "보안 우회", "안전하지 않"]
# NOTE: bare "확인"(OK) and "취소"(Cancel) are deliberately EXCLUDED — "취소"
# usually *stops* a risky action and a plain "확인" on an info dialog is harmless
# (review finding: they were too broad). Keep specific destructive/transmit verbs.
_CONFIRM_KO = ["삭제", "휴지통", "결제", "구매", "주문", "결제하기",
               "전송", "보내기", "전송하기", "게시", "제출", "구독",
               "구독 취소", "결제 취소", "예약 취소", "주문 취소", "로그아웃",
               "계정 만들기", "계정 생성", "이체", "송금", "예약",
               "설치", "확장 프로그램"]
_PREAPPROVED_KO = ["로그인", "로그인하기", "업로드", "파일 선택", "이동",
                   "이름 변경", "이름변경", "허용", "나이 확인"]

# Field kinds that count as sensitive-data entry -> transmission on submit.
_SENSITIVE_FIELD = re.compile(
    r"password|passcode|otp|one[- ]time|card number|cvv|ssn|social security|"
    r"account number|routing")
_SENSITIVE_FIELD_KO = ["비밀번호", "비번", "카드 번호", "카드번호", "카드 정보",
                       "cvc", "주민등록번호", "주민번호", "인증번호", "일회용 비밀번호"]


_INPUT_CLEAR_WORDS = ("all clear", "allclear", "모두 지우기", "지우기", "clear",
                      "backspace", "delete the last digit", "마지막 자리")
_STORED_EN = ("account", "mail", "message", "file", "photo", "history", "data")
_STORED_KO = ("계정", "메일", "메시지", "파일", "사진", "기록")


def classify(action: dict, target_line: str = "", *, into_sensitive_field=False,
             is_submit=False) -> tuple[str, str]:
    """Return (risk_class, reason).

    action:       the L2 action dict (name + params).
    target_line:  the resolved target's tree line (its label/identifier).
    into_sensitive_field / is_submit: caller-supplied context flags for
                  typing into credential fields and for final-submit steps.
    """
    name = action.get("name", "")
    if name in ('select_option', 'select_custom_option') and isinstance(action.get('option_label'), str):
        # Selecting an option can invoke a command. Keep the parent control's
        # signal as well: a harmless-looking option cannot lower its risk.
        without_label = {k: v for k, v in action.items() if k != 'option_label'}
        parent, parent_reason = classify(without_label, target_line,
                                        into_sensitive_field=into_sensitive_field, is_submit=is_submit)
        selected, selected_reason = classify(without_label, action['option_label'],
                                            into_sensitive_field=into_sensitive_field, is_submit=is_submit)
        return merge(parent, selected), parent_reason + '; selected option: ' + selected_reason
    low = target_line.lower()
    raw = target_line   # Korean matched case-sensitively on the original text

    # Observation-like / pure navigation never gates.
    if name in ("scroll",):
        return "none", "non-mutating navigation"

    # Input-editing controls (a calculator's AC / a field's backspace) clear an
    # ENTRY, not stored data, and are trivially reversible. They must not gate —
    # generic words like "지우기"/"clear"/"delete" on such a control were firing
    # false positives that blocked ordinary flows (real-run finding).
    if name == "click" and any(k in low or k in raw for k in _INPUT_CLEAR_WORDS):
        # only when it is NOT about stored items (mail/file/account …)
        if not any(w in low for w in _STORED_EN) and \
           not any(w in raw for w in _STORED_KO):
            return "none", "input-clearing control (not data deletion)"

    # Inspect the action payload, not just the label. A destructive key combo
    # carries its own risk regardless of what the target line says (review
    # finding: press_key('cmd+delete') classified as none).
    if name == "press_key":
        k = str(action.get("key", "")).lower()
        if k in ("delete", "forwarddelete") or "delete" in k.split("+") \
                or k in ("cmd+delete", "super+delete", "cmd+backspace"):
            return "confirm", f"destructive key: {k}"

    # Typing a password/OTP is transmission-adjacent -> hand off to the user.
    if name in ("type_text", "set_value", "select_option", "select_custom_option") and into_sensitive_field:
        return "handoff", "credential entry: user must type it"
    if name in ("type_text", "set_value", "select_option", "select_custom_option") and (
            _SENSITIVE_FIELD.search(low)
            or any(k in raw for k in _SENSITIVE_FIELD_KO)):
        return "handoff", "target looks like a credential field"

    for rx in _H:
        if rx.search(low):
            return "handoff", f"handoff signal: {rx.pattern}"
    for k in _HANDOFF_KO:
        if k in raw:
            return "handoff", f"handoff signal(ko): {k}"

    # A final submit inherits the risk of what it submits.
    if is_submit:
        return "confirm", "final submit step"

    for rx in _C:
        if rx.search(low):
            return "confirm", f"confirm signal: {rx.pattern}"
    for k in _CONFIRM_KO:
        if k in raw:
            return "confirm", f"confirm signal(ko): {k}"
    for rx in _P:
        if rx.search(low):
            return "preapproved", f"preapproval-eligible: {rx.pattern}"
    for k in _PREAPPROVED_KO:
        if k in raw:
            return "preapproved", f"preapproval-eligible(ko): {k}"

    return "none", "no risk signal"


def merge(recorded: str, classified: str) -> str:
    """Escalation-only merge: the stricter of the recipe's declared risk_class
    and the live classifier wins. A recipe can raise risk but never lower what
    the classifier detects at run time."""
    order = {"none": 0, "preapproved": 1, "confirm": 2, "handoff": 3}
    # Unknown/garbage risk strings canonicalize to the strictest KNOWN value
    # ("handoff"), never pass through as-is — otherwise act() (which only checks
    # the three known gated levels) would run them (review finding: returning the
    # original string was still fail-open).
    rc = recorded if recorded in order else "handoff"
    cc = classified if classified in order else "handoff"
    return rc if order[rc] >= order[cc] else cc


# ---- richer authorization model (codex browser-safety guidance) -------------
#
# A regex over a button's name is a first-pass signal, not a real authorization
# decision — a "Delete" label is not necessarily a data deletion, and typing a
# value into a form can be a *transmission* the name never hints at. The web
# safety review recommends deciding on the tuple, not the label:
#
#   effect        read | write | transmit | delete | financial | destructive
#   data_class    none | personal | credential | financial | biometric | health
#   destination   same-origin | third-party (origin/account the data reaches)
#   authorization user-authored intent | pre-approved scope | none
#
# and binding an approval token to (action + target + payload-digest +
# destination + expiry), re-evaluated on navigation/account/file change. A page
# that *says* "the user approved this" is untrusted content, never an approval.
# `classify()` above is the label-signal layer; this tuple layer is the P2+
# authorization engine that sits over it. Documented here as the agreed shape;
# the current build ships the label layer plus the escalation merge.


# ---------------------------------------------------------------- effects

# The same signals, read for WHAT an action does rather than how alarming it
# looks. `classify` answers "how much does this need?"; policy() wants "what is
# it?", and a risk level is not a thing you can compare against what the user
# asked for. Both read one table so they cannot drift.
_EFFECT_KO = [
    ("delete", ("삭제", "휴지통", "지우기", "버리기")),
    ("financial", ("결제", "구매", "주문", "이체", "송금", "결제하기")),
    ("transmit", ("전송", "보내기", "전송하기", "게시", "제출", "공유")),
    ("write_persistent", ("저장", "내보내기", "이름 변경", "이름변경", "이동")),
    ("install", ("설치", "확장 프로그램")),
    ("system_config", ("비밀번호 변경", "비밀번호 재설정", "보안 우회",
                       "안전하지 않", "로그아웃", "계정 만들기", "계정 생성")),
]
_EFFECT_EN = [
    ("delete", (r"\bdelete\b", r"\bremove\b", r"\btrash\b", r"\bempty trash\b")),
    ("financial", (r"\bpay\b", r"\bpurchase\b", r"\bbuy now\b",
                   r"\bplace order\b", r"\bcheckout\b", r"\btransfer\b")),
    ("transmit", (r"\bsend\b", r"\bpost\b", r"\bpublish\b", r"\bsubmit\b",
                  r"\bshare\b")),
    ("write_persistent", (r"\bsave\b", r"\bexport\b", r"\brename\b", r"\bmove\b")),
    ("install", (r"\binstall\b", r"\bextension\b")),
    ("system_config", (r"\bchange password\b", r"\bnew password\b",
                       r"\bbypass\b", r"\bnot secure\b", r"\bsign out\b")),
]
_EFFECT_RX = [(eff, [re.compile(p) for p in pats]) for eff, pats in _EFFECT_EN]


def document_edit_effects(action: dict, target_line: str = "", *, target: str = "") -> set:
    """Host-known TextEdit document mutations may autosave even when untitled.

    Target must come from the executor, never model-supplied action fields.
    Only the resolved role/identifier is inspected, not document text labels.
    Menu paths describe the actual command being dispatched; opening Save or
    selecting text is not itself a document mutation. This is a finite list,
    not an exhaustive classifier for every TextEdit menu, plugin, or shortcut.
    Unknown-role writes cannot establish a transient field and stay persistent;
    resolved ordinary TextFields (name/search) retain their existing treatment.
    """
    if target != "com.apple.TextEdit":
        return set()
    name = action.get("name")
    role_match = re.match(r"\s*\d+\s+(?:AX)?(\w+)", target_line)
    role = role_match.group(1) if role_match else ""
    body = role == "TextArea" or bool(re.search(
        r"(?:^|[, ]+)Identifier: First Text View(?:,|$)", target_line))
    if name in ("set_value", "type_text") and body:
        return {"write_persistent"}
    if name in ("type_text", "set_value") and not role:
        # No resolved field means the current document cannot be excluded.
        return {"write_persistent"}
    if name == "press_key":
        from .protocol import NAVIGATION_KEYS
        key = action.get("key")
        if isinstance(key, str) and key not in NAVIGATION_KEYS:
            normalized = key.lower().replace("command+", "cmd+").replace("super+", "cmd+")
            edits = {"cmd+v", "cmd+x", "cmd+z", "cmd+shift+z", "shift+cmd+z",
                     "cmd+b", "cmd+i", "cmd+u", "delete", "forwarddelete", "backspace",
                     "return", "enter", "space", "spacebar"}
            parts = normalized.split("+")
            if (normalized in edits
                    or ("cmd" in parts and parts[-1] in {"v", "x", "z", "b", "i", "u"})
                    or any(part in ("delete", "backspace", "forwarddelete") for part in parts)
                    or len(key) == 1 or (len(parts) == 2 and parts[0] == "shift" and len(parts[1]) == 1)):
                return {"write_persistent"}
    if name == "menu":
        path = action.get("path")
        if not isinstance(path, list) or len(path) < 2:
            return set()
        def alternatives(part):
            return part if isinstance(part, list) else [part]
        roots, leaves = alternatives(path[0]), alternatives(path[-1])
        edit = {"Cut", "Paste", "Paste and Match Style", "Delete", "Undo", "Redo",
                "오려두기", "붙여넣기", "스타일 일치시켜 붙여넣기", "삭제", "실행 취소", "실행 복귀"}
        formatting = {"Bold", "Italic", "Underline", "Bigger", "Smaller", "굵게", "볼드",
                      "기울임꼴", "밑줄", "더 크게", "더 작게", "Make Plain Text",
                      "Make Rich Text", "일반 텍스트 만들기", "리치 텍스트 만들기"}
        if ((any(x in ("Edit", "편집") for x in roots) and any(x in edit for x in leaves))
                or (any(x in ("Format", "포맷", "서식") for x in roots)
                    and any(x in formatting for x in leaves))):
            return {"write_persistent"}
    return set()


def effects_of(action: dict, target_line: str = "", *, target: str = "") -> tuple[set, str]:
    """What this action DOES, as effects — never as a risk level.

    A risk level cannot be compared against what the user asked for; an effect
    can. So the screen loop reads the control for effects, builds a contract out
    of them, and hands it to the same `policy()` the recipes go through, rather
    than keeping a second notion of "risky" that nothing else understands.
    """
    name = action.get("name", "")
    if name in ('select_option', 'select_custom_option') and isinstance(action.get('option_label'), str):
        without_label = {k: v for k, v in action.items() if k != 'option_label'}
        parent, parent_reason = effects_of(without_label, target_line, target=target)
        selected, selected_reason = effects_of(without_label, action['option_label'], target=target)
        combined = parent | selected
        if combined - {'read', 'write_ephemeral'}:
            combined -= {'read', 'write_ephemeral'}
        return combined, parent_reason + '; 선택 항목: ' + selected_reason
    low, raw = target_line.lower(), target_line
    if name == "menu":
        path = action.get("path")
        leaf = path[-1] if isinstance(path,list) and path else []
        leaves = [leaf] if isinstance(leaf,str) else leaf if isinstance(leaf,list) else []
        # These menu commands can mutate a persistent document in any app.
        # A background app or an unfamiliar menu root does not make them transient.
        edits = {"cut", "paste", "paste and match style", "delete", "undo", "redo",
                 "bold", "italic", "underline", "bigger", "smaller", "make plain text", "make rich text",
                 "오려두기", "붙여넣기", "스타일 일치시켜 붙여넣기", "삭제", "실행 취소", "실행 복귀",
                 "굵게", "볼드", "기울임꼴", "밑줄", "더 크게", "더 작게", "일반 텍스트 만들기", "리치 텍스트 만들기"}
        if any(isinstance(title,str) and title.strip().casefold().rstrip(".…") in edits for title in leaves):
            return {"write_persistent"}, "메뉴 명령은 저장되는 내용을 변경할 수 있습니다"
    from .protocol import NAVIGATION_KEYS
    if name == "prepare_window":
        return {"write_ephemeral"}, "관측된 창 준비 후 재관찰"
    if name == "press_key" and isinstance(action.get("key"), str) and action["key"] in NAVIGATION_KEYS:
        return {"write_ephemeral"}, "현재 포커스의 탐색 키"
    if name == "press_key" and action.get("key") == "Return":
        # Return commits what an input holds: in a chat's message field it
        # SENDS. Whatever a recipe declares, a text input's Return is treated
        # as transmit (asked first, needs a receipt). Only Finder's Go to
        # Folder path field navigates, and a list row's Return opens the row.
        toks = target_line.strip().split()
        role = toks[1].rstrip(",").lower().removeprefix("ax") if len(toks) > 1 else ""
        if role in ("textarea", "textfield", "textbox", "combobox", "searchfield"):
            if role == "textfield" and "Identifier: PathTextField" in raw:
                return {"write_ephemeral"}, "폴더 경로로 이동"
            return {"transmit"}, "입력란의 Return 은 내용을 보냅니다"
    if name in ("scroll", "read"):
        return {"read"}, "화면을 읽습니다"
    if name in ("type_text", "set_value", "select_option", "select_custom_option") and (
            _SENSITIVE_FIELD.search(low)
            or any(k in raw for k in _SENSITIVE_FIELD_KO)):
        return {"credential"}, "자격 증명 입력란"
    document_effects = document_edit_effects(action, target_line, target=target)
    if document_effects:
        return document_effects, "TextEdit 문서 편집은 자동 저장될 수 있습니다"
    # The calculator's AC clears an ENTRY, not stored data. Same carve-out as
    # `classify`, for the same reason and on the same words.
    if name == "click" and any(k in low or k in raw for k in _INPUT_CLEAR_WORDS) \
            and not any(w in low for w in _STORED_EN) \
            and not any(w in raw for w in _STORED_KO):
        return {"write_ephemeral"}, "입력 지우기"
    for eff, words in _EFFECT_KO:
        for w in words:
            if w in raw:
                return {eff}, f"'{w}'"
    for eff, rxs in _EFFECT_RX:
        for rx in rxs:
            if rx.search(low):
                return {eff}, f"{rx.pattern}"
    return {"write_ephemeral"}, "화면 조작"
