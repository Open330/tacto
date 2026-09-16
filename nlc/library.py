"""Recipes that were driven against this OS, not sketched against a guess.

`examples/scenarios.md` catalogues thirty plausible tasks; this module holds the
ones whose selectors, menu paths and postconditions were confirmed by actually
running them. The distinction matters, because the failures worth catching are
exactly the ones a plausible sketch cannot predict — Calculator exposing its
result as `StandardInputView;value:408`, or `Format > Font > Bold` returning
success and changing nothing when no text is selected.

Every recipe here is parameterized where the task has inputs: a folder name and
the text of a note are values, not procedure, so one recipe serves every folder
rather than one recipe per folder.
"""

from __future__ import annotations

from dataclasses import asdict

from .contract import ContextBinding, Resource, SafetyContract
from .flow import Flow, Postcondition, Selector, Step, pin_params

# Reading and ephemeral editing are the two harmless shapes most everyday tasks
# reduce to; naming them once keeps each recipe's contract to the part that is
# actually specific to it.
LOOK = SafetyContract(effects=["read"],
                      resource=Resource(object_type="화면", scope="item"),
                      reversibility="reversible")


def _scratch(object_type: str) -> SafetyContract:
    """An edit that lives in an open, unsaved document — undoable, never stored."""
    return SafetyContract(effects=["write_ephemeral"],
                          resource=Resource(object_type=object_type, scope="entry"),
                          reversibility="reversible")


# ---------------------------------------------------------------- Calculator

DIGIT_BUTTON = {"0": "Zero", "1": "One", "2": "Two", "3": "Three", "4": "Four",
                "5": "Five", "6": "Six", "7": "Seven", "8": "Eight",
                "9": "Nine", ".": "Decimal"}
OP_BUTTON = {"+": "Add", "-": "Subtract", "*": "Multiply", "/": "Divide"}


def calc_arith() -> Flow:
    """Arithmetic on two numbers — the operator is an input like the operands.

    `calc_multiply` could only multiply, which made "12 더하기 34" an unknown
    task for no reason: the procedure is identical and only the button differs.

    The expected answer is derived and asserted exactly, so a result that cannot
    be written down exactly (10 ÷ 3) fails verification rather than being
    reported as correct. That is the intended trade: this recipe's promise is a
    checked answer, and an unchecked one is worth less than an honest refusal.
    """
    KP = "CalculatorKeypadView"
    entry = Resource(object_type="계산기 입력", scope="entry")
    keypad = SafetyContract(effects=["write_ephemeral"], resource=entry,
                            reversibility="reversible")
    clear = SafetyContract(effects=["delete"], resource=entry,
                           reversibility="reversible")

    def digits_of(param: str, label: str) -> Step:
        return Step("click",
                    Selector(role="Button", identifier="{{digit[item]}}",
                             ancestor=KP),
                    intent_text=f"{label} {{{{item}}}}", safety=keypad,
                    for_each=f"{{{{{param}}}}}")

    return Flow(
        name="calc_arith", target="com.apple.calculator",
        params={"a": "12", "b": "34", "op": "*"},
        tables={"digit": DIGIT_BUTTON, "opbtn": OP_BUTTON},
        derived={"result": "{{a}} {{op}} {{b}}"},
        steps=[
            Step("click", Selector(role="Button",
                                   identifier=["AllClear", "Clear"], ancestor=KP),
                 intent_text="계산기를 지웁니다", safety=clear),
            digits_of("a", "첫 번째 수"),
            Step("click", Selector(role="Button", identifier="{{opbtn[op]}}",
                                   ancestor=KP),
                 intent_text="{{op}} 를 누릅니다", safety=keypad),
            digits_of("b", "두 번째 수"),
            Step("click", Selector(role="Button", identifier="Equals", ancestor=KP),
                 intent_text="계산합니다", safety=keypad,
                 postcondition=Postcondition(
                     field=Selector(role="ScrollArea",
                                    identifier="StandardInputView"),
                     equals="{{result}}", attribute="Value")),
        ])


# ---------------------------------------------------------------- Finder

