"""Touch, keyboard, keys and text into scrcpy's control socket: what they become, that they only enqueue, and what
happens to the keys that are held."""

from __future__ import annotations

import struct
import threading
import time

import pytest
from jumpstarter_driver_hmi.common import COMMON_KEYS, InputError, KeyboardCaps, PointerEvent, canonical_key
from jumpstarter_driver_hmi.proc.writer import CommandWriter
from jumpstarter_driver_hmi.testing.conformance import wait_for

from . import protocol
from .input import KEY_CODES, KEYBOARD_CODES, LOCK_META, LONG_PRESS_S, MODIFIER_META, ScrcpyInputSink, ThreadScheduler

SIZE = (540, 1200)


class RecordingWriter:
    """The writer's side that the sink sees: what was sent, with its coalesce key; ``refuse`` makes ``send`` raise."""

    def __init__(self) -> None:
        self.sent: list[tuple[bytes, str | None]] = []
        self.refuse: str | None = None

    def send(self, data: bytes, *, coalesce: str | None = None) -> bool:
        if self.refuse:
            raise InputError(self.refuse)
        self.sent.append((data, coalesce))
        return True

    @property
    def messages(self) -> list[bytes]:
        return [data for data, _ in self.sent]

    def key_events(self) -> list[tuple[int, int, int]]:
        """``(action, key code, repeat)`` of every key message."""
        return [(m[1], *struct.unpack_from(">ii", m, 2)) for m in self.messages if m[0] == 0]

    def key_messages(self) -> list[tuple[int, int, int, int]]:
        """``(action, key code, repeat, meta state)`` of every key message."""
        return [(m[1], *struct.unpack_from(">iii", m, 2)) for m in self.messages if m[0] == 0]


class ManualScheduler:
    """Time that stands still until the test moves it: a long press is decided by ``advance``, not by sleeping."""

    class Handle:
        def __init__(self, due, call):
            self.due, self.call, self.cancelled = due, call, False

        def cancel(self):
            self.cancelled = True

    def __init__(self) -> None:
        self.now = 0.0
        self.pending: list[ManualScheduler.Handle] = []

    def call_later(self, delay_s, call):
        handle = self.Handle(self.now + delay_s, call)
        self.pending.append(handle)
        return handle

    def advance(self, seconds: float) -> None:
        end = self.now + seconds
        while True:
            due = sorted((h for h in self.pending if not h.cancelled and h.due <= end), key=lambda h: h.due)
            if not due:
                break
            handle = due[0]
            self.pending.remove(handle)
            self.now = handle.due
            handle.call()
        self.now = end


@pytest.fixture
def writer():
    return RecordingWriter()


@pytest.fixture
def clock():
    return ManualScheduler()


@pytest.fixture
def sink(writer, clock):
    return ScrcpyInputSink(lambda: writer, scheduler=clock)


# -- the keys ------------------------------------------------------------------------------------------------


def test_the_common_keys_are_all_there_under_their_canonical_names():
    assert set(COMMON_KEYS) <= set(KEY_CODES)
    assert KEY_CODES["HOME"] == 3 and KEY_CODES["BACK"] == 4 and KEY_CODES["APP_SWITCH"] == 187
    assert KEY_CODES["POWER"] == 26 and KEY_CODES["VOLUME_UP"] == 24 and KEY_CODES["VOLUME_DOWN"] == 25
    assert KEY_CODES["MUTE"] == 164  # Android's VOLUME_MUTE
    assert KEY_CODES["ASSIST"] == 219
    assert KEY_CODES["LOCK"] == protocol.KEYCODES["SLEEP"] == 223  # the common LOCK is Android's SLEEP


def test_every_key_name_is_canonical_and_means_what_scrcpy_says_it_does():
    assert all(canonical_key(name) == name for name in KEY_CODES)
    assert KEY_CODES[canonical_key("recents")] == KEY_CODES[canonical_key("APP_SWITCHER")] == 187
    assert KEY_CODES[canonical_key("VOLUME_MUTE")] == KEY_CODES[canonical_key("mute")] == 164
    assert KEY_CODES["ENTER"] == 66 and KEY_CODES["A"] == 29 and KEY_CODES["0"] == 7
    assert "VOLUME_MUTE" not in KEY_CODES  # it is MUTE, in the interface's words


