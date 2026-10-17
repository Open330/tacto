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
from .flow import Capture, Flow, Postcondition, Selector, Step, pin_params

# Reading and ephemeral editing are the two harmless shapes most everyday tasks
# reduce to; naming them once keeps each recipe's contract to the part that is
# actually specific to it.
LOOK = SafetyContract(effects=["read"],
                      resource=Resource(object_type="화면", scope="item"),
                      reversibility="reversible")


def _scratch(object_type: str) -> SafetyContract:
    """Transient UI state only; document editing may autosave and must not use this."""
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
                 intent_text="계산합니다", safety=keypad, answers=True,
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


def finder_count_items() -> Flow:
    """Answer a question about a folder instead of changing anything.

    Half of what someone wants from a computer is to be told something, and
    until now every recipe here acted. This one opens the folder and reads
    Finder's own status line back.

    It reads Finder's own status line, which counts everything in the folder —
    including what is scrolled out of view. `finder_list_items` names them
    instead; "몇 개야?" and "뭐 있어?" are different questions.
    """
    path = ["Go", "{{folder}}"]
    return Flow(
        name="finder_count_items", target="com.apple.finder",
        params={"folder": "Downloads"},
        steps=[
            Step("menu", params={"path": path},
                 intent_text="{{folder}} 폴더를 봅니다", safety=LOOK,
                 binding=ContextBinding(bundle_id="com.apple.finder",
                                        operation="menu",
                                        params_digest=pin_params({"path": path})),
                 postcondition=Postcondition(equals="{{folder}}", attribute="Title"),
                 captures=[Capture(name="요약",
                                   field=Selector(role="StaticText",
                                                  identifier="statusBarText",
                                                  in_focused_window=True),
                                   attribute="Value")]),
        ])


def finder_list_items() -> Flow:
    """Name what is in a folder, rather than counting it.

    Finder renders filenames as editable text fields (that is how rename-in-place
    works), so a role plus the front window is the whole description — no
    guessing at row identifiers, and no switching the window to list view to
    reach them. What it reads is what the window currently shows, so a long
    folder is reported up to `limit` and the count recipe remains the way to
    ask how many there really are.

    Read from the END of the matches: in column view Finder shows every parent
    folder next to the current one, so the first names on screen belong to the
    drive and the folders above, not to the folder that was asked about. The
    rightmost column is the answer.
    """
    path = ["Go", "{{folder}}"]
    return Flow(
        name="finder_list_items", target="com.apple.finder",
        params={"folder": "Downloads"},
        steps=[
            Step("menu", params={"path": path},
                 intent_text="{{folder}} 폴더를 봅니다", safety=LOOK,
                 binding=ContextBinding(bundle_id="com.apple.finder",
                                        operation="menu",
                                        params_digest=pin_params({"path": path})),
                 postcondition=Postcondition(equals="{{folder}}", attribute="Title"),
                 captures=[
                     Capture(name="목록", many=True, limit=15, optional=True,
                             tail=True,
                             field=Selector(role="TextField",
                                            in_focused_window=True),
                             attribute="Value"),
                     Capture(name="요약",
                             field=Selector(role="StaticText",
                                            identifier="statusBarText",
                                            in_focused_window=True),
                             attribute="Value")]),
        ])


# ---------------------------------------------------------------- Mail

