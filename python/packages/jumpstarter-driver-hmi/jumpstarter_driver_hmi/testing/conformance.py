"""The conformance suite: what "implements the HMI interface" means, as tests an implementation runs against its own
fakes.

An implementation subclasses :class:`HmiConformance` in its own test module (the class name must start with ``Test``
to be collected) and says how to build a driver around a fake of its device::

    class TestMyHmi(HmiConformance):
        reports = ("touch", "keyboard", "key", "text")   # what input_events() has, see below

        def make_driver(self):
            return MyHmi(device=FakeDevice(), ...)

        def input_events(self, driver):          # optional: what the fake device received
            return driver._source.device.events

        def change_picture(self, driver):        # optional: make the fake's picture change size
            ...

The suite runs the driver through ``serve()`` (the real client, framing and hub in the loop) and **checks what the HMI
declares in ``hmi_info()``, never assuming**: a test of a pointer, a keyboard, hard keys, text, a wheel, buttons,
relative motion, a rotary, a codec or quality is skipped when the HMI does not declare it, and an HMI that declares no
input at all is checked to be a valid, view-only one. The same suite runs for H.264, HEVC and MJPEG: what a key
picture, the parameter sets and the first unit look like is read from the codec the HMI is in. It checks:

* ``hmi_info()`` is well-formed (the ``input`` schema, the ``codecs`` and the ``codec`` in use, ``recording``) and
  agrees with what the HMI does;
* the codec line comes first, with its required fields and the codec's own name, and again when it changes;
* the stream starts at a key picture with its parameter sets, whatever a key picture costs the source (``cheap``,
  ``restart`` or ``none``, as the exporter acts on it once the stream runs), and timestamps do not go backwards;
* a second viewer joins at once, and a slow one is shed without slowing another;
* ``resync`` gives a clean start again, and a ``lag`` line is accepted;
* a pointer of every declared kind presses, moves and lifts; a wheel, buttons and relative motion are taken when
  declared and refused (or ignored) when not; contacts beyond ``max_contacts`` and kinds that are not declared are
  ignored; a pointer aimed at another size is refused; what is down when the input stream ends is lifted;
* a keyboard key goes down and up, a repeat is dropped, a key that is not declared is refused, keys still down are
  released (in the opposite order) at the end; text is typed only if declared; hard keys are taken, others refused,
  and a press a hard key does not declare is refused with an error line while the key still takes the presses it does;
* every declared rotary turns one detent each way (the device sees the turns in order), a rotary that is not declared
  and a turn of 0 or beyond the limit are refused, and the push button of a rotary is a key the HMI takes;
* every declared codec can be started with ``start(codec=...)``, the codec line names it, and switching with a viewer
  attached sends it the new line and a key picture; H.264 and HEVC record to an MP4 and MJPEG refuses and says so;
* ``stop`` leaves nothing running, and the stream can start again.

**What the device received.** ``input_events(driver)`` returns what the fake device was given, in order, as tuples in
the shape of :class:`~jumpstarter_driver_hmi.testing.fake.FakeInputSink`: ``("touch", type, x, y, size)`` for finger 0,
``("pointer", type, kind, x, y, id, buttons, dx, dy, size)`` for any other pointer event, ``("keyboard", code, down)``,
``("key", name, press)``, ``("rotary", id, delta)``, ``("text", text)``, and ``("release",)`` when the input ended; or
``None`` if the fake cannot say. Which of these the fake reports is :attr:`HmiConformance.reports` (by the first
element of the tuple; a fake that reports only what it was given as touches says ``("touch",)``, the default): what
reached the device is checked only for those, and what the exporter answered with (the ``{"error": ...}`` lines) is
checked for everything. A ``move`` may be replaced by a later one when a device is behind, so the suite never counts
moves.
"""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Generator, Iterator
from contextlib import contextmanager
from typing import Any

import pytest

from ..client import HmiClient, HmiStreamClosedError, InputStream, VideoStream, declares_input
from ..common import (
    CODECS,
    DEFAULT_PRESSES,
    KEY_CODE,
    MAX_ROTARY_DELTA,
    POINTER_KINDS,
    PRESSES,
    ROTARY_ID,
    canonical_key,
    codec_family,
)
from ..driver import HmiDriver
from ..hub.codecs import scan_unit
from jumpstarter.client.core import DriverError
from jumpstarter.common.utils import serve

TIMEOUT_S = 15.0
#: Every kind of event a fake can report.
ALL_EVENTS = ("touch", "pointer", "keyboard", "key", "rotary", "text")
#: A key no HMI has: the exporter answers a line that names it with an error, in the order it took the lines, so the
#: answer to this one says everything before it has been taken.
_BARRIER_KEY = "BARRIER_OF_THE_CONFORMANCE_SUITE"


def wait_for(condition: Callable[[], Any], timeout: float = TIMEOUT_S, what: str = "the condition") -> Any:
    """Poll ``condition`` until it is truthy (and return it); fail after ``timeout`` seconds."""
    deadline = time.monotonic() + timeout
    while True:
        value = condition()
        if value:
            return value
        if time.monotonic() > deadline:
            pytest.fail(f"timed out waiting for {what}")  # ty: ignore[invalid-argument-type]
        time.sleep(0.01)


def media(video: VideoStream, count: int = 1, timeout: float = TIMEOUT_S) -> list[bytes]:
    """The next ``count`` media frames (codec lines in between are skipped)."""
    frames: list[bytes] = []
    deadline = time.monotonic() + timeout
    while len(frames) < count:
        frame = video.read_frame(max(deadline - time.monotonic(), 0.01))
        assert frame is not None, "the stream ended"
        if isinstance(frame, bytes):
            frames.append(frame)
    return frames


