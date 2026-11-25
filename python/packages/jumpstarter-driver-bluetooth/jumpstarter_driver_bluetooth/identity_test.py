import anyio
import pytest

from .bridge import BluetoothBridge
from .identity import MARK, AdbIdentity, Reading, class_of_device, parse, source_from_config
from jumpstarter.exporter import Session

MANAGER = """Bluetooth Status
  enabled: true
  state: ON
  address: 3C:5A:B4:01:02:03
  name: Pixel 9

AdapterProperties
  Name: Pixel 9
  Address: 3C:5A:B4:01:02:03
"""


def said(address="3C:5A:B4:01:02:03", device_class="90,2,12", stored="Pixel 9", manager=MANAGER) -> str:
    return f"{address}\n{MARK}\n{device_class}\n{MARK}\n{stored}\n{MARK}\n{manager}"


def test_what_a_device_says_of_its_bluetooth_is_read_as_its_identity():
    assert parse(said()) == Reading(name="Pixel 9", address="3C:5A:B4:01:02:03", class_of_device=0x5A020C, on=True)
    assert class_of_device("32,4,8") == 0x200408
    assert class_of_device("") is None and class_of_device("nonsense") is None


def test_what_a_device_does_not_say_is_left_out_not_made_up():
    # Bluetooth is off: the manager has no adapter, and the stored name is what it will be called.
    off = parse(said(manager="Bluetooth Status\n  enabled: false\n  state: OFF\n"))
    assert off == Reading(name="Pixel 9", address="3C:5A:B4:01:02:03", class_of_device=0x5A020C, on=False)
    # A device that has never had Bluetooth on has stored nothing.
    assert parse(said(address="null", device_class="", stored="null", manager="")) == Reading()
    assert parse("") == Reading()


def test_the_source_is_named_in_the_exporters_configuration():
    assert source_from_config(None) is None
    source = source_from_config({"adb": "127.0.0.1:6520", "server_port": 5037, "interval": 5})
    assert isinstance(source, AdbIdentity)
    assert (source.serial, source.server_port, source.adb_path) == ("127.0.0.1:6520", 5037, "adb")
    with pytest.raises(ValueError, match="adb"):
        source_from_config({"serial": "x"})
    with pytest.raises(ValueError, match="not understood"):
        source_from_config({"adb": "x", "colour": "blue"})


class Device:
    """A stand-in for `adb`: a device that is off, boots, and can be renamed."""

    def __init__(self) -> None:
        self.booted = False
        self.name = "Pixel 9"
        self.on = True
        self.asked: list[list[str]] = []

    async def adb(self, arguments: list[str]) -> str:
        self.asked.append(arguments)
        if not self.booted:
            raise RuntimeError("device '127.0.0.1:6520' not found")
        if arguments[0] == "connect":
            return "connected"
        state = "ON" if self.on else "OFF"
        manager = f"  enabled: {str(self.on).lower()}\n  state: {state}\n" + (
            f"  Name: {self.name}\n" if self.on else ""
        )
        return said(stored=self.name, manager=manager)


@pytest.mark.anyio
async def test_a_device_over_tcp_is_connected_to_when_the_server_does_not_know_it():
    device = Device()
    source = AdbIdentity("127.0.0.1:6520", run=device.adb)
    with pytest.raises(RuntimeError, match="not found"):
        await source.read()
    assert [arguments[0] for arguments in device.asked] == ["-s", "connect"]
    device.booted = True
    assert (await source.read()).name == "Pixel 9"


async def status_when(radio, what, timeout: float = 5):
    with anyio.fail_after(timeout):
        while True:
            status = await radio.status()
            if what(status):
                return status
            await anyio.sleep(0.02)


@pytest.mark.anyio
async def test_the_radio_says_what_the_device_says_it_is_and_goes_on_asking():
    device = Device()
    radio = BluetoothBridge(
        name="Cuttlefish phone",
        class_of_device=0x001F00,
        identity_from={"adb": "127.0.0.1:6520", "interval": 0.05},
        autostart=False,
    )
    assert radio._source is not None
    radio._source._run = device.adb
    with Session(root_device=radio) as session:
        async with session.serve_unix_async():
            # The lease has started and the device is not even powered on: what the configuration says is
            # what is said, and the radio says that the device does not answer.
            status = await status_when(radio, lambda s: s["identity_source"]["error"])
            assert status["identity_source"] == {
                "kind": "adb",
                "ok": False,
                "error": "device '127.0.0.1:6520' not found",
            }
            assert status["device_up"] is None
            assert (await radio.identity())["name"] == "Cuttlefish phone"

            # It boots: its own name, address and class take the place of the configured ones.
            device.booted = True
            await status_when(radio, lambda s: s["identity_source"]["ok"])
            assert await radio.identity() == {
                "name": "Pixel 9", "address": "3C:5A:B4:01:02:03", "class_of_device": 0x5A020C,
            }  # fmt: skip
            assert (await radio.status())["device_up"] is True

            # Somebody renames it: that is noticed without a test.
            device.name = "Kirk's Pixel"
            with anyio.fail_after(5):
                while (await radio.identity())["name"] != "Kirk's Pixel":
                    await anyio.sleep(0.02)

            # Its Bluetooth is switched off: it is not called while it is.
            device.on = False
            await status_when(radio, lambda s: s["device_up"] is False)
            assert (await radio.identity())["name"] == "Kirk's Pixel"
            device.on = True
            await status_when(radio, lambda s: s["device_up"] is True)

            # What a test says outright stays said, whatever the device says; the rest is still the device's.
            await radio.set_identity(name="The phone under test")
            device.name = "Pixel 9"
            await anyio.sleep(0.3)
            assert await radio.identity() == {
                "name": "The phone under test", "address": "3C:5A:B4:01:02:03", "class_of_device": 0x5A020C,
            }  # fmt: skip
