import bz2
import functools
import gzip
import http.server
import json
import lzma
import sys
import threading
from contextlib import contextmanager
from unittest.mock import MagicMock, PropertyMock

import click
import pytest
from esptool.logger import log
from jumpstarter_driver_opendal.client import FlasherClient
from jumpstarter_driver_pyserial.driver import PySerial

from .driver import Esp32Flasher, _parse_region
from jumpstarter.client.core import DriverInvalidArgument
from jumpstarter.client.flasher import FlashPhase
from jumpstarter.common.exceptions import ArgumentError
from jumpstarter.common.utils import serve
from jumpstarter.streams.encoding import Compression

if sys.version_info >= (3, 14):
    from compression import zstd
else:  # pragma: no cover
    from backports import zstd


class _MockEsp:
    CHIP_NAME = "ESP32-D0WD-V3"
    IS_STUB = False

    def __init__(self):
        self.bauds = []

    def change_baud(self, baud):
        self.bauds.append(baud)

    def get_chip_description(self):
        return "ESP32-D0WD-V3 (revision v3.1)"

    def get_chip_features(self):
        return ["Wi-Fi", "BT", "Dual Core"]

    def read_mac(self):
        return [0x5C, 0x01, 0x3B, 0x68, 0xAB, 0x0C]

    def run_stub(self):
        self.IS_STUB = True
        return self

    def hard_reset(self):
        pass


def _make_driver(driver_class=None, **config):
    serial = PySerial(url="loop://", check_present=False)
    return (driver_class or Esp32Flasher)(children={"serial": serial}, **config)


def _images(tmp_path, images: dict[str, bytes]) -> dict[str, object]:
    """Write each image to a file and map its address to that file."""
    mapping = {}
    for address, data in images.items():
        path = tmp_path / f"{address}.bin"
        path.write_bytes(data)
        mapping[address] = path
    return mapping


def _written(mock_esptool) -> list[tuple[int, bytes]]:
    """Every (address, data) handed to esptool's write_flash, in order."""
    return [entry for call in mock_esptool["write_flash"].call_args_list for entry in call.args[1]]


@pytest.fixture()
def mock_esptool(monkeypatch):
    mock_esp = _MockEsp()
    mocks: dict = {
        "detect_chip": MagicMock(return_value=mock_esp),
        "write_flash": MagicMock(),
        "read_flash": MagicMock(),
        "erase_flash": MagicMock(),
    }
    for name, mock in mocks.items():
        monkeypatch.setattr(f"esptool.cmds.{name}", mock)
    mocks["esp"] = mock_esp
    return mocks


def test_get_chip_info(mock_esptool):
    with serve(_make_driver()) as client:
        info = client.get_chip_info()
        assert "ESP32" in info["chip"]
        assert "Wi-Fi" in info["features"]
        assert info["mac"] == "5c:01:3b:68:ab:0c"


def test_flash(mock_esptool, tmp_path):
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"\xde\xad" * 512)

    with serve(_make_driver()) as client:
        client.flash(firmware, target="0x10000")

    mock_esptool["write_flash"].assert_called_once()
    _, flash_entries = mock_esptool["write_flash"].call_args[0]
    assert flash_entries[0][0] == 0x10000


def test_flash_default_address(mock_esptool, tmp_path):
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"\x00" * 64)

    with serve(_make_driver()) as client:
        client.flash(firmware)

    _, flash_entries = mock_esptool["write_flash"].call_args[0]
    assert flash_entries[0][0] == 0x0


@pytest.mark.parametrize(("flash_baudrate", "bauds"), [(921600, [921600]), (57600, [57600])])
def test_flash_baudrate(mock_esptool, tmp_path, flash_baudrate, bauds):
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"\x00" * 64)

    with serve(_make_driver(flash_baudrate=flash_baudrate)) as client:
        client.flash(firmware)

    assert mock_esptool["esp"].bauds == bauds


def test_flash_images_in_one_session(mock_esptool, tmp_path):
    images = {"0x1000": b"boot" * 10, "0x8000": b"table", "0x10000": b"app" * 100}

    with serve(_make_driver()) as client:
        client.flash(_images(tmp_path, images))

    assert mock_esptool["detect_chip"].call_count == 1
    assert _written(mock_esptool) == [(int(address, 0), data) for address, data in images.items()]


