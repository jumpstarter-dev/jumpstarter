"""Firmware older than 0.08 has no control interface: power and storage must say so.

These tests mock USB and udev, so they run without a DUTLink board.
"""

import logging
from contextlib import contextmanager
from unittest.mock import MagicMock, patch

import pytest

from jumpstarter_driver_dutlink.driver import Dutlink, DutlinkPower, DutlinkStorageMux

SERIAL = "e6058905"


def fake_board(bcd_device):
    dev = MagicMock()
    dev.bcdDevice = bcd_device
    return dev


@contextmanager
def board(dev, itf):
    tty = MagicMock(device_node="loop://")
    with (
        patch("usb.core.find", return_value=[dev]),
        patch("usb.util.get_string", return_value=SERIAL),
        patch("usb.util.find_descriptor", return_value=itf),
        patch("pyudev.Context") as context,
    ):
        context.return_value.list_devices.return_value = [tty]
        yield


def test_power_without_control_interface_names_the_firmware():
    dev = fake_board(0x0007)
    with board(dev, itf=None):
        power = DutlinkPower()
        with pytest.raises(
            RuntimeError,
            match=r"firmware 0\.07\): power and storage control need firmware 0\.08",
        ):
            power.on()
    dev.ctrl_transfer.assert_not_called()


def test_storage_without_control_interface_names_the_firmware():
    dev = fake_board(0x0007)
    with board(dev, itf=None):
        storage = DutlinkStorageMux(storage_device="/dev/null")
        with pytest.raises(RuntimeError, match=r"firmware 0\.07\)"):
            storage.off()
    dev.ctrl_transfer.assert_not_called()


def test_composite_without_control_interface_warns_and_keeps_the_console(caplog):
    dev = fake_board(0x0007)
    with board(dev, itf=None), caplog.at_level(logging.WARNING):
        instance = Dutlink(storage_device="/dev/null")
    assert "console" in instance.children
    assert "need firmware 0.08 or later" in caplog.text


def test_power_with_control_interface_sends_the_vendor_request():
    dev = fake_board(0x0009)
    with board(dev, itf=MagicMock(bInterfaceNumber=3)):
        DutlinkPower().on()
    dev.ctrl_transfer.assert_called_once()
    kwargs = dev.ctrl_transfer.call_args.kwargs
    assert (kwargs["wIndex"], kwargs["bRequest"], kwargs["wValue"]) == (3, 0x01, 1)
