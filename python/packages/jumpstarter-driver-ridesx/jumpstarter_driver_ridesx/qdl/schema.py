"""Schema definitions for Qualcomm firmware manifest YAML files."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated, Literal, Union

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator

EMBEDDED_MANIFEST_NAMES = ("jumpstarter_manifest.yaml",)


class StepBase(BaseModel):
    name: str | None = Field(None, description="Optional human-readable name for this step")
    retry_mode: Literal["edl", "fastboot"] | None = Field(
        None, description="Mode to retry in if the step fails"
    )


class SetModeStep(StepBase):
    set_mode: Literal["edl", "fastboot"]
    check_dmesg: str | None = Field(None, description="Expected dmesg output to verify the mode change")


class SleepStep(StepBase):
    sleep: int = Field(..., description="Sleep duration in seconds")


class QdlConfig(BaseModel):
    storage: Literal["ufs", "spinor"]
    programmer: str = Field(..., description="Firehose programmer ELF relative to the storage workdir")
    files: list[str] = Field(..., description="XML or glob patterns relative to the storage workdir")
    workdir: str | None = Field(
        None,
        description="Directory relative to firmware root. Defaults to the storage name (ufs/spinor).",
    )


class QdlStep(StepBase):
    qdl: QdlConfig


class FastbootFlashOp(BaseModel):
    partition: str
    file: str


class FastbootConfig(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    flash: list[FastbootFlashOp] | None = None
    erase: list[str] | None = None
    continue_: bool | None = Field(None, alias="continue")


class FastbootStep(StepBase):
    fastboot: FastbootConfig


Step = Annotated[
    Union[SetModeStep, SleepStep, QdlStep, FastbootStep],
    Field(discriminator=None),
]


class FirmwareData(BaseModel):
    folder: str = Field(..., description="Folder containing the firmware files after extraction")
    extract_to_folder: bool = Field(
        False,
        description="If True, extract the archive directly into /folder instead of preserving top-level paths",
    )
    abl_image: str | None = Field(None, description="Relative path to ABL ELF to flash via fastboot after steps")
    cdt_image: dict[str, str] | None = Field(
        None,
        description="Board-revision keys mapped to CDT binary paths flashed via fastboot after steps",
    )

    @field_validator("cdt_image")
    @classmethod
    def validate_cdt_image(cls, value: dict[str, str] | None) -> dict[str, str] | None:
        if value is None:
            return None
        cleaned: dict[str, str] = {}
        for key, path in value.items():
            if not isinstance(key, str) or not key.strip():
                raise ValueError("cdt_image keys must be non-empty strings")
            if not isinstance(path, str) or not path.strip():
                raise ValueError(f"cdt_image[{key!r}] must be a non-empty path")
            cleaned[_normalize_revision_key(key)] = path
        return cleaned


class FirmwareManifest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str
    description: str | None = None
    data: FirmwareData
    steps: list[Step]


def _parse_step(data: dict) -> Step:
    if "set_mode" in data:
        return SetModeStep.model_validate(data)
    if "sleep" in data:
        return SleepStep.model_validate(data)
    if "qdl" in data:
        return QdlStep.model_validate(data)
    if "fastboot" in data:
        return FastbootStep.model_validate(data)
    raise ValidationError.from_exception_data(
        "Step",
        [{"type": "value_error", "loc": (), "msg": f"Unknown step type: {sorted(data)}"}],
    )


def load_firmware_manifest(yaml_path: Path) -> FirmwareManifest:
    if not yaml_path.exists():
        raise FileNotFoundError(f"Firmware manifest not found: {yaml_path}")

    with yaml_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)

    if not isinstance(raw, dict):
        raise ValidationError.from_exception_data(
            "FirmwareManifest",
            [{"type": "value_error", "loc": (), "msg": "Manifest root must be a mapping"}],
        )

    steps_raw = raw.get("steps", [])
    parsed_steps = [_parse_step(step) for step in steps_raw]
    payload = {**raw, "steps": parsed_steps}
    return FirmwareManifest.model_validate(payload)


def load_firmware_manifest_from_mapping(data: dict) -> FirmwareManifest:
    steps_raw = data.get("steps", [])
    parsed_steps = [_parse_step(step) for step in steps_raw]
    payload = {**data, "steps": parsed_steps}
    return FirmwareManifest.model_validate(payload)


def find_embedded_manifest(work_dir: Path) -> Path | None:
    for filename in EMBEDDED_MANIFEST_NAMES:
        matches = sorted(work_dir.rglob(filename))
        if matches:
            return matches[0]
    return None


def _normalize_revision_key(board_revision: str) -> str:
    revision = board_revision.strip().lower()
    return revision if revision.startswith("v") else f"v{revision}"


def _cdt_image_entries(cdt_image: dict[str, str] | None) -> dict[str, str]:
    return dict(cdt_image or {})


def cdt_images_configured(cdt_image: dict[str, str] | None) -> bool:
    return bool(_cdt_image_entries(cdt_image))


def select_cdt_image_path(
    cdt_image: dict[str, str] | None,
    *,
    board_revision: str | None,
    manifest_name: str,
) -> str | None:
    entries = _cdt_image_entries(cdt_image)
    if not entries:
        return None

    name_upper = manifest_name.upper()
    if "CS4" in name_upper or "CS5" in name_upper:
        for key in ("v4", "v3"):
            if key in entries:
                return entries[key]
        return None

    if not board_revision:
        return None

    key = _normalize_revision_key(board_revision)
    if key in entries:
        return entries[key]
    if key == "v2.5" and "v2" in entries:
        return entries["v2"]
    return None
