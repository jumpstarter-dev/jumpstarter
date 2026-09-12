"""HID keyboard report builder and keycode mappings for NanoKVM-USB."""

from __future__ import annotations

MAX_KEYS = 6

MODIFIER_BITS: dict[str, int] = {
    "ControlLeft": 1 << 0,
    "ShiftLeft": 1 << 1,
    "AltLeft": 1 << 2,
    "MetaLeft": 1 << 3,
    "ControlRight": 1 << 4,
    "ShiftRight": 1 << 5,
    "AltRight": 1 << 6,
    "MetaRight": 1 << 7,
}

_MODIFIER_ALIASES: dict[str, str] = {
    "ctrl": "ControlLeft",
    "control": "ControlLeft",
    "shift": "ShiftLeft",
    "alt": "AltLeft",
    "meta": "MetaLeft",
    "win": "MetaLeft",
    "cmd": "MetaLeft",
    "super": "MetaLeft",
    "rctrl": "ControlRight",
    "rshift": "ShiftRight",
    "ralt": "AltRight",
    "rmeta": "MetaRight",
}

KEYCODE_MAP: dict[str, int] = {
    "KeyA": 0x04,
    "KeyB": 0x05,
    "KeyC": 0x06,
    "KeyD": 0x07,
    "KeyE": 0x08,
    "KeyF": 0x09,
    "KeyG": 0x0A,
    "KeyH": 0x0B,
    "KeyI": 0x0C,
    "KeyJ": 0x0D,
    "KeyK": 0x0E,
    "KeyL": 0x0F,
    "KeyM": 0x10,
    "KeyN": 0x11,
    "KeyO": 0x12,
    "KeyP": 0x13,
    "KeyQ": 0x14,
    "KeyR": 0x15,
    "KeyS": 0x16,
    "KeyT": 0x17,
    "KeyU": 0x18,
    "KeyV": 0x19,
    "KeyW": 0x1A,
    "KeyX": 0x1B,
    "KeyY": 0x1C,
    "KeyZ": 0x1D,
    "Digit1": 0x1E,
    "Digit2": 0x1F,
    "Digit3": 0x20,
    "Digit4": 0x21,
    "Digit5": 0x22,
    "Digit6": 0x23,
    "Digit7": 0x24,
    "Digit8": 0x25,
    "Digit9": 0x26,
    "Digit0": 0x27,
    "Enter": 0x28,
    "Escape": 0x29,
    "Backspace": 0x2A,
    "Tab": 0x2B,
    "Space": 0x2C,
    "Minus": 0x2D,
    "Equal": 0x2E,
    "BracketLeft": 0x2F,
    "BracketRight": 0x30,
    "Backslash": 0x31,
    "Semicolon": 0x33,
    "Quote": 0x34,
    "Backquote": 0x35,
    "Comma": 0x36,
    "Period": 0x37,
    "Slash": 0x38,
    "CapsLock": 0x39,
    "F1": 0x3A,
    "F2": 0x3B,
    "F3": 0x3C,
    "F4": 0x3D,
    "F5": 0x3E,
    "F6": 0x3F,
    "F7": 0x40,
    "F8": 0x41,
    "F9": 0x42,
    "F10": 0x43,
    "F11": 0x44,
    "F12": 0x45,
    "PrintScreen": 0x46,
    "ScrollLock": 0x47,
    "Pause": 0x48,
    "Insert": 0x49,
    "Home": 0x4A,
    "PageUp": 0x4B,
    "Delete": 0x4C,
    "End": 0x4D,
    "PageDown": 0x4E,
    "ArrowRight": 0x4F,
    "ArrowLeft": 0x50,
    "ArrowDown": 0x51,
    "ArrowUp": 0x52,
    "ControlLeft": 0xE0,
    "ShiftLeft": 0xE1,
    "AltLeft": 0xE2,
    "MetaLeft": 0xE3,
    "ControlRight": 0xE4,
    "ShiftRight": 0xE5,
    "AltRight": 0xE6,
    "MetaRight": 0xE7,
}

_KEY_ALIASES: dict[str, str] = {
    "enter": "Enter",
    "return": "Enter",
    "esc": "Escape",
    "escape": "Escape",
    "backspace": "Backspace",
    "bs": "Backspace",
    "tab": "Tab",
    "space": "Space",
    "delete": "Delete",
    "del": "Delete",
    "insert": "Insert",
    "ins": "Insert",
    "home": "Home",
    "end": "End",
    "pageup": "PageUp",
    "pgup": "PageUp",
    "pagedown": "PageDown",
    "pgdn": "PageDown",
    "up": "ArrowUp",
    "down": "ArrowDown",
    "left": "ArrowLeft",
    "right": "ArrowRight",
    "f1": "F1",
    "f2": "F2",
    "f3": "F3",
    "f4": "F4",
    "f5": "F5",
    "f6": "F6",
    "f7": "F7",
    "f8": "F8",
    "f9": "F9",
    "f10": "F10",
    "f11": "F11",
    "f12": "F12",
}

