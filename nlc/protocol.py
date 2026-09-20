"""Backend-independent L2 contract. Importing the core never loads macOS APIs."""

from typing import Callable, Protocol

PROTOCOL_VERSION = 1
NAVIGATION_KEYS = frozenset({"Tab", "shift+Tab", "Up", "Down", "Left", "Right", "Escape"})

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
