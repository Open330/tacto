"""Backend-independent L2 contract. Importing the core never loads macOS APIs."""

from typing import Callable, Protocol
import math

PROTOCOL_VERSION = 1
NAVIGATION_KEYS = frozenset({"Tab", "shift+Tab", "Up", "Down", "Left", "Right", "Escape"})
# Return commits, so it is not navigation and never offered with it. The
# signed helper takes it only into the Go to Folder sheet's path field, and a
# flow only in a step declared is_submit (see replay's press_key contract).
SUBMIT_KEYS = frozenset({"Return"})
RETURN_CAPABILITY = "press_key.return_in_go_to_folder"
# Return into a bound text input or selected list row, for a step the host
# vouched for: declared submit, bound, with a postcondition not yet true.
RETURN_BOUND_CAPABILITY = "press_key.return_bound"

# IR payloads cannot override the action name or smuggle an observation index.
ACTION_PARAMS = {
    "click": frozenset(), "double_click": frozenset(), "read": frozenset(), "prepare_window": frozenset(),
    "set_value": frozenset({"value"}), "type_text": frozenset({"text"}),
    "select_option": frozenset({"value", "option_label"}),
    "open_select": frozenset({"opening_action"}),
    "select_custom_option": frozenset({"option_label", "value"}),
    # Host-registered immutable browser payload, never a model-supplied path.
    "upload": frozenset({"scope", "attachment_digest"}),
    "press_key": frozenset({"key"}), "menu": frozenset({"path", "focused_document"}),
    "drag": frozenset({"from", "to", "duration", "via"}),
    "scroll": frozenset({"direction", "pages"}),
    "secondary_action": frozenset({"action"}),
    "goto": frozenset({"url"}),
    # Finder, signed helper only: open an existing folder without activating.
    "open_folder": frozenset({"path"}),
    # Any app: ask it to reopen its window (closed, or gone to another Space),
    # and close an open context menu with AXCancel instead of Escape.
    "reopen_window": frozenset(), "close_menu": frozenset(),
}


def native_opening_parameters_problem(action):
    """Explicit AXPress is a single alternative, never a retry strategy."""
    if 'opening_action' in action and action['opening_action'] not in ('AXShowMenu','AXPress'):
        return 'invalid_opening_action'
    return None


def native_selection_parameters_problem(action):
    """Native titles have no separate value space; never coerce a value.

    HTML option values may differ from labels. Native value may be omitted,
    but explicit null/number/bool/array/object or a different string is invalid.
    """
    if not isinstance(action, dict) or not isinstance(action.get('option_label'), str) or not action['option_label']:
        return 'option_label_required'
    try:
        action['option_label'].encode('utf-8')
    except UnicodeError:
        return 'invalid_option_label_encoding'
    if 'value' in action and (not isinstance(action['value'], str) or action['value'] != action['option_label']):
        return 'native_option_value_mismatch'
    return None


def window_key_reobservation(action, response, capabilities):
    """A completed bounded send needs fresh effect evidence, never a retry.

    Navigation: the window_key route, a full down/up pair. Return: only the
    Go to Folder route (window_submit_key) with exactly one keyDown — a
    normal Return closes the sheet, so no keyUp is sent (see the helper)."""
    if not ("press_key.element_bound" in capabilities
            and action.get('name') == 'press_key'
            and type(action.get('element_index')) is int and action['element_index'] >= 0
            and type(action.get('key')) is str
            and response.get('type') == 'acted'
            and response.get('unverified') == 'observe_effect'
            and response.get('reobserve_required') is True
            and type(response.get('posted_events')) is int):
        return False
    if action['key'] in NAVIGATION_KEYS - {'Escape'}:
        return response.get('input_route') == 'window_key' and response['posted_events'] == 2
    return (action['key'] in SUBMIT_KEYS and response['posted_events'] == 1
            and ((RETURN_CAPABILITY in capabilities
                  and response.get('input_route') == 'window_submit_key')
                 or (RETURN_BOUND_CAPABILITY in capabilities
                     and response.get('input_route') == 'window_bound_submit_key')))


def double_click_reobservation(action, response, capabilities):
    """A completed window-bound double click (two pairs, 4 events) needs fresh
    effect evidence, never a retry. The AXOpen route answers like a click."""
    return ("action.double_click" in (capabilities or ())
            and isinstance(action, dict) and set(action) == {'name', 'element_index'}
            and action.get('name') == 'double_click'
            and type(action.get('element_index')) is int and action['element_index'] >= 0
            and isinstance(response, dict) and response.get('type') == 'acted'
            and response.get('input_route') == 'window_bound_double_click'
            and response.get('unverified') == 'observe_effect'
            and response.get('reobserve_required') is True
            and type(response.get('posted_events')) is int and response['posted_events'] == 4)