CHAR_CODES: dict[int, int] = {
    9: 0x2B,
    10: 0x28,
    32: 0x2C,
    33: 0x1E,
    34: 0x34,
    35: 0x20,
    36: 0x21,
    37: 0x22,
    38: 0x24,
    39: 0x34,
    40: 0x26,
    41: 0x27,
    42: 0x25,
    43: 0x2E,
    44: 0x36,
    45: 0x2D,
    46: 0x37,
    47: 0x38,
    48: 0x27,
    49: 0x1E,
    50: 0x1F,
    51: 0x20,
    52: 0x21,
    53: 0x22,
    54: 0x23,
    55: 0x24,
    56: 0x25,
    57: 0x26,
    58: 0x33,
    59: 0x33,
    60: 0x36,
    61: 0x2E,
    62: 0x37,
    63: 0x38,
    64: 0x1F,
    65: 0x04,
    66: 0x05,
    67: 0x06,
    68: 0x07,
    69: 0x08,
    70: 0x09,
    71: 0x0A,
    72: 0x0B,
    73: 0x0C,
    74: 0x0D,
    75: 0x0E,
    76: 0x0F,
    77: 0x10,
    78: 0x11,
    79: 0x12,
    80: 0x13,
    81: 0x14,
    82: 0x15,
    83: 0x16,
    84: 0x17,
    85: 0x18,
    86: 0x19,
    87: 0x1A,
    88: 0x1B,
    89: 0x1C,
    90: 0x1D,
    91: 0x2F,
    92: 0x31,
    93: 0x30,
    94: 0x23,
    95: 0x2D,
    96: 0x35,
    97: 0x04,
    98: 0x05,
    99: 0x06,
    100: 0x07,
    101: 0x08,
    102: 0x09,
    103: 0x0A,
    104: 0x0B,
    105: 0x0C,
    106: 0x0D,
    107: 0x0E,
    108: 0x0F,
    109: 0x10,
    110: 0x11,
    111: 0x12,
    112: 0x13,
    113: 0x14,
    114: 0x15,
    115: 0x16,
    116: 0x17,
    117: 0x18,
    118: 0x19,
    119: 0x1A,
    120: 0x1B,
    121: 0x1C,
    122: 0x1D,
    123: 0x2F,
    124: 0x31,
    125: 0x30,
    126: 0x35,
}

SHIFT_CHARS: set[int] = {
    33,
    34,
    35,
    36,
    37,
    38,
    40,
    41,
    42,
    43,
    58,
    60,
    62,
    63,
    64,
    91,
    92,
    93,
    94,
    95,
    123,
    124,
    125,
    126,
}


def _is_upper(char_code: int) -> bool:
    return 65 <= char_code <= 90


def resolve_key_code(name: str) -> str:
    if name in KEYCODE_MAP or name in MODIFIER_BITS:
        return name
    lower = name.lower()
    if lower in _KEY_ALIASES:
        return _KEY_ALIASES[lower]
    if lower in _MODIFIER_ALIASES:
        return _MODIFIER_ALIASES[lower]
    if len(name) == 1:
        char = name.upper()
        if "A" <= char <= "Z":
            return f"Key{char}"
        if "0" <= char <= "9":
            return f"Digit{char}"
    raise ValueError(f"Unknown key: {name!r}")


def is_modifier(code: str) -> bool:
    return code in MODIFIER_BITS


class KeyboardReport:
    def __init__(self) -> None:
        self._modifier = 0
        self._pressed: dict[str, int] = {}

    def key_down(self, code: str) -> list[int]:
        if is_modifier(code):
            self._modifier |= MODIFIER_BITS[code]
        else:
            keycode = KEYCODE_MAP.get(code)
            if keycode is not None and len(self._pressed) < MAX_KEYS:
                self._pressed[code] = keycode
        return self._build_report()

    def key_up(self, code: str) -> list[int]:
        if is_modifier(code):
            self._modifier &= ~MODIFIER_BITS[code]
        else:
            self._pressed.pop(code, None)
        return self._build_report()

    def reset(self) -> list[int]:
        self._modifier = 0
        self._pressed.clear()
        return self._build_report()

    def _build_report(self) -> list[int]:
        report = [self._modifier, 0, 0, 0, 0, 0, 0, 0]
        for index, keycode in enumerate(self._pressed.values()):
            if index >= MAX_KEYS:
                break
            report[2 + index] = keycode
        return report

    def char_to_report(self, ch: str) -> tuple[list[int], list[int]]:
        code = ord(ch)
        hid_code = CHAR_CODES.get(code)
        if hid_code is None:
            raise ValueError(f"Unsupported character: {ch!r} (0x{code:02X})")

        needs_shift = code in SHIFT_CHARS or _is_upper(code)
        modifier = MODIFIER_BITS["ShiftLeft"] if needs_shift else 0

        down = [modifier, 0, hid_code, 0, 0, 0, 0, 0]
        up = [0, 0, 0, 0, 0, 0, 0, 0]
        return down, up
