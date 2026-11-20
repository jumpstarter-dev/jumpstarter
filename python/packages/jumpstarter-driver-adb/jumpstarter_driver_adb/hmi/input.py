"""Input into the device: touch, keyboard, keys and text as scrcpy control messages.

:class:`ScrcpyInputSink` is the HMI interface's :class:`~jumpstarter_driver_hmi.source.InputSink` for scrcpy, and
takes the subset of the interface that scrcpy genuinely has: one finger (a ``touch``), the keyboard keys that have an
Android key code (:data:`KEYBOARD_CODES`), the named hard keys (:data:`KEY_CODES`) and text. There is no mouse, pen,
wheel or second finger: the source does not declare them, so the exporter never sends them.
Every method **only enqueues** a message on the control socket's
:class:`~jumpstarter_driver_hmi.proc.writer.CommandWriter` and returns: a device that stops reading its control
socket makes the queue full (the call is refused with ``InputError``, which the exporter answers with an error line),
and never makes the caller wait.

**The keyboard.** scrcpy injects Android key events, and an injected event carries its meta state as given: nothing on
the device adds the modifiers that were injected before it. The sink therefore keeps which modifier keys are down (and
which of the lock keys, Caps Lock and Num Lock, are on) and says them in every key message, as the keyboard of a
person would: the meta state of a modifier's own key is the state after it went down or up, so a Shift down carries
``SHIFT_ON``. The keys held are let go by ``release`` (when the input stream ends) and forgotten by ``reset`` (when
the capture is gone: the new one never saw them).

A long press of a hard key is a key down, the repeat that makes Android call it a long press, and the key up some time
later. The later two come from a timer, never from the caller's thread, and go through the writer like everything else.

The sink belongs to the source, not to one run of it: it asks for the writer of the *current* session each time, so an
input stream that is open when the capture restarts (a quality step) goes on with the new one.
"""

from __future__ import annotations

import contextlib
import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from jumpstarter_driver_hmi.common import InputError, PointerEvent, canonical_key
from jumpstarter_driver_hmi.proc.writer import COALESCE_MOVE, CommandWriter
from jumpstarter_driver_hmi.source import InputSink

from . import protocol

log = logging.getLogger(__name__)

#: A key held this long is a long press.
LONG_PRESS_S = 0.6


def _key_codes() -> dict[str, int]:
    """Every key name the server takes, canonical (see :func:`~jumpstarter_driver_hmi.common.canonical_key`), with
    its Android key code."""
    table = {canonical_key(name): code for name, code in protocol.KEYCODES.items()}
    for alias, target in protocol.KEY_ALIASES.items():
        table.setdefault(canonical_key(alias), protocol.KEYCODES[target])
    table["LOCK"] = protocol.KEYCODES["SLEEP"]  # the common LOCK is Android's SLEEP: the screen goes off
    return table


#: The hard keys the HMI accepts, by canonical name: the Android key names and their aliases, plus the common ``LOCK``.
KEY_CODES: dict[str, int] = _key_codes()


