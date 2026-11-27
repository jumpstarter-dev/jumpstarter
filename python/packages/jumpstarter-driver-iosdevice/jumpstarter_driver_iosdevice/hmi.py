"""An iPhone or iPad as an HMI, through ``scrios``: its screen as H.264 or HEVC, with touch, a keyboard, buttons and
typing.

`scrios <https://github.com/kirkbrauer/scrios>`_ is a separate program (Apache-2.0, a static binary for Linux) that
streams an iOS device's screen through the CoreDevice display service and sends it touch, buttons and keyboard input.
This driver does not speak that protocol: it runs ``scrios stream --format framed`` as a child of the exporter, which
connects through the exporter's own usbmux daemon with the pairing the exporter already holds, reads its framed output
(:mod:`jumpstarter_driver_iosdevice.scrios_framed`) and hands the pictures to the shared hub of
:mod:`jumpstarter_driver_hmi`, which serves them to any number of viewers. Nothing of scrios reaches the client.

The HMI interface is a general one; this backend declares the subset of it that scrios genuinely supports, and the
exporter never sends it anything else:

* **codecs**: H.264 (the default) and HEVC, chosen with the ``codec`` option or ``start(codec=...)`` and passed to
  scrios as ``--video-codec``; the codec line names what scrios's ``config`` says. Not MJPEG.
* **pointer**: touch, one finger (scrios takes finger 0 only), at pixels of the shown picture.
* **keyboard**: the keys of :mod:`jumpstarter_driver_iosdevice.hid_usages` by their ``KeyboardEvent.code``, sent as
  ``kb down``/``kb up`` with the key's USB HID usage, and text, typed by scrios through a US layout. Both are refused,
  with scrios's reason, until its ``hid`` event says the keyboard works.
* **hard keys**: the buttons of the phone (Home, App Switcher, volume, mute, Siri; lock and power only when the
  exporter lists them).
* no clipboard, no mouse, no pen, no wheel, no adjustable quality.

::

    export:
      phone:
        type: jumpstarter_driver_iosdevice.driver.IosDevice
        config:
          usb_port: "1-4.2"
          hmi: {}                # the child ``hmi``; the options are the fields of IosDeviceHmi

The HMI package is an extra (``pip install jumpstarter-driver-iosdevice[hmi]``); a device without a ``hmi:``
option never imports it.

The binary is the one named by ``executable`` (found on ``PATH`` when it is a bare name) and is asked ``--version``
once before use; the first ``config`` it writes must say ``protocol: 1``. The child runs under ``PR_SET_PDEATHSIG``
(it dies with the exporter) and is supervised: it starts with the first viewer or input stream, ends (``stop`` first,
then SIGTERM, then SIGKILL, within one deadline) when the stream is stopped, idle, or the lease ends, and when it dies
by itself the viewers' streams end and the next one starts it again, after a back-off once it failed twice in a row.

Every write to scrios's stdin goes through one :class:`~jumpstarter_driver_hmi.proc.CommandWriter`: the thread that
reads its output, the touch and key handlers and the stop only enqueue, so a scrios that stops reading its stdin can
neither freeze the stream nor the end of a lease.
"""

from __future__ import annotations

import base64
import functools
import logging
import math
import os
import re
import shutil
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, fields, replace
from typing import TYPE_CHECKING, Any

import anyio
from anyio import from_thread
from jumpstarter_driver_hmi.common import (
    DEFAULT_CODEC,
    MAX_TEXT_CHARS,
    Capabilities,
    CodecLine,
    HardKey,
    InputError,
    KeyboardCaps,
    PointerCaps,
    canonical_key,
)
from jumpstarter_driver_hmi.driver import HmiDriver
from jumpstarter_driver_hmi.hub.codecs import is_reference
from jumpstarter_driver_hmi.hub.quality import Rung
from jumpstarter_driver_hmi.proc import COALESCE_KEYFRAME, Backoff, CommandWriter, ManagedProcess
from jumpstarter_driver_hmi.proc.process import DEFAULT_TERM_S
from jumpstarter_driver_hmi.proc.writer import DEFAULT_DEPTH
from jumpstarter_driver_hmi.source import HmiSource, HubSink, InputSink

from .hid_usages import CODES, USAGES, usage_argument
from .scrios_framed import (
    PROTOCOL,
    Config,
    Event,
    Fatal,
    FrameError,
    Problem,
    ScriosStream,
    Unit,
)
from jumpstarter.common.exceptions import ConfigurationError

if TYPE_CHECKING:
    from .driver import IosDevice

logger = logging.getLogger(__name__)

#: Where the releases of scrios are (named in the message of a missing binary).
RELEASES_URL = "https://github.com/kirkbrauer/scrios/releases"
#: How long ``scrios --version`` has to answer.
VERSION_TIMEOUT_S = 10.0
#: How long a start waits, once the picture is known, for scrios to say whether touch and keys are ready.
HID_WAIT_S = 2.0
#: What a start says when ``stop`` overtook it.
STOPPED_WHILE_STARTING = "the HMI stream was stopped while it started"
#: How long a start that failed gives scrios to leave (the driver stops the source again, with its own deadline).
START_FAIL_STOP_S = 4.0
#: The longest ``kb type`` command scrios takes, in characters; a longer text goes as several commands, in order.
TYPE_CHUNK_CHARS = 4096
#: The most pasted text one input line may carry (the HMI interface's own limit).
MAX_TYPED_CHARS = MAX_TEXT_CHARS
#: The codecs scrios streams, by the interface's names (the first is the default), and what scrios's ``--video-codec``
#: calls each.
VIDEO_CODECS = {"h264": "h264", "hevc": "h265"}
#: The layout scrios types text in (``kb type`` goes through a US keyboard).
LAYOUT = "us"

