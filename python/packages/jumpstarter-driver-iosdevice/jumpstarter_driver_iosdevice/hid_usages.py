"""The keyboard keys the iOS HMI takes: W3C ``KeyboardEvent.code`` names and the USB HID usages scrios types them with.

A ``KeyboardEvent.code`` names a physical key, not the character it makes, and the W3C UI Events specification gives
each one the usage it has on a USB keyboard (page 0x07, "Keyboard/Keypad"). scrios's ``kb down USAGE`` and
``kb up USAGE`` take exactly that usage, in hex (``04`` is A, ``28`` is Enter, ``e1`` is Left Shift), so the two
vocabularies map one to one and nothing here depends on a layout: the phone's own keyboard layout decides which
character a key makes.

:data:`USAGES` is the whole table, and the keys the HMI declares (``hmi_info()["input"]["keyboard"]["codes"]``) are
its keys, in the order of their usages. It is the part of the keyboard page that a full-size keyboard has and that
a phone does something with: letters, digits, the punctuation keys, the editing and arrow keys, the function keys
``F1`` to ``F12``, the numeric keypad and the modifiers. It leaves out what only some keyboards have (``PrintScreen``,
``ScrollLock``, ``Pause``, ``ContextMenu``, the international and language keys, ``F13`` and up): scrios would take
their usages (it takes ``01`` to ``e7``), so a key added here is one line.
"""

from __future__ import annotations

#: The highest usage of the keyboard page (Right GUI) and the highest scrios takes.
MAX_USAGE = 0xE7

#: The punctuation, whitespace and editing keys of the main block.
_MAIN = {
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
}
#: The block above the arrows, and the arrows (Right, Left, Down, Up, in that order in the usage table).
_NAVIGATION = {
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
}
#: The numeric keypad (``Numpad1`` to ``Numpad9`` are 0x59 to 0x61 and ``Numpad0`` follows them).
_KEYPAD = {
    "NumLock": 0x53,
    "NumpadDivide": 0x54,
    "NumpadMultiply": 0x55,
    "NumpadSubtract": 0x56,
    "NumpadAdd": 0x57,
    "NumpadEnter": 0x58,
    **{f"Numpad{n}": 0x58 + n for n in range(1, 10)},
    "Numpad0": 0x62,
    "NumpadDecimal": 0x63,
}
#: The modifiers (Left Control, Shift, Alt and GUI, then the right ones), a key like any other.
_MODIFIERS = {
    "ControlLeft": 0xE0,
    "ShiftLeft": 0xE1,
    "AltLeft": 0xE2,
    "MetaLeft": 0xE3,
    "ControlRight": 0xE4,
    "ShiftRight": 0xE5,
    "AltRight": 0xE6,
    "MetaRight": 0xE7,
}

#: ``KeyboardEvent.code`` to USB HID usage (keyboard page), ordered by usage.
USAGES: dict[str, int] = dict(
    sorted(
        {
            **{f"Key{chr(ord('A') + n)}": 0x04 + n for n in range(26)},  # KeyA 0x04 .. KeyZ 0x1D
            **{f"Digit{n}": 0x1D + n for n in range(1, 10)},  # Digit1 0x1E .. Digit9 0x26
            "Digit0": 0x27,
            **_MAIN,
            **{f"F{n}": 0x39 + n for n in range(1, 13)},  # F1 0x3A .. F12 0x45
            **_NAVIGATION,
            **_KEYPAD,
            **_MODIFIERS,
        }.items(),
        key=lambda item: item[1],
    )
)

#: The codes the HMI declares, in the order of their usages.
CODES: tuple[str, ...] = tuple(USAGES)


def usage_argument(code: str) -> str:
    """The argument of ``kb down`` and ``kb up`` for a key: its usage as two lower-case hex digits (``04``, ``e1``).

    Raises:
        KeyError: If the HMI does not take the key.
    """
    return f"{USAGES[code]:02x}"