def test_flash_esp_idf_build(mock_esptool, tmp_path):
    (tmp_path / "bootloader").mkdir()
    (tmp_path / "bootloader" / "bootloader.bin").write_bytes(b"boot")
    (tmp_path / "app.bin").write_bytes(b"app")
    (tmp_path / "flasher_args.json").write_text(
        json.dumps({"flash_files": {"0x2000": "bootloader/bootloader.bin", "0x20000": "app.bin"}})
    )

    with serve(_make_driver()) as client:
        client.flash(tmp_path)

    assert _written(mock_esptool) == [(0x2000, b"boot"), (0x20000, b"app")]


@pytest.mark.parametrize(
    ("flasher_args", "reason"),
    [(None, "No such file"), ("{not json", "Expecting property name"), ("{}", "'flash_files'")],
)
def test_flash_esp_idf_build_without_flash_files(mock_esptool, tmp_path, flasher_args, reason):
    if flasher_args is not None:
        (tmp_path / "flasher_args.json").write_text(flasher_args)

    with serve(_make_driver()) as client, pytest.raises(ArgumentError, match=f"flasher_args.json: .*{reason}"):
        client.flash(tmp_path)

    assert not mock_esptool["detect_chip"].called


@contextmanager
def _http_server(directory):
    """Serve ``directory`` over HTTP, yielding its base URL."""
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=directory)
    with http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler) as server:
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            yield f"http://127.0.0.1:{server.server_port}"
        finally:
            server.shutdown()


def test_flash_url(mock_esptool, tmp_path):
    """The exporter downloads a URL in the same session as the uploaded files around it."""
    images = _images(tmp_path, {"0x1000": b"boot", "0x10000": b"app", "0x200000": b"data"})

    with _http_server(tmp_path) as base, serve(_make_driver()) as client:
        client.flash({**images, "0x10000": f"{base}/0x10000.bin?token=x"})

    assert _written(mock_esptool) == [(0x1000, b"boot"), (0x10000, b"app"), (0x200000, b"data")]


_COMPRESSORS = {
    Compression.GZIP: gzip.compress,
    Compression.XZ: lzma.compress,
    Compression.BZ2: bz2.compress,
    Compression.ZSTD: zstd.compress,
}


@pytest.mark.parametrize("compression", list(Compression))
def test_flash_compressed(mock_esptool, tmp_path, compression):
    """Every image is decompressed on its own, uploaded or downloaded."""
    images = {"0x1000": b"boot" * 100, "0x10000": b"app" * 1000, "0x200000": b"data" * 10}
    compressed = _images(tmp_path, {address: _COMPRESSORS[compression](data) for address, data in images.items()})

    with _http_server(tmp_path) as base, serve(_make_driver()) as client:
        client.flash({**compressed, "0x10000": f"{base}/0x10000.bin"}, compression=compression)

    assert _written(mock_esptool) == [(int(address, 0), data) for address, data in images.items()]


@pytest.mark.parametrize(("kwargs", "skip_flashed"), [({}, True), ({"skip_unchanged": False}, False)])
def test_flash_skip_unchanged(mock_esptool, tmp_path, kwargs, skip_flashed):
    with serve(_make_driver()) as client:
        client.flash(_images(tmp_path, {"0x0": b"\x00" * 64}), **kwargs)

    assert mock_esptool["write_flash"].call_args.kwargs["skip_flashed"] is skip_flashed


def _write_only(*addresses):
    """A write_flash that writes, and reports progress for, only these addresses.

    esptool reports no progress for an image it skips as already flashed.
    """

    def fake_write_flash(esp, addr_data, **kwargs):
        address, data = addr_data[0]
        if address in addresses:
            log.progress_bar(0, len(data))
            log.progress_bar(len(data), len(data))

    return fake_write_flash


