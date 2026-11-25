import anyio
import pytest

from .android import ASK_MODE, SCRIPT, SNOOP_MODE, AndroidBluetooth, parse_state
from .capture import REDACTED
from .capture_test import ACL, CONNECTED, DISCONNECTED, LINK_KEY, RECEIVED, RESET, RESET_DONE, SENT, btsnoop
from .identity import MARK
from .identity_test import MANAGER
from jumpstarter.common.utils import serve

# What `dumpsys bluetooth_manager` goes on to say of an adapter that is on: the manager's own lines (as in the
# identity tests), then the adapter's properties as AOSP's AdapterProperties prints them.
ADAPTER = """
Bluetooth crashed 0 times

BluetoothManagerService:
  mEnable:true
  mQuietEnable:false

AdapterProperties
  Name: Pixel 9
  Address: XX:XX:XX:XX:02:03
  ConnectionState: STATE_CONNECTED
  State: ON
  MaxConnectedAudioDevices: 5
  A2dpOffloadEnabled: false
  Discovering: false
  DiscoveryEndMs: 0
  Bonded devices:
    XX:XX:XX:XX:17:01 [BR/EDR][ 0x200408 ] Head Unit
    XX:XX:XX:XX:AB:CD [ DUAL ][ 0x240404 ] Kirk's headphones
    XX:XX:XX:XX:11:22 => XX:XX:XX:XX:33:44 [  LE  ][ 0x000000 ] Tracker

AdapterState
  Transitions:
"""
# The same of a device with Android 17, as a Cuttlefish phone that is paired with a head unit said it: the
# headings have their counts, a device says whether it is linked now, and there are the devices it only met.
ANDROID_17 = """Bluetooth Status:
  State:         ON
  Address:       XX:XX:XX:XX:00:00
  Name:          Cuttlefish x86_64 phone 64-bit only
  Inner app:     com.android.bluetooth

AdapterProperties
  Name: Cuttlefish x86_64 phone 64-bit only
  Address: XX:XX:XX:XX:00:00
  ConnectionState: STATE_CONNECTED
  MaxConnectedAudioDevices: 5
  A2dpOffloadEnabled: true
  Discovering: false
  DiscoveryEndMs: 1795568858446

BluetoothRemoteDevices
  Bonded devices: 2
    XX:XX:XX:XX:00:01(Public ) => XX:XX:XX:XX:00:01(Public ) [BR/EDR] [0x260420] [Pairing Algorithm BR/EDR: 2 LE: N/A] [ACL BR/EDR:Y LE:N] [ Encryption status(BR/EDR): EncryptionStatus{keySize=16, algorithm=1] LE: N/A] Cuttlefish x86_64 auto 64-bit only
        [BR/EDR UUIDs]: 0000110a-0000-1000-8000-00805f9b34fb 0000110b-0000-1000-8000-00805f9b34fb
    XX:XX:XX:XX:AB:CD(Public ) => XX:XX:XX:XX:AB:CD(Public ) [ DUAL ] [0x240404] [Pairing Algorithm BR/EDR: 2 LE: N/A] [ACL BR/EDR:N LE:N] [ Encryption status(BR/EDR): N/A LE: N/A] Kirk's headphones

  Other devices: 2
    XX:XX:XX:XX:EF:01(Public ) => XX:XX:XX:XX:XX:XX(Unknown) [  LE  ] [0x001F00] [Pairing Algorithm BR/EDR: N/A LE: N/A] [ACL BR/EDR:N LE:Y] [ Encryption status(BR/EDR): N/A LE: N/A] A tag
    XX:XX:XX:XX:EF:02(Random ) => XX:XX:XX:XX:XX:XX(Unknown) [  LE  ] [0x001F00] [Pairing Algorithm BR/EDR: N/A LE: N/A] [ACL BR/EDR:N LE:N] [ Encryption status(BR/EDR): N/A LE: N/A]

BluetoothActiveDeviceManager
  Audio mode: 0
"""  # noqa: E501
OFF = "Bluetooth Status\n  enabled: false\n  state: OFF\n  address: 3C:5A:B4:01:02:03\n  name: Pixel 9\n"