def test_a_short_press_is_a_down_and_an_up(sink, writer):
    sink.key("HOME", "short")
    assert writer.key_events() == [(protocol.ACTION_DOWN, 3, 0), (protocol.ACTION_UP, 3, 0)]


def test_a_key_is_known_by_any_spelling_of_its_name(sink, writer):
    sink.key("recents", "short")
    sink.key("app-switcher", "short")
    sink.key("KEYCODE_BACK", "short")
    assert [code for _, code, _ in writer.key_events()] == [187, 187, 187, 187, 4, 4]


def test_an_unknown_key_or_press_is_refused_and_nothing_is_sent(sink, writer):
    with pytest.raises(InputError, match="no key NOPE"):
        sink.key("NOPE", "short")
    with pytest.raises(InputError, match="press must be"):
        sink.key("HOME", "tap")
    assert writer.sent == []


def test_down_and_up_hold_a_key_until_it_is_let_go(sink, writer, clock):
    sink.key("VOLUME_UP", "down")
    clock.advance(30)
    assert writer.key_events() == [(protocol.ACTION_DOWN, 24, 0)]  # nothing lets go of it on a timer
    sink.key("VOLUME_UP", "up")
    assert writer.key_events()[-1] == (protocol.ACTION_UP, 24, 0)


# -- the keyboard -------------------------------------------------------------------------------------------------

DOWN, UP = protocol.ACTION_DOWN, protocol.ACTION_UP
SHIFT, LEFT_SHIFT, RIGHT_SHIFT = protocol.META_SHIFT_ON, protocol.META_SHIFT_LEFT_ON, protocol.META_SHIFT_RIGHT_ON
CTRL = protocol.META_CTRL_ON | protocol.META_CTRL_LEFT_ON
#: Num Lock is on until a key press turns it off (as on a desktop keyboard), so every key message says it.
NUM = protocol.META_NUM_LOCK_ON
A = 29  # KEYCODE_A