def element_click_reobservation(action, response, capabilities):
    """A click on an element that offers no AXPress, made as one window-bound
    click at its centre (2 events): reobserve, never retry."""
    return ("click.window_bound_element" in (capabilities or ())
            and isinstance(action, dict) and set(action) == {'name', 'element_index'}
            and action.get('name') == 'click'
            and type(action.get('element_index')) is int and action['element_index'] >= 0
            and isinstance(response, dict) and response.get('type') == 'acted'
            and response.get('input_route') == 'window_bound_element_click'
            and response.get('unverified') == 'observe_effect'
            and response.get('reobserve_required') is True
            and type(response.get('posted_events')) is int and response['posted_events'] == 2)


def window_point_reobservation(action, response, capabilities, *, obs_id=None, window_id=None):
    """Only the expected window's complete authorized pair may be reobserved."""
    if not isinstance(action, dict) or not isinstance(response, dict):
        return False
    point = action.get('point')
    return (isinstance(capabilities, (set, frozenset, tuple, list))
            and {'action.click_point', 'click_point.window_bound', 'click_point.host_authorization'} <= set(capabilities)
            and set(action) == {'name', 'element_index', 'point'}
            and action.get('name') == 'click_point'
            and type(action.get('element_index')) is int and action['element_index'] >= 0
            and isinstance(point, dict) and set(point) == {'x', 'y'}
            and all(type(value) in (int, float) and math.isfinite(value) and value >= 0 for value in point.values())
            and response.get('type') == 'acted'
            and response.get('input_route') == 'window_bound_mouse'
            and response.get('mechanism') == 'pid_window_events'
            and response.get('unverified') == 'observe_effect'
            and response.get('reobserve_required') is True
            and type(response.get('posted_events')) is int and response['posted_events'] == 2
            and type(response.get('window_id')) is int and response['window_id'] > 0
            and (window_id is None or response['window_id'] == window_id)
            and (obs_id is None or response.get('obs_id') == obs_id))


def window_input_reobservation(action, response, capabilities, *, obs_id=None, window_id=None):
    """A complete opaque pair permits receiving verification, never success."""
    from .native_window_payload import window_input_problem, WINDOW_INPUT_CAPABILITIES
    return (window_input_problem(action) is None and isinstance(response, dict)
            and isinstance(capabilities, (set, frozenset, tuple, list))
            and WINDOW_INPUT_CAPABILITIES <= set(capabilities)
            and response.get('type') == 'acted'
            and response.get('input_route') == 'window_input'
            and response.get('mechanism') == 'pid_window_events'
            and response.get('unverified') == 'observe_effect'
            and response.get('reobserve_required') is True
            and type(response.get('posted_events')) is int and response['posted_events'] == 2
            and type(response.get('window_id')) is int and response['window_id'] > 0
            and (window_id is None or response['window_id'] == window_id)
            and (obs_id is None or response.get('obs_id') == obs_id))


class Driver(Protocol):
    def identity(self, target: str) -> str: ...
    def observe(self, target: str, mode: str = "tree_full",
                require_window: bool = True) -> dict: ...
    def act(self, obs_id: str, target: str, action: dict, expect=None,
            risk_class: str = "none", intent_text: str = "",
            approved: bool = False) -> dict: ...
    def probe(self, obs_id: str, target: str, element_index: int) -> dict: ...
    def selection(self, target: str) -> dict: ...
    def wait_until(self, target: str, predicate: Callable[[str], bool],
                   timeout_ms: int = 4000, poll_ms: int = 250) -> dict: ...


def default_driver():
    from .driver import Driver as NativeDriver
    return NativeDriver()


def normalize_action_response(response):
    """Preserve known act replies; malformed acknowledgements leave effects unknown.

    Called only after invoking act. Never infer not_started or include the
    malformed payload (which can contain application data) in diagnostics.
    """
    if (type(response) is dict and type(response.get('type')) is str
            and response['type'] in ('acted','error','confirm_request','handoff_request')):
        return response
    return {'type':'error','error':'driver_response_invalid','effect':'unknown'}


def drag_reobservation(action, response, capabilities):
    from .native_drag import drag_problem, drag_event_count
    return ('action.drag' in (capabilities or ()) and 'drag.window_bound' in capabilities
            and action.get('name') == 'drag' and drag_problem(action) is None
            and type(action.get('element_index')) is int and action['element_index'] >= 0
            and response.get('type') == 'acted' and response.get('input_route') == 'window_bound_drag'
            and response.get('unverified') == 'observe_effect' and response.get('reobserve_required') is True
            and type(response.get('posted_events')) is int
            and response['posted_events'] == drag_event_count(action))


REVEAL_CAPABILITY = 'observe.reveal'


def reveal_problem(reveal):
    """None when `reveal` is absent or 1-8 exact label texts of 1-256 characters.

    Refuses what the helper and launcher refuse: control and format
    characters (Unicode Cc, Cf) and every line separator.
    """
    import unicodedata
    if reveal is None:
        return None

    def plain(text):
        return all(unicodedata.category(c) not in ('Cc', 'Cf', 'Zl', 'Zp') and c != '\x85' for c in text)
    if (not isinstance(reveal, (list, tuple)) or not 1 <= len(reveal) <= 8
            or any(not isinstance(text, str) or not 1 <= len(text) <= 256 or not plain(text)
                   for text in reveal)):
        return 'reveal must be 1-8 label texts of 1-256 characters without control characters'
    return None