def said(manager: str = MANAGER.split("AdapterProperties")[0] + ADAPTER, user: str = "10", snoop: str = "") -> str:
    answers = ["3C:5A:B4:01:02:03", "90,2,12", "Pixel 9", manager, user, snoop]
    return f"\n{MARK}\n".join(answers)


def test_what_a_device_says_of_its_bluetooth_is_its_state():
    state = parse_state(said())
    assert state["adapter"] == {
        "enabled": True,
        "state": "ON",
        "name": "Pixel 9",
        "address": "3C:5A:B4:01:02:03",
        "class_of_device": 0x5A020C,
        "connection_state": "CONNECTED",
        "discovering": False,
        "android_user": 10,  # (a car: its person is not the system user)
        "snoop_log_mode": None,
    }
    assert state["bonds"] == [
        {"address": "XX:XX:XX:XX:17:01", "type": "BR/EDR", "class_of_device": 0x200408, "name": "Head Unit"},
        {"address": "XX:XX:XX:XX:AB:CD", "type": "DUAL", "class_of_device": 0x240404, "name": "Kirk's headphones"},
        {
            "address": "XX:XX:XX:XX:11:22", "identity_address": "XX:XX:XX:XX:33:44", "type": "LE",
            "class_of_device": 0, "name": "Tracker",
        },
    ]  # fmt: skip
    assert state["connections"] is None  # it has a link, and does not say to whom


def test_what_a_device_does_not_say_is_not_known():
    # Bluetooth is off: there is no adapter to have bonds or links, and nobody can say there are none.
    off = parse_state(said(manager=OFF, user="0", snoop="full"))
    assert off["adapter"]["enabled"] is False and off["adapter"]["state"] == "OFF"
    assert off["adapter"]["connection_state"] is None and off["adapter"]["snoop_log_mode"] == "full"
    assert off["adapter"]["android_user"] == 0
    assert off["bonds"] is None and off["connections"] is None
    # On, with nothing bonded and nothing linked.
    alone = ADAPTER.replace("STATE_CONNECTED", "STATE_DISCONNECTED").split("    XX:")[0] + "\nAdapterState\n"
    state = parse_state(said(manager=MANAGER.split("AdapterProperties")[0] + alone))
    assert state["bonds"] == [] and state["connections"] == []
    nothing = parse_state("")
    assert nothing["adapter"]["enabled"] is None and nothing["adapter"]["name"] is None and nothing["bonds"] is None


def test_android_17_says_of_each_device_it_knows_whether_it_is_linked_now():
    state = parse_state(said(manager=ANDROID_17, user="0", snoop="full"))
    assert state["adapter"]["state"] == "ON" and state["adapter"]["name"] == "Cuttlefish x86_64 phone 64-bit only"
    head_unit = {
        "address": "XX:XX:XX:XX:00:01", "type": "BR/EDR", "class_of_device": 0x260420,
        "name": "Cuttlefish x86_64 auto 64-bit only",
    }  # fmt: skip
    assert state["bonds"] == [
        {**head_unit, "connected": True},
        {
            "address": "XX:XX:XX:XX:AB:CD", "type": "DUAL", "class_of_device": 0x240404,
            "name": "Kirk's headphones", "connected": False,
        },
    ]  # fmt: skip
    # Whom it is linked to: the bonded device that is, and a device it has only met.
    assert state["connections"] == [
        {**head_unit, "bonded": True},
        {"address": "XX:XX:XX:XX:EF:01", "type": "LE", "class_of_device": 0x1F00, "name": "A tag", "bonded": False},
    ]
    # Nothing bonded, nothing linked: said so, with the count.
    alone = ANDROID_17.split("  Bonded devices:")[0] + "  Bonded devices: 0\n\n  Other devices: 0\n\nBluetoothActive\n"
    state = parse_state(said(manager=alone.replace("STATE_CONNECTED", "STATE_DISCONNECTED")))
    assert state["bonds"] == [] and state["connections"] == []