def test_flash_stream_reports_progress(mock_esptool, tmp_path):
    mock_esptool["write_flash"].side_effect = _write_only(0x1000)

    with serve(_make_driver()) as client:
        statuses = list(client.flash_stream(_images(tmp_path, {"0x1000": b"a" * 64, "0x2000": b"b" * 64})))

    assert statuses[0].message == "Connected to ESP32-D0WD-V3 (revision v3.1)"
    assert [(s.step_index, s.step_name, s.message, s.progress) for s in statuses[1:-1]] == [
        (1, "0x1000.bin @ 0x1000", "writing", 0.0),
        (1, "0x1000.bin @ 0x1000", "writing", 1.0),
        (2, "0x2000.bin @ 0x2000", "unchanged, not rewritten", None),
    ]
    assert statuses[-1].phase == FlashPhase.COMPLETE
    assert statuses[-1].message == "Flashed 2 image(s): 1 written, 1 unchanged"


@pytest.mark.parametrize(("first_size", "refused"), [(0x1000, False), (0x1001, True)])
def test_flash_refuses_images_sharing_a_sector(mock_esptool, tmp_path, first_size, refused):
    images = _images(tmp_path, {"0x1000": b"a" * first_size, "0x2000": b"b"})

    with serve(_make_driver()) as client:
        if refused:
            with pytest.raises(BaseExceptionGroup) as excinfo:
                client.flash(images)
            assert excinfo.group_contains(
                DriverInvalidArgument, match=r"^0x2000.bin @ 0x2000 overlaps 0x1000.bin @ 0x1000$"
            )
        else:
            client.flash(images)

    assert mock_esptool["detect_chip"].called is not refused


def test_flash_refuses_an_empty_image(mock_esptool, tmp_path):
    with serve(_make_driver()) as client, pytest.raises(BaseExceptionGroup) as excinfo:
        client.flash(_images(tmp_path, {"0x1000": b"boot", "0x8000": b""}))

    assert excinfo.group_contains(DriverInvalidArgument, match=r"^0x8000.bin @ 0x8000 is empty$")
    assert not mock_esptool["detect_chip"].called


def test_flash_on_exporter_without_flash_images(mock_esptool, tmp_path):
    """An exporter from before flash_images (jumpstarter 0.9) still gets every image."""

    class OldEsp32Flasher(Esp32Flasher):
        flash_images = None  # not exported, as in 0.9

    with serve(_make_driver(OldEsp32Flasher)) as client:
        client.flash(_images(tmp_path, {"0x1000": b"boot", "0x8000": b"table"}))

    assert _written(mock_esptool) == [(0x1000, b"boot"), (0x8000, b"table")]


def test_flash_from_client_without_flash_images(mock_esptool, tmp_path):
    """A client from before flash_images (jumpstarter 0.9) still flashes, through the unary call."""
    firmware = tmp_path / "firmware.bin"
    firmware.write_bytes(b"app")

    with serve(_make_driver()) as client:
        FlasherClient.flash(client, firmware, target="0x10000")

    assert _written(mock_esptool) == [(0x10000, b"app")]


def test_cli_flash_targets(mock_esptool, tmp_path, monkeypatch):
    # Collect what the command prints. Reading stdout back is racy here:
    # log_cli swaps sys.stdout whenever the exporter thread logs.
    printed = []
    monkeypatch.setattr(click, "echo", lambda message=None, **kwargs: printed.append(message))
    mock_esptool["write_flash"].side_effect = _write_only(0x1000)
    images = _images(tmp_path, {"0x1000": b"boot", "0x8000": b"table"})
    args = ["flash"] + [arg for address, path in images.items() for arg in ("-t", f"{address}:{path}")]

    with serve(_make_driver()) as client:
        client.cli().main(args, standalone_mode=False)

    assert _written(mock_esptool) == [(0x1000, b"boot"), (0x8000, b"table")]
    assert printed == [
        "Connected to ESP32-D0WD-V3 (revision v3.1)",
        "[1/2] 0x1000.bin @ 0x1000: writing 0%",
        "[1/2] 0x1000.bin @ 0x1000: writing 100%",
        "[2/2] 0x8000.bin @ 0x8000: unchanged, not rewritten",
        "Flashed 2 image(s): 1 written, 1 unchanged",
    ]


def test_cli_flash_repeated_target(mock_esptool, tmp_path):
    first, second = tmp_path / "a.bin", tmp_path / "b.bin"
    first.write_bytes(b"a")
    second.write_bytes(b"b")
    args = ["flash", "-t", f"0x1000:{first}", "-t", f"0x1000:{second}"]

    with serve(_make_driver()) as client, pytest.raises(click.UsageError, match="0x1000 is given more than once"):
        client.cli().main(args, standalone_mode=False)

    mock_esptool["write_flash"].assert_not_called()