def test_the_keyboard_has_exactly_the_keys_that_android_has_an_equivalent_of():
    letters = {f"Key{c}" for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ"}
    digits = {f"Digit{d}" for d in range(10)}
    function = {f"F{n}" for n in range(1, 13)}
    arrows = {"ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight"}
    editing = {"Enter", "Escape", "Backspace", "Delete", "Tab", "Space", "Home", "End", "PageUp", "PageDown", "Insert"}
    punctuation = {
        "Minus", "Equal", "BracketLeft", "BracketRight", "Backslash", "Semicolon", "Quote", "Comma", "Period", "Slash",
        "Backquote",
    }  # fmt: skip
    modifiers = {
        "ShiftLeft", "ShiftRight", "ControlLeft", "ControlRight", "AltLeft", "AltRight", "MetaLeft", "MetaRight",
    }  # fmt: skip
    locks = {"CapsLock", "NumLock", "ScrollLock"}
    system = {"PrintScreen", "Pause"}
    numpad = {f"Numpad{d}" for d in range(10)} | {
        "NumpadAdd", "NumpadSubtract", "NumpadMultiply", "NumpadDivide", "NumpadDecimal", "NumpadComma", "NumpadEnter",
        "NumpadEqual", "NumpadParenLeft", "NumpadParenRight",
    }  # fmt: skip
    expected = letters | digits | function | arrows | editing | punctuation | modifiers | locks | system | numpad
    assert set(KEYBOARD_CODES) == expected and len(KEYBOARD_CODES) == 26 + 10 + 12 + 4 + 11 + 11 + 8 + 3 + 2 + 20
    assert KeyboardCaps(codes=tuple(KEYBOARD_CODES)).allows("KeyA")


def test_no_two_keyboard_codes_are_the_same_android_key_and_each_is_a_key_android_has():
    assert len(set(KEYBOARD_CODES.values())) == len(KEYBOARD_CODES)
    assert all(0 < keycode < 300 for keycode in KEYBOARD_CODES.values())


@pytest.mark.parametrize(
    ("code", "keycode"),
    [
        ("KeyA", 29), ("KeyZ", 54), ("Digit0", 7), ("Digit9", 16), ("F1", 131), ("F12", 142),
        ("ArrowUp", 19), ("ArrowDown", 20), ("ArrowLeft", 21), ("ArrowRight", 22),
        ("Enter", 66), ("Escape", 111), ("Backspace", 67), ("Delete", 112), ("Tab", 61), ("Space", 62),
        ("Minus", 69), ("Equal", 70), ("BracketLeft", 71), ("BracketRight", 72), ("Backslash", 73),
        ("Semicolon", 74), ("Quote", 75), ("Comma", 55), ("Period", 56), ("Slash", 76), ("Backquote", 68),
        ("Home", 122), ("End", 123), ("PageUp", 92), ("PageDown", 93), ("Insert", 124),
        ("ShiftLeft", 59), ("ShiftRight", 60), ("ControlLeft", 113), ("ControlRight", 114),
        ("AltLeft", 57), ("AltRight", 58), ("MetaLeft", 117), ("MetaRight", 118), ("CapsLock", 115),
        ("NumLock", 143), ("Numpad0", 144), ("Numpad9", 153), ("NumpadDivide", 154), ("NumpadMultiply", 155),
        ("NumpadSubtract", 156), ("NumpadAdd", 157), ("NumpadDecimal", 158), ("NumpadEnter", 160),
        ("ScrollLock", 116), ("PrintScreen", 120), ("Pause", 121),
    ],
)  # fmt: skip
def test_a_keyboard_code_is_the_android_key_that_is_its_equivalent(sink, writer, code, keycode):
    assert KEYBOARD_CODES[code] == keycode
    sink.keyboard(code, True)
    sink.keyboard(code, False)
    assert [(action, sent) for action, sent, _, _ in writer.key_messages()] == [(DOWN, keycode), (UP, keycode)]


def test_the_letters_and_digits_agree_with_the_names_scrcpy_knows():
    assert all(KEYBOARD_CODES[f"Key{c}"] == KEY_CODES[c] for c in "ABCDEFGHIJKLMNOPQRSTUVWXYZ")
    assert all(KEYBOARD_CODES[f"Digit{d}"] == KEY_CODES[str(d)] for d in range(10))
    assert KEYBOARD_CODES["Backspace"] == KEY_CODES["DEL"] and KEYBOARD_CODES["Delete"] == KEY_CODES["FORWARD_DEL"]


def test_a_key_is_a_down_and_an_up_with_no_repeat_and_no_modifiers(sink, writer):
    sink.keyboard("KeyA", True)
    assert writer.key_messages() == [(DOWN, A, 0, NUM)]
    sink.keyboard("KeyA", False)
    assert writer.key_messages()[1:] == [(UP, A, 0, NUM)]
    assert writer.messages[0] == struct.pack(">BBiii", 0, 0, 29, 0, 0x200000)  # type, action, key, repeat, meta state
    assert writer.messages[0] == protocol.inject_keycode(DOWN, A, meta_state=NUM)


def test_a_modifier_that_is_down_is_in_the_meta_state_of_the_keys_after_it(sink, writer):
    sink.keyboard("ShiftLeft", True)
    sink.keyboard("KeyA", True)
    sink.keyboard("KeyA", False)
    sink.keyboard("ShiftLeft", False)
    sink.keyboard("KeyA", True)
    sink.keyboard("KeyA", False)
    assert writer.key_messages() == [
        (DOWN, 59, 0, NUM | SHIFT | LEFT_SHIFT),  # the modifier's own message has it: the state once it is down
        (DOWN, A, 0, NUM | SHIFT | LEFT_SHIFT),
        (UP, A, 0, NUM | SHIFT | LEFT_SHIFT),
        (UP, 59, 0, NUM),  # and not once it is let go
        (DOWN, A, 0, NUM),
        (UP, A, 0, NUM),
    ]


def test_every_modifier_says_its_own_side_and_the_generic_flag(sink, writer):
    expected = {
        "ShiftLeft": SHIFT | LEFT_SHIFT,
        "ShiftRight": SHIFT | RIGHT_SHIFT,
        "ControlLeft": protocol.META_CTRL_ON | protocol.META_CTRL_LEFT_ON,
        "ControlRight": protocol.META_CTRL_ON | protocol.META_CTRL_RIGHT_ON,
        "AltLeft": protocol.META_ALT_ON | protocol.META_ALT_LEFT_ON,
        "AltRight": protocol.META_ALT_ON | protocol.META_ALT_RIGHT_ON,
        "MetaLeft": protocol.META_META_ON | protocol.META_META_LEFT_ON,
        "MetaRight": protocol.META_META_ON | protocol.META_META_RIGHT_ON,
    }
    assert MODIFIER_META == expected
    for code, meta in expected.items():
        sink.keyboard(code, True)
        sink.keyboard("KeyA", True)
        sink.keyboard("KeyA", False)
        sink.keyboard(code, False)
        down, key_down, key_up, up = writer.key_messages()[-4:]
        assert (down[3], key_down[3], key_up[3], up[3]) == (NUM | meta, NUM | meta, NUM | meta, NUM), code


def test_the_flag_values_are_those_of_android_view_keyevent():
    assert [protocol.META_SHIFT_ON, protocol.META_ALT_ON, protocol.META_ALT_LEFT_ON, protocol.META_ALT_RIGHT_ON] == [
        0x1, 0x2, 0x10, 0x20,
    ]  # fmt: skip
    assert [protocol.META_SHIFT_LEFT_ON, protocol.META_SHIFT_RIGHT_ON] == [0x40, 0x80]
    assert [protocol.META_CTRL_ON, protocol.META_CTRL_LEFT_ON, protocol.META_CTRL_RIGHT_ON] == [0x1000, 0x2000, 0x4000]
    assert [protocol.META_META_ON, protocol.META_META_LEFT_ON, protocol.META_META_RIGHT_ON] == [
        0x10000, 0x20000, 0x40000,
    ]  # fmt: skip
    assert [protocol.META_CAPS_LOCK_ON, protocol.META_NUM_LOCK_ON] == [0x100000, 0x200000]


def test_several_modifiers_add_up_and_letting_go_of_one_leaves_the_others(sink, writer):
    for code in ("ShiftLeft", "ControlLeft", "ShiftRight"):
        sink.keyboard(code, True)
    sink.keyboard("ShiftLeft", False)
    sink.keyboard("KeyC", True)
    metas = [m[3] for m in writer.key_messages()]
    assert metas[2] == NUM | SHIFT | LEFT_SHIFT | RIGHT_SHIFT | CTRL  # all three are down
    assert metas[3] == NUM | SHIFT | RIGHT_SHIFT | CTRL  # the left shift is up: the generic flag stays for the right
    assert metas[4] == NUM | SHIFT | RIGHT_SHIFT | CTRL  # and the letter says what is down now


def test_num_lock_starts_on_and_caps_lock_off_and_each_press_of_a_lock_turns_it_over(sink, writer):
    num, caps = NUM, protocol.META_CAPS_LOCK_ON
    assert LOCK_META == {"CapsLock": caps, "NumLock": num}
    sink.keyboard("KeyA", True)
    sink.keyboard("KeyA", False)
    assert [m[3] for m in writer.key_messages()] == [num, num]  # the numpad types digits unless it is turned off
    sink.keyboard("CapsLock", True)  # on: its own message already says it
    sink.keyboard("CapsLock", False)  # a key's up does not turn it back
    sink.keyboard("KeyA", True)
    sink.keyboard("KeyA", False)
    sink.keyboard("CapsLock", True)  # off
    sink.keyboard("CapsLock", False)
    sink.keyboard("KeyA", True)
    assert [m[3] for m in writer.key_messages()[2:]] == [num | caps] * 4 + [num, num, num]
    sink.keyboard("NumLock", True)
    sink.keyboard("Numpad7", True)
    assert [m[3] for m in writer.key_messages()[-2:]] == [0, 0]  # Num Lock turned off


def test_a_key_that_is_down_is_not_pressed_again_and_one_that_is_not_down_is_not_let_go(sink, writer):
    sink.keyboard("KeyA", False)  # a late up of a key that was never down
    assert writer.sent == []
    sink.keyboard("KeyA", True)
    sink.keyboard("KeyA", True)  # a repeat: the device repeats by itself
    sink.keyboard("CapsLock", True)
    sink.keyboard("CapsLock", True)  # and a repeat of a lock does not turn it over again
    assert [(a, k) for a, k, _, _ in writer.key_messages()] == [(DOWN, A), (DOWN, 115)]
    sink.keyboard("KeyA", False)
    sink.keyboard("KeyA", False)
    assert [(a, k) for a, k, _, _ in writer.key_messages()][2:] == [(UP, A)]


def test_a_code_outside_the_table_is_refused_and_nothing_is_sent(sink, writer):
    for code in ("F13", "IntlBackslash", "Lang1", "ContextMenu", "MediaPlayPause", "NoSuchKey", "keya", ""):
        with pytest.raises(InputError, match="does not take the key"):
            sink.keyboard(code, True)
    assert writer.sent == []


def test_a_key_the_writer_refuses_is_not_held_and_does_not_change_the_modifiers(sink, writer):
    writer.refuse = "the device is busy: its input queue is full"
    with pytest.raises(InputError, match="busy"):
        sink.keyboard("ShiftLeft", True)
    with pytest.raises(InputError, match="busy"):
        sink.keyboard("CapsLock", True)
    writer.refuse = None
    sink.keyboard("KeyA", True)
    assert writer.key_messages() == [(DOWN, A, 0, NUM)]  # no shift, no caps
    sink.release()
    assert [m[:2] for m in writer.key_messages()] == [(DOWN, A), (UP, A)]  # a shift that never went down: not let go


def test_ending_the_input_lets_go_of_the_keys_held_the_last_pressed_first_with_each_steps_meta_state(sink, writer):
    for code in ("ControlLeft", "ShiftLeft", "KeyA"):
        sink.keyboard(code, True)
    sink.release()
    assert writer.key_messages()[3:] == [
        (UP, A, 0, NUM | CTRL | SHIFT | LEFT_SHIFT),  # the letter, then the modifiers, in the opposite order
        (UP, 59, 0, NUM | CTRL),
        (UP, 113, 0, NUM),
    ]
    sink.release()
    assert len(writer.key_messages()) == 6  # and nothing is left to let go of
    sink.keyboard("KeyB", True)
    assert writer.key_messages()[-1][3] == NUM  # no modifier is remembered


def test_a_key_the_device_would_not_take_the_release_of_is_forgotten_all_the_same(sink, writer):
    sink.keyboard("ShiftLeft", True)
    writer.refuse = "the device is busy: its input queue is full"
    sink.release()  # the up of the shift is refused: nothing can be done about it, and it is not "down" any more
    writer.refuse = None
    sink.keyboard("KeyA", True)
    assert writer.key_messages()[-1] == (DOWN, A, 0, NUM)  # no shift stuck on in the messages that follow


def test_a_restart_of_the_capture_forgets_the_keys_held_without_sending_anything_into_the_new_one(sink, writer):
    sink.keyboard("ShiftLeft", True)
    sink.keyboard("KeyA", True)
    sink.reset()  # the source's stop: the session they were pressed in is gone
    assert len(writer.sent) == 2
    sink.release()
    assert len(writer.sent) == 2  # nothing is let go of in the next session
    sink.keyboard("KeyA", True)
    assert writer.key_messages()[-1] == (DOWN, A, 0, NUM)  # the shift is not carried over
    sink.keyboard("ShiftLeft", False)  # the person's late up of a shift that the new capture never saw
    assert len(writer.sent) == 3


def test_the_locks_survive_a_restart_of_the_capture(sink, writer):
    sink.keyboard("CapsLock", True)
    sink.keyboard("CapsLock", False)
    sink.reset()
    sink.keyboard("KeyA", True)
    assert writer.key_messages()[-1][3] == NUM | protocol.META_CAPS_LOCK_ON


def test_a_hard_key_and_a_keyboard_key_are_each_their_own_and_a_hard_key_has_no_modifiers(sink, writer):
    sink.keyboard("ShiftLeft", True)
    sink.key("HOME", "short")  # a named hard key is a button, not a keystroke: it carries no meta state
    assert writer.key_messages()[1:] == [(DOWN, 3, 0, 0), (UP, 3, 0, 0)]  # not even Num Lock's
    sink.keyboard("ShiftLeft", False)


def test_keys_text_and_touch_keep_the_order_they_were_sent_in_across_the_kinds(sink, writer):
    sink.keyboard("KeyA", True)
    sink.text("hi")
    sink.touch("down", 1, 1, SIZE)
    sink.keyboard("KeyA", False)
    sink.touch("up", 1, 1, SIZE)
    assert [m[0] for m in writer.messages] == [0, 1, 2, 0, 2]


# -- the pointer ---------------------------------------------------------------------------------------------------


def test_a_pointer_event_of_a_touch_is_the_touch_of_finger_zero(sink, writer):
    sink.pointer(PointerEvent("down", "touch", 270.0, 600.0, 0, 1), SIZE)
    sink.pointer(PointerEvent("move", "touch", 280.0, 610.0, 0, 1), SIZE)
    sink.pointer(PointerEvent("up", "touch", 280.0, 610.0, 0, 0), SIZE)
    assert [m[:2] for m in writer.messages] == [bytes([2, 0]), bytes([2, 2]), bytes([2, 1])]
    assert struct.unpack_from(">iiHH", writer.messages[0], 10) == (270, 600, 540, 1200)


def test_the_one_finger_is_whatever_id_the_sender_gave_it(sink, writer):
    """The exporter lets one contact through at a time, so an id (a browser's are arbitrary) says nothing more."""
    sink.pointer(PointerEvent("down", "touch", 10.0, 20.0, 7, 1), SIZE)
    sink.pointer(PointerEvent("move", "touch", 11.4, 21.6, 7, 1), SIZE)
    sink.pointer(PointerEvent("up", "touch", 11.4, 21.6, 7, 0), SIZE)
    assert [m[0:2] for m in writer.messages] == [bytes([2, 0]), bytes([2, 2]), bytes([2, 1])]
    assert all(struct.unpack_from(">q", m, 2)[0] == 0 for m in writer.messages)  # scrcpy's own finger 0
    assert [struct.unpack_from(">ii", m, 10) for m in writer.messages] == [(10, 20), (11, 22), (11, 22)]


@pytest.mark.parametrize(
    "event",
    [
        PointerEvent("down", "mouse", 1.0, 1.0, 0, 1),
        PointerEvent("down", "pen", 1.0, 1.0, 0, 1),
        PointerEvent("wheel", "touch", 1.0, 1.0, 0, None, 0.0, 120.0),
        PointerEvent("move", "touch", None, None, 0, None, 5.0, 5.0),  # relative motion
    ],
)
def test_what_scrcpy_has_no_pointer_for_is_refused_and_nothing_is_sent(sink, writer, event):
    with pytest.raises(InputError, match="does not take that pointer"):
        sink.pointer(event, SIZE)
    assert writer.sent == []


# -- the long press ---------------------------------------------------------------------------------------------------


def test_a_long_press_is_a_down_now_then_the_repeat_and_the_up_from_a_timer(sink, writer, clock):
    sink.key("APP_SWITCH", "long")
    assert writer.key_events() == [(protocol.ACTION_DOWN, 187, 0)]  # the caller got back with only the down queued
    clock.advance(LONG_PRESS_S * 5 / 6 - 0.01)
    assert len(writer.key_events()) == 1
    clock.advance(0.02)
    assert writer.key_events()[1:] == [(protocol.ACTION_DOWN, 187, 1)]  # the repeat is what makes Android call it long
    clock.advance(LONG_PRESS_S / 6 + 0.01)
    assert writer.key_events()[2:] == [(protocol.ACTION_UP, 187, 0)]
    clock.advance(30)
    assert len(writer.key_events()) == 3  # and that was all


def test_a_long_press_that_is_let_go_early_is_not_continued_by_its_timer(sink, writer, clock):
    sink.key("BACK", "long")
    sink.key("BACK", "up")
    clock.advance(30)
    assert writer.key_events() == [(protocol.ACTION_DOWN, 4, 0), (protocol.ACTION_UP, 4, 0)]


def test_a_second_long_press_of_a_key_finishes_the_first(sink, writer, clock):
    sink.key("HOME", "long")
    sink.key("HOME", "long")
    assert writer.key_events() == [(0, 3, 0), (1, 3, 0), (0, 3, 0)]
    clock.advance(30)
    assert writer.key_events()[3:] == [(0, 3, 1), (1, 3, 0)]  # the second one runs its course, once


def test_two_keys_can_be_held_at_once(sink, writer, clock):
    sink.key("VOLUME_UP", "long")
    sink.key("VOLUME_DOWN", "long")
    clock.advance(30)
    assert sorted(writer.key_events()) == sorted(
        [(0, 24, 0), (0, 25, 0), (0, 24, 1), (0, 25, 1), (1, 24, 0), (1, 25, 0)]
    )


def test_ending_the_input_releases_what_is_still_held_now_and_cancels_the_timers(sink, writer, clock):
    sink.key("POWER", "long")  # under way
    sink.key("VOLUME_UP", "down")  # held
    sink.release()
    assert sorted(writer.key_events()[2:]) == [(protocol.ACTION_UP, 24, 0), (protocol.ACTION_UP, 26, 0)]
    clock.advance(30)
    assert len(writer.key_events()) == 4  # the long press's own repeat and up never come
    sink.release()
    assert len(writer.key_events()) == 4  # and releasing twice is nothing


def test_reset_forgets_what_was_held_without_sending(sink, writer, clock):
    sink.key("POWER", "long")
    sink.key("HOME", "down")
    sink.reset()
    clock.advance(30)
    assert len(writer.key_events()) == 2  # the session they were pressed in is gone: nothing is sent into another
    sink.release()
    assert len(writer.key_events()) == 2


def test_a_timer_that_finds_the_device_refusing_drops_the_hold(sink, writer, clock):
    sink.key("POWER", "long")
    writer.refuse = "the device stopped taking input"
    clock.advance(30)  # the repeat is refused: nothing is raised on the timer's thread
    writer.refuse = None
    sink.release()
    assert writer.key_events() == [(0, 26, 0)]  # nothing is left held to release: the hold was dropped with the refusal


def test_the_real_timer_makes_the_long_press_without_the_caller_waiting(writer):
    sink = ScrcpyInputSink(lambda: writer, long_press_s=0.12, scheduler=ThreadScheduler())
    sink.key("HOME", "long")
    assert writer.key_events() == [(protocol.ACTION_DOWN, 3, 0)]  # when the call has returned only the down is queued
    wait_for(lambda: len(writer.key_events()) == 3, what="the repeat and the up from the timer")
    assert writer.key_events()[1:] == [(protocol.ACTION_DOWN, 3, 1), (protocol.ACTION_UP, 3, 0)]


# -- touch ------------------------------------------------------------------------------------------------------------


def test_a_touch_is_finger_zero_at_the_pixel_of_the_picture(sink, writer):
    sink.touch("down", 270, 600, SIZE)
    sink.touch("move", 280, 610, SIZE)
    sink.touch("up", 280, 610, SIZE)
    down, move, up = writer.messages
    assert [m[0:2] for m in (down, move, up)] == [bytes([2, 0]), bytes([2, 2]), bytes([2, 1])]  # down, move, up
    assert struct.unpack_from(">q", down, 2)[0] == 0  # finger 0
    assert struct.unpack_from(">iiHHH", down, 10) == (270, 600, 540, 1200, 0xFFFF)  # the picture the touch was aimed at
    assert struct.unpack_from(">H", up, 22)[0] == 0  # no pressure when lifted


def test_an_unknown_kind_of_touch_is_refused(sink, writer):
    with pytest.raises(InputError, match="touch.type"):
        sink.touch("poke", 1, 1, SIZE)
    assert writer.sent == []


def test_only_moves_may_be_replaced_and_only_by_a_move_of_the_same_gesture(sink, writer):
    sink.touch("down", 1, 1, SIZE)
    sink.touch("move", 2, 2, SIZE)
    sink.touch("up", 2, 2, SIZE)
    sink.touch("down", 5, 5, SIZE)
    sink.touch("move", 6, 6, SIZE)
    assert [key for _, key in writer.sent] == [None, "move:1", None, None, "move:2"]


def test_the_writer_replaces_a_move_by_the_next_of_its_gesture_and_keeps_the_last_move_before_a_lift():
    """The sink's keys against a real writer whose pipe is stuck: what is delivered is what the gesture needs."""
    gate = threading.Event()
    written: list[bytes] = []

    def write(data: bytes) -> None:
        gate.wait()
        written.append(data)

    real = CommandWriter(write, depth=16)
    sink = ScrcpyInputSink(lambda: real)
    sink.touch("down", 1, 1, SIZE)  # taken by the writer's thread, which is stuck writing it
    wait_for(lambda: real.stats()["queued"] == 1, what="the down to be taken")
    for x in (2, 3, 4):
        sink.touch("move", x, x, SIZE)
    sink.touch("up", 4, 4, SIZE)
    sink.touch("down", 7, 7, SIZE)
    sink.touch("move", 8, 8, SIZE)
    gate.set()
    wait_for(lambda: len(written) == 5, what="the writes")
    real.close(2)
    xs = [(m[1], struct.unpack_from(">i", m, 10)[0]) for m in written]
    assert xs == [(0, 1), (2, 4), (1, 4), (0, 7), (2, 8)]  # down 1, the last move of the first gesture, up, down, move


# -- text -------------------------------------------------------------------------------------------------------------


def test_text_goes_in_the_messages_the_server_takes_in_order(sink, writer):
    sink.text("a" * 700)
    sizes = [struct.unpack_from(">I", m, 1)[0] for m in writer.messages]
    assert sizes == [300, 300, 100] and all(m[0] == 1 for m in writer.messages)
    sink.text("héllo")
    assert writer.messages[-1] == protocol.inject_text("héllo")


def test_text_a_key_and_a_touch_keep_the_order_they_were_sent_in(sink, writer):
    sink.text("hi")
    sink.key("DEL", "short")
    sink.touch("down", 1, 1, SIZE)
    sink.touch("up", 1, 1, SIZE)
    assert [m[0] for m in writer.messages] == [1, 0, 0, 2, 2]


# -- the writer -------------------------------------------------------------------------------------------------------


def test_a_session_that_is_not_running_takes_nothing():
    sink = ScrcpyInputSink(lambda: None)
    for call in (
        lambda: sink.touch("down", 1, 1, SIZE),
        lambda: sink.keyboard("KeyA", True),
        lambda: sink.key("HOME", "short"),
        lambda: sink.text("x"),
        lambda: sink.press_code(3, "short"),
    ):
        with pytest.raises(InputError, match="not running"):
            call()


def test_what_the_writer_refuses_is_refused_to_the_sender(sink, writer):
    writer.refuse = "the device is busy: its input queue is full"
    with pytest.raises(InputError, match="busy"):
        sink.touch("down", 1, 1, SIZE)
    with pytest.raises(InputError, match="busy"):
        sink.text("x")


def test_the_sink_follows_the_session_that_runs_now(clock):
    first, second = RecordingWriter(), RecordingWriter()
    current = [first]
    sink = ScrcpyInputSink(lambda: current[0], scheduler=clock)  # ty: ignore[invalid-argument-type]
    sink.touch("down", 1, 1, SIZE)
    current[0] = second  # the capture restarted: the same sink goes on, with the new session's writer
    sink.touch("up", 1, 1, SIZE)
    assert len(first.sent) == 1 and len(second.sent) == 1


def test_a_stuck_pipe_never_makes_the_sender_wait():
    """At the sink: the writer's pipe blocks for good; every call still returns at once, and when the
    queue is full the sender is refused (a move is dropped) instead of waiting."""
    gate = threading.Event()

    def stuck(data: bytes) -> None:
        gate.wait()

    real = CommandWriter(stuck, depth=8)
    sink = ScrcpyInputSink(lambda: real)
    began = time.monotonic()
    sink.touch("down", 1, 1, SIZE)
    for x in range(100):
        sink.touch("move", x, x, SIZE)  # moves of one gesture: replaced, never queued behind one another
    with pytest.raises(InputError, match="busy"):
        for _ in range(100):
            sink.text("x")  # anything that must not be lost is refused once the queue is full
    assert time.monotonic() - began < 1.0
    assert real.stats()["queued"] == 8 and real.stats()["coalesced"] > 0
    gate.set()
    real.close(2)
