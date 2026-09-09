import asyncio
import subprocess
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from .client import (
    ADB_CONNECT_TIMEOUT,
    ADB_DISCONNECT_TIMEOUT,
    AdbClient,
    AdbDeviceClient,
    _adb_connect,
    _wait_for_interrupt,
)

# ------------------------------------------------------------- `adb connect`
#
# `adb connect` exits 0 even when it fails, printing the reason to STDOUT. Verified
# against adb 1.0.41: a refused port, an unresolvable host and an out-of-range port
# all return 0. So the exit status cannot be used to tell whether a device attached.
#
# This guards the ONE adb invocation left in the client. Jumpstarter no longer wraps
# the adb CLI; `attach` runs `adb connect` and nothing else.


def _completed(stdout, returncode=0):
    return subprocess.CompletedProcess(args=["adb", "connect", "x"], returncode=returncode, stdout=stdout, stderr="")


@pytest.mark.parametrize(
    "output",
    [
        "failed to connect to '127.0.0.1:59999': Connection refused",
        "failed to connect to 127.0.0.1:15055",
        "failed to resolve host: 'nope.invalid': nodename nor servname provided",
        "bad port number '99999' in '127.0.0.1:99999'",
        "cannot connect to daemon at tcp:127.0.0.1:5037: Connection refused",
        "",
    ],
)
def test_a_failed_connect_is_detected_despite_exit_zero(output):
    """This is the whole point: rc=0 with a failure message on stdout."""
    with patch("subprocess.run", return_value=_completed(output, returncode=0)):
        with pytest.raises(RuntimeError, match="did not connect"):
            _adb_connect("adb", "127.0.0.1:59999")


@pytest.mark.parametrize(
    "output",
    ["connected to 127.0.0.1:16000", "already connected to 127.0.0.1:16000"],
)
def test_a_successful_connect_is_accepted(output):
    """adb's only two success strings: `connected to %s`, `already connected to %s`."""
    with patch("subprocess.run", return_value=_completed(output)):
        assert _adb_connect("adb", "127.0.0.1:16000") == output


def test_a_hung_connect_raises_rather_than_blocking():
    with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("adb connect", 60)):
        with pytest.raises(RuntimeError, match="failed"):
            _adb_connect("adb", "127.0.0.1:16000")


# --------------------------------------------------------- attach and endpoint
#
# The client's whole job: expose the device's adbd locally, and optionally run one
# `adb connect`. Anything more would be wrapping the adb CLI, which is what this
# design deliberately does not do.

TARGET = "127.0.0.1:41000"


@contextmanager
def _fake_endpoint(_client, host="127.0.0.1", port=0):
    """Stand in for the port-forward, yielding a fixed local address."""
    yield TARGET


def _device_client():
    """An AdbDeviceClient with its transport stubbed out."""
    client = MagicMock(spec=AdbDeviceClient)
    client.endpoint = lambda **kwargs: _fake_endpoint(client, **kwargs)
    client.logger = MagicMock()
    return client


def test_attach_runs_exactly_one_adb_connect_and_one_disconnect():
    """A regression here is how the CLI wrapper creeps back in.

    Jumpstarter's contribution is the endpoint; the single `adb connect` exists only
    because adding a device to a server the client already owns *is* the feature.
    """
    client = _device_client()
    with patch("subprocess.run", return_value=_completed("connected to " + TARGET)) as run:
        with AdbDeviceClient.attach(client) as target:
            assert target == TARGET
            connects = [c.args[0] for c in run.call_args_list]
            assert connects == [["adb", "connect", TARGET]]
        argvs = [c.args[0] for c in run.call_args_list]

    assert argvs == [["adb", "connect", TARGET], ["adb", "disconnect", TARGET]]