class Device:
    """A stand-in for `adb`: an Android device with a Bluetooth stack that writes a snoop log."""

    def __init__(self) -> None:
        self.there = True
        self.on = True
        self.root = True  # a userdebug build
        self.mode = ""
        self.mode_is_roots = False  # Android 17: the shell user is not told the log's mode
        self.filters = False  # a userdebug build: with no mode set, the stack writes a filtered log
        self.reset_known = True
        self.logs: dict[str, bytes] = {}
        self.bonds = ADAPTER
        self.shells: list[str] = []
        self.started = 1_800_000_000.0

    def restart(self) -> None:
        """The stack starts: the log of the run before is put aside, and a new one is written (or none)."""
        log = self.log()
        if self.mode != "full" and not (self.mode == "" and self.filters):
            self.logs.clear()  # (a stack that starts with its log disabled deletes what there was)
            return
        before = self.logs.pop(log, None)
        self.logs.clear()  # (and the logs of another mode)
        if before is not None:
            self.logs[log + ".last"] = before
        self.started += 100
        self.logs[log] = btsnoop((SENT, RESET), (RECEIVED, RESET_DONE), start=self.started)

    def log(self) -> str:
        return "/data/misc/bluetooth/logs/btsnoop_hci.log" + ("" if self.mode == "full" else ".filtered")

    def say(self, *packets: tuple[int, bytes]) -> None:
        log = self.log()
        whole = btsnoop(*packets, start=self.started + 1)
        self.logs[log] = self.logs[log] + whole[16:]

    async def adb(self, arguments: list[str]) -> bytes:  # noqa: C901
        if not self.there:
            raise RuntimeError("device '127.0.0.1:6520' not found")
        if arguments[0] == "connect":
            return b"connected"
        _, _, verb, command = arguments
        if verb == "exec-out":
            return self.logs.get(command.split()[3], b"") if self.root else b""
        self.shells.append(command)
        if command.startswith("su 0 sh -c "):
            if not self.root:
                raise RuntimeError("/system/bin/sh: su: inaccessible or not found")
            command = command.removeprefix("su 0 sh -c ").strip("'")
            if command.startswith(f"setprop {SNOOP_MODE} "):
                self.mode = command.split()[2].strip('"')
            elif command == "cmd bluetooth_manager factoryReset":
                if not self.reset_known:
                    raise RuntimeError("Unknown command: factoryReset")
                # (the stack is stopped and started; the log's mode is emptied with everything else)
                self.bonds, self.mode = ADAPTER.split("    XX:")[0] + "\nAdapterState\n", ""
                self.restart()
            return b""
        if command == SCRIPT:
            manager = MANAGER.split("AdapterProperties")[0] + self.bonds if self.on else OFF
            return said(manager=manager, snoop=self.mode).encode()
        if command == ASK_MODE:
            return self.mode.encode() + b"\n"
        if command == f"getprop {SNOOP_MODE}":
            return b"\n" if self.mode_is_roots else self.mode.encode() + b"\n"
        if command.startswith("cmd bluetooth_manager disable"):
            self.on = False
        elif command.startswith("cmd bluetooth_manager enable"):
            self.on = True
            self.restart()
        return b""


def android(device: Device, **config) -> AndroidBluetooth:
    driver = AndroidBluetooth(adb="127.0.0.1:6520", poll=0.02, **config)
    driver._run = device.adb
    return driver


def test_the_device_is_asked_its_state_and_switched_as_its_settings_would():
    device = Device()
    with serve(android(device)) as client:
        assert client.capabilities() == ["state", "enable", "bonds", "capture", "packets"]
        assert client.ports() == []  # it joins nothing
        state = client.get_bluetooth_state()
        assert state["adapter"]["name"] == "Pixel 9" and state["adapter"]["enabled"] is True
        assert [bond["name"] for bond in state["bonds"]] == ["Head Unit", "Kirk's headphones", "Tracker"]
        assert state["link"] is None and "error" not in state
        assert client.status() == {
            "running": True, "attached": True, "peer": False, "capturing": False, "last_error": "",
        }  # fmt: skip

        off = client.set_enabled(False)
        assert off["adapter"]["enabled"] is False and off["bonds"] is None
        assert device.shells[-2] == "cmd bluetooth_manager disable; cmd bluetooth_manager wait-for-state:STATE_OFF"
        assert client.set_enabled(True)["adapter"]["enabled"] is True

        # What it cannot do is a link's to do, and is said so.
        with pytest.raises(NotImplementedError, match="'identity' is not supported by this driver .AndroidBluetooth"):
            client.identity()


