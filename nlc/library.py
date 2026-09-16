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

TEXT_AREA = Selector(role="TextArea", identifier="First Text View")


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
    bold_box = Selector(role="CheckBox", desc="bold")
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
            "textedit_emphasize": textedit_emphasize}
