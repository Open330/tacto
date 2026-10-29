"""Small, native-free command help for cold-start flow authors."""

import json


def calculator_example(*, compact=True):
    example = {
        "schema": 2, "name": "calculate", "target": "com.apple.calculator",
        "safety_defaults": {
            "effects": ["write_ephemeral"], "reversibility": "reversible",
            "resource": {"object_type": "calculator entry", "scope": "entry"},
        },
        "steps": [
            {"action": "click", "selector": {"role": "Button", "identifier": ["AllClear", "Clear"]},
             "safety": {"effects": ["delete"]},
             "skip_if": {"field": {"role": "ScrollArea", "identifier": "StandardInputView"},
                         "equals": "0", "attribute": "Value"}},
            {"action": "click", "selector": {"role": "Button", "identifier": ["AllClear", "Clear"]},
             "safety": {"effects": ["delete"]},
             "skip_if": {"field": {"role": "ScrollArea", "identifier": "StandardInputView"},
                         "equals": "0", "attribute": "Value"},
             "postcondition": {"field": {"role": "ScrollArea", "identifier": "StandardInputView"},
                               "equals": "0", "attribute": "Value"}},
            *[{"action": "click", "selector": {"role": "Button", "identifier": identifier},
               **({"postcondition": {"field": {"role": "ScrollArea", "identifier": "StandardInputView"},
                                     "equals": value, "attribute": "Value"}} if value else {})}
              for identifier, value in (("One", "1"), ("Two", "12"), ("Multiply", None),
                                        ("Three", "12 × 3"), ("Four", "12 × 34"))],
            {"action": "click", "selector": {"role": "Button", "identifier": "Equals"},
             "postcondition": {"field": {"role": "ScrollArea", "identifier": "StandardInputView"},
                               "equals": "408", "attribute": "Value"}, "answers": True},
        ],
    }
    if compact:
        example['selector_defaults'] = {'role': 'Button'}
        example['check_defaults'] = {
            'field': {'role': 'ScrollArea', 'identifier': 'StandardInputView'},
            'attribute': 'Value',
        }
        for step in example['steps']:
            step['selector'].pop('role')
            for key in ('postcondition', 'skip_if'):
                if key in step:
                    step[key].pop('field')
                    step[key].pop('attribute')
    return example



def session_guide(target, *, reusable=False, document=None):
    """Trusted authoring instructions; never derived from UI content."""
    template = {
        "schema": 2, "name": "task", "target": target,
        "selector_defaults": {"role": "Button"},
        "check_defaults": {"field": {"identifier": "<observed result field>"}, "attribute": "Value"},
        "safety_defaults": {"effects": ["write_ephemeral"], "reversibility": "reversible",
                            "resource": {"object_type": "entry", "scope": "entry"}},
        "steps": [{"click": "<observed identifier>", "equals": "<requested result>", "answers": True}],
    }
    details = ""
    if target == 'com.apple.calculator':
        template['check_defaults']['field'] = {'role': 'ScrollArea', 'identifier': 'StandardInputView'}
        details = (
            "Calculator: start at 0. Prepend two reset steps, each "
            '{"click":["AllClear","Clear"],"safety":{"effects":["delete"]},"skip_equals":"0"}; '
            'the second also has "equals":"0". Clear removes the operand; next reset clears the expression. '
            'Add observed digit/operator/Equals identifiers. Digit checks include the expression '
            '(e.g. "2 × 3"); final Equals checks the computed result. '
        )
    if document is not None:
        template['selector_defaults'] = {'role':'TextArea','identifier':'First Text View','ancestor':document}
        template['check_defaults']['field'] = dict(template['selector_defaults'])
        template['safety_defaults'] = {'effects':['read'],'reversibility':'reversible',
                                       'resource':{'object_type':'document','scope':'item'}}
        template['steps'] = [{'action':'read','selector':{'identifier':'First Text View'},'postcondition':{'equals':'<requested text>'},'answers':True}]
        details += (
            'Only this TextEdit document is pinned for this connection; no app-wide fallback or token reuse in another command. '
            'For a requested edit keep selector:{"identifier":"First Text View"}, use action:set_value, params:{"value":"<requested text>"}, '
            'postcondition:{"equals":"<requested text>"}; safety:{"effects":["write_persistent"],'
            '"evidence":[{"source":"user_intent","field":"asked","value":"<actual user request>","covers":["write_persistent"]}]}. '
            'Quote the actual edit request; do not invent coverage. '
            'Use selector keys identifier/title/desc, not description. '
            'An edit can return commit_unverified after input: observe the same document to check its text; do not retype just to remove that status. '
            'Visible text is not a Save or persistence receipt. Use run/replay for a saved-state task requiring the built-in Save adapter. '
            'A saved Flow file does not store this read scope: later run/replay must include --document with the intended path; omitting it reads the app windows. '
            'TACTO_FLOW_SCOPE returns the scoped replay argv after --save. '
        )
    if reusable:
        details += (
            'Reusable template: params is an OBJECT of current defaults, e.g. {"amount":"11"}; '
            'step value {"value":"{{amount}}"}, check equals:"{{result}}". '
            'Use --save /absolute/new.json to save the same successful template, not the bound values. '
            'Later run file --helper --think 0 -p amount=17 -p result=85. '
            'When using all_of set schema:3 (older engines refuse it); for simultaneous checks use postcondition {"all_of":[{"field":{"identifier":"observed-id"},"equals":"expected","attribute":"Value"}]}. '
            'A primary field/equals can coexist with all_of; answers:true reports the primary field. '
            'Use captures on that verified action for values needed later. Keep separate reads for a later checkpoint or new data. '
            'Do not invent observe as an action; read is supported. '
        )
    return (
        "TACTO_FLOW_GUIDE (trusted schema instructions)\n"
        + json.dumps(template, ensure_ascii=False, separators=(',', ':')) + "\n"
        "Adapt selectors, effects/resource and result to the user task. Selectors must be unique. "
        'AX-first, background by default: click invokes AXPress when supported; use set_value/read on observed elements. '
        'Do not activate or use coordinates/screenshots first. Pin the observed window with ancestor (TextEdit: --document); '
        'inspect --all-windows for missing controls. Activate only for a demonstrated foreground requirement. '
        'Compact click expands to action:click/selector.identifier; equals and skip_equals require '
        'check_defaults.field and expand to postcondition/skip_if. Put answers:true only on the result step. '
        'Full steps use action/selector/params. set_value uses params.value; its postcondition uses field:{"identifier":"same-id"}, equals:"text", attribute:"Value". Checks use equals, not value. '
        'Shared defaults fill explicit fields only; local effects replace and resource/destination merge. '
        'Persistent writes/sending need their actual contract, binding and completion evidence; use run for approvals. '
        + details
        + "Inspect uncertain outcomes before retrying. Send one minified JSON line plus newline on this session."
    )

