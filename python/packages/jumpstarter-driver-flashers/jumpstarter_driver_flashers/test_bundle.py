from pathlib import Path

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


