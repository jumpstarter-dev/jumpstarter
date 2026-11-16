import os
import tempfile

import pytest
from jumpstarter_driver_composite.driver import Composite, Proxy
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_driver_pyserial.driver import PySerial
from jumpstarter_driver_tftp.driver import Tftp

from .driver import BaseFlasher
from jumpstarter.client.core import DriverError, DriverInvalidArgument
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
        flasher_bundle="quay.io/jumpstarter-dev/jumpstarter-flasher-test:new",
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


def test_drivers_flashers_exporter_ip_override(temp_dirs):
    """Test that exporter_ip is passed as advertised_host to auto-created tftp/http children"""
    cache, http, tftp = temp_dirs
    flasher = BaseFlasher(
        flasher_bundle="quay.io/jumpstarter-dev/jumpstarter-flasher-test:new",
        cache_dir=cache,
        http_dir=http,
        tftp_dir=tftp,
        exporter_ip="192.168.0.100",
        children={
            "serial": PySerial(url="loop://"),
            "power": MockPower(),
        },
    )
    with serve(flasher) as client:
        assert client.tftp.get_host() == "192.168.0.100"
        assert client.http.get_url().startswith("http://192.168.0.100:")

    # without exporter_ip, auto-created children advertise the detected bind host
    default = BaseFlasher(
        flasher_bundle="quay.io/jumpstarter-dev/jumpstarter-flasher-test:new",
        cache_dir=cache,
        http_dir=http,
        tftp_dir=tftp,
        children={
            "serial": PySerial(url="loop://"),
            "power": MockPower(),
        },
    )
    assert default.tftp.advertised_host is None
    assert default.http.advertised_host is None


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


def test_drivers_flashers_get_bootcmd_default(complete_flasher):
    """Test getting the default boot command"""
    with serve(complete_flasher) as client:
        bootcmd = client.call("get_bootcmd")
        assert bootcmd == "booti 0x82000000 - 0x84000000"


def test_drivers_flashers_get_bootcmd_with_dtb_variant(complete_flasher):
    """Test getting boot command with DTB variant that has custom bootcmd"""
    with serve(complete_flasher) as client:
        # Switch to variant with custom boot command
        client.call("use_dtb_variant", "othercmd")
        bootcmd = client.call("get_bootcmd")
        assert bootcmd == "bootm"


def test_drivers_flashers_get_bootcmd_with_dtb_variant_no_custom_cmd(complete_flasher):
    """Test getting boot command with DTB variant that has no custom bootcmd (should use default)"""
    with serve(complete_flasher) as client:
        # Switch to variant without custom boot command
        client.call("use_dtb_variant", "alternate")
        bootcmd = client.call("get_bootcmd")
        assert bootcmd == "booti 0x82000000 - 0x84000000"


def test_drivers_flashers_get_bootcmd_variant_switching(complete_flasher):
    """Test that boot command changes when switching between DTB variants"""
    with serve(complete_flasher) as client:
        # Start with default variant
        bootcmd = client.call("get_bootcmd")
        assert bootcmd == "booti 0x82000000 - 0x84000000"

        # Switch to variant with custom boot command
        client.call("use_dtb_variant", "othercmd")
        bootcmd = client.call("get_bootcmd")
        assert bootcmd == "bootm"

        # Switch back to default variant
        client.call("use_dtb_variant", "test-dtb")
        bootcmd = client.call("get_bootcmd")
        assert bootcmd == "booti 0x82000000 - 0x84000000"


def test_drivers_flashers_get_bootcmd_invalid_variant(complete_flasher):
    """Test that get_bootcmd raises DriverInvalidArgument for invalid DTB variant"""
    with serve(complete_flasher) as client, pytest.raises(DriverInvalidArgument):
        client.call("use_dtb_variant", "noexists")


def test_drivers_flashers_rejects_wrong_http_child(temp_dirs):
    cache, http, tftp = temp_dirs
    with pytest.raises(ConfigurationError, match="'http' child"):
        BaseFlasher(
            cache_dir=cache, http_dir=http, tftp_dir=tftp,
            children={"serial": PySerial(url="loop://"), "power": MockPower(), "http": MockPower()},
        )


def test_drivers_flashers_get_default_target(temp_dirs):
    cache, http, tftp = temp_dirs
    flasher = BaseFlasher(
        flasher_bundle="quay.io/jumpstarter-dev/jumpstarter-flasher-test:new",
        cache_dir=cache,
        http_dir=http,
        tftp_dir=tftp,
        default_target="emmc",
        children={
            "serial": PySerial(url="loop://"),
            "power": MockPower(),
        },
    )
    with serve(flasher) as client:
        assert client.call("get_default_target") == "emmc"


