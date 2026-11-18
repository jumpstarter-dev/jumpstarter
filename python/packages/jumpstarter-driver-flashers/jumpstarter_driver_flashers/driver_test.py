import os
import shutil
import tempfile
from pathlib import Path

import pytest
from jumpstarter_driver_composite.driver import Composite, Proxy
from jumpstarter_driver_power.driver import MockPower
from jumpstarter_driver_pyserial.driver import PySerial
from jumpstarter_driver_tftp.driver import Tftp
from oras.provider import Registry

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


@pytest.fixture
def new_session(tmp_path):
    """Start a session of a new flasher on a cache shared by the test"""

    def session():
        return serve(
            BaseFlasher(
                flasher_bundle="quay.io/jumpstarter-dev/jumpstarter-flasher-test:new",
                cache_dir=str(tmp_path / "cache"),
                http_dir=str(tmp_path / "http"),
                tftp_dir=str(tmp_path / "tftp"),
                children={"serial": PySerial(url="loop://"), "power": MockPower()},
            )
        )

    return session


@pytest.fixture
def downloads(monkeypatch):
    """The blobs sessions download from the registry"""
    downloaded = []
    download_blob = Registry.download_blob

    def counting_download_blob(self, container, digest, outfile):
        downloaded.append(digest)
        return download_blob(self, container, digest, outfile)

    monkeypatch.setattr(Registry, "download_blob", counting_download_blob)
    return downloaded


def cached_bundles(tmp_path):
    """The directories of the bundle versions in the cache"""
    return [manifest.parent for manifest in (tmp_path / "cache").rglob("manifest.yaml")]


def move_tag(monkeypatch):
    """Move the bundle's tag to the manifest :latest points at, which ships another manifest.yaml"""
    do_request = Registry.do_request
    monkeypatch.setattr(
        Registry,
        "do_request",
        lambda self, url, *args, **kwargs: do_request(
            self, url.replace("/manifests/new", "/manifests/latest"), *args, **kwargs
        ),
    )


def test_drivers_flashers_bundle_cache_outlives_session(tmp_path, monkeypatch, new_session, downloads):
    """A new session reuses the cached bundle until its tag moves to another manifest"""
    with new_session() as client:
        manifest = client.call("get_flasher_manifest_yaml")
    pulled = len(downloads)
    assert pulled > 0

    with new_session() as client:
        assert client.call("get_flasher_manifest_yaml") == manifest
    assert len(downloads) == pulled

    move_tag(monkeypatch)
    with new_session() as client:
        assert client.call("get_flasher_manifest_yaml") != manifest
    assert len(downloads) > pulled
    assert len(cached_bundles(tmp_path)) == 1


def test_drivers_flashers_bundle_cache_keeps_version_in_use(tmp_path, monkeypatch, new_session):
    """A session keeps the version it reads while another session pulls a newer one"""
    with new_session() as client:
        client.call("setup_flasher_bundle")
        move_tag(monkeypatch)
        with new_session() as other:
            other.call("get_flasher_manifest_yaml")
        client.call("setup_flasher_bundle")
        assert client.tftp.storage.read_bytes(client.call("get_kernel_filename")) == b"\x00" * 1024

    # the next session finds the older version unused
    with new_session() as client:
        client.call("get_flasher_manifest_yaml")
    assert len(cached_bundles(tmp_path)) == 1


def test_drivers_flashers_bundle_cache_slow_pull_keeps_newer_version(monkeypatch, new_session, downloads):
    """A pull finishing after the tag moved and a newer version was published leaves the newer one cached"""
    pull = Registry.pull

    def tag_moves_during_pull(self, *args, **kwargs):
        monkeypatch.setattr(Registry, "pull", pull)
        move_tag(monkeypatch)
        with new_session() as client:
            client.call("get_flasher_manifest_yaml")
        return pull(self, *args, **kwargs)

    monkeypatch.setattr(Registry, "pull", tag_moves_during_pull)
    with new_session() as client:
        client.call("get_flasher_manifest_yaml")
    pulled = len(downloads)

    with new_session() as client:
        client.call("get_flasher_manifest_yaml")
    assert len(downloads) == pulled


