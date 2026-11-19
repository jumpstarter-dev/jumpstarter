"""Hotplug simulation: no real GPIO access, including during failure cleanup."""

import errno
from types import SimpleNamespace
from typing import cast
from unittest.mock import MagicMock

import pytest

from jumpstarter_driver_copilot.driver import CoPilotGPIO, CoPilotOutput

gpiod = pytest.importorskip("gpiod")


@pytest.fixture
def usb(monkeypatch):
    state = SimpleNamespace(node=1, present=True, chips=[], events=[])

    def identity(path):
        if not state.present:
            raise FileNotFoundError(errno.ENOENT, "CoPilot unplugged")
        return (1, state.node, 3)

    def chip_factory(path):
        chip = MagicMock()
        request = chip.request_lines.return_value
        request.get_values.return_value = [gpiod.line.Value.INACTIVE] * 3
        request.get_value.return_value = gpiod.line.Value.INACTIVE
        request.set_value.side_effect = lambda line, value: state.events.append((len(state.chips), line, value))
        state.chips.append(chip)
        return chip

    monkeypatch.setattr(CoPilotGPIO, "_path_identity", staticmethod(identity))
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", chip_factory)
    state.controller = CoPilotGPIO(device="/dev/copilot/by-id/selected/gpiochip", managed_lines=[1, 2, 3])
    return state


def test_replug_idle_reopens_once_and_initializes_inactive(usb):
    usb.node = 2
    assert usb.controller.read_line(1) is False
    assert len(usb.chips) == 2
    usb.chips[0].request_lines.return_value.release.assert_called_once()
    usb.chips[0].close.assert_called_once()
    settings = next(iter(usb.chips[1].request_lines.call_args.kwargs["config"].values()))
    assert settings.output_value == gpiod.line.Value.INACTIVE
    assert usb.controller.info()["connected"] is True
    assert usb.controller.info()["connection_generation"] == 2
    assert len(usb.chips) == 2


def test_absent_info_is_truthful_and_new_commands_recover_after_plug(usb):
    usb.present = False
    info = usb.controller.info()
    assert info["connected"] is False
    assert "unplugged" in info["last_error"]
    with pytest.raises(FileNotFoundError):
        usb.controller.set_line(1, True)
    usb.present = True
    usb.node = 2
    usb.controller.set_line(1, True)
    usb.chips[1].request_lines.return_value.set_value.assert_called_once_with(1, gpiod.line.Value.ACTIVE)


@pytest.mark.parametrize("error", [errno.ENODEV, errno.EIO, errno.ENXIO, errno.EBADF])
def test_invalid_request_detected_even_if_node_number_reused(usb, error):
    usb.chips[0].request_lines.return_value.get_values.side_effect = OSError(error, "removed")
    assert usb.controller.read_line(1) is False
    assert len(usb.chips) == 2


def test_permission_errors_not_treated_as_usb_loss(usb):
    usb.chips[0].request_lines.return_value.get_values.side_effect = PermissionError(errno.EACCES, "denied")
    with pytest.raises(PermissionError):
        usb.controller.read_line(1)
    assert len(usb.chips) == 1
    usb.chips[0].close.assert_not_called()


def test_write_failure_is_not_replayed(usb):
    request = usb.chips[0].request_lines.return_value
    request.set_value.side_effect = OSError(errno.ENODEV, "lost after write")
    with pytest.raises(OSError):
        usb.controller.set_line(1, True)
    # Cleanup may attempt OFF but must never repeat ON.
    assert sum(call.args[1] == gpiod.line.Value.ACTIVE for call in request.set_value.call_args_list) == 1
    assert len(usb.chips) == 1
    info = usb.controller.info()
    assert info["connected"] and info["interrupted_lines"] == [1]
    with pytest.raises(RuntimeError, match="disconnected during an action"):
        usb.controller.set_line(3, True)
    usb.chips[1].request_lines.return_value.set_value.assert_not_called()
    usb.controller.reconnect()
    usb.controller.set_line(3, True)  # Explicit new action after acknowledgement.


def test_pulse_interrupted_during_sleep_is_not_success(usb, monkeypatch):
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.time.sleep", lambda _: setattr(usb, "node", 2))
    with pytest.raises(RuntimeError, match="action interrupted"):
        usb.controller.pulse(1, 1)
    new_request = usb.chips[1].request_lines.return_value
    assert all(call.args[1] == gpiod.line.Value.INACTIVE for call in new_request.set_value.call_args_list)
    assert usb.controller.info()["interrupted_lines"] == []


def test_replug_during_multi_button_gesture_does_not_press_reset(usb):
    controller = usb.controller
    controller.set_line(2, True)
    usb.node = 2
    # A status read must not consume the interruption and let a sequence continue.
    assert controller.info()["interrupted_lines"] == [2]
    with pytest.raises(RuntimeError, match="disconnected during an action"):
        CoPilotOutput(line=3, children={"controller": controller}).on()
    with pytest.raises(RuntimeError, match="action interrupted"):
        controller.set_line(2, False)
    assert controller.info()["interrupted_lines"] == []


