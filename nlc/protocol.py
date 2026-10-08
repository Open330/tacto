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

# IR payloads cannot override the action name or smuggle an observation index.
ACTION_PARAMS = {
    "click": frozenset(), "read": frozenset(), "prepare_window": frozenset(),
    "set_value": frozenset({"value"}), "type_text": frozenset({"text"}),
    "select_option": frozenset({"value", "option_label"}),
    "open_select": frozenset(),
    "select_custom_option": frozenset({"option_label", "value"}),
    "press_key": frozenset({"key"}), "menu": frozenset({"path"}),
    "scroll": frozenset({"direction", "pages"}),
    "secondary_action": frozenset({"action"}),
    "goto": frozenset({"url"}),
}


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
    return (action['key'] in SUBMIT_KEYS and RETURN_CAPABILITY in capabilities
            and response.get('input_route') == 'window_submit_key'
            and response['posted_events'] == 1)


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
