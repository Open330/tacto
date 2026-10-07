"""Construct and verify window-addressed navigation keys; never dispatch.

NSEvent's public key-event constructor supplies the window number. Retain its
owners because each CGEvent is borrowed. No text, shortcuts, or Escape here.
"""
from dataclasses import dataclass
import time


NAVIGATION = {
    'Tab': (48, '\t', '\t', 0),
    'shift+Tab': (48, '\x19', '\t', 1 << 17),
    'Up': (126, '\uf700', '\uf700', 1 << 23),
    'Down': (125, '\uf701', '\uf701', 1 << 23),
    'Left': (123, '\uf702', '\uf702', 1 << 23),
    'Right': (124, '\uf703', '\uf703', 1 << 23),
}


@dataclass(frozen=True)
class PreparedWindowKey:
    window_id: int
    key: str
    down: object
    up: object
    _owners: tuple


class _MacBackend:
    def __init__(self):
        import AppKit
        import Quartz
        self.appkit, self.q = AppKit, Quartz

    def create(self, kind, window, code, chars, plain, flags, timestamp):
        owner = self.appkit.NSEvent.keyEventWithType_location_modifierFlags_timestamp_windowNumber_context_characters_charactersIgnoringModifiers_isARepeat_keyCode_(
            kind, (0, 0), flags, timestamp, window, None, chars, plain, False, code)
        event = owner.CGEvent() if owner is not None else None
        if event is None:
            raise RuntimeError('key event construction failed')
        return owner, event

    def unicode(self, event, text):
        self.q.CGEventKeyboardSetUnicodeString(event, len(text.encode('utf-16-le')) // 2, text)

    def unicode_text(self, event):
        return self.q.CGEventKeyboardGetUnicodeString(event, 64, None, None)

    def read(self, event):
        reflected = self.appkit.NSEvent.eventWithCGEvent_(event)
        if reflected is None:
            raise RuntimeError('key event reflection failed')
        q = self.q
        return (int(q.CGEventGetType(event)), int(reflected.type()),
                int(reflected.windowNumber()), int(reflected.keyCode()),
                int(q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventKeycode)),
                reflected.characters(), reflected.charactersIgnoringModifiers(),
                int(q.CGEventGetFlags(event)), int(reflected.modifierFlags()),
                bool(reflected.isARepeat()),
                int(q.CGEventGetIntegerValueField(event, q.kCGKeyboardEventAutorepeat)))


def build_window_key(window_id, key, *, backend=None):
    if type(window_id) is not int or not 0 < window_id <= 0xFFFFFFFF:
        raise ValueError('key event requires a positive CG window ID')
    if type(key) is not str or key not in NAVIGATION:
        raise ValueError('only Tab, shift+Tab and arrow navigation are supported')
    backend = backend or _MacBackend()
    code, chars, plain, flags = NAVIGATION[key]
    owners, events = [], []
    timestamp = time.monotonic()
    for kind in (10, 11):
        owner, event = backend.create(kind, window_id, code, chars, plain, flags, timestamp)
        owners.append(owner)
        events.append(event)
    for kind, event in zip((10, 11), events):
        if backend.read(event) != (kind, kind, window_id, code, code, chars, plain,
                                   flags, flags, False, 0):
            raise RuntimeError('key event readback mismatch')
    return PreparedWindowKey(window_id, key, *events, tuple(owners))


@dataclass(frozen=True)
class PreparedWindowText:
    window_id: int
    down: object
    up: object
    _owners: tuple


def build_window_text(window_id, text, *, backend=None):
    """Set Unicode on window-addressed events, then verify BOTH borrowed pairs."""
    from .native_window_payload import valid_window_text
    if type(window_id) is not int or not 0 < window_id <= 0xFFFFFFFF or not valid_window_text(text):
        raise ValueError('invalid opaque window text or window ID')
    backend = backend or _MacBackend()
    owners, events, key_up_characters = [], [], None
    timestamp = time.monotonic()
    for kind in (10, 11):
        owner, event = backend.create(kind, window_id, 0, '', '', 0, timestamp)
        owners.append(owner)
        events.append(event)
        if kind == 11:
            key_up_characters = backend.read(event)[5:7]
        backend.unicode(event, text)
    for kind, event in zip((10, 11), events):
        # AppKit key-up characters reflect the physical key (code 0), while
        # the Unicode payload remains exact on the CG event. Only key-down
        # inserts text. Preserve the constructor's key-up interpretation.
        chars, plain = (text, text) if kind == 10 else key_up_characters
        if (backend.unicode_text(event) != (len(text.encode('utf-16-le')) // 2, text)
                or backend.read(event) != (kind, kind, window_id, 0, 0, chars, plain, 0, 0, False, 0)):
            raise RuntimeError('Unicode window event readback mismatch')
    return PreparedWindowText(window_id, *events, tuple(owners))