def test_local_adb_timeouts_are_bounded_and_overridable():
    """The local `adb connect` timeout is a CLIENT setting, not the exporter's.

    It bounds a command on the developer's machine against a local port-forward, so
    it is deliberately separate from the driver's `connect_timeout`. Both calls must
    be bounded, or a wedged local adb hangs the session (connect) or teardown
    (disconnect).
    """
    client = _device_client()

    # Default: the documented client-side constant, not the driver's connect_timeout.
    with patch("subprocess.run", return_value=_completed("connected to " + TARGET)) as run:
        with AdbDeviceClient.attach(client):
            pass
    defaults = {c.args[0][1]: c.kwargs["timeout"] for c in run.call_args_list}
    assert defaults["connect"] == ADB_CONNECT_TIMEOUT
    assert defaults["disconnect"] == ADB_DISCONNECT_TIMEOUT

    # ...and overridable per call.
    with patch("subprocess.run", return_value=_completed("connected to " + TARGET)) as run:
        with AdbDeviceClient.attach(client, timeout=5):
            pass
    timeouts = {c.args[0][1]: c.kwargs["timeout"] for c in run.call_args_list}
    assert timeouts["connect"] == 5, "attach(timeout=...) must reach adb connect"
    assert all(t and t > 0 for t in timeouts.values()), timeouts


def test_attach_honours_a_custom_adb_path():
    """`--adb` locates the binary for that one call; nothing else shells out."""
    client = _device_client()
    with patch("subprocess.run", return_value=_completed("connected to " + TARGET)) as run:
        with AdbDeviceClient.attach(client, adb="/opt/sdk/adb"):
            pass
    assert [c.args[0][0] for c in run.call_args_list] == ["/opt/sdk/adb", "/opt/sdk/adb"]


def test_attach_disconnects_even_when_the_body_raises():
    """Otherwise a crash leaves a stale `offline` entry in the developer's server."""
    client = _device_client()
    with patch("subprocess.run", return_value=_completed("connected to " + TARGET)) as run:
        with pytest.raises(ValueError):
            with AdbDeviceClient.attach(client):
                raise ValueError("boom")
    assert ["adb", "disconnect", TARGET] in [c.args[0] for c in run.call_args_list]


def test_a_failed_disconnect_does_not_mask_the_session():
    """Teardown is best-effort: a hung local adb must not turn into a raised error."""
    client = _device_client()

    def run(argv, **kwargs):
        if argv[1] == "disconnect":
            raise subprocess.TimeoutExpired("adb disconnect", 30)
        return _completed("connected to " + TARGET)

    with patch("subprocess.run", side_effect=run):
        with AdbDeviceClient.attach(client) as target:
            assert target == TARGET


def test_attach_does_not_run_adb_when_the_connect_fails():
    """No disconnect for a device that never attached, and the error propagates."""
    client = _device_client()
    with patch("subprocess.run", return_value=_completed("failed to connect to " + TARGET)) as run:
        with pytest.raises(RuntimeError, match="did not connect"):
            with AdbDeviceClient.attach(client):
                pass
    assert [c.args[0] for c in run.call_args_list] == [["adb", "connect", TARGET]]


def test_endpoint_runs_no_adb_at_all():
    """The honest primitive: Jumpstarter moves bytes, the user drives adb."""
    client = MagicMock(spec=AdbDeviceClient)
    forwarded = MagicMock()
    forwarded.__enter__ = MagicMock(return_value=("127.0.0.1", 41000))
    forwarded.__exit__ = MagicMock(return_value=False)

    with (
        patch("jumpstarter_driver_adb.client.TcpPortforwardAdapter", return_value=forwarded),
        patch("subprocess.run", side_effect=AssertionError("endpoint must not run adb")) as run,
    ):
        with AdbDeviceClient.endpoint(client) as target:
            assert target == TARGET
    run.assert_not_called()


# ------------------------------------------------- waiting inside the event loop
#
# The wait must return rather than propagate, or Ctrl+C leaves a stale `adb connect`
# entry behind and a second Ctrl+C hangs in threading._shutdown.