def mail_inbox() -> Flow:
    """What is in the inbox — sender, subject, and the window's own tally.

    Mail is the app that forced a limit on how much of a tree is worth walking:
    a mailbox holds tens of thousands of rows that all look alike, and reading
    every one of them took over a minute before the driver learned to stop. What
    a person asked for is the top of the list, which is exactly what survives.

    Getting to the inbox is a menu command rather than a click on the sidebar,
    so it does not depend on which mailbox happened to be selected. That is
    navigation, not a preference: "메일 왔어?" means the inbox, whatever was on
    screen a moment ago.
    """
    CELL = "Mail.messageList"
    inbox = ["Mailbox", ["Go to Favorite Mailbox", "즐겨찾는 메일함으로 이동"],
             ["All Inboxes", "모든 받은 편지함"]]

    def column(name: str, identifier: str) -> Capture:
        return Capture(name=name, many=True, limit=8, empty_ok=True,
                       field=Selector(role="StaticText", identifier=identifier,
                                      ancestor=CELL, in_focused_window=True),
                       attribute="Value")

    return Flow(
        name="mail_inbox", target="com.apple.mail",
        steps=[
            Step("menu", params={"path": inbox},
                 intent_text="받은 편지함을 봅니다", safety=LOOK,
                 binding=ContextBinding(bundle_id="com.apple.mail",
                                        operation="menu",
                                        params_digest=pin_params({"path": inbox})),
                 postcondition=Postcondition(contains=CELL),
                 captures=[
                     # The window title carries Mail's own count, including the
                     # unread tally when there is one — no arithmetic of ours.
                     Capture(name="메일함", many=True, limit=1,
                             field=Selector(role="Window", in_focused_window=True),
                             attribute="Title"),
                     column("보낸사람", "Mail.messageList.cell.view.addressLabel"),
                     column("제목", "Mail.messageList.cell.view.subjectLabel"),
                 ]),
        ])


# ---------------------------------------------------------------- Calendar

def calendar_today() -> Flow:
    """What is on today — the request a calendar exists to answer.

    Calendar lays the week out as one list per day, labelled with the weekday in
    the SYSTEM's language, and each event carries its whole summary (title, time,
    place) in its description. So the recipe scopes to today's column and reads
    the descriptions out; it never switches the window to day view, because the
    week the person is looking at is a view they chose.

    The weekday arrives as both spellings, since the app writes it in the
    system's language while the caller computes it in its own. An empty column
    is a real answer here — a free day — which is why the postcondition proves
    the column is on screen before the capture concludes anything from its
    being empty.
    """
    today = ["{{weekday_en}}", "{{weekday_ko}}"]
    go_today = ["View", ["Go to Today", "오늘로 이동"]]
    return Flow(
        name="calendar_today", target="com.apple.iCal",
        params={"weekday_en": "Monday", "weekday_ko": "월요일"},
        steps=[
            Step("menu", params={"path": go_today},
                 intent_text="오늘 날짜를 봅니다", safety=LOOK,
                 binding=ContextBinding(bundle_id="com.apple.iCal",
                                        operation="menu",
                                        params_digest=pin_params({"path": go_today})),
                 postcondition=Postcondition(
                     field=Selector(role="List", desc=today,
                                    in_focused_window=True),
                     contains="List"),
                 captures=[Capture(name="오늘 일정", many=True, limit=12,
                                   empty_ok=True,
                                   field=Selector(role="StaticText",
                                                  ancestor=today,
                                                  in_focused_window=True),
                                   attribute="Description")]),
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
                 safety=SafetyContract(effects=["write_persistent"],
                     resource=Resource(object_type="문서 본문", scope="item"),
                     reversibility="recoverable"),
                 binding=ContextBinding(bundle_id="com.apple.TextEdit",
                                        role="TextArea",
                                        identifier="First Text View",
                                        operation="set_value",
                                        params_digest=pin_params(
                                            {"value": "{{text}}"})),
                 answers=True,
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
                 intent_text="굵게 만듭니다", safety=SafetyContract(effects=["write_persistent"],
                     resource=Resource(object_type="글자 서식", scope="item"),
                     reversibility="recoverable"),
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
                 # A dialog left open by an earlier run disables the whole menu
                 # bar, and clearing it is a decision about the screen rather
                 # than about the task. Worth asking; not worth guessing.
                 on_uncertain=["action_failed"],
                 binding=ContextBinding(bundle_id="com.apple.TextEdit",
                                        operation="menu",
                                        params_digest=pin_params({"path": path})),
                 # "there is now a blank document to type into": a text area
                 # exists in the front window.
                 postcondition=Postcondition(field=TEXT_AREA, contains="TextArea")),
        ])


SAVE_INTENT = "'{{document}}' 문서를 지금 이름 그대로 저장합니다"