def read_until(video: VideoStream, wanted: Callable[[str | bytes], bool], timeout: float = TIMEOUT_S) -> str | bytes:
    """Frames of the stream until one is ``wanted``: that frame."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        frame = video.read_frame(max(deadline - time.monotonic(), 0.01))
        assert frame is not None, "the stream ended"
        if wanted(frame):
            return frame
    pytest.fail("timed out waiting for a frame")  # ty: ignore[invalid-argument-type]


def settle(stream: InputStream, timeout: float = TIMEOUT_S) -> list[str]:
    """The errors the exporter answered with to everything sent so far on ``stream`` (it has taken it all by then)."""
    stream.send({"key": {"key": _BARRIER_KEY}})
    errors: list[str] = []
    while True:
        message = stream.read_error(timeout)
        assert message is not None, "the input stream ended"
        if message.endswith(_BARRIER_KEY):
            return errors
        errors.append(message)


def is_start(unit: bytes, family: str) -> bool:
    """Whether ``unit`` is a picture a decoder can start at, with what it needs in front: a JPEG, or an IDR/IRAP
    picture that has its parameter sets (SPS and PPS; VPS too for HEVC) before its first slice.
    """
    if family == "mjpeg":
        return unit[:2] == b"\xff\xd8" and unit[-2:] == b"\xff\xd9"
    scan = scan_unit(unit, family)
    return bool(scan.idr and scan.sps and scan.pps and (scan.vps or family != "hevc"))


class HmiConformance:
    """Subclass, name the subclass ``Test...`` and implement :meth:`make_driver`."""

    #: The kinds of event ``input_events`` has (the first element of its tuples, see the module documentation).
    reports: tuple[str, ...] = ("touch",)
    #: How long a device may take to show what the exporter sent it: what must **not** have reached it is checked after
    #: this long.
    delivery_s: float = 0.3

    def make_driver(self) -> HmiDriver:
        """A driver around a fake of the device (no real device, no network)."""
        raise NotImplementedError

    def input_events(self, driver: HmiDriver) -> list[tuple] | None:
        """What the fake device received, in order, in the shape of the module documentation; ``None`` if the fake
        cannot say (what reached the device is then not checked).
        """
        return None

    def change_picture(self, driver: HmiDriver) -> bool:
        """Make the fake's picture change size; ``False`` if it cannot (the test is skipped)."""
        return False

    # -- fixtures ------------------------------------------------------------------------------------------------------

    @pytest.fixture
    def driver(self) -> Iterator[HmiDriver]:
        """The driver under test, closed at the end."""
        driver = self.make_driver()
        yield driver
        driver.close()

    @pytest.fixture
    def hmi(self, driver: HmiDriver) -> Iterator[HmiClient]:
        """The client of the driver, through the real session."""
        with serve(driver) as client:
            assert isinstance(client, HmiClient)
            yield client

    # -- helpers -------------------------------------------------------------------------------------------------------

    @contextmanager
    def _input(self, hmi: HmiClient) -> Generator[InputStream]:
        """The input stream, or a skip for an HMI that declares no input (it must refuse the stream)."""
        if not declares_input(hmi.hmi_info()):
            with pytest.raises(HmiStreamClosedError), hmi.input():
                pass
            pytest.skip("the HMI declares no input")  # ty: ignore[too-many-positional-arguments]
        with hmi.input() as stream:
            yield stream

    @staticmethod
    def _pointer(hmi: HmiClient) -> dict:
        """What the HMI declares of its pointer, or a skip."""
        pointer = hmi.hmi_info()["input"]["pointer"]
        if not pointer["kinds"]:
            pytest.skip("the HMI declares no pointer")  # ty: ignore[too-many-positional-arguments]
        return pointer

    @staticmethod
    def _keyboard(hmi: HmiClient) -> dict:
        """What the HMI declares of its keyboard, or a skip."""
        keyboard = hmi.hmi_info()["input"]["keyboard"]
        if not (keyboard["codes"] or keyboard["text"]):
            pytest.skip("the HMI declares no keyboard")  # ty: ignore[too-many-positional-arguments]
        return keyboard

    @staticmethod
    def _key_code(keyboard: dict) -> str:
        codes = keyboard["codes"]
        if not codes:
            pytest.skip("the HMI declares no key codes")  # ty: ignore[too-many-positional-arguments]
        return "KeyA" if codes == "all" or "KeyA" in codes else codes[0]

    @staticmethod
    def _rotaries(hmi: HmiClient) -> list[dict]:
        """What the HMI declares of its rotaries, or a skip."""
        rotaries = hmi.hmi_info()["input"]["rotaries"]
        if not rotaries:
            pytest.skip("the HMI declares no rotary")  # ty: ignore[too-many-positional-arguments]
        return rotaries

    @staticmethod
    def _presses_of(declared: dict, key: str) -> list[str]:
        """The presses the HMI says ``key`` takes: its ``hard_keys`` entry's, else the default ones."""
        for hard in declared["hard_keys"]:
            if canonical_key(hard["key"]) == key:
                return list(hard["presses"])
        return list(DEFAULT_PRESSES)

    @staticmethod
    def _shown(hmi: HmiClient) -> tuple[int, int]:
        """The size of the picture the viewer is shown."""
        info = hmi.hmi_info()
        return info["width"], info["height"]

    @staticmethod
    def _at(size: tuple[int, int], fx: float, fy: float) -> tuple[int, int]:
        return int(size[0] * fx), int(size[1] * fy)

    def _got(self, driver: HmiDriver) -> list[tuple] | None:
        """What the device was given, in a common shape and only of the kinds the fake reports: ``("pointer", type,
        kind, id)``, ``("keyboard", code, down)``, ``("key", name, press)``, ``("rotary", id, delta)``,
        ``("text", text)`` and ``("release",)``.
        """
        raw = self.input_events(driver)
        if raw is None:
            return None
        got: list[tuple] = []
        for event in raw:
            if event[0] not in (*self.reports, "release"):
                continue
            if event[0] == "touch":
                got.append(("pointer", event[1], "touch", 0))
            elif event[0] == "pointer":
                got.append(("pointer", event[1], event[2], event[5]))
            else:
                got.append(event)
        return got

    def _reports(self, driver: HmiDriver, what: str, kind: str | None = None) -> bool:
        """Whether the fake reports ``what`` (``pointer``, ``keyboard``, ``key``, ``rotary``, ``text``) of a ``kind`` of
        pointer.
        """
        if self.input_events(driver) is None:
            return False
        if what == "pointer":
            return "pointer" in self.reports or (kind == "touch" and "touch" in self.reports)
        return what in self.reports

    def _presses(self, driver: HmiDriver, kind: str, *types: str) -> list[str]:
        """The types among ``types`` (``down``, ``up``, ``wheel``) of the pointer events of ``kind`` the device got."""
        return [e[1] for e in self._got(driver) or [] if e[0] == "pointer" and e[2] == kind and e[1] in types]

    def _keys(self, driver: HmiDriver, code: str) -> list[bool]:
        return [e[2] for e in self._got(driver) or [] if e[0] == "keyboard" and e[1] == code]

    def _nothing_more(self, condition: Callable[[], bool], what: str) -> None:
        """``condition`` holds now and still after the device has had time to show anything that was in flight."""
        assert condition(), what
        time.sleep(self.delivery_s)
        assert condition(), what

    # -- the stream ----------------------------------------------------------------------------------------------------

    def test_codec_line_comes_first(self, hmi: HmiClient) -> None:
        """The first frame is the codec line, with its required fields and the name of the codec in use."""
        codec = hmi.hmi_info()["codec"]
        with hmi.video() as video:
            header = video.header
            assert codec_family(header["codec"]) == codec
            assert header["format"] == ("jpeg" if codec == "mjpeg" else "annexb")
            assert header["timestamp"] == "us"
            assert header["v"] == 1
            assert header["width"] > 0
            assert header["height"] > 0
            assert isinstance(header["fps"], int)
            assert isinstance(header["viewer"], int)

    def test_starts_at_a_key_picture_with_parameter_sets(self, hmi: HmiClient) -> None:
        """The first picture a viewer gets is a key picture a decoder can start at: an IDR (IRAP) with its parameter
        sets in front, or a JPEG.
        """
        with hmi.video() as video:
            first = media(video)[0]
            assert is_start(first[8:], codec_family(video.header["codec"]))

    def test_a_stream_starts_at_a_key_picture_whatever_a_key_picture_costs(
        self, hmi: HmiClient, driver: HmiDriver
    ) -> None:
        """What a key picture costs (``cheap``, ``restart`` or ``none``) may be said only once the stream has started:
        the exporter goes by what the source says then, and the first viewer and one that joins later start at a key
        picture.
        """
        with hmi.video() as first:
            family = codec_family(first.header["codec"])
            assert is_start(media(first)[0][8:], family)
            cost = hmi.status()["stream"]["keyframe_cost"]
            assert cost in ("cheap", "restart", "none")
            assert driver._the_source().capabilities().keyframe_cost == cost, (
                "the exporter goes by what the source says"
            )
            media(first, 5)
            with hmi.video() as second:
                assert is_start(media(second)[0][8:], family), f"a joiner at keyframe_cost {cost}"

    def test_timestamps_do_not_go_backwards(self, hmi: HmiClient) -> None:
        """Microsecond timestamps increase within a stream."""
        with hmi.video() as video:
            stamps = [int.from_bytes(f[:8], "big") for f in media(video, 20)]
            assert stamps == sorted(stamps)

    def test_a_second_viewer_starts_at_once_with_a_key_picture(self, hmi: HmiClient) -> None:
        """A joiner is served from the cache (or the next key picture) and starts decodable."""
        with hmi.video() as first:
            media(first, 5)
            with hmi.video() as second:
                picture = media(second)[0]
                assert is_start(picture[8:], codec_family(second.header["codec"]))

    def test_a_slow_viewer_does_not_slow_another(self, hmi: HmiClient) -> None:
        """A viewer that never reads is shed on its own; the other one keeps receiving."""
        with hmi.video() as slow, hmi.video() as fast:
            del slow  # never read
            started = time.monotonic()
            media(fast, 60)
            assert time.monotonic() - started < TIMEOUT_S

    def test_resync_gives_a_clean_start(self, hmi: HmiClient) -> None:
        """After ``resync`` the exporter starts this viewer again (from its cache or the next key picture), and a key
        picture reaches it.
        """
        with hmi.video() as video:
            media(video, 5)
            seeded = self._seeded(hmi)
            video.request_resync()
            wait_for(lambda: self._seeded(hmi) > seeded, what="the exporter to start the viewer again")
            family = codec_family(video.header["codec"])
            deadline = time.monotonic() + TIMEOUT_S
            while time.monotonic() < deadline:
                if is_start(media(video)[0][8:], family):
                    return
            pytest.fail("no key picture after resync")  # ty: ignore[invalid-argument-type]

    @staticmethod
    def _seeded(hmi: HmiClient) -> int:
        """How many times the exporter has given a viewer a clean start (a join or a resync)."""
        joins = hmi.status()["stream"]["joins"]
        return joins["replayed"] + joins["waited"]

    def test_a_lag_line_is_accepted(self, hmi: HmiClient) -> None:
        """The back-channel's ``lag`` line does not disturb the stream."""
        with hmi.video() as video:
            media(video, 3)
            video._send_lag(0.05)
            video._send_lag(0.0)
            assert len(media(video, 5)) == 5

    def test_the_codec_line_is_sent_again_when_the_picture_changes(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """A change of size reaches the viewer as a new codec line, followed by a key picture."""
        with hmi.video() as video:
            media(video, 3)
            if not self.change_picture(driver):
                pytest.skip("the fake cannot change its picture")  # ty: ignore[too-many-positional-arguments]
            before = (video.header["width"], video.header["height"])
            deadline = time.monotonic() + TIMEOUT_S
            while time.monotonic() < deadline:
                frame = video.read_frame(5.0)
                assert frame is not None
                if isinstance(frame, str):
                    assert (video.header["width"], video.header["height"]) != before
                    assert is_start(media(video)[0][8:], codec_family(video.header["codec"]))
                    return
            pytest.fail("no new codec line")  # ty: ignore[invalid-argument-type]

    # -- what it says about itself -------------------------------------------------------------------------------------

    def test_hmi_info_is_well_formed(self, hmi: HmiClient) -> None:
        """``hmi_info()`` has what a UI reads, in the shape the interface says."""
        info = hmi.hmi_info()
        assert info["interface"] == "hmi"
        assert info["v"] == 1
        assert info["codecs"] and set(info["codecs"]) <= set(CODECS) and len(set(info["codecs"])) == len(info["codecs"])
        assert info["codec"] in info["codecs"]
        assert info["recording"] is (info["codec"] != "mjpeg")
        for name in ("width", "height"):
            assert info[name] is None or (isinstance(info[name], int) and info[name] > 0)
        assert info["rotation"] in (0, 90, 180, 270)
        declared = info["input"]
        assert set(declared) >= {"pointer", "keyboard", "keys", "hard_keys", "rotaries", "clipboard"}
        assert isinstance(declared["clipboard"], bool)

        pointer = declared["pointer"]
        assert set(pointer["kinds"]) <= set(POINTER_KINDS) and len(set(pointer["kinds"])) == len(pointer["kinds"])
        assert all(isinstance(pointer[name], bool) for name in ("absolute", "relative", "buttons", "wheel"))
        assert isinstance(pointer["max_contacts"], int) and not isinstance(pointer["max_contacts"], bool)
        if pointer["kinds"]:
            assert pointer["max_contacts"] >= 1
            assert pointer["absolute"] or pointer["relative"]
        else:  # no pointer says nothing else about one
            assert pointer["max_contacts"] == 0
            assert not (pointer["absolute"] or pointer["relative"] or pointer["buttons"] or pointer["wheel"])

        keyboard = declared["keyboard"]
        assert keyboard["codes"] == "all" or (
            isinstance(keyboard["codes"], list) and all(KEY_CODE.fullmatch(code) for code in keyboard["codes"])
        )
        assert isinstance(keyboard["text"], bool)
        if keyboard["codes"] or keyboard["text"]:
            assert isinstance(keyboard["layout"], str) and keyboard["layout"]
        else:
            assert keyboard["layout"] is None

        keys = [canonical_key(k) for k in declared["keys"]]
        assert keys == declared["keys"], "keys are named as the interface compares them"
        for hard in declared["hard_keys"]:
            assert canonical_key(hard["key"]) in keys
            assert isinstance(hard["label"], str) and hard["label"]
            assert hard["presses"] and set(hard["presses"]) <= set(PRESSES)

        rotaries = declared["rotaries"]
        assert isinstance(rotaries, list)
        assert len({rotary["id"] for rotary in rotaries}) == len(rotaries), "a rotary id is used once"
        for rotary in rotaries:
            assert set(rotary) == {"id", "label", "push"}
            assert isinstance(rotary["id"], str) and ROTARY_ID.fullmatch(rotary["id"])
            assert isinstance(rotary["label"], str) and rotary["label"]
            assert rotary["push"] is None or rotary["push"] in keys, (
                "the push button of a rotary is a key the HMI takes"
            )
        if not declares_input(info):
            assert rotaries == [], "a view-only HMI declares no rotary"

        quality = info["quality"]
        assert isinstance(quality["adjustable"], bool) and isinstance(quality["auto"], bool)
        assert (quality["levels"] != []) == quality["adjustable"]

    def test_hmi_info_agrees_with_what_the_hmi_does(self, hmi: HmiClient) -> None:
        """Quality is adjustable if and only if ``set_quality`` works."""
        info = hmi.hmi_info()
        if info["quality"]["adjustable"]:
            assert hmi.set_quality("auto")["quality"]["mode"] == "auto"
        else:
            with pytest.raises(ValueError):
                hmi.set_quality("low")

    def test_hmi_info_names_the_codec_the_stream_is_in(self, hmi: HmiClient) -> None:
        """The codec of ``hmi_info()`` is the one the codec line names, before and while the stream runs."""
        before = hmi.hmi_info()["codec"]
        with hmi.video() as video:
            assert codec_family(video.header["codec"]) == before == hmi.hmi_info()["codec"]

    def test_a_view_only_hmi_refuses_input_and_still_shows_the_picture(self, hmi: HmiClient) -> None:
        """An HMI that declares no input is valid: its input stream is refused, and its picture is shown."""
        if declares_input(hmi.hmi_info()):
            pytest.skip("the HMI declares input")  # ty: ignore[too-many-positional-arguments]
        with pytest.raises(HmiStreamClosedError), hmi.input():
            pass
        with hmi.video() as video:
            assert media(video)

    def test_status_has_the_common_head(self, hmi: HmiClient) -> None:
        """``status()`` has the state, the picture, the viewers, the quality and the recording."""
        with hmi.video():
            status = hmi.status()
        assert status["state"] in ("running", "starting", "stopped")
        assert {"picture", "viewers", "quality", "recording", "device"} <= set(status)

    # -- input: the pointer --------------------------------------------------------------------------------------------

    def test_a_pointer_of_every_declared_kind_presses_moves_and_lifts(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """A touch is a finger down, moved and up; a mouse or a pen hovers, presses, drags and lets go."""
        pointer = self._pointer(hmi)
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            size = self._shown(hmi)
            a, b = self._at(size, 0.5, 0.5), self._at(size, 0.25, 0.25)
            for kind in pointer["kinds"]:
                if kind != "touch":  # a hover
                    if pointer["absolute"]:
                        stream.pointer("move", *a, kind=kind)
                    else:
                        stream.pointer("move", kind=kind, dx=2, dy=2)
                stream.pointer("down", *a, kind=kind)
                if pointer["absolute"]:
                    stream.pointer("move", *b, kind=kind)
                else:
                    stream.pointer("move", kind=kind, dx=-2, dy=-2)
                stream.pointer("up", *b, kind=kind)
            assert settle(stream) == []
            for kind in pointer["kinds"]:
                if self._reports(driver, "pointer", kind):
                    wait_for(
                        lambda kind=kind: self._presses(driver, kind, "down", "up") == ["down", "up"],
                        what=f"the {kind} to press and lift",
                    )

    def test_a_second_contact_beyond_max_contacts_is_ignored(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """Contact 0 goes down, then 1, and so on: the one past ``max_contacts`` is ignored, not an error, and reaches
        nobody.
        """
        pointer = self._pointer(hmi)
        kind, limit = pointer["kinds"][0], pointer["max_contacts"]
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            size = self._shown(hmi)
            for contact in range(limit + 1):
                stream.pointer("down", *self._at(size, 0.2 + 0.02 * contact, 0.5), kind=kind, id=contact)
            for contact in range(limit + 1):
                stream.pointer("up", *self._at(size, 0.2 + 0.02 * contact, 0.5), kind=kind, id=contact)
            assert settle(stream) == []
            if self._reports(driver, "pointer", kind):
                wait_for(
                    lambda: self._presses(driver, kind, "down", "up") == ["down"] * limit + ["up"] * limit,
                    what="the contacts the HMI follows",
                )
                self._nothing_more(
                    lambda: self._presses(driver, kind, "down") == ["down"] * limit,
                    "a contact beyond max_contacts reached the device",
                )

    def test_a_pointer_kind_the_hmi_does_not_declare_is_ignored(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """An event of a kind the device has none of is not an error and reaches nobody."""
        pointer = self._pointer(hmi)
        others = [kind for kind in POINTER_KINDS if kind not in pointer["kinds"]]
        if not others:
            pytest.skip("the HMI declares every kind of pointer")  # ty: ignore[too-many-positional-arguments]
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            point = self._at(self._shown(hmi), 0.5, 0.5)
            for kind in others:
                stream.pointer("down", *point, kind=kind)
                stream.pointer("move", *point, kind=kind)
                stream.pointer("up", *point, kind=kind)
            assert settle(stream) == []
            for kind in others:
                if self._reports(driver, "pointer", kind):
                    self._nothing_more(lambda kind=kind: not self._presses(driver, kind, "down", "up"), kind)

    def test_buttons_beyond_the_primary_are_taken_only_if_declared(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """A press of the secondary button alone reaches a device that declares buttons, and is ignored (no error) by
        one that does not.
        """
        pointer = self._pointer(hmi)
        kind = pointer["kinds"][0]
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            point = self._at(self._shown(hmi), 0.5, 0.5)
            stream.pointer("down", *point, kind=kind, buttons=2)
            stream.pointer("up", *point, kind=kind)
            assert settle(stream) == []
            if self._reports(driver, "pointer", kind):
                if pointer["buttons"]:
                    wait_for(lambda: self._presses(driver, kind, "down", "up") == ["down", "up"], what="the press")
                else:
                    self._nothing_more(lambda: not self._presses(driver, kind, "down", "up"), "an undeclared button")

    def test_a_wheel_is_taken_only_if_declared(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """A wheel turns a declared wheel and is answered with an error line by a pointer that has none."""
        pointer = self._pointer(hmi)
        kind = pointer["kinds"][0]
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            stream.pointer("wheel", *self._at(self._shown(hmi), 0.5, 0.5), kind=kind, dy=120)
            errors = settle(stream)
            if pointer["wheel"]:
                assert errors == []
                if self._reports(driver, "pointer", kind):
                    wait_for(lambda: self._presses(driver, kind, "wheel") == ["wheel"], what="the wheel")
            else:
                assert len(errors) == 1 and "wheel" in errors[0]
                if self._reports(driver, "pointer", kind):
                    self._nothing_more(lambda: not self._presses(driver, kind, "wheel"), "a wheel the HMI has not")

    def test_relative_motion_is_taken_only_if_declared(self, hmi: HmiClient) -> None:
        """Motion by a distance (a ``move`` with no position) is taken by a pointer that declares it and answered with
        an error line by one that does not; so is a position, for a pointer that is relative only.
        """
        pointer = self._pointer(hmi)
        kind = pointer["kinds"][0]
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            stream.pointer("move", kind=kind, dx=5, dy=-5)
            assert (settle(stream) == []) == pointer["relative"]
            stream.pointer("move", *self._at(self._shown(hmi), 0.5, 0.5), kind=kind)
            assert (settle(stream) == []) == pointer["absolute"]

    def test_a_pointer_aimed_at_another_size_is_refused(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """An event whose ``w``/``h`` is not the shown picture's is answered with an error line and not applied."""
        pointer = self._pointer(hmi)
        kind = pointer["kinds"][0]
        with hmi.video() as video, self._input(hmi) as stream:
            width, height = video.header["width"], video.header["height"]
            stream.pointer("down", 1, 1, kind=kind, size=(width + 7, height + 7))
            errors = settle(stream)
            assert len(errors) == 1 and "picture" in errors[0]
            if self._reports(driver, "pointer", kind):
                self._nothing_more(lambda: not self._presses(driver, kind, "down"), "an event aimed elsewhere")

    def test_a_pointer_down_when_the_input_ends_is_lifted(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """Closing the input stream with a contact down lifts it (a finger, a button)."""
        pointer = self._pointer(hmi)
        kind = pointer["kinds"][0]
        if not self._reports(driver, "pointer", kind):
            pytest.skip("the fake cannot say what it received")  # ty: ignore[too-many-positional-arguments]
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            stream.pointer("down", *self._at(self._shown(hmi), 0.5, 0.5), kind=kind)
            wait_for(lambda: self._presses(driver, kind, "down") == ["down"], what="the press")
        wait_for(lambda: self._presses(driver, kind, "down", "up") == ["down", "up"], what="the contact to be lifted")

    # -- input: the keyboard, text and hard keys -----------------------------------------------------------------------

    def test_a_key_goes_down_and_up_and_a_repeat_is_dropped(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """A key code goes to the device down, then up, in order; a ``down`` of a key that is down is a repeat; an
        ``up`` of a key that is not down is dropped.
        """
        code = self._key_code(self._keyboard(hmi))
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            stream.keyboard(code, False)  # not down: dropped
            for _ in range(3):
                stream.keyboard(code, True)  # the device repeats by itself
            stream.keyboard(code, False)
            stream.keyboard(code, False)
            assert settle(stream) == []
            if self._reports(driver, "keyboard"):
                wait_for(lambda: self._keys(driver, code) == [True, False], what="the key to go down and up")
                self._nothing_more(lambda: self._keys(driver, code) == [True, False], "a repeat reached the device")

    def test_keys_go_down_in_order_and_are_released_in_the_opposite_order(
        self, hmi: HmiClient, driver: HmiDriver
    ) -> None:
        """Two keys pressed one after the other (a modifier, then a key) go down in that order and, left down when the
        input ends, are released in the opposite order: the modifier goes up after the key it modified.
        """
        codes = self._keyboard(hmi)["codes"]
        if codes == "all":
            first, second = "ShiftLeft", "KeyA"
        elif len(codes) >= 2:
            first, second = codes[0], codes[1]
        else:
            pytest.skip("the HMI takes fewer than two key codes")  # ty: ignore[too-many-positional-arguments]
        if not self._reports(driver, "keyboard"):
            pytest.skip("the fake cannot say what it received")  # ty: ignore[too-many-positional-arguments]
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            stream.keyboard(first, True)
            stream.keyboard(second, True)
            assert settle(stream) == []
            wait_for(lambda: len(self._keys(driver, first) + self._keys(driver, second)) == 2, what="both keys")
            assert [e[1:] for e in self._got(driver) or [] if e[0] == "keyboard"] == [(first, True), (second, True)]
        wait_for(
            lambda: len(self._keys(driver, first) + self._keys(driver, second)) == 4, what="both keys to be let go"
        )
        released = [e[1] for e in self._got(driver) or [] if e[0] == "keyboard" and e[2] is False]
        assert released == [second, first]

    def test_a_key_the_hmi_does_not_declare_is_refused(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """A key code that is not in the keyboard's list is answered with an error line and reaches nobody; so is every
        key code, for an HMI that declares none.
        """
        codes = hmi.hmi_info()["input"]["keyboard"]["codes"]
        if codes == "all":
            pytest.skip("the keyboard takes every key")  # ty: ignore[too-many-positional-arguments]
        undeclared = next(code for code in ("KeyQ", "IntlRo", "F24", "Lang5", "BrowserBack") if code not in codes)
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            stream.keyboard(undeclared, True)
            stream.keyboard(undeclared, False)
            errors = settle(stream)
            assert len(errors) == 2 and all(errors)
            if self._reports(driver, "keyboard"):
                self._nothing_more(lambda: not self._keys(driver, undeclared), "an undeclared key reached the device")

    def test_a_malformed_key_code_is_an_error(self, hmi: HmiClient) -> None:
        """What is not a ``KeyboardEvent.code`` is answered with an error line, whatever the keyboard."""
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            stream.send({"keyboard": {"code": "not a code", "type": "down"}})
            stream.send({"keyboard": {"code": "KeyA", "type": "hold"}})
            assert len(settle(stream)) == 2

    def test_keys_left_down_are_released_when_the_input_ends(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """Closing the input stream with a key down releases it."""
        code = self._key_code(self._keyboard(hmi))
        if not self._reports(driver, "keyboard"):
            pytest.skip("the fake cannot say what it received")  # ty: ignore[too-many-positional-arguments]
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            stream.keyboard(code, True)
            wait_for(lambda: self._keys(driver, code) == [True], what="the key to go down")
        wait_for(lambda: self._keys(driver, code) == [True, False], what="the key to be released")

    def test_text_is_typed_only_if_declared(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """Text reaches a device that types it and is answered with an error line by one that does not."""
        text = hmi.hmi_info()["input"]["keyboard"]["text"]
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            stream.send({"text": "hello"})
            errors = settle(stream)
            if text:
                assert errors == []
                if self._reports(driver, "text"):
                    wait_for(lambda: ("text", "hello") in (self._got(driver) or []), what="the text")
            else:
                assert len(errors) == 1
                if self._reports(driver, "text"):
                    self._nothing_more(
                        lambda: ("text", "hello") not in (self._got(driver) or []), "text it cannot type"
                    )

    def test_hard_keys_are_taken_and_other_keys_refused(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """Every hard key the HMI declares can be pressed; a key it does not have is answered with an error line."""
        declared = hmi.hmi_info()["input"]
        hard_keys = declared["hard_keys"]
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            stream.send({"key": {"key": "NO_SUCH_KEY_AT_ALL"}})
            assert len(settle(stream)) == 1
            for hard in hard_keys:
                stream.key(hard["key"], hard["presses"][0])
            assert settle(stream) == []
            if hard_keys and self._reports(driver, "key"):
                wait_for(
                    lambda: [e[1] for e in self._got(driver) or [] if e[0] == "key"] == [h["key"] for h in hard_keys],
                    what="the hard keys",
                )
            if not declared["keys"]:
                stream.send({"key": {"key": "HOME"}})
                assert len(settle(stream)) == 1

    def test_a_press_a_hard_key_does_not_declare_is_refused_and_the_key_still_works(
        self, hmi: HmiClient, driver: HmiDriver
    ) -> None:
        """A hard key takes the presses it declares (``short`` and ``long`` unless it says): any other press is
        answered with an error line and never reaches the device, and the key still takes a press it declares.
        """
        declared = hmi.hmi_info()["input"]
        names = dict.fromkeys([*(hard["key"] for hard in declared["hard_keys"]), *declared["keys"]])  # drawn ones first
        candidates = [
            (key, press, presses)
            for key in names
            if (presses := self._presses_of(declared, key))
            and (press := next((p for p in PRESSES if p not in presses), None))
        ]
        if not candidates:
            pytest.skip("the HMI declares no hard key that refuses a press")  # ty: ignore[too-many-positional-arguments]
        key, refused, presses = candidates[0]
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            stream.key(key, refused)
            errors = settle(stream)
            assert len(errors) == 1 and key in errors[0] and refused in errors[0], errors
            if self._reports(driver, "key"):
                self._nothing_more(
                    lambda: ("key", key, refused) not in (self._got(driver) or []), "a press the key does not take"
                )
            stream.key(key, presses[0])
            assert settle(stream) == []
            if self._reports(driver, "key"):
                wait_for(lambda: ("key", key, presses[0]) in (self._got(driver) or []), what="the press it takes")

    # -- input: rotaries -----------------------------------------------------------------------------------------------

    def test_a_declared_rotary_turns_one_detent_each_way(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """Each rotary the HMI declares takes a turn of +1 and one of -1, and the device sees them in that order."""
        rotaries = self._rotaries(hmi)
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            for rotary in rotaries:
                stream.rotate(rotary["id"], 1)
                stream.rotate(rotary["id"], -1)
            assert settle(stream) == []
            if self._reports(driver, "rotary"):
                wanted = [("rotary", r["id"], d) for r in rotaries for d in (1, -1)]
                wait_for(
                    lambda: [e for e in self._got(driver) or [] if e[0] == "rotary"] == wanted,
                    what="the turns in order",
                )

    def test_a_rotary_that_is_not_declared_is_refused(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """A turn of a rotary the HMI does not have (all of them, when it declares none) is answered with an error line
        and reaches nobody.
        """
        declared = {rotary["id"] for rotary in hmi.hmi_info()["input"]["rotaries"]}
        undeclared = next(name for name in ("no-such-knob", "no-such-knob-either") if name not in declared)
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            stream.send({"rotary": {"id": undeclared, "delta": 1}})
            errors = settle(stream)
            assert len(errors) == 1 and undeclared in errors[0], errors
            if self._reports(driver, "rotary"):
                self._nothing_more(
                    lambda: not [e for e in self._got(driver) or [] if e[0] == "rotary"], "an undeclared rotary"
                )

    def test_a_rotary_turn_of_nothing_or_beyond_the_limit_is_refused(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """A ``delta`` of 0, or of more than the limit either way, is answered with an error line and reaches
        nobody; so is one that is not a whole number.
        """
        rotary = self._rotaries(hmi)[0]["id"]
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            deltas = [0, MAX_ROTARY_DELTA + 1, -MAX_ROTARY_DELTA - 1, 1.5, "1", None]
            for delta in deltas:
                stream.send({"rotary": {"id": rotary, "delta": delta}})
            assert len(settle(stream)) == len(deltas)
            if self._reports(driver, "rotary"):
                self._nothing_more(
                    lambda: not [e for e in self._got(driver) or [] if e[0] == "rotary"], "a turn that is not one"
                )

    def test_the_push_button_of_a_rotary_is_a_key_the_hmi_takes(self, hmi: HmiClient, driver: HmiDriver) -> None:
        """A rotary that declares a ``push`` names a hard key the HMI has, and pushing it is that key pressed."""
        declared = hmi.hmi_info()["input"]
        pushes = [rotary for rotary in self._rotaries(hmi) if rotary["push"]]
        if not pushes:
            pytest.skip("no rotary declares a push button")  # ty: ignore[too-many-positional-arguments]
        with hmi.video() as video, self._input(hmi) as stream:
            media(video)
            wanted = []
            for rotary in pushes:
                assert rotary["push"] in declared["keys"]
                press = self._presses_of(declared, rotary["push"])[0]
                stream.push(rotary["id"], press)
                wanted.append(("key", rotary["push"], press))
            assert settle(stream) == []
            if self._reports(driver, "key"):
                wait_for(
                    lambda: [e for e in self._got(driver) or [] if e[0] == "key" and e in wanted] == wanted,
                    what="the pushes",
                )

    # -- codecs --------------------------------------------------------------------------------------------------------

    def test_every_declared_codec_can_be_started_and_the_line_names_it(self, hmi: HmiClient) -> None:
        """``start(codec=...)`` for each codec the HMI declares gives a stream in that codec: the codec line names it,
        the first picture is a key picture of it, and ``hmi_info()`` agrees.
        """
        for codec in hmi.hmi_info()["codecs"]:
            hmi.start(codec=codec)
            with hmi.video() as video:
                assert codec_family(video.header["codec"]) == codec
                assert video.header["format"] == ("jpeg" if codec == "mjpeg" else "annexb")
                assert is_start(media(video)[0][8:], codec)
                info = hmi.hmi_info()
                assert (info["codec"], info["recording"]) == (codec, codec != "mjpeg")

    def test_a_codec_the_hmi_does_not_declare_is_refused(self, hmi: HmiClient) -> None:
        """``start`` with a codec that is not in ``hmi_info()["codecs"]`` (or not a codec) is a ``ValueError``."""
        declared = hmi.hmi_info()["codecs"]
        for codec in [c for c in CODECS if c not in declared] + ["vp9"]:
            with pytest.raises(ValueError, match="produces"):
                hmi.start(codec=codec)

    def test_switching_the_codec_sends_a_viewer_the_new_codec_line_and_a_key_picture(self, hmi: HmiClient) -> None:
        """A viewer that is attached when the stream restarts in another codec is sent that codec's line, and starts at
        a key picture of it.
        """
        codecs = hmi.hmi_info()["codecs"]
        if len(codecs) < 2:
            pytest.skip("the HMI declares one codec")  # ty: ignore[too-many-positional-arguments]
        current = hmi.hmi_info()["codec"]
        with hmi.video() as video:
            media(video)
            for codec in [*(c for c in codecs if c != current), current]:  # each a change, and back again
                hmi.start(codec=codec)
                read_until(
                    video, lambda f, codec=codec: isinstance(f, str) and codec_family(json.loads(f)["codec"]) == codec
                )
                assert is_start(media(video)[0][8:], codec)

    def test_a_recording_is_an_mp4_or_is_refused_with_the_reason(self, hmi: HmiClient) -> None:
        """H.264 and HEVC streams record to an MP4 with the boxes of one; an MJPEG stream is refused, and
        ``hmi_info()`` says it is not recorded.
        """
        from ..recording.mp4_testing import parse_boxes

        for codec in hmi.hmi_info()["codecs"]:
            hmi.start(codec=codec)
            if codec == "mjpeg":
                assert hmi.hmi_info()["recording"] is False
                with pytest.raises(DriverError, match="MJPEG"):
                    hmi.start_recording(max_seconds=30)
                assert hmi.recording_status()["active"] is False
                continue
            assert hmi.hmi_info()["recording"] is True
            assert hmi.start_recording(max_seconds=30)["active"]
            wait_for(lambda: hmi.recording_status()["frames"] >= 20, what="recorded frames")
            result = hmi.stop_recording()
            assert result["delivered"]
            kinds = [box.kind for box in parse_boxes(result["data"])]
            assert kinds[0] == "ftyp"
            assert "moov" in kinds
            assert "moof" in kinds
            assert result["width"] > 0
            assert result["height"] > 0
            assert (b"hev1" if codec == "hevc" else b"avc1") in result["data"]

    # -- stop ----------------------------------------------------------------------------------------------------------

    def test_stop_lets_go_and_the_stream_can_start_again(self, hmi: HmiClient) -> None:
        """``stop`` ends the viewers' streams and the capture; a new viewer starts it again."""
        with hmi.video() as video:
            media(video, 3)
            began = time.monotonic()
            assert hmi.stop()["state"] == "stopped"
            assert time.monotonic() - began < 30

            def ended() -> bool:
                try:
                    return video.read_frame(1.0) is None
                except TimeoutError:
                    return False

            wait_for(ended, what="the end of the stream")
        with hmi.video() as again:
            assert media(again, 3)