def test_dump(mock_esptool, tmp_path):
    test_data = b"flash content"

    def fake_read_flash(esp, address, size, filename):
        with open(filename, "wb") as f:
            f.write(test_data)

    mock_esptool["read_flash"].side_effect = fake_read_flash

    output = tmp_path / "dump.bin"
    with serve(_make_driver()) as client:
        client.dump(output, target="0x0:0x1000")

    assert output.read_bytes() == test_data


def test_erase(mock_esptool):
    with serve(_make_driver()) as client:
        client.erase()

    mock_esptool["erase_flash"].assert_called_once()


def _make_mock_serial():
    """Create a mock serial with tracked .dtr and .rts property assignments."""
    mock_serial = MagicMock()
    dtr_prop = PropertyMock()
    rts_prop = PropertyMock()
    type(mock_serial).dtr = dtr_prop
    type(mock_serial).rts = rts_prop
    return mock_serial, dtr_prop, rts_prop


def test_hard_reset(mock_esptool, monkeypatch):
    mock_serial, dtr_prop, rts_prop = _make_mock_serial()
    monkeypatch.setattr(
        "jumpstarter_driver_pyserial.driver.serial_for_url",
        MagicMock(return_value=mock_serial),
    )

    with serve(_make_driver()) as client:
        client.hard_reset()

    dtr_calls = [c.args[0] for c in dtr_prop.call_args_list]
    rts_calls = [c.args[0] for c in rts_prop.call_args_list]
    assert dtr_calls == [False]
    assert rts_calls == [True, False]


def test_enter_bootloader(mock_esptool, monkeypatch):
    mock_serial, dtr_prop, rts_prop = _make_mock_serial()
    monkeypatch.setattr(
        "jumpstarter_driver_pyserial.driver.serial_for_url",
        MagicMock(return_value=mock_serial),
    )

    with serve(_make_driver()) as client:
        client.enter_bootloader()

    dtr_calls = [c.args[0] for c in dtr_prop.call_args_list]
    rts_calls = [c.args[0] for c in rts_prop.call_args_list]
    assert dtr_calls == [False, True, False]
    assert rts_calls == [True, False]


def test_parse_region_default():
    assert _parse_region(None) == (0x0, 0x400000)


def test_parse_region_address_only():
    assert _parse_region("0x1000") == (0x1000, 0x400000)


def test_parse_region_address_and_size():
    assert _parse_region("0x1000:0x2000") == (0x1000, 0x2000)


def test_parse_region_decimal():
    assert _parse_region("4096:8192") == (4096, 8192)


def test_connect_esp_calls_close_before_detect_chip(mock_esptool):
    """_connect_esp() must call serial.close() before esptool.detect_chip()
    to release any active serial stream that would lock the port.
    """
    call_order = []

    serial = PySerial(url="loop://", check_present=False)
    original_close = serial.close

    def tracking_close():
        call_order.append("close")
        original_close()

    serial.close = tracking_close

    def tracking_detect_chip(**kwargs):
        call_order.append("detect_chip")
        return _MockEsp()

    mock_esptool["detect_chip"].side_effect = tracking_detect_chip

    driver = Esp32Flasher(children={"serial": serial})
    driver._connect_esp()

    assert call_order == ["close", "detect_chip"]


def test_connect_esp_clears_traceback_on_failure(mock_esptool):
    """If detect_chip fails, the exception traceback is cleared so esptool's
    internal serial port references are freed.
    """
    import pytest

    mock_esptool["detect_chip"].side_effect = RuntimeError("connection failed")

    serial = PySerial(url="loop://", check_present=False)
    driver = Esp32Flasher(children={"serial": serial})

    with pytest.raises(RuntimeError, match="connection failed") as exc_info:
        driver._connect_esp()

    assert exc_info.value.__traceback__ is not None
    # The original deep frames from detect_chip are not in the traceback -
    # only the re-raise site and above
    tb = exc_info.value.__traceback__
    frame_names = []
    while tb is not None:
        frame_names.append(tb.tb_frame.f_code.co_name)
        tb = tb.tb_next
    assert "detect_chip" not in frame_names
