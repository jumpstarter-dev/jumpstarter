"""Test fixtures: a stateful fake ``fastboot`` binary with fault injection.

The fake keeps device state in a JSON file (``FAKE_FASTBOOT_STATE``) so the
driver, the detached runner process, and the test all see the same device.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

import pytest

FAKE_FASTBOOT = r'''#!__PYTHON__ -S
import fcntl, hashlib, json, os, sys, time

STATE = os.environ["FAKE_FASTBOOT_STATE"]

def load():
    fd = os.open(STATE + ".lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    with open(STATE) as f:
        return fd, json.load(f)

def save(fd, state):
    tmp = STATE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(state, f)
    os.replace(tmp, STATE)
    fcntl.flock(fd, fcntl.LOCK_UN)
    os.close(fd)

def out(*lines):
    for line in lines:
        sys.stderr.write(line + "\n")
    sys.stderr.flush()

def present(dev):
    return dev["present"] and time.time() >= dev.get("vanish_until", 0)

def consume(state, table, key):
    left = state.get(table, {}).get(key, 0)
    if left:
        state[table][key] = left - 1
        return True
    return False

args = sys.argv[1:]
selector = None
if args[:1] == ["-s"]:
    selector, args = args[1], args[2:]

fd, state = load()
dev = state["device"]
swap = dev.get("swap_serial_after_writes")
serial_now = "OTHER" if swap is not None and len(state["written"]) >= swap else dev["serial"]

if args == ["devices", "-l"]:
    if present(dev):
        sys.stdout.write(f"{serial_now}\t fastboot {dev['usb']}\n")
    for extra in state.get("other_devices", []):
        sys.stdout.write(f"{extra[0]}\t fastboot {extra[1]}\n")
    save(fd, state)
    sys.exit(0)

state["selectors"] = state.get("selectors", []) + [selector]
if selector != serial_now or not present(dev):
    save(fd, state)
    out(f"< waiting for {selector} >")
    time.sleep(3600)
    sys.exit(1)

cmd = args[0]
key = cmd + ":" + (args[1] if len(args) > 1 else "")
state["commands"].append(" ".join(args))

if consume(state, "vanish", key):
    dev["vanish_until"] = time.time() + state.get("vanish_seconds", 2)
    save(fd, state)
    out("FAILED (Write to device failed (No such device))")
    sys.exit(1)
if consume(state, "fail", key):
    save(fd, state)
    out("FAILED (remote: 'injected failure')")
    sys.exit(1)
if consume(state, "hang", key):
    save(fd, state)
    time.sleep(3600)
    sys.exit(1)

userspace = dev["mode"] == "userspace"
parts = state["partitions"]

def var(name):
    v = state["vars"]
    if name == "is-userspace":
        return "yes" if userspace else "no"
    if name == "serialno":
        return serial_now
    if name.startswith("has-slot:"):
        p = name.split(":", 1)[1]
        return "yes" if p + "_a" in parts else "no"
    if name.startswith("partition-size:"):
        p = name.split(":", 1)[1]
        return hex(parts[p]) if p in parts else None
    if name.startswith("is-logical:"):
        p = name.split(":", 1)[1]
        return "yes" if p in state["logical"] else "no"
    return v.get(name)

if cmd == "getvar":
    value = var(args[1])
    save(fd, state)
    if value is None:
        out(f"getvar:{args[1]} FAILED (remote: 'GetVar Variable Not found')")
        sys.exit(1)
    out(f"{args[1]}: {value}", "Finished. Total time: 0.001s")
    sys.exit(0)

if cmd == "flash":
    p, path = args[1], args[2]
    if p not in parts:
        save(fd, state); out(f"FAILED (remote: 'Partition {p} not found')"); sys.exit(1)
    if (p in state["logical"]) != userspace:
        save(fd, state); out(f"FAILED (remote: 'wrong fastboot mode for {p}')"); sys.exit(1)
    data = open(path, "rb").read()
    delay = state.get("flash_delay", 0)
    save(fd, state)
    out(f"Sending '{p}' ({len(data) // 1024} KB)    OKAY [  0.01s]")
    time.sleep(delay)
    fd, state = load()
    state["written"].append([p, hashlib.sha256(data).hexdigest()])
    save(fd, state)
    out(f"Writing '{p}'    OKAY [  0.01s]", "Finished. Total time: 0.02s")
    sys.exit(0)

if cmd == "erase":
    state["erased"].append(args[1]); save(fd, state); out(f"Erasing '{args[1]}'    OKAY"); sys.exit(0)
if cmd == "set_active":
    state["vars"]["current-slot"] = args[1]
    save(fd, state); out(f"Setting current slot to '{args[1]}'    OKAY"); sys.exit(0)
if cmd == "reboot":
    target = args[1] if len(args) > 1 else None
    if target == "bootloader":
        dev["mode"] = "bootloader"
    elif target == "fastboot":
        dev["mode"] = "userspace"
    else:
        dev["mode"] = "android"; dev["present"] = False
    save(fd, state); out("Rebooting    OKAY"); sys.exit(0)
if cmd == "continue":
    dev["mode"] = "android"; dev["present"] = False; save(fd, state); out("Resuming boot    OKAY"); sys.exit(0)
if cmd == "oem":
    save(fd, state); out("OKAY"); sys.exit(0)

save(fd, state)
out(f"unknown command {cmd}")
sys.exit(1)
'''


class FakeDevice:
    def __init__(self, path: Path, binary: str):
        self.path = path
        self.binary = binary

    @contextmanager
    def edit(self):
        fd = os.open(str(self.path) + ".lock", os.O_RDWR | os.O_CREAT)
        fcntl.flock(fd, fcntl.LOCK_EX)
        try:
            state = json.loads(self.path.read_text())
            yield state
            self.path.write_text(json.dumps(state))
        finally:
            fcntl.flock(fd, fcntl.LOCK_UN)
            os.close(fd)

    @property
    def state(self) -> dict:
        return json.loads(self.path.read_text())

    def written(self) -> list[str]:
        return [p for p, _ in self.state["written"]]

    def inject(self, table: str, key: str, count: int = 1) -> None:
        with self.edit() as state:
            state.setdefault(table, {})[key] = count


MIB = 1024 * 1024


@pytest.fixture
def fake_fastboot(tmp_path, monkeypatch):
    state_file = tmp_path / "fake-state.json"
    state_file.write_text(
        json.dumps(
            {
                "device": {"serial": "SER123", "usb": "usb:1-2", "present": True, "mode": "bootloader"},
                "vars": {"product": "testdev", "slot-count": "2", "current-slot": "a", "unlocked": "yes"},
                "partitions": {
                    "abl_a": 4 * MIB,
                    "abl_b": 4 * MIB,
                    "boot_a": 64 * MIB,
                    "boot_b": 64 * MIB,
                    "vbmeta_a": MIB,
                    "vbmeta_b": MIB,
                    "system_a": 256 * MIB,
                    "system_b": 256 * MIB,
                    "userdata": 512 * MIB,
                    "cdt": MIB,
                    "misc": MIB,
                },
                "logical": ["system_a", "system_b"],
                "written": [],
                "erased": [],
                "commands": [],
                "fail": {},
                "hang": {},
                "vanish": {},
            }
        )
    )
    binary = tmp_path / "fastboot"
    binary.write_text(FAKE_FASTBOOT.replace("__PYTHON__", sys.executable))
    binary.chmod(0o755)
    monkeypatch.setenv("FAKE_FASTBOOT_STATE", str(state_file))
    return FakeDevice(state_file, str(binary))


def wait_for(predicate, timeout: float = 30.0, interval: float = 0.1):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("condition not met in time")


@pytest.fixture(autouse=True)
def _no_live_progress(monkeypatch):
    # The core upload progress bar (rich) stops only when its stream is garbage
    # collected; a failed upload's traceback keeps it alive, and the next test's
    # bar then can't start. Plain logging output instead.
    monkeypatch.setenv("TERM", "dumb")
