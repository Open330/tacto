"""Short schema feedback without echoing user-supplied JSON values."""
import json


class FlowFieldError(ValueError):
    def __init__(self, message, field, hint):
        super().__init__(message)
        self.field = field
        self.hint = hint


def step_error(exc, path):
    message = str(exc)
    rules = {
        'compact click requires an identifier and no action/selector':
            ('click', 'Use {"click":"identifier"} OR action+selector, never both.'),
        'compact check requires check_defaults.field and no duplicate check':
            ('equals/skip_equals', 'Use text; set check_defaults.field; omit the equivalent full check.'),
        'unknown Flow action':
            ('action', 'Use a supported action, e.g. click, set_value or read. Compact click needs no action.'),
        'invalid action parameters; names and indices are not parameters':
            ('params', 'Use an object of action parameters; set_value uses {"value":"text"}. Put identifiers in selector.'),
        'step.selector must be an object': ('selector', 'Use {"identifier":"observed identifier"}.'),
        'postcondition must be an object, not str': ('postcondition', 'Use {"field":{"identifier":"result"},"equals":"text"}.'),
    }
    field, hint = rules.get(message, ('', 'Correct this step schema; no UI action was dispatched by parsing.'))
    return FlowFieldError(message, path + ('.'+field if field else ''), hint)


def feedback(exc):
    if isinstance(exc, FlowFieldError):
        return f'{exc.field}: {exc.hint}'
    if isinstance(exc, json.JSONDecodeError):
        return f'JSON line {exc.lineno}, column {exc.colno}: use valid JSON, with double-quoted keys and no trailing commas.'
    if isinstance(exc, OSError):
        return 'file: could not read/write this path; check its parent directory and access.'
    return 'schema: invalid Flow; check name, target, steps and object fields. No input values are echoed.'