def _keyboard_codes() -> dict[str, int]:
    """The keyboard keys that have an Android key code: the W3C ``KeyboardEvent.code`` of each, and the key code
    ``android.view.KeyEvent`` gives the key that is its equivalent."""
    named = protocol.KEYCODES
    table: dict[str, int] = {f"Key{chr(ord('A') + i)}": named[chr(ord("A") + i)] for i in range(26)}
    table.update({f"Digit{d}": named[str(d)] for d in range(10)})
    table.update({f"F{n}": 130 + n for n in range(1, 13)})  # KEYCODE_F1 is 131
    table.update(
        {
            "ArrowUp": named["DPAD_UP"],
            "ArrowDown": named["DPAD_DOWN"],
            "ArrowLeft": named["DPAD_LEFT"],
            "ArrowRight": named["DPAD_RIGHT"],
            "Enter": named["ENTER"],
            "Escape": named["ESCAPE"],
            "Backspace": named["DEL"],  # Android's DEL is the key that deletes backwards
            "Delete": named["FORWARD_DEL"],
            "Tab": named["TAB"],
            "Space": named["SPACE"],
            "Minus": 69,
            "Equal": 70,
            "BracketLeft": 71,
            "BracketRight": 72,
            "Backslash": 73,
            "Semicolon": 74,
            "Quote": 75,  # KEYCODE_APOSTROPHE
            "Comma": named["COMMA"],
            "Period": named["PERIOD"],
            "Slash": 76,
            "Backquote": 68,  # KEYCODE_GRAVE
            "Home": named["MOVE_HOME"],
            "End": named["MOVE_END"],
            "PageUp": named["PAGE_UP"],
            "PageDown": named["PAGE_DOWN"],
            "Insert": 124,
            "PrintScreen": 120,  # KEYCODE_SYSRQ
            "ScrollLock": 116,
            "Pause": 121,  # KEYCODE_BREAK
            "ShiftLeft": 59,
            "ShiftRight": 60,
            "ControlLeft": 113,
            "ControlRight": 114,
            "AltLeft": 57,
            "AltRight": 58,
            "MetaLeft": 117,
            "MetaRight": 118,
            "CapsLock": 115,
            "NumLock": 143,
            "NumpadDivide": 154,
            "NumpadMultiply": 155,
            "NumpadSubtract": 156,
            "NumpadAdd": 157,
            "NumpadDecimal": 158,  # KEYCODE_NUMPAD_DOT
            "NumpadComma": 159,
            "NumpadEnter": 160,
            "NumpadEqual": 161,
            "NumpadParenLeft": 162,
            "NumpadParenRight": 163,
        }
    )
    table.update({f"Numpad{d}": 144 + d for d in range(10)})
    return table


#: The keyboard keys the HMI takes, by W3C ``KeyboardEvent.code``, with their Android key codes: exactly the keys that
#: have an equivalent on Android (letters, digits, F1 to F12, the arrows and the editing keys, the punctuation of a US
#: layout, the modifiers and lock keys, the numpad). Every other code (``F13``, ``IntlBackslash``, ``Lang1``,
#: ``ContextMenu``...) is not declared, so the exporter refuses it before the sink.
KEYBOARD_CODES: dict[str, int] = _keyboard_codes()

#: The modifier keys and the meta state each puts in the key messages while it is down.
MODIFIER_META: dict[str, int] = {
    "ShiftLeft": protocol.META_SHIFT_ON | protocol.META_SHIFT_LEFT_ON,
    "ShiftRight": protocol.META_SHIFT_ON | protocol.META_SHIFT_RIGHT_ON,
    "ControlLeft": protocol.META_CTRL_ON | protocol.META_CTRL_LEFT_ON,
    "ControlRight": protocol.META_CTRL_ON | protocol.META_CTRL_RIGHT_ON,
    "AltLeft": protocol.META_ALT_ON | protocol.META_ALT_LEFT_ON,
    "AltRight": protocol.META_ALT_ON | protocol.META_ALT_RIGHT_ON,
    "MetaLeft": protocol.META_META_ON | protocol.META_META_LEFT_ON,
    "MetaRight": protocol.META_META_ON | protocol.META_META_RIGHT_ON,
}
#: The lock keys: each press (a ``down``) turns the lock on or off, and the meta state says it while it is on. Num Lock
#: starts on (as a desktop keyboard's does: with it off the numpad types no digits) and Caps Lock off.
LOCK_META: dict[str, int] = {"CapsLock": protocol.META_CAPS_LOCK_ON, "NumLock": protocol.META_NUM_LOCK_ON}


class Handle(Protocol):
    """A scheduled call that can be cancelled."""

    def cancel(self) -> None:
        """Do not make the call (a no-op once it has been made)."""


class Scheduler(Protocol):
    """Makes a call after a delay, on a thread of its own."""

    def call_later(self, delay_s: float, call: Callable[[], None]) -> Handle:
        """Make ``call`` after ``delay_s`` seconds."""


class ThreadScheduler:
    """The scheduler of a running exporter: one daemon timer thread per scheduled call."""

    def call_later(self, delay_s: float, call: Callable[[], None]) -> Handle:
        """Make ``call`` after ``delay_s`` seconds, on a timer thread."""
        timer = threading.Timer(delay_s, call)
        timer.daemon = True
        timer.start()
        return timer


@dataclass(eq=False)
class _Held:
    """A key that is down: with a timer while it is a long press under way."""

    handle: Handle | None = None


