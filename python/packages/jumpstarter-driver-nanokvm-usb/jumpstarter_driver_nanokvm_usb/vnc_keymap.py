"""Map RFB/X11 keysyms to HID keys for a DUT keyboard layout.

Printable characters are injected as HID combos for the configured DUT layout
so the VNC client's keyboard layout does not matter. HID cannot send Unicode;
the DUT OS must use the same layout as ``vnc_layout``.
"""

from __future__ import annotations

from .keyboard import CHAR_CODES, KEYCODE_MAP, MODIFIER_BITS, SHIFT_CHARS, _is_upper

XK_UNICODE = 0x01000000

NONE: frozenset[str] = frozenset()
SHIFT: frozenset[str] = frozenset({"ShiftLeft"})
ALTGR: frozenset[str] = frozenset({"AltRight"})

CharCombo = tuple[str, frozenset[str]]

# Client-only modifiers used to produce a character keysym — do not forward.
_SWALLOWED_KEYSYMS: frozenset[int] = frozenset(
    {
        0xFE03,  # ISO_Level3_Shift (AltGr)
        0xFE04,  # ISO_Level3_Latch
        0xFE05,  # ISO_Level3_Lock
        0xFF7E,  # Mode_switch
    }
)

_NAMED_KEYSYMS: dict[int, str] = {
    0xFF08: "Backspace",
    0xFF09: "Tab",
    0xFF0D: "Enter",
    0xFF1B: "Escape",
    0xFFFF: "Delete",
    0xFF50: "Home",
    0xFF51: "ArrowLeft",
    0xFF52: "ArrowUp",
    0xFF53: "ArrowRight",
    0xFF54: "ArrowDown",
    0xFF55: "PageUp",
    0xFF56: "PageDown",
    0xFF57: "End",
    0xFF63: "Insert",
    0xFFE1: "ShiftLeft",
    0xFFE2: "ShiftRight",
    0xFFE3: "ControlLeft",
    0xFFE4: "ControlRight",
    0xFFE5: "CapsLock",
    0xFFE7: "MetaLeft",
    0xFFE8: "MetaRight",
    0xFFE9: "AltLeft",
    0xFFEA: "AltRight",
    0xFFEB: "MetaLeft",
    0xFFEC: "MetaRight",
    0xFF7F: "NumLock",
    0xFF14: "ScrollLock",
    0xFF13: "Pause",
    0xFF61: "PrintScreen",
    0xFF67: "ContextMenu",
    0xFF8D: "NumpadEnter",
    0xFFAA: "NumpadMultiply",
    0xFFAB: "NumpadAdd",
    0xFFAD: "NumpadSubtract",
    0xFFAE: "NumpadDecimal",
    0xFFAF: "NumpadDivide",
}
for _i in range(12):
    _NAMED_KEYSYMS[0xFFBE + _i] = f"F{_i + 1}"
for _i in range(10):
    _NAMED_KEYSYMS[0xFFB0 + _i] = f"Numpad{_i}"

_HID_TO_NAME = {code: name for name, code in KEYCODE_MAP.items() if name not in MODIFIER_BITS}


def normalize_keysym(keysym: int) -> int:
    if keysym >= XK_UNICODE:
        return keysym & 0xFFFFFF
    return keysym


def is_swallowed_keysym(keysym: int) -> bool:
    return keysym in _SWALLOWED_KEYSYMS


def named_key(keysym: int) -> str | None:
    return _NAMED_KEYSYMS.get(keysym)


def normalize_layout(layout: str) -> str:
    name = (layout or "us").strip().lower()
    if name not in LAYOUTS:
        supported = ", ".join(sorted(LAYOUTS))
        raise ValueError(f"unsupported vnc_layout {layout!r}; use one of: {supported}")
    return name


def _us_layout() -> dict[int, CharCombo]:
    table: dict[int, CharCombo] = {}
    for code, hid in CHAR_CODES.items():
        if code in {9, 10}:
            continue
        name = _HID_TO_NAME.get(hid)
        if name is None:
            continue
        mods = SHIFT if code in SHIFT_CHARS or _is_upper(code) else NONE
        table[code] = (name, mods)
    return table


def _es_layout() -> dict[int, CharCombo]:
    table: dict[int, CharCombo] = {
        ord(" "): ("Space", NONE),
        **{ord(str(i)): (f"Digit{i}", NONE) for i in range(10)},
        ord("!"): ("Digit1", SHIFT),
        ord('"'): ("Digit2", SHIFT),
        0x00B7: ("Digit3", SHIFT),  # ·
        ord("$"): ("Digit4", SHIFT),
        ord("%"): ("Digit5", SHIFT),
        ord("&"): ("Digit6", SHIFT),
        ord("/"): ("Digit7", SHIFT),
        ord("("): ("Digit8", SHIFT),
        ord(")"): ("Digit9", SHIFT),
        ord("="): ("Digit0", SHIFT),
        ord("'"): ("Minus", NONE),
        ord("?"): ("Minus", SHIFT),
        0x00A1: ("Equal", NONE),  # ¡
        0x00BF: ("Equal", SHIFT),  # ¿
        ord("@"): ("Digit2", ALTGR),
        ord("#"): ("Digit3", ALTGR),
        ord("~"): ("Digit4", ALTGR),
        0x20AC: ("Digit5", ALTGR),  # €
        0x00AC: ("Digit6", ALTGR),  # ¬
        ord("|"): ("Digit1", ALTGR),
        ord("\\"): ("Backquote", ALTGR),
        0x00BA: ("Backquote", NONE),  # º
        0x00AA: ("Backquote", SHIFT),  # ª
        ord("`"): ("BracketLeft", NONE),
        ord("^"): ("BracketLeft", SHIFT),
        ord("["): ("BracketLeft", ALTGR),
        ord("+"): ("BracketRight", NONE),
        ord("*"): ("BracketRight", SHIFT),
        ord("]"): ("BracketRight", ALTGR),
        0x00B4: ("Quote", NONE),  # ´
        0x00A8: ("Quote", SHIFT),  # ¨
        ord("{"): ("Quote", ALTGR),
        0x00E7: ("Backslash", NONE),  # ç
        0x00C7: ("Backslash", SHIFT),  # Ç
        ord("}"): ("Backslash", ALTGR),
        0x00F1: ("Semicolon", NONE),  # ñ
        0x00D1: ("Semicolon", SHIFT),  # Ñ
        ord("<"): ("IntlBackslash", NONE),
        ord(">"): ("IntlBackslash", SHIFT),
        ord(","): ("Comma", NONE),
        ord(";"): ("Comma", SHIFT),
        ord("."): ("Period", NONE),
        ord(":"): ("Period", SHIFT),
        ord("-"): ("Slash", NONE),
        ord("_"): ("Slash", SHIFT),
    }
    for offset in range(26):
        lower = ord("a") + offset
        upper = ord("A") + offset
        key = f"Key{chr(upper)}"
        table[lower] = (key, NONE)
        table[upper] = (key, SHIFT)
    return table


LAYOUTS: dict[str, dict[int, CharCombo]] = {
    "us": _us_layout(),
    "es": _es_layout(),
}


def char_combo(keysym: int, layout: str = "us") -> CharCombo | None:
    """Return (HID key, modifiers) to type the printable keysym on ``layout``."""
    table = LAYOUTS[normalize_layout(layout)]
    return table.get(normalize_keysym(keysym))
