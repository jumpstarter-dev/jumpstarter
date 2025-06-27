from pathlib import Path

import pytest

from . import bundle


def test_bundle_read():
    # get current package directory
    manifest_file = Path(__file__).parent / "../oci_bundles/ti_j784s4xevm/manifest.yaml"
    flasher_bundle = bundle.FlasherBundleManifestV1Alpha1.from_file(manifest_file)
    assert flasher_bundle.apiVersion == "jumpstarter.dev/v1alpha1"
    assert flasher_bundle.kind == "FlashBundleManifest"
    assert flasher_bundle.spec.targets == {
        "usd": "/sys/class/block#4fb0000",
        "emmc": "/sys/class/block#4f80000",
    }


def test_bundle_get_boot_cmd_default():
    """Test getting default boot command from test bundle"""
    manifest_file = Path(__file__).parent / "../oci_bundles/test/manifest.yaml"
    flasher_bundle = bundle.FlasherBundleManifestV1Alpha1.from_file(manifest_file)

    # Test default boot command (no variant specified)
    bootcmd = flasher_bundle.get_boot_cmd()
    assert bootcmd == "booti 0x82000000 - 0x84000000"


def test_bundle_get_boot_cmd_with_variant():
    """Test getting boot command with specific DTB variant"""
    manifest_file = Path(__file__).parent / "../oci_bundles/test/manifest.yaml"
    flasher_bundle = bundle.FlasherBundleManifestV1Alpha1.from_file(manifest_file)

    # Test variant with custom boot command
    bootcmd = flasher_bundle.get_boot_cmd("othercmd")
    assert bootcmd == "bootm"

    # Test variant without custom boot command (should use default)
    bootcmd = flasher_bundle.get_boot_cmd("alternate")
    assert bootcmd == "booti 0x82000000 - 0x84000000"

    # Test default variant explicitly
    bootcmd = flasher_bundle.get_boot_cmd("test-dtb")
    assert bootcmd == "booti 0x82000000 - 0x84000000"


def test_bundle_get_boot_cmd_invalid_variant():
    """Test that get_boot_cmd raises ValueError for invalid variant"""
    manifest_file = Path(__file__).parent / "../oci_bundles/test/manifest.yaml"
    flasher_bundle = bundle.FlasherBundleManifestV1Alpha1.from_file(manifest_file)

    with pytest.raises(ValueError, match="DTB variant noexists not found in the manifest"):
        flasher_bundle.get_boot_cmd("noexists")
