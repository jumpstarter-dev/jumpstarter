"""How a device is pinned and addressed: USB port, serial, or TCP address."""

import time

import pytest

from .fastboot import Fastboot, FastbootError, normalize_address, normalize_usb_port


@pytest.mark.parametrize(("address", "selector"), [
    ("192.168.1.5", "tcp:192.168.1.5:5554"),  # fastboot's default port, written out
    ("192.168.1.5:5555", "tcp:192.168.1.5:5555"),
    ("tcp:192.168.1.5", "tcp:192.168.1.5:5554"),
    ("tcp:192.168.1.5:15554", "tcp:192.168.1.5:15554"),
    ("  bench-pi.local:5554 ", "tcp:bench-pi.local:5554"),
    ("127.0.0.1:15554", "tcp:127.0.0.1:15554"),  # an `adb forward`ed fastbootd
    ("[::1]", "tcp:[::1]:5554"),
    ("tcp:[fe80::1]:5555", "tcp:[fe80::1]:5555"),
])
def test_addresses_are_normalized_to_a_tcp_selector(address, selector):
    assert normalize_address(address) == selector
    assert normalize_address(selector) == selector  # idempotent: the normalized form is a valid input


@pytest.mark.parametrize(("address", "error"), [
    ("", "must not be empty"),
    ("tcp:", "must not be empty"),
    (":5554", "has no host"),
    ("host:", "invalid port"),
    ("host:http", "invalid port"),
    ("host:0", "invalid port"),
    ("host:70000", "invalid port"),
    ("udp:host:5554", "TCP only"),
    ("::1", "put it in brackets"),
    ("fe80::1:5554", "put it in brackets"),
    ("[::1", "is not .ipv6"),
    ("[::1]5554", "is not .ipv6"),
    ("[]:5554", "is not .ipv6"),
])
def test_bad_addresses_are_refused(address, error):
    with pytest.raises(ValueError, match=error):
        normalize_address(address)


def test_a_device_is_pinned_by_exactly_one_of_port_serial_address():
    for pins in ({}, {"usb_port": "1-2", "serial": "S"}, {"usb_port": "1-2", "address": "h"},
                 {"serial": "S", "address": "h"}, {"usb_port": "1-2", "serial": "S", "address": "h"}):
        with pytest.raises(ValueError, match="exactly one of 'usb_port'.*'serial' or 'address'"):
            Fastboot(**pins)
    assert Fastboot(usb_port="1-2").label == normalize_usb_port("1-2") == "usb:1-2"
    assert Fastboot(serial="SER").label == "SER"
    assert Fastboot(address="10.0.0.9").label == "tcp:10.0.0.9:5554"
    with pytest.raises(ValueError, match="invalid port"):
        Fastboot(address="10.0.0.9:x")


async def test_a_tcp_device_is_addressed_by_its_selector_without_listing_devices(tmp_path):
    """`fastboot devices` never shows a network device, so nothing may depend on finding it there."""
    binary = tmp_path / "fastboot"
    binary.write_text("#!/bin/sh\necho \"$@\" >> " + str(tmp_path / "calls") + "\n"
                      "case \"$*\" in *'getvar version'*) echo 'version: 0.4' >&2;; esac\n")
    binary.chmod(0o755)
    fb = Fastboot(address="127.0.0.1:15554", binary=str(binary), probe_timeout=5)
    assert await fb.resolve() == "tcp:127.0.0.1:15554"
    assert await fb.present()
    assert await fb.getvar("version") == "0.4"
    calls = (tmp_path / "calls").read_text().splitlines()
    assert calls and all(call.startswith("-s tcp:127.0.0.1:15554 ") for call in calls)  # never `devices -l`


async def test_a_refused_address_is_reported_at_once_with_fastboots_reason(tmp_path):
    """fastboot prints why, then waits forever: the probe must not wait with it."""
    binary = tmp_path / "fastboot"
    binary.write_text("#!/bin/sh\necho 'error: Failed to connect to 127.0.0.1:1: Connection refused' >&2\n"
                      "echo '< waiting for tcp:127.0.0.1:1>' >&2\nexec sleep 30\n")
    binary.chmod(0o755)
    fb = Fastboot(address="127.0.0.1:1", binary=str(binary), probe_timeout=20)
    started = time.monotonic()
    assert not await fb.present()
    refused = r"no fastboot device at tcp:127.0.0.1:1 \(.*Connection refused.*check the forward"
    with pytest.raises(FastbootError, match=refused):
        await fb.resolve()
    assert not await fb.wait_present(0)
    assert time.monotonic() - started < 10  # three probes, none of them waited for probe_timeout


async def test_an_address_that_never_answers_is_bounded_by_the_probe_timeout(tmp_path):
    binary = tmp_path / "fastboot"
    binary.write_text("#!/bin/sh\nexec sleep 30\n")  # a blackholed host: no output at all
    binary.chmod(0o755)
    fb = Fastboot(address="10.255.255.1", binary=str(binary), probe_timeout=0.5)
    with pytest.raises(FastbootError, match=r"no fastboot device at tcp:10.255.255.1:5554 \(.*timed out after 0.5s"):
        await fb.resolve()


async def test_a_bootloader_that_refuses_the_probe_is_still_there(tmp_path):
    """An error answered by the device (`remote:`) proves fastboot is on the other end."""
    binary = tmp_path / "fastboot"
    binary.write_text("#!/bin/sh\necho \"getvar:version FAILED (remote: 'GetVar Variable Not found')\" >&2\nexit 1\n")
    binary.chmod(0o755)
    assert await Fastboot(address="h", binary=str(binary)).present()


async def test_a_failing_fastboot_is_not_mistaken_for_a_device(tmp_path):
    binary = tmp_path / "fastboot"
    binary.write_text("#!/bin/sh\necho 'error: invalid address' >&2\nexit 1\n")
    binary.chmod(0o755)
    with pytest.raises(FastbootError, match="invalid address"):
        await Fastboot(address="h", binary=str(binary)).resolve()
    with pytest.raises(FastbootError, match="fastboot binary not found"):
        await Fastboot(address="h", binary=str(tmp_path / "missing")).resolve()