def command_help(command, args):
    allowed = {"--help", "-h", "help", "--helper"}
    if command in ("run", "session"):
        allowed.add("--example")
    if (not any(arg in ("--help", "-h", "help") for arg in args)
            or any(arg not in allowed for arg in args)):
        return None
    if command == "batch":
        return (
            'usage: tacto batch <flow.json> --helper [--max-jobs 1..1000] [--idle-timeout 1..600] [--timings] [--repair-packet /absolute/new.json] [--auto-repair codex|claude] [--max-repairs 1..10] [--repair-timeout 1..120]\n'
            'Wait for TACTO_BATCH_READY; send one {"params":{"name":"value"}} JSON line per job.\n'
            'Pipe known JSONL rows once; for dependent jobs wait for each TACTO_BATCH_RESULT.\n'
            'One template/connection; fresh checks per job. --auto-repair codex|claude enables bounded selector repair.\n'
            'EOF ends it; default ten jobs, 90s idle. No approval or unknown-effect retry.'
        )
    if command == "observe":
        return (
            "usage: tacto observe <bundle-id> --helper [--json] "
            "[--all-windows | --document /absolute/path]\n"
            "Example: tacto observe com.apple.calculator --helper\n"
            "Read current accessibility selectors. --json includes capabilities.\n"
            "Use --document to pin a TextEdit document; session reuses the connection."
        )
    if command == "session":
        text = (
            "usage: tacto session <bundle-id> --helper [--document /absolute/path] [--guide | --reuse-guide] [--save /absolute/new.json] [--timings] [--idle-timeout 1..600]\n"
            "exec_command tty=true, yield_time_ms=1000; wait for observation and\n"
            "TACTO_SESSION_READY. write_stdin: one compact Flow JSON line + newline, yield_time_ms=5000.\n"
            "Poll the same session. One flow then exit. --document pins one TextEdit file.\n"
            "Use run for approval or judgment. Schema example: tacto session --help --example."
        )
    elif command == "run":
        text = (
            "usage: tacto run <flow.json> --helper [-p key=value] [--think N] [--timings] "
            "[--document /absolute/path] [--repair-packet /absolute/new.json] [--journal-dir /absolute/private/run] [--yes] [--auto-repair codex|claude] [--max-repairs 1..10] [--repair-timeout 1..120]\n"
            "Observe first; author a Flow using current selectors and result checks.\n"
            "Plain flows use no model judgment. --timings writes durations to stderr.\n"
            "Schema example: tacto run --help --example."
        )
    else:
        return None
    if "--example" in args:
        text += "\n" + json.dumps(calculator_example(), ensure_ascii=False, separators=(',', ':'))
    return text