#: scrios's buttons by the canonical key names of the HMI interface. ``POWER`` is the lock button under its other
#: name.
BUTTONS = {
    "HOME": "home",
    "LOCK": "lock",
    "POWER": "power",
    "VOLUME_UP": "volume-up",
    "VOLUME_DOWN": "volume-down",
    "MUTE": "mute",
    "ASSIST": "siri",
    "APP_SWITCH": "app-switcher",
}
#: The buttons whose press is not a choice: ``key <name>`` with no ``short`` or ``long`` (it is the Home button pressed
#: twice, which opens the App Switcher).
PRESSLESS_BUTTONS = frozenset({"app-switcher"})
#: The hard keys the HMI accepts, canonical, and the keys it can send but does not offer unless the exporter lists them
#: in ``hard_keys``: the lock and power buttons can lock the user out of a device whose only way in is its screen.
OPTIONAL_KEYS = ("LOCK", "POWER")
KEYS = tuple(key for key in BUTTONS if key not in OPTIONAL_KEYS)
#: The buttons a UI draws by default, in order (the exporter may replace them with ``hard_keys:``).
DEFAULT_HARD_KEYS = (
    HardKey("HOME", "Home"),
    HardKey("APP_SWITCH", "App Switcher", ("short",)),
    HardKey("VOLUME_UP", "Volume Up"),
    HardKey("VOLUME_DOWN", "Volume Down"),
    HardKey("MUTE", "Mute"),
    HardKey("ASSIST", "Siri"),
)
#: What ``describe()`` counts about input.
INPUT_COUNTERS = (
    "touch_sent",
    "touch_dropped",
    "touch_refused",
    "keys_sent",
    "keys_refused",
    "keyboard_sent",
    "keyboard_refused",
    "chars_typed",
    "text_refused",
)
#: The values of ``--hid-space``.
HID_SPACES = ("auto", "panel", "ui")
#: What the driver sets itself: a name in ``extra_args`` that is one of these would fight it.
MANAGED_OPTIONS = frozenset(
    {
        "stream",
        "--format",
        "--output",
        "-o",
        "--record",
        "-r",
        "--udid",
        "-s",
        "--usbmuxd",
        "--video-codec",
        "--codec",
        "--no-control",
        "-n",
        "--no-stdin",
        "--list-devices",
        "--probe-secs",
        "--probe-raw",
        "--help",
        "-h",
        "--version",
        "-V",
    }
)
#: Fields of a ``hmi:`` mapping that the driver owns.
_RESERVED_FIELDS = frozenset({"device", "uuid", "labels", "children"})


# -- scrios, the program -----------------------------------------------------------------------------------------------


def find_scrios(executable: str) -> str:
    """The path of the scrios binary: ``executable`` as it is when it names a file, else looked up on ``PATH``.

    Raises:
        RuntimeError: If there is no such executable.
    """
    program = shutil.which(executable)
    if program is None:
        raise RuntimeError(
            f"scrios was not found: {executable!r} is not an executable file and not on PATH. Install a release "
            f"(scrios-<arch>-linux-musl, {RELEASES_URL}) on the exporter, put it on PATH, or give its path as "
            "`executable:` in the hmi options."
        )
    return program


def scrios_version(program: str) -> str:
    """What ``program --version`` says (``0.1.0``), asked once per file: the answer is kept until the file changes.

    Raises:
        RuntimeError: If the program cannot be run, does not answer in time, or is not scrios.
    """
    try:
        status = os.stat(program)
    except OSError as e:
        raise RuntimeError(f"scrios {program!r} cannot be read: {e}") from e
    return _version(program, status.st_mtime_ns, status.st_size)


#: How often ``--version`` is asked again when the program was killed by a signal before it could answer: the exporter
#: is a busy process with a gRPC server, and a fork in it can leave the child to die of a stray SIGPIPE.
VERSION_ATTEMPTS = 3


@functools.lru_cache(maxsize=32)
def _version(program: str, mtime_ns: int, size: int) -> str:
    del mtime_ns, size  # they only make the cache notice a new file
    for attempt in range(VERSION_ATTEMPTS):
        try:
            done = subprocess.run(  # the configured program, a fixed argument, no shell
                [program, "--version"],
                capture_output=True,
                stdin=subprocess.DEVNULL,
                timeout=VERSION_TIMEOUT_S,
                check=False,
                text=True,
                errors="replace",
            )
        except subprocess.TimeoutExpired:
            raise RuntimeError(f"`{program} --version` did not answer within {VERSION_TIMEOUT_S:g} s") from None
        except OSError as e:
            raise RuntimeError(f"scrios {program!r} could not be run: {e}") from e
        if done.returncode >= 0 or attempt == VERSION_ATTEMPTS - 1:  # not killed by a signal: that is its answer
            break
    said = done.stdout.strip().splitlines()[0][:200] if done.stdout.strip() else ""
    words = said.split()
    if done.returncode != 0 or not words or words[0].lower() != "scrios":
        error = done.stderr.strip().splitlines()[-1][:200] if done.stderr.strip() else ""
        shown = said or error or "nothing"
        raise RuntimeError(
            f"{program!r} does not look like scrios: `--version` said {shown!r} (exit status {done.returncode})"
        )
    return words[1] if len(words) > 1 else ""


def _device_info(device: IosDevice) -> dict:
    """``device.info()`` from a worker thread of the exporter, or from any other thread."""
    try:
        return from_thread.run(device.info)
    except RuntimeError as e:
        if "AnyIO worker thread" not in str(e):
            raise
    return anyio.run(device.info)


# -- one run of scrios -------------------------------------------------------------------------------------------------


class _Run:
    """One launch of scrios: its process, its writer and what it has said so far."""

    def __init__(self, sink: HubSink, argv: list[str], program: str, codec: str = DEFAULT_CODEC) -> None:
        self.sink = sink
        self.argv = argv
        self.program = program
        #: the codec this run was asked for (``h264`` or ``hevc``): a ``config`` that names another is fatal
        self.codec = codec
        self.stream = ScriosStream(expect=codec)
        self.proc: ManagedProcess
        self.writer: CommandWriter
        #: a usable ``config`` has come, or the run is over
        self.ready = threading.Event()
        #: scrios has said whether touch and keys are ready, or the run is over
        self.hid_known = threading.Event()
        #: the exit has been accounted for
        self.exited = threading.Event()
        #: ``stop`` was called: the exit is expected
        self.stop_wanted = False
        #: why this run counts as a failure even if it was stopped (a protocol or codec that cannot be used)
        self.failure: str | None = None
        self.failing = False
        #: scrios's own words, from its ``error`` event
        self.error: str | None = None
        #: why it ended by itself (``None``: not yet, or it was stopped)
        self.ended_reason: str | None = None
        self.returncode: int | None = None
        self.line: CodecLine | None = None
        self.labels: dict[str, str | None] = {}
        self.first_unit_at: float | None = None
        self.units = 0
        self.keys_without_params = 0
        self.stats: dict[str, Any] | None = None
        self.dropped_seen = 0
        self.stopped_event = False
        self.hid_ready: bool | None = None
        self.hid_keyboard: bool | None = None
        #: what scrios said when it reported the keyboard unavailable
        self.hid_keyboard_reason: str | None = None
        self.hid_mapping: str | None = None
        self.started_at = time.monotonic()


