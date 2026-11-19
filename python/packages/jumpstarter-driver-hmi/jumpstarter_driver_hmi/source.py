"""What a device gives the HMI interface: a :class:`HmiSource`.

A source captures and encodes **on the device or next to it** and hands the exporter one stream in one codec (H.264
unless
it says and is asked otherwise; HEVC and MJPEG are optional); the exporter, in
:class:`~jumpstarter_driver_hmi.driver.HmiDriver`, does everything else: the group-of-pictures cache, the fan-out to
viewers, the adaptation to the network, recording, the framing and the input lines. A source therefore knows nothing
about viewers, queues, framing or JSON lines. It has four jobs:

* **Produce.** :meth:`HmiSource.start` begins capturing and calls ``sink.config(line)`` (before the first picture, and
  again whenever the picture's parameters change) and ``sink.media(ts_us, access_unit)`` (a key picture with its
  parameter sets in front). It returns once the capture is connected; the first codec line may arrive a little later.
* **Stop, in bounded time.** :meth:`HmiSource.stop` takes a deadline and never blocks past it (escalating, for a
  process, from a polite request to SIGTERM to SIGKILL).
* **Take input without blocking.** :meth:`HmiSource.input_sink` returns an :class:`InputSink` whose methods only
  enqueue; a device that stops reading must never stall the exporter (:mod:`jumpstarter_driver_hmi.proc.writer` is the
  queue to use). A source takes **the subset it genuinely supports** and declares it in
  :meth:`HmiSource.capabilities`; the exporter never sends it anything else.
* **Say what it is.** :meth:`HmiSource.capabilities` is the honest answer to ``hmi_info()``: the codecs, the pointer,
  the keyboard, the hard keys. A source that declares no input is a valid, view-only HMI.

Everything here runs on worker threads, never on the event loop.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Protocol, cast

from .common import DEFAULT_CODEC, Capabilities, CodecLine, InputError, PointerEvent
from .hub.quality import Rung


class HubSink(Protocol):
    """What the driver gives a source to publish into (thread-safe, never blocks)."""

    def config(self, line: CodecLine) -> None:
        """Say what the stream is now; viewers get a new codec line only when it changed."""

    def media(self, ts_us: int, access_unit: bytes, *, idr: bool | None = None, reference: bool | None = None) -> None:
        """One access unit. ``idr`` and ``reference`` are found from the NAL units when left out."""

    def discontinuity(self) -> None:
        """Pictures were lost: no viewer may be given another until a key picture."""

    def ended(self, error: str | None = None) -> None:
        """The capture ended by itself (``error``: why, or ``None`` for a clean end); the driver ends the viewers'
        streams.
        """


class InputSink:
    """Input into the device. Every method only enqueues work and **never blocks**; one that cannot take an event raises
    ``InputError``.

    The exporter calls only what the source declared in its :class:`~jumpstarter_driver_hmi.common.Capabilities`, and
    has
    already checked it: coordinates are pixels of the shown picture, clamped to it; a contact is down before it moves; a
    key is not down twice; buttons are masked to the declared ones. A source overrides the methods it declares and
    leaves the others, which refuse. Events arrive in the order they were sent, from one thread at a time.
    """

    def pointer(self, event: PointerEvent, size: tuple[int, int]) -> None:
        """One pointer event: ``event.x`` and ``event.y`` are pixels of the shown picture of ``size`` (``None`` for a
        relative ``move``), ``event.buttons`` is the state of the buttons **after** the event (as
        ``PointerEvent.buttons``
        in a browser: ``down`` when the first button goes down, ``move`` when others change, ``up`` when the last is
        let go; a sink that needs the button that changed keeps the previous state), and a ``wheel`` has its deltas in
        ``dx`` and ``dy``.

        A ``move`` may be replaced by a later one when the device is behind; nothing else may be dropped. The default
        is the touch subset: one finger (``kind`` ``touch``, ``id`` 0), passed to :meth:`touch`.
        """
        if event.kind == "touch" and event.type in ("down", "move", "up") and event.id == 0 and event.x is not None:
            self.touch(event.type, round(event.x), round(cast(float, event.y)), size)
            return
        raise InputError("this HMI does not take that pointer")

    def touch(self, kind: str, x: int, y: int, size: tuple[int, int]) -> None:
        """One touch (``down``, ``move`` or ``up``, finger 0) at ``x``, ``y`` pixels of the shown picture of ``size``
        (already clamped to it): the single-finger subset that :meth:`pointer` hands on by default.

        A ``move`` may be replaced by a later one when the device is behind; nothing else may be dropped.
        """
        raise InputError("this HMI takes no touch")

    def keyboard(self, code: str, down: bool) -> None:
        """One key going down or up, by its W3C ``KeyboardEvent.code`` (``capabilities().keyboard`` allows it)."""
        raise InputError("this HMI takes no keyboard")

    def key(self, key: str, press: str) -> None:
        """A hard key by its canonical name (one of ``capabilities().keys``), ``press`` being ``short``, ``long``,
        ``down`` or ``up``.
        """
        raise InputError("this HMI has no hard keys")

    def text(self, text: str) -> None:
        """Type text, in the layout the source declares. A source that cannot (``capabilities().keyboard.text`` false)
        never sees it.
        """
        raise InputError("this HMI does not take text")

    def rotary(self, rotary_id: str, delta: int) -> None:
        """A rotary of ``capabilities().rotaries`` turned by ``delta`` detents (positive is clockwise). Its push button
        arrives as the hard key it names (:meth:`key`).
        """
        raise InputError("this HMI has no rotary")

    def release(self) -> None:
        """The last input stream ended: let go of whatever is still held (keys, buttons). Contacts that are down are
        lifted by the exporter first, with a ``pointer`` ``up``, and keys that are down with ``keyboard(code, False)``;
        an input stream that ends while another is open lets go only of what it held itself.
        """


class HmiSource(ABC):
    """The device-specific half of an HMI: see the module documentation."""

    @abstractmethod
    def capabilities(self) -> Capabilities:
        """What the HMI is and takes: its codecs, pointer, keyboard and keys (called often; keep it cheap)."""

    @abstractmethod
    def start(self, sink: HubSink, rung: Rung | None, timeout_s: float, *, codec: str = DEFAULT_CODEC) -> None:
        """Begin capturing into ``sink`` and return once connected (within ``timeout_s``).

        ``rung`` is the quality to start at (``None``: the source's own choice); it is only given when
        ``capabilities().quality`` is set. ``codec`` is the one stream's codec, one of ``capabilities().codecs``: the
        codec line the source publishes names it (``avc1.*``, ``hev1.*``/``hvc1.*`` or ``mjpeg``).

        Raises:
            RuntimeError: If the capture could not start (the message is shown to the user).
        """

    @abstractmethod
    def stop(self, deadline_s: float) -> None:
        """End the capture and release the device, within ``deadline_s`` seconds. Idempotent; never raises."""

    @property
    @abstractmethod
    def alive(self) -> bool:
        """Whether the capture is running."""

    @property
    def error(self) -> str | None:
        """Why the capture ended by itself, once it has (``None`` otherwise)."""
        return None

    def input_sink(self) -> InputSink | None:
        """Where input goes, or ``None`` for a view-only source.

        The driver asks for it **for every event**, so after a restart of the capture (a stall, a change of quality) a
        source may answer with a new sink; a sink must refuse (``InputError``) while its capture is not running. A
        finger
        that went down on an earlier capture is not carried into the next one: its moves and its up are dropped.
        """
        return None

    def request_keyframe(self) -> bool:
        """Ask the device for a key picture (never blocks); ``False``: none was asked (the source cannot, or it is rate
        limiting itself).

        Only called when ``capabilities().keyframe_cost`` is ``cheap``: for a source whose request restarts the capture
        for every viewer the hub relies on the periodic key picture and never asks.
        """
        return False

    def apply_rung(self, rung: Rung) -> bool:
        """Change the encoder's quality while running; ``True`` if it needed no restart, ``False`` to be stopped and
        started again with it.
        """
        return False

    def native_size(self) -> tuple[int, int] | None:
        """The display's own size, if known (the top of the quality ladder is made for it). May ask the device (it runs
        on a worker thread,
        before :meth:`start`) and returns ``None`` when it cannot."""
        return None

    def describe(self) -> dict:
        """The source's own part of ``status()["device"]``."""
        return {}

    def on_stall(self, silent_s: float, attempt: int) -> str:
        """A viewer is attached and no picture has come for ``silent_s`` seconds (called from a worker thread; it may
        block briefly).

        Returns ``"wait"`` (the screen is just still), ``"restart"`` (stop and start the capture; the viewers stay) or
        ``"end"`` (the
        capture is gone: end the stream, viewers reconnect). ``attempt`` counts the stalls since the last picture, from
        1: by default the
        first is waited out (the screen may be still) and the second restarts the capture.
        """
        return "restart" if attempt >= 2 else "wait"