class _Portal:
    def __init__(self, raises):
        self._raises = raises

    def call(self, *args, **kwargs):
        raise self._raises


@pytest.mark.parametrize(
    "exc",
    [KeyboardInterrupt(), SystemExit(), GeneratorExit(), RuntimeError("portal is closed")],
)
def test_an_interrupt_ends_the_wait_without_propagating(exc):
    client = MagicMock(portal=_Portal(exc))
    _wait_for_interrupt(client)  # must return, so teardown can run


def test_anyio_cancellation_ends_the_wait():
    """Cancellation is a BaseException, not an Exception, so it needs its own arm.

    Deliberately synchronous and with no event loop: these waits run in a worker
    thread, and `get_cancelled_exc_class()` in the except arm used to raise
    NoEventLoopError there, masking the cancellation it was meant to detect.
    """
    client = MagicMock(portal=_Portal(asyncio.CancelledError()))
    _wait_for_interrupt(client)


def test_an_unexpected_error_is_not_swallowed():
    """A real bug must surface, not look like a clean Ctrl+C."""
    client = MagicMock(portal=_Portal(ValueError("something else")))
    with pytest.raises(ValueError):
        _wait_for_interrupt(client)


def test_ctrl_c_during_attach_still_detaches():
    """The end-to-end teardown path: interrupt the hold, and the device is released."""
    client = _device_client()
    client.portal = _Portal(KeyboardInterrupt())

    with patch("subprocess.run", return_value=_completed("connected to " + TARGET)) as run:
        with AdbDeviceClient.attach(client) as target:
            _wait_for_interrupt(client)  # returns, as a real Ctrl+C would
            assert target == TARGET
    assert ["adb", "disconnect", TARGET] in [c.args[0] for c in run.call_args_list]


# ------------------------------------------------------------------ CLI surface
#
# The CLI is the user-facing contract documented in the README, so it is worth
# pinning: which commands exist, that they run the adb calls they claim to, and that
# `endpoint` runs none.


def _cli_device_client():
    """A device client whose transport is stubbed, for driving its CLI."""
    client = MagicMock(spec=AdbDeviceClient)
    client.endpoint = lambda **kwargs: _fake_endpoint(client, **kwargs)
    client.attach = lambda **kwargs: AdbDeviceClient.attach(client, **kwargs)
    client.info = lambda: {"transport": "usb", "selector": "usb:1-4.2", "present": "yes"}
    client.logger = MagicMock()
    client.portal = _Portal(KeyboardInterrupt())
    return client


def test_device_cli_exposes_only_attach_endpoint_info():
    """No `shell`, `install`, `logcat` — Jumpstarter does not wrap the adb CLI."""
    group = AdbDeviceClient.cli(_cli_device_client())
    assert sorted(group.commands) == ["attach", "endpoint", "info"]


def test_device_cli_info_prints_the_fields():
    from click.testing import CliRunner

    group = AdbDeviceClient.cli(_cli_device_client())
    result = CliRunner().invoke(group, ["info"])
    assert result.exit_code == 0, result.output
    assert "transport: usb" in result.output
    assert "selector: usb:1-4.2" in result.output


def test_device_cli_attach_connects_and_tells_you_how_to_use_it():
    from click.testing import CliRunner

    client = _cli_device_client()
    with patch("subprocess.run", return_value=_completed("connected to " + TARGET)) as run:
        result = CliRunner().invoke(group := AdbDeviceClient.cli(client), ["attach"])
    assert group is not None
    assert result.exit_code == 0, result.output
    assert TARGET in result.output
    # It must tell the user to drive their own adb, since we no longer proxy it.
    assert f"adb -s {TARGET} shell" in result.output
    assert "detached" in result.output
    argvs = [c.args[0] for c in run.call_args_list]
    assert argvs == [["adb", "connect", TARGET], ["adb", "disconnect", TARGET]]


