"""Tests for iSCSI driver block device allowlist and path confinement."""

import os
import sys

import pytest

if sys.platform != "linux":
    pytest.skip("iSCSI driver requires Linux (libudev)", allow_module_level=True)

from jumpstarter_driver_iscsi.driver import ISCSI, ConfigurationError, ISCSIError


@pytest.fixture
def tmp_root(tmp_path):
    return str(tmp_path)


def _make_driver(tmp_root, block_device_allowlist=None):
    """Create an ISCSI driver instance without initializing rtslib."""
    driver = object.__new__(ISCSI)
    driver.root_dir = tmp_root
    driver.block_device_allowlist = block_device_allowlist or []
    return driver


class TestGetFullPathBlock:
    """Tests for _get_full_path with is_block=True."""

    def test_block_empty_allowlist_rejects(self, tmp_root):
        driver = _make_driver(tmp_root, block_device_allowlist=[])
        with pytest.raises(ISCSIError, match="block_device_allowlist is empty"):
            driver._get_full_path("/dev/sda", is_block=True)

    def test_block_not_in_allowlist_rejects(self, tmp_root):
        driver = _make_driver(tmp_root, block_device_allowlist=["/dev/sdb"])
        with pytest.raises(ISCSIError, match="not in the configured allowlist"):
            driver._get_full_path("/dev/sda", is_block=True)

    def test_block_in_allowlist_accepted(self, tmp_root):
        driver = _make_driver(tmp_root, block_device_allowlist=["/dev/sda"])
        result = driver._get_full_path("/dev/sda", is_block=True)
        assert result == "/dev/sda"

    def test_block_relative_path_rejected(self, tmp_root):
        driver = _make_driver(tmp_root, block_device_allowlist=["/dev/sda"])
        with pytest.raises(ISCSIError, match="must be an absolute path"):
            driver._get_full_path("dev/sda", is_block=True)

    def test_block_symlink_resolved(self, tmp_root):
        """Symlinks are resolved before checking the allowlist."""
        real_dev = os.path.join(tmp_root, "real_device")
        link_path = os.path.join(tmp_root, "link_device")
        # Create a real file and symlink
        with open(real_dev, "w") as f:
            f.write("")
        os.symlink(real_dev, link_path)

        driver = _make_driver(tmp_root, block_device_allowlist=[real_dev])
        result = driver._get_full_path(link_path, is_block=True)
        assert result == real_dev

    def test_block_symlink_not_in_allowlist(self, tmp_root):
        """Symlink target not in allowlist should be rejected."""
        real_dev = os.path.join(tmp_root, "real_device")
        link_path = os.path.join(tmp_root, "link_device")
        with open(real_dev, "w") as f:
            f.write("")
        os.symlink(real_dev, link_path)

        driver = _make_driver(tmp_root, block_device_allowlist=[link_path])
        with pytest.raises(ISCSIError, match="not in the configured allowlist"):
            driver._get_full_path(link_path, is_block=True)


class TestGetFullPathFile:
    """Tests for _get_full_path with is_block=False (unchanged behavior)."""

    def test_file_relative_path_confined(self, tmp_root):
        driver = _make_driver(tmp_root)
        result = driver._get_full_path("subdir/test.img", is_block=False)
        assert result.startswith(tmp_root)

    def test_file_absolute_path_rejected(self, tmp_root):
        driver = _make_driver(tmp_root)
        with pytest.raises(ISCSIError, match="Invalid file path"):
            driver._get_full_path("/etc/passwd", is_block=False)

    def test_file_traversal_rejected(self, tmp_root):
        driver = _make_driver(tmp_root)
        with pytest.raises(ISCSIError, match="Invalid file path"):
            driver._get_full_path("../../etc/passwd", is_block=False)


class TestAllowlistValidation:
    """Tests for block_device_allowlist validation in __post_init__."""

    def test_relative_path_in_allowlist_rejected(self, tmp_root):
        """Non-absolute paths in block_device_allowlist raise ConfigurationError."""
        driver = object.__new__(ISCSI)
        driver.root_dir = tmp_root
        driver.block_device_allowlist = ["dev/sda"]
        driver.remove_created_on_close = False
        driver.host = "127.0.0.1"
        driver.iqn_prefix = "iqn.2024-06.dev.jumpstarter"
        driver.target_name = "test"
        driver.children = {}
        with pytest.raises(ConfigurationError, match="not an absolute path"):
            driver.__post_init__()

    def test_allowlist_entries_resolved(self, tmp_root):
        """Allowlist symlink entries are resolved to real paths during init."""
        real_dev = os.path.join(tmp_root, "real_device")
        link_path = os.path.join(tmp_root, "link_device")
        with open(real_dev, "w") as f:
            f.write("")
        os.symlink(real_dev, link_path)

        driver = object.__new__(ISCSI)
        driver.root_dir = tmp_root
        driver.block_device_allowlist = [link_path]
        driver.remove_created_on_close = False
        driver.host = "127.0.0.1"
        driver.iqn_prefix = "iqn.2024-06.dev.jumpstarter"
        driver.target_name = "test"
        driver.children = {}
        driver.__post_init__()
        assert driver.block_device_allowlist == [real_dev]
