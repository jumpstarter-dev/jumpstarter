import os
import tempfile

import pytest
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_driver_pyserial.driver import PySerial

from .driver import BaseFlasher
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.common.utils import serve


@pytest.fixture(scope="session")
def temp_dirs():
    with tempfile.TemporaryDirectory() as temp_dir:
        cache = os.path.join(temp_dir, "cache")
        http = os.path.join(temp_dir, "http")
        tftp = os.path.join(temp_dir, "tftp")
        os.mkdir(cache)
        os.mkdir(http)
        os.mkdir(tftp)
        yield cache, http, tftp


@pytest.fixture(scope="session")  # session to retain cache over time
def complete_flasher(temp_dirs):
    cache, http, tftp = temp_dirs
    yield BaseFlasher(
        cache_dir=cache,
        http_dir=http,
        tftp_dir=tftp,
        children={
            "serial": PySerial(url="loop://"),
            "power": MockPower(),
        },
    )


def test_missing_serial(temp_dirs):
    cache, http, tftp = temp_dirs
    with pytest.raises(ConfigurationError):
        BaseFlasher(cache_dir=cache, http_dir=http, tftp_dir=tftp, children={"power": MockPower()})


def test_missing_power(temp_dirs):
    cache, http, tftp = temp_dirs
    with pytest.raises(ConfigurationError):
        BaseFlasher(cache_dir=cache, http_dir=http, tftp_dir=tftp, children={"serial": PySerial(url="loop://")})


def test_drivers_flashers_setup_flasher_bundle(complete_flasher):
    with serve(complete_flasher) as client:
        client.call("setup_flasher_bundle")
        dtb = client.call("get_dtb_filename")
        kernel = client.call("get_kernel_filename")
        initram = client.call("get_initram_filename")
        assert client.tftp.storage.read_bytes(kernel) == b"\x00" * 1024
        assert client.tftp.storage.read_bytes(initram) == b"\x00" * 1024 * 2
        assert client.tftp.storage.read_bytes(dtb) == b"\x00" * 1024 * 3


def test_drivers_flashers_manifest(complete_flasher):
    with serve(complete_flasher) as client:
        assert client.manifest.spec.kernel.file == "data/kernel"


def test_drivers_flashers_dtb_switching(complete_flasher):
    with serve(complete_flasher) as client:
        assert client.call("get_dtb_filename") == "test-dtb.dtb"
        client.call("use_dtb_variant", "alternate")
        assert client.call("get_dtb_filename") == "alternate.dtb"
        client.call("use_dtb_variant", "test-dtb")
        assert client.call("get_dtb_filename") == "test-dtb.dtb"
        # verify dtb variant switching to nonexisting
        with pytest.raises(ValueError):
            client.call("use_dtb_variant", "noexists")


def test_drivers_flashers_filenames(complete_flasher):
    with serve(complete_flasher) as client:
        assert client.call("get_dtb_filename") == "test-dtb.dtb"
        assert client.call("get_kernel_filename") == "kernel"
        assert client.call("get_initram_filename") == "initramfs"


def test_drivers_flashers_addresses(complete_flasher):
    with serve(complete_flasher) as client:
        assert client.call("get_kernel_address") == "0x82000000"
        assert client.call("get_initram_address") == "0x83000000"
        assert client.call("get_dtb_address") == "0x84000000"