class IosDeviceHmiSource(HmiSource):
    """The device half of the hmi: runs scrios and turns its stream and the input into the interface's calls.

    Everything here runs on worker threads. The input sink only enqueues, and stopping is bounded.
    """

    def __init__(
        self,
        *,
        executable: str,
        udid: Callable[[], str],
        usbmuxd: Callable[[], str | None],
        control: bool = True,
        display_id: int | None = None,
        visible_size: str | None = None,
        hid_space: str | None = None,
        hid_swap_xy: bool = False,
        hid_flip_x: bool = False,
        hid_flip_y: bool = False,
        extra_args: Sequence[str] = (),
        stop_grace_s: float = 2.0,
        restart_backoff_s: float = 1.0,
        restart_backoff_max_s: float = 30.0,
        log: logging.Logger | None = None,
    ) -> None:
        """Describe what to run; nothing is started or looked up until :meth:`start`."""
        self._executable = executable
        self._udid = udid
        self._usbmuxd = usbmuxd
        self._control = control
        self._display_id = display_id
        self._visible_size = visible_size
        self._hid = (hid_space, hid_swap_xy, hid_flip_x, hid_flip_y)
        self._extra_args = list(extra_args)
        self._stop_grace_s = stop_grace_s
        self._log = log or logger
        self._lock = threading.Lock()
        self._backoff = Backoff(restart_backoff_s, restart_backoff_max_s)
        self._run: _Run | None = None
        self._epoch = 0
        self._input = _Input(self) if control else None
        self._counters = dict.fromkeys(INPUT_COUNTERS, 0)
        self._program_path: str | None = None
        self._version: str | None = None
        self._starts = 0
        self._crashes = 0
        self._stall_restarts = 0
        self._stall_restarted = False
        self._last_stall_at: float | None = None
        self._last_error: str | None = None
        self._last_error_at: float | None = None
        self._last_exit: dict[str, Any] | None = None
        self._hid_errors = 0
        self._last_hid_error: str | None = None

    # -- what it is -------------------------------------------------------------------------------------------------

    def capabilities(self) -> Capabilities:
        """What scrios supports and nothing else: H.264 and HEVC; touch with one finger; the keyboard keys of
        :mod:`~jumpstarter_driver_iosdevice.hid_usages` and typed text (US layout); the phone's buttons. Not adjustable
        in quality, and a key picture is a cheap request.
        """
        codecs = tuple(VIDEO_CODECS)
        if not self._control:
            return Capabilities(codecs=codecs, keyframe_cost="cheap", rotation=True)
        return Capabilities(
            codecs=codecs,
            pointer=PointerCaps.touch(1),
            keyboard=KeyboardCaps(codes=CODES, text=True, layout=LAYOUT),
            keys=KEYS,
            optional_keys=OPTIONAL_KEYS,
            hard_keys=DEFAULT_HARD_KEYS,
            quality=None,
            keyframe_cost="cheap",
            rotation=True,
        )

    def command(self, program: str, udid: str, codec: str = DEFAULT_CODEC) -> list[str]:
        """The command line: scrios's documented options and nothing else.

        ``stream`` is mandatory (a bare ``scrios`` opens a window in a build that has a viewer); framed output makes it
        wait for nothing, and the end of its stdin is a stop. ``codec`` (``h264`` or ``hevc``) is passed as scrios's
        ``--video-codec`` (``h264`` or ``h265``).

        Raises:
            RuntimeError: If scrios does not stream ``codec``.
        """
        if codec not in VIDEO_CODECS:
            raise RuntimeError(_refused_codec(codec))
        argv = [program, "stream", "--format", "framed", "--udid", udid, "--video-codec", VIDEO_CODECS[codec]]
        if not self._control:
            argv.append("--no-control")
        if (address := self._usbmuxd()) is not None:
            argv += ["--usbmuxd", address]
        if self._display_id is not None:
            argv += ["--display-id", str(self._display_id)]
        if self._visible_size is not None:
            argv += ["--visible-size", self._visible_size]
        if self._control:
            space, swap, flip_x, flip_y = self._hid
            if space is not None:
                argv += ["--hid-space", space]
            argv += [
                flag for flag, on in (("--hid-swap-xy", swap), ("--hid-flip-x", flip_x), ("--hid-flip-y", flip_y)) if on
            ]
        return argv + self._extra_args

    # -- start -------------------------------------------------------------------------------------------------------

    def _program(self) -> str:
        """The binary to run, found and asked ``--version`` (a worker thread: it may take a moment)."""
        program = find_scrios(self._executable)
        version = scrios_version(program)
        with self._lock:
            self._program_path, self._version = program, version
        return program

    def _check_backoff(self) -> None:
        with self._lock:
            wait, failures, last = self._backoff.wait_s(), self._backoff.failures, self._last_error
        if wait > 0:
            raise RuntimeError(
                f"scrios failed {failures} times in a row (last: {last or 'unknown'}); "
                f"it is not started again for {wait:.1f} s"
            )

    def forget_failures(self) -> None:
        """A new lease: the back-off and the last error are forgotten."""
        with self._lock:
            self._backoff.reset()
            self._last_error = self._last_error_at = None

    def start(self, sink: HubSink, rung: Rung | None, timeout_s: float, *, codec: str = DEFAULT_CODEC) -> None:
        """Run scrios and return once it has said what the picture is (and, with control, whether touch is ready).

        ``codec`` is ``h264`` or ``hevc``; the codec line is what scrios's ``config`` says, and a ``config`` that names
        the other codec ends the start.

        Raises:
            RuntimeError: If ``codec`` is not one scrios streams, scrios is missing, is not scrios or speaks another
                protocol, cannot find the device, ends or says nothing within ``timeout_s``, or is backing off after
                failures.
        """
        if codec not in VIDEO_CODECS:
            raise RuntimeError(f"the HMI stream could not start: {_refused_codec(codec)}")
        deadline = time.monotonic() + timeout_s
        with self._lock:
            epoch = self._epoch
        program = self._program()
        self._check_backoff()
        argv = self.command(program, self._udid(), codec)
        run = _Run(sink, argv, program, codec)
        run.proc = ManagedProcess(
            argv,
            name="scrios",
            on_stdout=functools.partial(self._feed, run),
            on_exit=functools.partial(self._exited, run),
            log=self._log,
        )
        run.writer = CommandWriter(
            run.proc.stdin_write, name="scrios-stdin", on_failure=functools.partial(self._writer_failed, run)
        )
        with self._lock:
            previous, self._run = self._run, run
            gone = self._epoch != epoch
            self._starts += 1
        try:
            if previous is not None:
                self._stop_run(previous, START_FAIL_STOP_S)
            if gone:  # stop() came while the program was being looked up
                self._abort(run, STOPPED_WHILE_STARTING, failure=False)
            try:
                run.proc.start()
            except RuntimeError as e:
                self._abort(run, STOPPED_WHILE_STARTING if run.stop_wanted else str(e), failure=False, spawned=False)
            self._wait_ready(run, deadline, timeout_s)
        except BaseException:
            # whatever went wrong, no child is left behind (``_abort`` has done this already for the errors it raises)
            if not run.exited.is_set():
                self._stop_run(run, START_FAIL_STOP_S)
            with self._lock:
                if self._run is run:
                    self._run = None
            raise

    def _wait_ready(self, run: _Run, deadline: float, timeout_s: float) -> None:
        if not run.ready.wait(max(0.0, deadline - time.monotonic())) and run.line is None:
            tail = run.proc.stderr_tail[-1:]
            self._abort(
                run,
                f"scrios did not say what the picture is within {timeout_s:g} s"
                + (f" (it said: {tail[0]})" if tail else ""),
            )
        if run.stop_wanted and run.failure is None:
            self._abort(run, STOPPED_WHILE_STARTING, failure=False)
        if run.line is None or run.failure is not None:
            self._abort(run, run.failure or self._explain(run, run.returncode), failure=False)
        if self._control:
            run.hid_known.wait(max(0.0, min(HID_WAIT_S, deadline - time.monotonic())))

    def _abort(self, run: _Run, reason: str, *, failure: bool = True, spawned: bool = True) -> None:
        """A start that failed: stop the run and say why."""
        if failure and run.failure is None:
            run.failure = reason
        if spawned:
            self._stop_run(run, START_FAIL_STOP_S)
        else:  # it never ran: nothing will account for its end
            if not run.stop_wanted:
                with self._lock:
                    self._backoff.record_exit(0.0, False)
                    self._crashes += 1
                    self._last_error, self._last_error_at = reason, time.time()
            run.writer.close(0.0)
        with self._lock:
            if self._run is run:
                self._run = None
        raise RuntimeError(f"the HMI stream could not start: {reason}")

    # -- reading what scrios says --------------------------------------------------------------------------------------

    def _feed(self, run: _Run, chunk: bytes) -> None:
        """scrios's stdout: decode it and hand it on (the stdout reader thread; it never writes to scrios and never
        blocks).
        """
        fatal = None
        try:
            items = run.stream.feed(chunk)
        except FrameError as e:
            items, fatal = e.items, str(e)
        for item in items:
            if isinstance(item, Unit):
                self._unit(run, item)
            elif isinstance(item, Config):
                self._config(run, item)
            elif isinstance(item, Event):
                self._event(run, item)
            elif isinstance(item, Problem):
                self._log.warning("scrios: %s", item.message)
            elif isinstance(item, Fatal):
                fatal = fatal or item.message
        if fatal is not None:
            self._fail(run, fatal)

    def _unit(self, run: _Run, unit: Unit) -> None:
        if run.first_unit_at is None:
            run.first_unit_at = time.monotonic()
        run.units += 1
        self._stall_restarted = False
        if unit.key and not unit.params:
            run.keys_without_params += 1  # the hub puts the sets it has seen in front of it
        # scrios says which units are key pictures; whether one may be predicted from is read in the codec it is in
        run.sink.media(unit.pts_us, unit.payload, idr=unit.key, reference=is_reference(unit.payload, run.codec))

    def _config(self, run: _Run, config: Config) -> None:
        line = replace(config.line, input="stream" if self._control else None)
        first = run.line is None
        run.line, run.labels = line, config.labels
        run.sink.config(line)
        if first:
            self._log.info(
                "scrios %s %dx%d (coded %sx%s) %d fps, rotation %s, point scale %s",
                line.codec,
                line.width,
                line.height,
                line.coded_width or line.width,
                line.coded_height or line.height,
                line.fps,
                "unknown" if line.rotation is None else line.rotation,
                "unknown" if line.point_scale is None else line.point_scale,
            )
            run.ready.set()

    def _event(self, run: _Run, event: Event) -> None:
        data = event.data
        if event.name == "stats":
            run.stats = {
                k: v for k, v in list(data.items())[:32] if k != "event" and isinstance(v, int | float | str | bool)
            }
            dropped = run.stats.get("dropped_units")
            if isinstance(dropped, int | float) and dropped > run.dropped_seen:
                run.dropped_seen = int(dropped)
                run.sink.discontinuity()  # a picture was lost before us: the cached group of pictures has a hole
        elif event.name == "hid":
            self._hid_event(run, data)
        elif event.name == "error":
            run.error = _text(data.get("message"), 500) or "scrios reported an error"
            self._log.error("scrios: %s", run.error)
        elif event.name == "stopped":
            run.stopped_event = True
        else:
            self._log.debug("scrios: unknown event %r", event.name)

    def _hid_event(self, run: _Run, data: dict[str, Any]) -> None:
        """What the touch and key services said: ``ready`` and ``keyboard`` say what works, ``ok: false`` is a command
        that failed, ``warning`` a surprise that is not an error.
        """
        message = _text(data.get("message"), 300)
        if data.get("ok") is False:
            text = message or "scrios refused a touch, a key or a keyboard command"
            with self._lock:
                self._hid_errors += 1
                self._last_hid_error = text
            self._log.warning("scrios touch input: %s", text)
        if isinstance(warning := data.get("warning"), str):
            self._log.warning("scrios touch input: %s", _text(warning, 300))
        if isinstance(keyboard := data.get("keyboard"), bool):
            run.hid_keyboard = keyboard
            run.hid_keyboard_reason = None if keyboard else message
        if isinstance(mapping := data.get("mapping"), str):
            run.hid_mapping = _text(mapping, 64)
        ready = data.get("ready")
        if isinstance(ready, bool):
            if not ready:
                with self._lock:
                    self._last_hid_error = message or "its touch and key services are not ready"
            run.hid_ready = ready
            run.hid_known.set()
            self._log.info(
                "scrios touch input %s%s",
                "ready" if ready else "not ready",
                f" ({message})" if message and not ready else "",
            )

    # -- the end of a run ----------------------------------------------------------------------------------------------

    def _fail(self, run: _Run, reason: str) -> None:
        """This run cannot be used (an unusable stream): end it, and count it as a failure."""
        with self._lock:
            run.failure = run.failure or reason
            first = not run.failing
            run.failing = True
        self._log.error("scrios: %s", reason)
        run.ready.set()
        if first:  # not on this thread: it is the one that reads scrios's output, which a stop waits for
            threading.Thread(
                target=self._stop_run, args=(run, START_FAIL_STOP_S), name="scrios-fail-stop", daemon=True
            ).start()

    def _writer_failed(self, run: _Run, error: BaseException) -> None:
        """A write to scrios's stdin failed: it is gone or stopped listening; its exit is dealt with as it comes."""
        self._log.debug("scrios: stdin: %s", error)

    def _exited(self, run: _Run, returncode: int | None) -> None:
        """The child is reaped (the waiter thread): account for how it ended, and say so unless it was asked to."""
        ended_at = time.monotonic()
        expected = run.stop_wanted and run.failure is None
        reason = self._explain(run, returncode)
        lasted = ended_at - run.first_unit_at if run.first_unit_at is not None else 0.0
        with self._lock:
            run.returncode = returncode
            delay = self._backoff.record_exit(lasted, expected)
            failures = self._backoff.failures
            if not expected:
                self._crashes += 1
                self._last_error, self._last_error_at = reason, time.time()
                run.ended_reason = reason
            self._last_exit = {"returncode": returncode, "expected": expected, "reason": reason, "at": time.time()}
        if not expected:
            self._log.error("scrios ended: %s (failures in a row: %d, next start in %.0f s)", reason, failures, delay)
        run.ready.set()
        run.hid_known.set()
        run.writer.close(0.0)
        run.exited.set()
        if not expected:
            run.sink.ended(reason)

    @staticmethod
    def _explain(run: _Run, returncode: int | None) -> str:
        if run.failure:
            return run.failure
        if run.error:
            return run.error
        what = (
            "scrios could not be waited for"
            if returncode is None
            else f"scrios exited with status {returncode}"
            if returncode >= 0
            else f"scrios was killed by signal {-returncode}"
        )
        if run.stop_wanted:
            return what
        tail = run.proc.stderr_tail[-1:]
        return f"{what}: {tail[0]}" if tail else what

    # -- stop --------------------------------------------------------------------------------------------------------

    def stop(self, deadline_s: float) -> None:
        """Ask scrios to stop (``stop`` on its stdin, then SIGTERM, then SIGKILL) within ``deadline_s``. Idempotent."""
        with self._lock:
            self._epoch += 1
            run = self._run
        if run is None:
            return
        self._stop_run(run, deadline_s)
        with self._lock:
            if self._run is run:
                self._run = None

    def _stop_run(self, run: _Run, deadline_s: float) -> None:
        run.stop_wanted = True
        stopped = run.proc.stop(
            deadline_s,
            stop_command=b"stop\n",
            send=run.writer.send,
            flush=run.writer.close,
            grace_s=self._stop_grace_s,
            term_s=DEFAULT_TERM_S,
        )
        run.writer.close(0.0)
        run.ready.set()
        run.hid_known.set()
        if not stopped:
            self._log.error("scrios did not exit within %.1f s of being told to", deadline_s)

    @property
    def alive(self) -> bool:
        """Whether scrios is running."""
        run = self._run
        return run is not None and run.proc.alive and not run.exited.is_set()

    @property
    def error(self) -> str | None:
        """Why scrios ended by itself, once it has."""
        run = self._run
        return run.ended_reason if run is not None else None

    # -- what the driver asks of a running source ----------------------------------------------------------------------

    def input_sink(self) -> InputSink | None:
        """Touch, the keyboard, buttons and typing (``None`` when scrios runs with ``--no-control``)."""
        return self._input

    def request_keyframe(self) -> bool:
        """Ask scrios for a key picture: one command is enough, and the queue never blocks."""
        run = self._run
        if run is None or run.stop_wanted or not run.proc.alive:
            return False
        try:
            return run.writer.send(b"keyframe\n", coalesce=COALESCE_KEYFRAME)
        except InputError:
            return False

    def on_stall(self, silent_s: float, attempt: int) -> str:
        """No picture for a while: the first time ask for a key picture and wait; the second time restart scrios, once
        (if the restarted one is silent too, end the stream: the viewers start it again, after a back-off).
        """
        run = self._run
        if run is None or not run.proc.alive:
            return "end"
        if attempt <= 1:
            self._last_stall_at = time.time()
            self._log.warning("no scrios pictures for %.0f s: asking for a key picture", silent_s)
            self.request_keyframe()
            return "wait"
        if self._stall_restarted:
            return "end"
        self._stall_restarted = True
        self._stall_restarts += 1
        return "restart"

    def describe(self) -> dict[str, Any]:
        """The source's own part of ``status()``: the program, the picture's labels, what scrios said about its input,
        how it started and ended, and the last lines of its log.
        """
        with self._lock:
            run = self._run
            counters = dict(self._counters)
            state = {
                "program": self._program_path,
                "version": self._version,
                "protocol": PROTOCOL,
                "starts": self._starts,
                "crashes": self._crashes,
                "stall_restarts": self._stall_restarts,
                "last_stall_at": self._last_stall_at,
                "failures_in_a_row": self._backoff.failures,
                "retry_in_s": math.ceil(self._backoff.wait_s() * 100) / 100,  # 0 only when a start is allowed now
                "last_error": self._last_error,
                "last_error_age_s": None
                if self._last_error_at is None
                else round(time.time() - self._last_error_at, 1),
                "last_exit": dict(self._last_exit) if self._last_exit is not None else None,
                "hid_errors": self._hid_errors,
                "last_hid_error": self._last_hid_error,
            }
        alive = run is not None and run.proc.alive
        state.update(
            {
                "control": self._control,
                "pid": run.proc.pid if run is not None and alive else None,
                "uptime_s": round(time.monotonic() - run.started_at, 1) if run is not None and alive else 0,
                "stats": dict(run.stats) if run is not None and run.stats is not None else None,
                "codec": run.codec if run is not None else None,
                "picture": dict(run.labels) if run is not None else {},
                "point_scale": run.line.point_scale if run is not None and run.line is not None else None,
                "hid": {
                    "ready": run.hid_ready if run is not None else None,
                    "keyboard": run.hid_keyboard if run is not None else None,
                    "mapping": run.hid_mapping if run is not None else None,
                },
                "input": counters,
                "units_before_config": run.stream.units_before_config if run is not None else 0,
                "bad_control_frames": run.stream.bad_control if run is not None else 0,
                "keys_without_params": run.keys_without_params if run is not None else 0,
                "log": run.proc.stderr_tail[-5:] if run is not None else [],
            }
        )
        return state

    def count(self, name: str, amount: int = 1) -> None:
        """One more of an input counter."""
        with self._lock:
            self._counters[name] += amount

    def current_run(self) -> _Run | None:
        """The run there is, if any (it may be ending)."""
        return self._run

    def last_hid_error(self) -> str | None:
        """What scrios last said about its touch and key services not working (``None``: nothing)."""
        with self._lock:
            return self._last_hid_error

    def input_run(self) -> _Run:
        """The run that input goes to.

        Raises:
            InputError: If scrios is not running.
        """
        run = self._run
        if run is None or run.stop_wanted or run.exited.is_set() or not run.proc.alive:
            raise InputError("the HMI stream is not running")
        return run