class ScrcpyInputSink(InputSink):
    """Touch, keys and text for one device over scrcpy's control socket. See the module documentation."""

    def __init__(
        self,
        writer_of: Callable[[], CommandWriter | None],
        *,
        long_press_s: float = LONG_PRESS_S,
        scheduler: Scheduler | None = None,
    ) -> None:
        """``writer_of`` gives the control writer of the running session (``None``: there is none)."""
        self._writer_of = writer_of
        self._long_press_s = long_press_s
        self._scheduler: Scheduler = scheduler or ThreadScheduler()
        self._lock = threading.Lock()
        self._held: dict[int, _Held] = {}
        #: The keyboard keys that are down, by ``KeyboardEvent.code``, in the order they went down.
        self._keys: dict[str, int] = {}
        self._locks = protocol.META_NUM_LOCK_ON
        self._gesture = 0

    # -- what the driver calls -----------------------------------------------------------------------------------------

    def pointer(self, event: PointerEvent, size: tuple[int, int]) -> None:
        """Queue a touch: the one finger the source declares, whatever the id the sender gave it.

        The exporter lets at most ``max_contacts`` (one) contact through at a time and sends a contact's moves and lift
        only after its own ``down``, so the finger is the contact that is down, and the id (a browser's pointer ids are
        arbitrary numbers) says nothing more. Mouse, pen, wheel and relative motion are not declared and are refused.
        """
        x, y = event.x, event.y
        if event.kind == "touch" and event.type in ("down", "move", "up") and x is not None and y is not None:
            self.touch(event.type, round(x), round(y), size)
            return
        raise InputError("this HMI does not take that pointer")

    def touch(self, kind: str, x: int, y: int, size: tuple[int, int]) -> None:
        """Queue one touch of finger 0 at ``x``, ``y`` of the picture of ``size``.

        Only a ``move`` may be replaced by a newer one (of the same gesture, so that the last move before a lift is not
        lost to one of the next touch).
        """
        action = protocol.MOTION_ACTION.get(kind)
        if action is None:
            raise InputError('touch.type must be "down", "move" or "up"')
        pressure = 0.0 if action == protocol.ACTION_UP else 1.0
        message = protocol.inject_touch(action, 0, x, y, size[0], size[1], pressure)
        with self._lock:
            if action == protocol.ACTION_DOWN:
                self._gesture += 1
            coalesce = f"{COALESCE_MOVE}:{self._gesture}" if action == protocol.ACTION_MOVE else None
            self._send(message, coalesce)

    def keyboard(self, code: str, down: bool) -> None:
        """Queue a keyboard key going down or up, by its ``KeyboardEvent.code``, with the meta state of the modifiers
        that are down (and the locks that are on) once it has.

        A key that is down is not pressed again, and a key that is not down is not let go: the device repeats a key by
        itself, and a late ``up`` is nothing. A code outside :data:`KEYBOARD_CODES` is refused (the exporter has
        refused it already, as the source does not declare it).
        """
        keycode = KEYBOARD_CODES.get(code)
        if keycode is None:
            raise InputError(f"this HMI does not take the key {code}")
        with self._lock:
            self._key_event(code, down)

    def key(self, key: str, press: str) -> None:
        """Queue a hard key by its canonical name."""
        code = KEY_CODES.get(canonical_key(key))
        if code is None:
            raise InputError(f"this HMI has no key {key}")
        self.press_code(code, press)

    def press_code(self, code: int, press: str) -> None:
        """Queue a key by its Android key code: ``short``, ``long`` (held, then released on a timer), ``down`` or
        ``up``."""
        with self._lock:
            if press == "short":
                self._send(protocol.inject_keycode(protocol.ACTION_DOWN, code))
                self._send(protocol.inject_keycode(protocol.ACTION_UP, code))
            elif press == "long":
                self._let_go(code)
                self._send(protocol.inject_keycode(protocol.ACTION_DOWN, code))
                self._hold(code, self._long_press_s * 5 / 6, self._repeat)
            elif press == "down":
                self._let_go(code)
                self._send(protocol.inject_keycode(protocol.ACTION_DOWN, code))
                self._held[code] = _Held()
            elif press == "up":
                self._let_go(code, send=False)
                self._send(protocol.inject_keycode(protocol.ACTION_UP, code))
            else:
                raise InputError(f"press must be short, long, down or up: {press!r}")

    def text(self, text: str) -> None:
        """Queue text, in the messages the server takes (at most 300 characters each)."""
        with self._lock:
            for start in range(0, len(text), protocol.INJECT_TEXT_MAX_LENGTH):
                self._send(protocol.inject_text(text[start : start + protocol.INJECT_TEXT_MAX_LENGTH]))

    def release(self) -> None:
        """The input stream ended: release every key still down, a long press under way included (now, not on its
        timer), and the keyboard keys, the last pressed first."""
        with self._lock:
            for code in list(self._held):
                with contextlib.suppress(InputError):
                    self._let_go(code)
            for code in reversed(list(self._keys)):
                with contextlib.suppress(InputError):
                    self._key_event(code, False)
            self._keys = {}  # one the writer refused is let go of too: a modifier must not stay "down" in what follows

    def reset(self) -> None:
        """The session is gone: forget what was held (nobody is left to release it) and cancel what was scheduled.

        The keyboard keys and the modifiers go with it (the next capture never saw them go down); the locks stay, as
        they are what the person at the keyboard has, not what the device was told.
        """
        with self._lock:
            held, self._held = self._held, {}
            self._keys = {}
        for entry in held.values():
            if entry.handle is not None:
                entry.handle.cancel()

    # -- the keyboard --------------------------------------------------------------------------------------------------

    def _key_event(self, code: str, down: bool) -> None:
        """One keyboard key going down or up (lock held); nothing if it already is."""
        if (code in self._keys) == down:
            return
        keycode = KEYBOARD_CODES[code]
        locks = self._locks ^ LOCK_META.get(code, 0) if down else self._locks
        keys = {**self._keys, code: keycode} if down else {k: v for k, v in self._keys.items() if k != code}
        meta = locks
        for held in keys:
            meta |= MODIFIER_META.get(held, 0)
        action = protocol.ACTION_DOWN if down else protocol.ACTION_UP
        self._send(protocol.inject_keycode(action, keycode, meta_state=meta))
        self._keys, self._locks = keys, locks  # a key the writer refused was not pressed

    # -- the long press ------------------------------------------------------------------------------------------------

    def _hold(self, code: int, delay_s: float, call: Callable[[int, _Held], None]) -> None:
        """Hold ``code`` and call ``call`` in ``delay_s`` seconds, unless it is let go before (lock held)."""
        entry = _Held()
        entry.handle = self._scheduler.call_later(delay_s, lambda: self._timed(code, entry, call))
        self._held[code] = entry

    def _timed(self, code: int, entry: _Held, call: Callable[[int, _Held], None]) -> None:
        with self._lock:
            if self._held.get(code) is not entry:
                return  # let go, or pressed again, since
            try:
                call(code, entry)
            except InputError as e:
                log.debug("the device did not take a key release: %s", e)
                self._held.pop(code, None)

    def _repeat(self, code: int, entry: _Held) -> None:
        """The key repeats once (what makes Android call it a long press), and is released a little later."""
        self._send(protocol.inject_keycode(protocol.ACTION_DOWN, code, repeat=1))
        entry.handle = self._scheduler.call_later(
            self._long_press_s / 6, lambda: self._timed(code, entry, self._finish)
        )

    def _finish(self, code: int, entry: _Held) -> None:
        self._held.pop(code, None)
        self._send(protocol.inject_keycode(protocol.ACTION_UP, code))

    def _let_go(self, code: int, *, send: bool = True) -> None:
        """End a hold on ``code`` (lock held): cancel its timer and release the key, if it is down."""
        entry = self._held.pop(code, None)
        if entry is None:
            return
        if entry.handle is not None:
            entry.handle.cancel()
        if send:
            self._send(protocol.inject_keycode(protocol.ACTION_UP, code))

    # -- the writer ----------------------------------------------------------------------------------------------------

    def _send(self, message: bytes, coalesce: str | None = None) -> None:
        writer = self._writer_of()
        if writer is None:
            raise InputError("the HMI stream is not running")
        writer.send(message, coalesce=coalesce)