def test_device_cli_endpoint_prints_the_address_and_runs_no_adb():
    from click.testing import CliRunner

    client = _cli_device_client()
    with patch("subprocess.run", side_effect=AssertionError("endpoint must not run adb")):
        result = CliRunner().invoke(AdbDeviceClient.cli(client), ["endpoint"])
    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[0] == TARGET
    assert f"adb connect {TARGET}" in result.output


def _cli_server_client():
    """A server client with its tunnel stubbed, for driving its CLI."""
    client = MagicMock(spec=AdbClient)
    client.list_devices = lambda: "List of devices attached\nHVA1234567\tdevice usb:1-4.2\n"
    client.forward_adb = MagicMock()
    client.forward_adb.return_value.__enter__ = MagicMock(return_value=("127.0.0.1", 54321))
    client.forward_adb.return_value.__exit__ = MagicMock(return_value=False)
    client.portal = _Portal(KeyboardInterrupt())
    return client


def test_server_cli_exposes_only_devices_and_tunnel():
    group = AdbClient.cli(_cli_server_client())
    assert sorted(group.commands) == ["devices", "tunnel"]


def test_server_cli_devices_lists_them():
    from click.testing import CliRunner

    result = CliRunner().invoke(AdbClient.cli(_cli_server_client()), ["devices"])
    assert result.exit_code == 0, result.output
    assert "HVA1234567" in result.output


def test_server_cli_tunnel_prints_the_env_vars_to_export():
    """The tunnel's whole purpose: hand the user variables for their own tooling."""
    from click.testing import CliRunner

    client = _cli_server_client()
    result = CliRunner().invoke(AdbClient.cli(client), ["tunnel"])
    assert result.exit_code == 0, result.output
    assert "ANDROID_ADB_SERVER_ADDRESS=127.0.0.1" in result.output
    assert "ANDROID_ADB_SERVER_PORT=54321" in result.output


# --------------------------------------------------------- AdbClient call wiring


def test_server_client_methods_map_to_driver_calls():
    """Thin wrappers, but cuttlefish and androidemulator depend on these names."""
    client = MagicMock(spec=AdbClient)
    client.call = MagicMock(return_value="ok")

    assert AdbClient.start_server(client) == "ok"
    assert AdbClient.kill_server(client) == "ok"
    assert AdbClient.connect_device(client, "10.0.0.5:5555") == "ok"
    assert AdbClient.disconnect_device(client, "10.0.0.5:5555") == "ok"
    assert AdbClient.list_devices(client) == "ok"

    assert [c.args for c in client.call.call_args_list] == [
        ("start_server",),
        ("kill_server",),
        ("connect_device", "10.0.0.5:5555"),
        ("disconnect_device", "10.0.0.5:5555"),
        ("list_devices",),
    ]


@pytest.mark.parametrize(
    ("output", "expected"),
    [
        ("List of devices attached\nA\tdevice\nB\toffline\n", ["A"]),
        ("List of devices attached\nA\tunauthorized\n", []),
        ("List of devices attached\n", []),
        ("* daemon started *\nList of devices attached\nA\tdevice usb:1-1\n", ["A"]),
        ("", []),
    ],
)
def test_only_forwardable_devices_are_listed(output, expected):
    """`offline`/`unauthorized` cannot be forwarded, and adb's noise lines are not devices."""
    client = MagicMock(spec=AdbClient)
    client.list_devices = lambda: output
    assert AdbClient.devices(client) == expected


def test_forward_adb_yields_the_local_listener():
    client = MagicMock(spec=AdbClient)
    forwarded = MagicMock()
    forwarded.__enter__ = MagicMock(return_value=("127.0.0.1", 54321))
    forwarded.__exit__ = MagicMock(return_value=False)
    with patch("jumpstarter_driver_adb.client.TcpPortforwardAdapter", return_value=forwarded):
        with AdbClient.forward_adb(client, port=0) as addr:
            assert addr == ("127.0.0.1", 54321)