def _refused_codec(codec: str) -> str:
    """Why scrios cannot stream ``codec``."""
    return f"scrios streams {' or '.join(VIDEO_CODECS)}, not {codec}"


def _text(value: object, limit: int) -> str | None:
    """A short printable string scrios sent, or ``None``."""
    if not isinstance(value, str) or not value.strip():
        return None
    return "".join(c if c.isprintable() else " " for c in value)[:limit]


# -- input -------------------------------------------------------------------------------------------------------------


class _Input(InputSink):
    """Touch, keyboard keys, buttons and typing into scrios's stdin. Every method only enqueues (the writer thread does
    the write), and one that cannot take an event raises ``InputError``.

    Events arrive from one thread at a time, in order. One contact is tracked here as well: a ``move`` or an ``up`` of a
    finger that is not down is nothing (a late event after a restart), and a ``down`` of one that is down is a ``move``.
    So are the keys that are down, per run of scrios: the ``down`` of a key that is down is a repeat and is dropped (the
    device repeats by itself), the ``up`` of one that is not down is nothing, and :meth:`release` lets go of the ones
    that are still down. A run that has ended took its keys with it (scrios releases every key when it stops).
    """

    def __init__(self, source: IosDeviceHmiSource) -> None:
        self._source = source
        self._down_in: _Run | None = None
        self._gesture = 0
        self._keys_lock = threading.Lock()
        self._keys_run: _Run | None = None
        self._keys: set[str] = set()

    def _ready(self, run: _Run, what: str = "touch input is", *, keyboard: bool = False) -> None:
        """Refuse (with scrios's reason) while its touch and key services, or with ``keyboard`` its virtual keyboard,
        are known not to work. ``what`` is the subject with its verb: ``touch input is``, ``typing is``.
        """
        if run.hid_ready is False:
            reason = self._source.last_hid_error() or "its touch and key services are not ready"
            raise InputError(f"{what} not available: {reason}")
        if keyboard and run.hid_keyboard is False:
            reason = run.hid_keyboard_reason
            raise InputError(f"the device's keyboard is not ready{f': {reason}' if reason else ''}")

    def touch(self, kind: str, x: int, y: int, size: tuple[int, int]) -> None:
        """``touch <kind> <x> <y> 0``: the position is in pixels of the shown picture, which is what scrios takes (it
        turns it into the digitizer's, whatever the rotation).
        """
        source = self._source
        try:
            run = source.input_run()
            self._ready(run)
            finger_down = self._down_in is run
            if kind != "down" and not finger_down:
                return  # a finger that is not down (a late event after the finger was lifted, or a restart)
            if kind == "down" and finger_down:
                kind = "move"
            if kind == "down":
                self._gesture += 1
            command = f"touch {kind} {int(x)} {int(y)} 0\n".encode()
            # moves are coalesced within their gesture: a move queued before a lift must not replace the next gesture's
            queued = run.writer.send(command, coalesce=f"move:{self._gesture}" if kind == "move" else None)
        except InputError:
            source.count("touch_refused")
            raise
        if not queued:
            source.count("touch_dropped")  # the queue is full: this move is replaced by the next one
            return
        source.count("touch_sent")
        self._down_in = None if kind == "up" else run

    def keyboard(self, code: str, down: bool) -> None:
        """``kb down <usage>`` or ``kb up <usage>``: the key's USB HID usage in hex. Refused (with scrios's reason)
        until its ``hid`` event says the keyboard works; a key that is down is let go whatever scrios says now.
        """
        source = self._source
        try:
            if code not in USAGES:
                raise InputError(f"this HMI does not take the key {code}")
            run = source.input_run()
            with self._keys_lock:
                held = self._held(run)
                if down == (code in held):
                    return  # a repeat of a key that is down, or the end of one that is not
                if down:
                    self._ready(run, "the keyboard is", keyboard=True)
                run.writer.send(f"kb {'down' if down else 'up'} {usage_argument(code)}\n".encode())
                if down:
                    held.add(code)
                else:
                    held.discard(code)
        except InputError:
            source.count("keyboard_refused")
            raise
        source.count("keyboard_sent")

    def _held(self, run: _Run) -> set[str]:
        """The keys down on ``run`` (a new run has none: the one before it let them go). Under ``_keys_lock``."""
        if self._keys_run is not run:
            self._keys_run, self._keys = run, set()
        return self._keys

    def release(self) -> None:
        """Let go of the keys that are still down (``kb up`` for each, by usage). Never raises: a key that could not be
        let go stays down here, for the next call, and scrios releases it by itself 30 seconds after the last keyboard
        command, and when it stops.
        """
        run = self._source.current_run()
        with self._keys_lock:
            if run is None or self._keys_run is not run or run.stop_wanted or run.exited.is_set():
                self._keys_run, self._keys = None, set()  # the run is over: scrios let go of them with it
                return
            for code in sorted(self._keys, key=USAGES.__getitem__):
                try:
                    run.writer.send(f"kb up {usage_argument(code)}\n".encode())
                except InputError:
                    self._source.count("keyboard_refused")
                    return
                self._keys.discard(code)
                self._source.count("keyboard_sent")

    def key(self, key: str, press: str) -> None:
        """``key <button> [short|long]``: a press and a release, after the button's hold time."""
        source = self._source
        try:
            name = BUTTONS.get(canonical_key(key))
            if name is None:
                raise InputError(f"this HMI has no key {key}")
            if press not in ("short", "long"):
                raise InputError(f"{key} is pressed short or long (scrios holds it itself), not {press}")
            run = source.input_run()
            self._ready(run, "buttons are")
            command = f"key {name}\n" if name in PRESSLESS_BUTTONS else f"key {name} {press}\n"
            run.writer.send(command.encode())
        except InputError:
            source.count("keys_refused")
            raise
        source.count("keys_sent")

    def text(self, text: str) -> None:
        """``kb type <base64>``: the text, as UTF-8, in commands of at most ``TYPE_CHUNK_CHARS`` characters.

        scrios types through the US layout and skips what it has no key for (and says so in a ``hid`` warning). Until
        its virtual keyboard is ready the text is refused with an error line.
        """
        source = self._source
        if not text:
            return
        try:
            if len(text) > MAX_TYPED_CHARS:
                raise InputError(
                    f"text is {len(text)} characters: at most {MAX_TYPED_CHARS} a line, and none was typed"
                )
            try:
                payloads = [
                    base64.b64encode(text[at : at + TYPE_CHUNK_CHARS].encode("utf-8")).decode("ascii")
                    for at in range(0, len(text), TYPE_CHUNK_CHARS)
                ]
            except UnicodeEncodeError:
                raise InputError("the text is not valid Unicode") from None
            run = source.input_run()
            self._ready(run, "typing is", keyboard=True)
            if run.writer.stats()["queued"] + len(payloads) > DEFAULT_DEPTH:
                raise InputError("the device is busy: its input queue is full")  # all of the text or none of it
            for payload in payloads:
                run.writer.send(f"kb type {payload}\n".encode())
        except InputError:
            source.count("text_refused")
            raise
        source.count("chars_typed", len(text))