def finder_open_folder() -> Flow:
    """Open a folder — the plainest 'navigate somewhere' task there is.

    It starts from an app with no window at all, which is how Finder usually
    sits, and is driven entirely from the menu bar: `Go > Downloads` needs no
    window to exist first and no coordinates to be guessed.
    """
    path = ["Go", "{{folder}}"]
    return Flow(
        name="finder_open_folder", target="com.apple.finder",
        params={"folder": "Downloads"},
        steps=[
            Step("menu", params={"path": path},
                 intent_text="{{folder}} 폴더를 엽니다", safety=LOOK,
                 binding=ContextBinding(bundle_id="com.apple.finder",
                                        operation="menu",
                                        params_digest=pin_params({"path": path})),
                 postcondition=Postcondition(equals="{{folder}}", attribute="Title")),
        ])


# ---------------------------------------------------------------- TextEdit

# Scoped to the front window: every open TextEdit document exposes a text area
# with this same identifier, so an unscoped selector stops working the moment a
# second document is open — which on a real desktop is most of the time.
TEXT_AREA = Selector(role="TextArea", identifier="First Text View",
                     in_focused_window=True)


def textedit_write_note() -> Flow:
    """Put a line of text into an open document.

    Written with `set_value` rather than synthesized keystrokes: setting the
    value is a per-element AX write, so it needs neither keyboard focus nor the
    app to be frontmost, and it cannot leak into whatever window the user
    switches to mid-run.
    """
    return Flow(
        name="textedit_write_note", target="com.apple.TextEdit",
        params={"text": "안녕하세요"},
        steps=[
            Step("set_value", TEXT_AREA, params={"value": "{{text}}"},
                 intent_text="'{{text}}' 라고 적습니다",
                 safety=_scratch("문서 본문"),
                 binding=ContextBinding(bundle_id="com.apple.TextEdit",
                                        role="TextArea",
                                        identifier="First Text View",
                                        operation="set_value",
                                        params_digest=pin_params(
                                            {"value": "{{text}}"})),
                 postcondition=Postcondition(field=TEXT_AREA, equals="{{text}}",
                                             attribute="Value")),
        ])


def textedit_emphasize() -> Flow:
    """Make the whole document bold — a two-menu task with a real gotcha.

    `Format > Font > Bold` reports success even when it does nothing, because
    with no selection there is nothing to embolden. That is why the selection
    step comes first and why the bold step verifies the toolbar checkbox instead
    of trusting the press.

    It is also a toggle rather than a setting, so the bold step stands down when
    the document is already bold — otherwise running "make it bold" twice would
    leave it plain, which is how this recipe failed the first time it ran.
    """
    bold_box = Selector(role="CheckBox", desc="bold", in_focused_window=True)
    bold_path = ["Format", ["Font", "폰트"], ["Bold", "볼드"]]
    return Flow(
        name="textedit_emphasize", target="com.apple.TextEdit",
        steps=[
            # Selection is not exposed anywhere in the AX tree, so this step
            # genuinely cannot verify itself. It is safe to leave unverified: it
            # changes no content, it is idempotent, and the next step's
            # postcondition fails loudly if the selection did not happen.
            Step("menu", params={"path": ["Edit", ["Select All", "전체 선택"]]},
                 intent_text="글 전체를 선택합니다", safety=_scratch("선택 영역"),
                 unverified_ok=True),
            Step("menu", params={"path": bold_path},
                 intent_text="굵게 만듭니다", safety=_scratch("글자 서식"),
                 binding=ContextBinding(bundle_id="com.apple.TextEdit",
                                        operation="menu",
                                        params_digest=pin_params(
                                            {"path": bold_path})),
                 skip_if=Postcondition(field=bold_box, equals="1",
                                       attribute="Value"),
                 postcondition=Postcondition(field=bold_box, equals="1",
                                             attribute="Value")),
        ])


