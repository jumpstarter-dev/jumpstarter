from pathlib import Path

import pytest

from jumpstarter_driver_ridesx.qdl.schema import (
    FastbootStep,
    QdlStep,
    SetModeStep,
    SleepStep,
    load_firmware_manifest,
    select_cdt_image_path,
)
from jumpstarter_driver_ridesx.qdl.soc_profiles import SA8775P, get_soc_profile

MANIFEST_DIR = Path(__file__).parent / "examples" / "manifests"


@pytest.mark.parametrize(
    "manifest_name",
    ["cs4.yaml", "cs5.yaml", "es13.yaml", "es21.yaml", "es22.yaml"],
)
def test_example_manifests_validate(manifest_name):
    manifest = load_firmware_manifest(MANIFEST_DIR / manifest_name)
    assert manifest.name
    assert manifest.steps
    assert any(isinstance(step, (SetModeStep, QdlStep, FastbootStep, SleepStep)) for step in manifest.steps)


def test_es22_manifest_contains_qdl_steps():
    manifest = load_firmware_manifest(MANIFEST_DIR / "es22.yaml")
    qdl_steps = [step for step in manifest.steps if isinstance(step, QdlStep)]
    assert len(qdl_steps) == 3
    assert qdl_steps[0].qdl.storage == "ufs"
    assert qdl_steps[2].qdl.storage == "spinor"


def test_es13_manifest_contains_fastboot_hypervisor_step():
    manifest = load_firmware_manifest(MANIFEST_DIR / "es13.yaml")
    fastboot_steps = [step for step in manifest.steps if isinstance(step, FastbootStep)]
    assert len(fastboot_steps) == 1
    assert fastboot_steps[0].fastboot.continue_ is True
    assert fastboot_steps[0].fastboot.flash


def test_soc_profiles_match_reference_sequences():
    profile = get_soc_profile("sa8775p")
    assert profile.power_on_commands[0][0] == "devicePower 1"
    assert profile.edl_commands[3][0] == "pin 1 31"
    assert profile is SA8775P


@pytest.fixture
def sample_cdt_images():
    return {
        "v1": "cdt/v1.bin",
        "v2": "cdt/v2.bin",
        "v3": "cdt/v3.bin",
        "v4": "cdt/v4.bin",
    }


def test_select_cdt_image_path_uses_board_revision(sample_cdt_images):
    assert (
        select_cdt_image_path(
            sample_cdt_images,
            board_revision="v3",
            manifest_name="SA8775P ES22 AWE Firmware",
        )
        == "cdt/v3.bin"
    )


def test_select_cdt_image_path_v25_falls_back_to_v2(sample_cdt_images):
    assert (
        select_cdt_image_path(
            sample_cdt_images,
            board_revision="v2.5",
            manifest_name="SA8775P ES22 AWE Firmware",
        )
        == "cdt/v2.bin"
    )


def test_select_cdt_image_path_cs4_prefers_v4(sample_cdt_images):
    assert (
        select_cdt_image_path(
            sample_cdt_images,
            board_revision=None,
            manifest_name="SA8650P CS4 Firmware",
        )
        == "cdt/v4.bin"
    )


def test_select_cdt_image_path_supports_future_revision_keys():
    cdt_images = {"v5": "cdt/v5.bin", "v6": "cdt/v6.bin"}
    assert (
        select_cdt_image_path(
            cdt_images,
            board_revision="v6",
            manifest_name="Future platform",
        )
        == "cdt/v6.bin"
    )


def test_cdt_image_normalizes_revision_keys():
    manifest = load_firmware_manifest(MANIFEST_DIR / "es22.yaml")
    assert manifest.data.cdt_image is not None
    assert "v1" in manifest.data.cdt_image
    assert "V1" not in manifest.data.cdt_image