def test_reconnect_never_restores_initial_active_outputs(usb):
    usb.controller.initial_active = True
    usb.node = 2
    usb.controller.info()
    settings = next(iter(usb.chips[1].request_lines.call_args.kwargs["config"].values()))
    assert settings.output_value == gpiod.line.Value.INACTIVE


def test_close_after_unplug_does_not_reopen(usb):
    usb.present = False
    usb.controller.close()
    usb.controller.close()
    assert len(usb.chips) == 1
    assert not usb.controller.info()["connected"]


def test_request_failure_after_replug_closes_new_chip_and_can_retry(usb, monkeypatch):
    failing = MagicMock()
    failing.request_lines.side_effect = OSError(errno.EBUSY, "another owner")
    usb.node = 2
    original = gpiod.Chip
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", lambda _: failing)
    with pytest.raises(OSError, match="another owner"):
        usb.controller.read_line(1)
    failing.close.assert_called_once()
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", original)
    assert usb.controller.read_line(1) is False


def test_auto_selection_stays_on_original_serial(monkeypatch):
    from pathlib import Path

    paths = [Path("/dev/copilot/by-id/ORIGINAL/gpiochip")]
    monkeypatch.setattr(Path, "glob", lambda self, pattern: paths)
    monkeypatch.setattr(Path, "exists", lambda self: str(self) in [str(p) for p in paths])
    monkeypatch.setattr(CoPilotGPIO, "_path_identity", staticmethod(lambda path: (1, 2, 3)))
    monkeypatch.setattr("jumpstarter_driver_copilot.driver.gpiod.Chip", lambda path: MagicMock())
    controller = CoPilotGPIO()
    assert controller.serial == "ORIGINAL"
    paths[:] = [Path("/dev/copilot/by-id/OTHER/gpiochip")]
    with pytest.raises(FileNotFoundError, match="ORIGINAL"):
        controller.reconnect()


def test_node_changes_during_open_does_not_keep_request(usb, monkeypatch):
    identities = iter([(1, 2, 3), (1, 3, 3)])
    monkeypatch.setattr(CoPilotGPIO, "_path_identity", staticmethod(lambda path: next(identities)))
    with pytest.raises(OSError, match="changed during GPIO acquisition"):
        usb.controller.reconnect()
    usb.chips[1].request_lines.return_value.release.assert_called_once()
    usb.chips[1].close.assert_called_once()
    assert usb.controller._request is None


def test_concurrent_reads_reconnect_only_once(usb):
    from concurrent.futures import ThreadPoolExecutor

    usb.node = 2
    with ThreadPoolExecutor(max_workers=8) as pool:
        assert list(pool.map(usb.controller.read_line, [1] * 16)) == [False] * 16
    assert len(usb.chips) == 2


def test_cli_reconnect_delegates_to_client():
    from click.testing import CliRunner

    from jumpstarter_driver_copilot.client import CoPilotClient

    calls = []
    fake = SimpleNamespace(
        description=None, methods_description={}, reconnect=lambda: calls.append("reconnect") or {"connected": True}
    )
    result = CliRunner().invoke(CoPilotClient.cli(cast(CoPilotClient, fake)), ["reconnect"])
    assert result.exit_code == 0
    assert calls == ["reconnect"]


def test_rpc_integral_numbers_are_normalized_before_gpiod(usb):
    usb.controller.hats = [{"type": "triple-relay", "relay_lines": [1, 2, 3]}]
    assert usb.controller.read_relay(1.0) is False
    request = usb.chips[0].request_lines.return_value
    assert type(request.get_value.call_args.args[0]) is int
    usb.controller.set_line(2.0, False)
    assert type(request.set_value.call_args.args[0]) is int


@pytest.mark.parametrize("value", [1.5, float("nan"), float("inf"), True, "1"])
def test_rpc_nonintegral_indices_rejected_without_write(usb, value):
    with pytest.raises(ValueError, match="integer"):
        usb.controller.set_line(value, True)
    usb.chips[0].request_lines.return_value.set_value.assert_not_called()


@pytest.mark.parametrize("error", [errno.EINVAL, errno.EINTR, errno.EAGAIN, None])
def test_unrelated_write_error_keeps_request(usb, error):
    usb.controller.set_line(2, True)
    request = usb.chips[0].request_lines.return_value
    request.set_value.side_effect = OSError(error, "transient")
    with pytest.raises(OSError, match="transient"):
        usb.controller.set_line(1, True)
    # The still-valid request is kept: no release, no forced OFF, no interruption.
    request.release.assert_not_called()
    assert (2, gpiod.line.Value.INACTIVE) not in [call.args for call in request.set_value.call_args_list]
    info = usb.controller.info()
    assert info["connected"] and info["interrupted_lines"] == [] and info["connection_generation"] == 1
    request.set_value.side_effect = None
    usb.controller.set_line(3, True)  # No reconnect acknowledgement required.


@pytest.mark.parametrize("error, lost", [(errno.EINVAL, False), (errno.EIO, True)])
def test_read_error_disposes_only_on_usb_loss(usb, error, lost):
    request = usb.chips[0].request_lines.return_value
    request.get_value.side_effect = OSError(error, "read failed")
    with pytest.raises(OSError, match="read failed"):
        usb.controller.read_line(1)
    assert request.release.called is lost