def test_drivers_flashers_bundle_cache_interrupted_removal(tmp_path, monkeypatch, new_session):
    """A version whose removal was cut short is pulled again, not served half removed"""
    with new_session() as client:
        client.call("get_flasher_manifest_yaml")

    rmtree = shutil.rmtree

    def removal_cut_short(path, onexc=None):
        # the session removes its socket directory too, as a TemporaryDirectory
        if not Path(path).is_relative_to(tmp_path / "cache"):
            return rmtree(path, onexc=onexc)
        next(Path(path).rglob("kernel")).unlink()
        raise OSError("removal cut short")

    move_tag(monkeypatch)
    monkeypatch.setattr(shutil, "rmtree", removal_cut_short)
    with new_session() as client, pytest.raises(DriverError, match="removal cut short"):
        client.call("get_flasher_manifest_yaml")

    # the tag moves back to the version whose removal was cut short
    monkeypatch.undo()
    with new_session() as client:
        client.call("setup_flasher_bundle")
        assert client.tftp.storage.read_bytes(client.call("get_kernel_filename")) == b"\x00" * 1024


def test_drivers_flashers_bundle_cache_tag_moves_during_pull(monkeypatch, new_session):
    """A tag moving between the digest lookup and the pull does not cache the new files under the old digest"""
    do_request = Registry.do_request
    lookups = []

    def tag_moves_after_lookup(self, url, *args, **kwargs):
        if "/manifests/new" in url:
            if lookups:
                url = url.replace("/manifests/new", "/manifests/latest")
            lookups.append(url)
        return do_request(self, url, *args, **kwargs)

    monkeypatch.setattr(Registry, "do_request", tag_moves_after_lookup)
    with new_session() as client:
        before = client.call("get_flasher_manifest_yaml")
    with new_session() as client:
        assert client.call("get_flasher_manifest_yaml") != before


def test_drivers_flashers_bundle_cache_skips_failed_pull(monkeypatch, new_session):
    """A pull that breaks off halfway leaves nothing a later session trusts"""
    download_blob = Registry.download_blob

    def broken_kernel_stream(self, container, digest, outfile):
        if outfile.endswith("kernel"):
            # oras truncates the file before it reads the stream
            Path(outfile).write_bytes(b"\x00" * 10)
            raise ConnectionError("stream broke")
        return download_blob(self, container, digest, outfile)

    monkeypatch.setattr(Registry, "download_blob", broken_kernel_stream)
    with new_session() as client, pytest.raises(DriverError, match="stream broke"):
        client.call("setup_flasher_bundle")

    monkeypatch.setattr(Registry, "download_blob", download_blob)
    with new_session() as client:
        client.call("setup_flasher_bundle")
        assert client.tftp.storage.read_bytes(client.call("get_kernel_filename")) == b"\x00" * 1024


def test_drivers_flashers_bundle_cache_concurrent_pull(tmp_path, monkeypatch, new_session):
    """A session whose pull another session publishes first serves the published copy"""
    pull = Registry.pull
    published = []

    def pull_beside_another_session(self, *args, **kwargs):
        monkeypatch.setattr(Registry, "pull", pull)
        with new_session() as client:
            published.append(client.call("get_flasher_manifest_yaml"))
        [published_dir] = cached_bundles(tmp_path)
        (published_dir / "marker").touch()
        return pull(self, *args, **kwargs)

    monkeypatch.setattr(Registry, "pull", pull_beside_another_session)
    with new_session() as client:
        assert [client.call("get_flasher_manifest_yaml")] == published
    # the copy published first is kept, not replaced
    [kept] = cached_bundles(tmp_path)
    assert (kept / "marker").exists()


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