def test_a_device_that_does_not_answer_is_said_so_not_made_up():
    device = Device()
    device.there = False
    with serve(android(device)) as client:
        state = client.get_bluetooth_state()
        assert state["adapter"] is None and state["bonds"] is None
        assert state["error"] == "device '127.0.0.1:6520' not found"
        assert client.status()["attached"] is False and client.status()["last_error"] == state["error"]
        device.there = True
        assert client.get_bluetooth_state()["adapter"]["name"] == "Pixel 9"
        assert client.status()["attached"] is True


def test_a_factory_reset_forgets_every_bond_and_switches_bluetooth_on_again():
    device = Device()
    with serve(android(device)) as client:
        state = client.factory_reset()
        assert state["bonds"] == [] and state["adapter"]["enabled"] is True
        assert "su 0 sh -c 'cmd bluetooth_manager factoryReset'" in device.shells
        # A device whose Bluetooth has no such command, or that is not a userdebug build, is left as it was.
        device.reset_known = False
        with pytest.raises(Exception, match="was not reset .*Unknown command: factoryReset"):
            client.factory_reset()
        device.root = False
        with pytest.raises(Exception, match="was not reset .*su: inaccessible"):
            client.factory_reset()


def test_the_hci_is_recorded_with_androids_own_snoop_log():
    device = Device()
    with serve(android(device)) as client:
        assert client.capture_status()["capturing"] is False and client.capture_status()["mode"] == ""
        with pytest.raises(Exception, match="no snoop log"):
            client.read_capture()

        # The log is off: it is set to full, and the stack is restarted to take that up.
        status = client.start_capture()
        assert status == {
            "capturing": True, "mode": "full", "changed_mode": True, "mode_before": "",
            "path": "/data/misc/bluetooth/logs/btsnoop_hci.log",
            "file": "/data/misc/bluetooth/logs/btsnoop_hci.log", "kept": 0,
        }  # fmt: skip
        assert device.shells[-4:-1] == [
            f"su 0 sh -c 'setprop {SNOOP_MODE} \"full\"'",
            "cmd bluetooth_manager disable; cmd bluetooth_manager wait-for-state:STATE_OFF",
            "cmd bluetooth_manager enable; cmd bluetooth_manager wait-for-state:STATE_ON",
        ]
        assert client.status()["capturing"] is True
        # What the stack said as it started is before the recording.
        assert client.read_packets() == []
        client.should_not_have_event("connection_complete")

        device.say((RECEIVED, CONNECTED), (RECEIVED, LINK_KEY), (SENT, ACL))
        connected = client.wait_for_event("connection_complete", timeout=5, bd_addr="06:05:04:03:02:01")
        assert connected["index"] == 2 and connected["fields"]["connection_handle"] == 1
        assert [packet["name"] for packet in client.read_packets()] == [
            "HCI_CONNECTION_COMPLETE_EVENT", "HCI_LINK_KEY_NOTIFICATION_EVENT", "ACL",
        ]  # fmt: skip
        assert client.read_packets("link_key_notification")[0]["fields"]["link_key"] == REDACTED
        assert [packet["index"] for packet in client.read_packets(since=connected["index"] + 1)] == [3, 4]
        with pytest.raises(AssertionError, match="HCI_CONNECTION_COMPLETE_EVENT"):
            client.should_not_have_event("connection_complete")
        client.should_not_have_event("disconnection_complete", within=0.1)
        with pytest.raises(TimeoutError, match="no disconnection_complete"):
            client.wait_for_event("disconnection_complete", timeout=0.1)

        # The file is the device's own, whole: a tool that reads btsnoop reads it.
        whole = client.read_capture()
        assert whole == device.logs["/data/misc/bluetooth/logs/btsnoop_hci.log"] and whole[:8] == b"btsnoop\x00"

        # Stopped: the log is taken first, and only then is the device put back as it was, which deletes it.
        device.say((RECEIVED, DISCONNECTED))
        stopped = client.stop_capture(restore=True)
        assert stopped["capturing"] is False and stopped["mode"] == "disabled" and stopped["changed_mode"] is False
        assert device.logs == {} and stopped["kept"] == len(whole) + 24 + len(DISCONNECTED)
        assert client.wait_for_event("disconnection_complete", timeout=1)["fields"]["reason"] == 0x13
        assert len(client.read_capture()) == stopped["kept"]