def test_drivers_flashers_get_cacert_not_configured(temp_dirs):
    cache, http, tftp = temp_dirs
    flasher = BaseFlasher(
        cache_dir=cache,
        http_dir=http,
        tftp_dir=tftp,
        children={
            "serial": PySerial(url="loop://"),
            "power": MockPower(),
        },
    )
    with serve(flasher) as client:
        assert client.call("get_cacert") is None


def test_drivers_flashers_get_cacert_configured(temp_dirs, tmp_path):
    cache, http, tftp = temp_dirs
    cacert_path = tmp_path / "ca.crt"
    cacert_path.write_text("-----BEGIN CERTIFICATE-----\n")
    flasher = BaseFlasher(
        cache_dir=cache,
        http_dir=http,
        tftp_dir=tftp,
        cacert=str(cacert_path),
        children={
            "serial": PySerial(url="loop://"),
            "power": MockPower(),
        },
    )
    with serve(flasher) as client:
        assert client.call("get_cacert") == "-----BEGIN CERTIFICATE-----\n"


def test_drivers_flashers_setup_fls_binary(complete_flasher, tmp_path, monkeypatch):
    """The exporter's fls binary is copied into the http root for the DUT to fetch"""
    fls = tmp_path / "fls-aarch64"
    fls.write_bytes(b"\x7fELF fake fls")
    monkeypatch.setattr("jumpstarter_driver_flashers.driver.FLS_BINARY_PATHS", (fls,))

    with serve(complete_flasher) as client:
        client.call("setup_fls_binary")
        assert client.http.storage.read_bytes("fls") == b"\x7fELF fake fls"


def test_drivers_flashers_setup_fls_binary_missing(complete_flasher, tmp_path, monkeypatch):
    monkeypatch.setattr("jumpstarter_driver_flashers.driver.FLS_BINARY_PATHS", (tmp_path / "nope",))

    with serve(complete_flasher) as client, pytest.raises(DriverError, match="fls binary not found"):
        client.call("setup_fls_binary")


def test_drivers_flashers_get_fls_version(temp_dirs):
    cache, http, tftp = temp_dirs
    default = BaseFlasher(cache_dir=cache, http_dir=http, tftp_dir=tftp,
                          children={"serial": PySerial(url="loop://"), "power": MockPower()})
    with serve(default) as client:
        assert client.call("get_fls_version") is None

    pinned = BaseFlasher(cache_dir=cache, http_dir=http, tftp_dir=tftp, fls_version="0.4.0",
                         children={"serial": PySerial(url="loop://"), "power": MockPower()})
    with serve(pinned) as client:
        assert client.call("get_fls_version") == "0.4.0"


def test_drivers_flashers_rejects_wrong_tftp_child(temp_dirs):
    cache, http, tftp = temp_dirs
    with pytest.raises(ConfigurationError, match="'tftp' child"):
        BaseFlasher(
            cache_dir=cache,
            http_dir=http,
            tftp_dir=tftp,
            children={"serial": PySerial(url="loop://"), "power": MockPower(), "tftp": MockPower()},
        )


def test_drivers_flashers_follows_ref_children(temp_dirs):
    cache, http, tftp = temp_dirs
    shared_tftp = Tftp(root_dir=tftp)
    flasher = BaseFlasher(
        cache_dir=cache,
        http_dir=http,
        tftp_dir=tftp,
        children={"serial": PySerial(url="loop://"), "power": MockPower(), "tftp": Proxy(ref="shared_tftp")},
    )
    Composite(children={"shared_tftp": shared_tftp, "storage": flasher}).enumerate()
    assert flasher.tftp is shared_tftp


def test_drivers_flashers_rejects_wrong_ref_child(temp_dirs):
    cache, http, tftp = temp_dirs
    flasher = BaseFlasher(
        cache_dir=cache,
        http_dir=http,
        tftp_dir=tftp,
        children={"serial": PySerial(url="loop://"), "power": MockPower(), "tftp": Proxy(ref="other")},
    )
    Composite(children={"other": MockPower(), "storage": flasher}).enumerate()
    with pytest.raises(ConfigurationError, match="'tftp' child of BaseFlasher must be a Tftp driver, got MockPower"):
        _ = flasher.tftp