# -- the driver --------------------------------------------------------------------------------------------------------


@dataclass(kw_only=True)
class IosDeviceHmi(HmiDriver):
    """The iOS device as an HMI, through scrios. See the module documentation.

    Besides its own options this takes the common ones of every HMI (``control``, ``codec``, ``hard_keys``,
    ``idle_stop_s``, ``stop_deadline_s``, ...). ``codec`` is ``h264`` (the default) or ``hevc``; ``mjpeg`` is refused.
    ``max_size``, ``bit_rate`` and ``max_fps`` are refused too: scrios streams the display at its own size, rate and bit
    rate. ``LOCK`` and ``POWER`` are accepted only when ``hard_keys`` lists them.
    """

    device: IosDevice | None = field(default=None, repr=False)

    # -- which device and which scrios -----------------------------------------------------------------------------
    #: The scrios binary: a path, or a name looked up on ``PATH``.
    executable: str = "scrios"
    #: More options for ``scrios stream`` (the ones the driver sets itself are refused).
    extra_args: list[str] = field(default_factory=list)
    #: The usbmux daemon scrios connects through (``UNIX:/path`` or ``host:port``). Default: the device driver's own.
    usbmuxd: str | None = None
    #: The device. Default: the device driver's own.
    udid: str | None = None
    #: The display to stream (scrios's ``--display-id``; default: its own, 1, the built-in display).
    display_id: int | None = None
    #: The picture's visible size, ``"1206x2622"`` (scrios's ``--visible-size``; default: what the device says).
    visible_size: str | None = None
    #: How a touch is mapped onto the digitizer (scrios's ``--hid-space``, ``--hid-swap-xy``, ``--hid-flip-x`` and
    #: ``--hid-flip-y``). ``auto`` is scrios's own default, worked out from the display's native orientation; the three
    #: flags are applied on top of it. Only with ``control``.
    hid_space: str | None = None
    hid_swap_xy: bool = False
    hid_flip_x: bool = False
    hid_flip_y: bool = False

    # -- lifecycle ---------------------------------------------------------------------------------------------------
    #: After ``stop`` and the end of its stdin scrios is given this long to leave before it is sent SIGTERM (and then
    #: the same time again before SIGKILL, all within ``stop_deadline_s``). A clean stop of scrios can take longer;
    #: raise
    #: both to let it finish.
    stop_grace_s: float = 2.0
    #: After a second failure in a row the next start waits this long, doubling for each further failure up to
    #: ``restart_backoff_max_s``.
    restart_backoff_s: float = 1.0
    restart_backoff_max_s: float = 30.0
    #: scrios connects through the device's tunnel, which takes seconds.
    start_timeout_s: float = 30.0
    #: Silence with a viewer attached: the first time scrios is asked for a key picture, the second time it is
    #: restarted.
    stall_s: float = 8.0

    def __post_init__(self) -> None:
        """Check the configuration; runs nothing."""
        super().__post_init__()
        self._validate_scrios()

    def _validate_scrios(self) -> None:  # noqa: C901 (one check per option)
        for name in ("executable", "usbmuxd", "udid"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, str) or not value.strip()):
                raise ConfigurationError(f"{name} must be a non-empty string")
        if not isinstance(self.executable, str):
            raise ConfigurationError("executable must be the path or the name of scrios")
        if self.udid is not None and not re.fullmatch(r"[A-Za-z0-9-]+", self.udid):
            raise ConfigurationError("udid must be a device identifier")
        if self.device is None and self.udid is None:
            raise ConfigurationError("an HMI without a device driver needs a udid")
        if self.display_id is not None and (
            isinstance(self.display_id, bool) or not isinstance(self.display_id, int) or self.display_id < 0
        ):
            raise ConfigurationError("display_id must be a whole number")
        if self.visible_size is not None and (
            not isinstance(self.visible_size, str)
            or not re.fullmatch(r"[1-9][0-9]{1,4}x[1-9][0-9]{1,4}", self.visible_size)
        ):
            raise ConfigurationError("visible_size must look like 1206x2622")
        if self.hid_space is not None and self.hid_space not in HID_SPACES:
            raise ConfigurationError(f"hid_space must be {', '.join(HID_SPACES)}")
        for name in ("hid_swap_xy", "hid_flip_x", "hid_flip_y"):
            if not isinstance(getattr(self, name), bool):
                raise ConfigurationError(f"{name} must be true or false")
        if not self.control and (self.hid_space is not None or self.hid_swap_xy or self.hid_flip_x or self.hid_flip_y):
            raise ConfigurationError("the hid_* options map touch, which needs control (it is off)")
        self._validate_extra_args()
        for name in ("stop_grace_s",):
            if not _seconds(getattr(self, name), positive=True):
                raise ConfigurationError(f"{name} must be a positive number of seconds")
        for name in ("restart_backoff_s", "restart_backoff_max_s"):
            if not _seconds(getattr(self, name)):
                raise ConfigurationError(f"{name} must be a number of seconds, not negative")
        if self.restart_backoff_max_s < self.restart_backoff_s:
            raise ConfigurationError("restart_backoff_max_s must not be less than restart_backoff_s")
        if self.max_size is not None or self.bit_rate is not None or self.max_fps is not None:
            raise ConfigurationError(
                "scrios streams at the display's own size, rate and bit rate: "
                "max_size, bit_rate and max_fps are not options here"
            )
        if self.codec is not None and self.codec not in VIDEO_CODECS:
            raise ConfigurationError(f"codec: {_refused_codec(self.codec)}")

    def _validate_extra_args(self) -> None:
        if not isinstance(self.extra_args, list) or not all(isinstance(a, str) and a for a in self.extra_args):
            raise ConfigurationError("extra_args must be a list of non-empty strings")
        for argument in self.extra_args:
            name = argument.split("=", 1)[0]
            if name in MANAGED_OPTIONS or (len(name) > 2 and name[:2] in ("-s", "-o", "-n", "-r") and name[1] != "-"):
                raise ConfigurationError(
                    f"extra_args may not contain {name}: the driver sets it itself (scrios stream --format framed ...)"
                )

    # -- the source --------------------------------------------------------------------------------------------------

    def _resolve_udid(self) -> str:
        """The device to stream: the configured one, the device driver's, or what its USB port holds now (a worker
        thread).
        """
        if self.udid:
            return self.udid
        device = self.device
        assert device is not None  # an HMI without a device driver has a udid (checked at construction)
        if device.udid:
            return device.udid
        info = _device_info(device)
        if not info.get("present"):
            raise RuntimeError(info.get("reason") or "the iOS device is not present")
        return info["udid"]

    def _usbmuxd_address(self) -> str | None:
        if self.usbmuxd:
            return self.usbmuxd
        device = self.device
        if device is None:
            return None
        if device.transport == "network":
            return f"{device.usbmux_host}:{device.usbmux_port}"
        return f"UNIX:{device.usbmux_socket}"

    def make_source(self) -> HmiSource:
        """The scrios source (nothing is run until the stream starts)."""
        return IosDeviceHmiSource(
            executable=self.executable,
            udid=self._resolve_udid,
            usbmuxd=self._usbmuxd_address,
            control=self.control,
            display_id=self.display_id,
            visible_size=self.visible_size,
            hid_space=self.hid_space,
            hid_swap_xy=self.hid_swap_xy,
            hid_flip_x=self.hid_flip_x,
            hid_flip_y=self.hid_flip_y,
            extra_args=self.extra_args,
            stop_grace_s=self.stop_grace_s,
            restart_backoff_s=self.restart_backoff_s,
            restart_backoff_max_s=self.restart_backoff_max_s,
            log=self.logger,
        )

    def reset(self) -> None:
        """A new lease: what an earlier one left running is ended, and its failures are forgotten."""
        super().reset()
        source = self._source
        if isinstance(source, IosDeviceHmiSource):
            source.forget_failures()


def _seconds(value: object, *, positive: bool = False) -> bool:
    return (
        isinstance(value, int | float)
        and not isinstance(value, bool)
        and math.isfinite(value)
        and (value > 0 if positive else value >= 0)
    )


#: The options a ``hmi:`` mapping of the device driver may carry: the fields of the HMI, but the ones the driver
#: owns.
HMI_OPTIONS = frozenset(f.name for f in fields(IosDeviceHmi) if f.init) - _RESERVED_FIELDS


def build_hmi(device: IosDevice, options: dict[str, Any]) -> IosDeviceHmi:
    """The hmi child of ``device`` for its ``hmi:`` option.

    Raises:
        ConfigurationError: If ``options`` is not a mapping of known options, or one of them is wrong.
    """
    if not isinstance(options, dict):
        raise ConfigurationError("hmi must be a mapping of options (an empty one enables the HMI)")
    unknown = sorted(set(options) - HMI_OPTIONS, key=str)
    if unknown:
        raise ConfigurationError(
            f"unknown hmi options: {', '.join(map(str, unknown))} (known: {', '.join(sorted(HMI_OPTIONS))})"
        )
    return IosDeviceHmi(device=device, **options)