def test_a_device_that_already_records_is_not_restarted_and_its_log_is_marked(tmp_path):
    device = Device()
    device.mode = "full"
    device.restart()
    device.say((RECEIVED, CONNECTED))
    with serve(android(device)) as client:
        status = client.start_capture()
        assert status["changed_mode"] is False and not any("disable" in shell for shell in device.shells)
        assert client.read_packets() == []  # the link that was there is before the recording
        device.say((RECEIVED, CONNECTED), (RECEIVED, DISCONNECTED))
        assert [packet["index"] for packet in client.read_packets()] == [3, 4]

        # Its Bluetooth is restarted by somebody: the log is a new one, and all of it is the recording's.
        device.restart()
        assert [packet["name"] for packet in client.read_packets()] == [
            "HCI_RESET_COMMAND", "HCI_COMMAND_COMPLETE_EVENT",
        ]  # fmt: skip
        # ... and the one before is beside it.
        saved = tmp_path / "before.btsnoop"
        assert client.read_capture(str(saved), last=True) == saved.read_bytes() != client.read_capture()

        # Stopping leaves the mode alone unless it is asked to put it back, and there is nothing to put back.
        assert client.stop_capture()["mode"] == "full"
        assert client.stop_capture(restore=True)["mode"] == "full"


def test_the_logs_mode_is_asked_as_root_which_android_17_asks_for():
    device = Device()
    device.mode, device.mode_is_roots = "full", True
    device.restart()
    with serve(android(device)) as client:
        assert client.get_bluetooth_state()["adapter"]["snoop_log_mode"] == "full"
        # A device that records already is seen to: it is not restarted, and its links stay.
        status = client.start_capture()
        assert status["mode"] == "full" and status["changed_mode"] is False
        assert not any("disable" in shell for shell in device.shells)


def test_a_recording_goes_on_after_a_factory_reset_which_empties_the_logs_mode():
    device = Device()
    device.filters = True
    with serve(android(device)) as client:
        client.start_capture()
        device.say((RECEIVED, CONNECTED))
        assert [packet["name"] for packet in client.read_packets()] == ["HCI_CONNECTION_COMPLETE_EVENT"]

        # The driver's own reset sets the mode again, and the stack starts once more with it: a new log.
        assert client.factory_reset()["adapter"]["snoop_log_mode"] == "full"
        assert device.mode == "full" and list(device.logs) == ["/data/misc/bluetooth/logs/btsnoop_hci.log"]
        device.say((RECEIVED, CONNECTED), (RECEIVED, LINK_KEY))
        assert client.wait_for_event("link_key_notification", timeout=5)["index"] == 3
        assert client.capture_status()["file"] == "/data/misc/bluetooth/logs/btsnoop_hci.log"

        # A reset behind the driver (a suite's own `adb shell`): the stack writes its build's filtered log
        # under another name, and that is what there is to read. The status says so.
        await_reset = "su 0 sh -c 'cmd bluetooth_manager factoryReset'"
        anyio.run(device.adb, ["-s", "127.0.0.1:6520", "shell", await_reset])
        device.say((RECEIVED, DISCONNECTED))
        assert client.wait_for_event("disconnection_complete", timeout=5)["index"] == 2
        status = client.capture_status()
        assert status["mode"] == "" and status["file"] == "/data/misc/bluetooth/logs/btsnoop_hci.log.filtered"
        assert client.read_capture()[:8] == b"btsnoop\x00"
        assert client.stop_capture()["kept"] == len(device.logs[status["file"]])