def textedit_new_document() -> Flow:
    """Start a blank document.

    Like `finder_open_folder`, this begins with no window to act in — TextEdit
    launched with nothing open shows a file picker, not a document — so the
    recipe has to reach the menu bar before there is anything on screen.
    """
    path = ["File", ["New", "새로 만들기"]]
    return Flow(
        name="textedit_new_document", target="com.apple.TextEdit",
        steps=[
            Step("menu", params={"path": path},
                 intent_text="빈 문서를 엽니다", safety=_scratch("새 문서"),
                 binding=ContextBinding(bundle_id="com.apple.TextEdit",
                                        operation="menu",
                                        params_digest=pin_params({"path": path})),
                 # "there is now a blank document to type into": a text area
                 # exists in the front window.
                 postcondition=Postcondition(field=TEXT_AREA, contains="TextArea")),
        ])


def textedit_save_as() -> Flow:
    """Save the open document under a name — the first genuinely consequential
    task in this library, and the one that exercises the consent gate.

    Three shapes appear here that the calculator never had: a menu command that
    opens a sheet, a field inside that sheet, and a step whose contract says
    `write_persistent`, which the policy will not run on its own authority.

    The sheet saves wherever the app last saved. Choosing the folder is a
    separate capability this recipe does not claim — saying so is better than a
    step that quietly lands the file somewhere the person did not expect.
    """
    # The menu item's own title tracks document state: an unsaved document shows
    # "Save…", a saved one shows "Save" and a separate "Save As…". Listing the
    # alternatives is not defensive padding — it is the same command wearing the
    # name the app currently gives it.
    save_as = ["File", ["Save As…", "Save…", "다른 이름으로 저장…", "저장…"]]
    name_field = Selector(role="TextField", identifier="saveAsNameTextField",
                          in_focused_window=True)
    return Flow(
        name="textedit_save_as", target="com.apple.TextEdit",
        params={"filename": "무제.rtf"},
        steps=[
            Step("menu", params={"path": save_as},
                 intent_text="저장 창을 엽니다", safety=_scratch("저장 대화상자"),
                 binding=ContextBinding(bundle_id="com.apple.TextEdit",
                                        operation="menu",
                                        params_digest=pin_params({"path": save_as})),
                 postcondition=Postcondition(contains="save-panel")),
            Step("set_value", name_field, params={"value": "{{filename}}"},
                 intent_text="파일 이름을 '{{filename}}' 으로 적습니다",
                 safety=_scratch("파일 이름"),
                 binding=ContextBinding(bundle_id="com.apple.TextEdit",
                                        role="TextField",
                                        identifier="saveAsNameTextField",
                                        operation="set_value",
                                        params_digest=pin_params(
                                            {"value": "{{filename}}"})),
                 postcondition=Postcondition(field=name_field,
                                             equals="{{filename}}",
                                             attribute="Value")),
            Step("click", Selector(role="Button", identifier="OKButton",
                                   ancestor="save-panel",
                                   in_focused_window=True),
                 intent_text="저장합니다",
                 safety=SafetyContract(
                     effects=["write_persistent"],
                     resource=Resource(object_type="문서", object_identity="{{filename}}",
                                       scope="item"),
                     reversibility="recoverable"),
                 binding=ContextBinding(bundle_id="com.apple.TextEdit",
                                        role="Button", identifier="OKButton",
                                        ancestor="save-panel", operation="click",
                                        # Consent was given to save THIS name, so
                                        # the name field has to still say it at
                                        # the moment the button is pressed.
                                        resource_identity="{{filename}}",
                                        # Derived from the field itself rather
                                        # than restated: a second copy of the
                                        # selector drifts from the first, and
                                        # the copy that drifted here dropped the
                                        # front-window scope and read nothing.
                                        resource_identity_from={
                                            **asdict(name_field),
                                            "attribute": "Value"}),
                 postcondition=Postcondition(equals="{{filename}}",
                                             attribute="Title")),
        ])


# ---------------------------------------------------------------- registry

def _calculator():
    from .flow import calculator_12x34, calculator_multiply
    return {"calc_12x34": calculator_12x34, "calc_multiply": calculator_multiply}


def builders() -> dict:
    """Name -> zero-argument builder, for every recipe that ships with NLC."""
    return {**_calculator(),
            "calc_arith": calc_arith,
            "finder_open_folder": finder_open_folder,
            "textedit_write_note": textedit_write_note,
            "textedit_emphasize": textedit_emphasize,
            "textedit_new_document": textedit_new_document,
            "textedit_save_as": textedit_save_as}