def textedit_save() -> Flow:
    """Save one named TextEdit document in place (File > Save), witnessed.

    `document` is the file's canonical absolute path and `name` its file
    name. The path is in the consent text, so the person approves saving
    THAT file; the focused window's title must still be `name` right before
    the press, and the signed helper checks right before pressing that the
    focused and main windows show exactly that file (focused_document — a
    title alone matched two note.txt in different folders); the host receipt
    (tacto/existing_file_receipt.py) reads that document by path before and
    after and accepts only a .txt file that holds exactly the document's
    text. It certifies that saved state, not which writer wrote it (macOS
    autosave). Recognised only in this exact form.
    """
    from .existing_file_receipt import SAVE_PATH
    return Flow(
        name="textedit_save", target="com.apple.TextEdit",
        params={"document": "", "name": ""},
        steps=[
            Step("menu", params={"path": SAVE_PATH, "focused_document": "{{document}}"},
                 intent_text=SAVE_INTENT,
                 # Nothing on screen changes on a save; the host receipt is
                 # what verifies it, and a write_persistent step without one
                 # still ends commit_unverified.
                 unverified_ok=True,
                 safety=SafetyContract(
                     effects=["write_persistent"],
                     resource=Resource(object_type="문서", object_identity="{{name}}", scope="item"),
                     reversibility="recoverable"),
                 binding=ContextBinding(
                     bundle_id="com.apple.TextEdit", operation="menu",
                     params_digest=pin_params({"path": SAVE_PATH,
                                               "focused_document": "{{document}}"}),
                     resource_identity="{{name}}",
                     # Found only when the FOCUSED window is titled {{name}};
                     # otherwise nothing re-reads it and replay refuses.
                     resource_identity_from={"role": "Window", "title": "{{name}}",
                                             "in_focused_window": True,
                                             "attribute": "Title"})),
        ],
    )


def textedit_save_as() -> Flow:
    """Save the open document under a name — the first genuinely consequential
    task in this library, and the one that exercises the consent gate.

    Three shapes appear here that the calculator never had: a menu command that
    opens a sheet, a field inside that sheet, and a step whose contract says
    `write_persistent`, which the policy will not run on its own authority.

    The folder is chosen, not inherited. Letting the sheet keep whatever
    location the app last used meant a file could land somewhere the person
    never picked and would not think to look — the save panel here was still
    pointing at a terminal's support directory. The two location steps stand
    down when the sheet already shows the right folder, so re-saving to the same
    place costs nothing.
    """
    # One list, one meaning: these are the same command in two languages.
    #
    # It used to also carry "Save…" and "저장…", on the reading that the menu
    # item's title tracks document state — an unsaved document showing "Save…"
    # where a saved one shows a separate "Save As…". Those are different
    # COMMANDS, not translations, and TextEdit shows both at once (measured
    # 2026-09-20). So both matched, `_menu_item` refused the path as ambiguous
    # — correctly, because two items answering one list of equivalents is not a
    # choice to make on the caller's behalf — and five save scenarios failed
    # with it. The guard was right; the list was wrong.
    save_as = ["File", ["Save As…", "다른 이름으로 저장…"]]
    name_field = Selector(role="TextField", identifier="saveAsNameTextField",
                          in_focused_window=True)
    # The popup's own value is the folder it will save into, so it doubles as
    # the condition for skipping the two steps that set it.
    where = Selector(role="PopUpButton", identifier="where popup",
                     in_focused_window=True)
    at_folder = Postcondition(field=where, equals="{{folder}}", attribute="Value")
    sidebar = Selector(role="Outline", desc="sidebar", in_focused_window=True)
    return Flow(
        name="textedit_save_as", target="com.apple.TextEdit",
        params={"filename": "무제.rtf", "folder": "Downloads"},
        steps=[
            Step("menu", params={"path": save_as},
                 intent_text="저장 창을 엽니다", safety=_scratch("저장 대화상자"),
                 on_uncertain=["action_failed"],
                 binding=ContextBinding(bundle_id="com.apple.TextEdit",
                                        operation="menu",
                                        params_digest=pin_params({"path": save_as})),
                 postcondition=Postcondition(contains="save-panel")),
            # The sidebar, not the "Where:" popup. That popup is a menu, and a
            # menu only opens for the active app — pressing it from here reports
            # success and shows nothing. The sidebar is an ordinary list, and
            # clicking a row works whatever has focus.
            Step("click", Selector(role="DisclosureTriangle",
                                   identifier="NS_OPEN_SAVE_DISCLOSURE_TRIANGLE",
                                   in_focused_window=True),
                 intent_text="저장 위치 목록을 펼칩니다", safety=_scratch("저장 대화상자"),
                 skip_if=Postcondition(field=sidebar, contains="Outline")),
            # The label identifies the row; the row is what responds to the
            # click, so the selector matches the one and acts on the other.
            Step("click", Selector(role="StaticText", value="{{folder}}",
                                   ancestor="sidebar", in_focused_window=True,
                                   click_ancestor=["Row", "Cell"]),
                 intent_text="'{{folder}}' 폴더를 고릅니다",
                 safety=_scratch("저장 위치"), skip_if=at_folder,
                 binding=ContextBinding(bundle_id="com.apple.TextEdit",
                                        role="StaticText", operation="click"),
                 postcondition=at_folder),
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
                                        # …and into the folder that was agreed.
                                        destination_identity="{{folder}}",
                                        destination_identity_from={
                                            **asdict(where), "attribute": "Value"},
                                        # Derived from the field itself rather
                                        # than restated: a second copy of the
                                        # selector drifts from the first, and
                                        # the copy that drifted here dropped the
                                        # front-window scope and read nothing.
                                        resource_identity_from={
                                            **asdict(name_field),
                                            "attribute": "Value"}),
                 answers=True,
                 postcondition=Postcondition(equals="{{filename}}",
                                             attribute="Title")),
        ])


# ---------------------------------------------------------------- recovery

def close_dialog() -> Flow:
    """Dismiss a dialog the app is waiting on — the way OUT of a stuck run.

    A run that stops midway leaves its sheet open, and a modal sheet disables
    the whole menu bar, so the next request fails before it starts. Nothing
    closes it on its own: cancelling a dialog is an action nobody asked for, and
    guessing at it is how an automation throws away work. So it is a recipe the
    person invokes — "대화상자 닫아줘" — and it presses Cancel, never the button
    that commits.

    It is verified by the sheet being gone, which is the only shape of evidence
    that fits: nothing new appears when a dialog closes.
    """
    return Flow(
        name="close_dialog", target="{{app}}",
        params={"app": "com.apple.TextEdit"},
        steps=[
            # Deliberately NOT scoped to the front window: the dialog holding
            # things up is usually on the window the run abandoned, not the one
            # the person is looking at now. Two open dialogs make this ambiguous
            # and it says so rather than guessing which to discard.
            Step("click", Selector(role="Button", title=["Cancel", "취소"]),
                 intent_text="열려 있는 대화상자를 닫습니다",
                 safety=_scratch("대화상자"),
                 # Nothing to close is not a failure; it is the state this
                 # recipe exists to reach.
                 skip_if=Postcondition(absent="Sheet"),
                 postcondition=Postcondition(absent="Sheet")),
        ])


# ---------------------------------------------------------------- registry

def _calculator():
    from .flow import calculator_12x34, calculator_multiply
    return {"calc_12x34": calculator_12x34, "calc_multiply": calculator_multiply}


def builders() -> dict:
    """Name -> zero-argument builder, for every recipe that ships with Tacto."""
    return {**_calculator(),
            "calc_arith": calc_arith,
            "finder_open_folder": finder_open_folder,
            "finder_count_items": finder_count_items,
            "finder_list_items": finder_list_items,
            "calendar_today": calendar_today,
            "mail_inbox": mail_inbox,
            "close_dialog": close_dialog,
            "textedit_write_note": textedit_write_note,
            "textedit_emphasize": textedit_emphasize,
            "textedit_new_document": textedit_new_document,
            "textedit_save_as": textedit_save_as,
            "textedit_save": textedit_save}
