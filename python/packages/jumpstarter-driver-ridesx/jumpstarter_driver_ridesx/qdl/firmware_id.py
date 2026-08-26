"""Firmware identification helpers based on Qualcomm boot logs."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class VersionInfo:
    sail_versions: dict[str, str] = field(default_factory=dict)
    main_versions: dict[str, str] = field(default_factory=dict)
    firmware_variant: str | None = None
    raw_output: str = ""
    sail_raw: str = ""
    main_raw: str = ""


class _SerialCapture:
    def __init__(self):
        self._chunks: list[str] = []

    def write(self, data):
        if isinstance(data, bytes):
            self._chunks.append(data.decode("utf-8", errors="replace"))
        else:
            self._chunks.append(str(data))

    def flush(self):
        pass

    def getvalue(self) -> str:
        return "".join(self._chunks)


def extract_sail_version(sail, timeout=30, log_buffer=None) -> dict[str, str]:
    sail.logfile_read = log_buffer
    version_info: dict[str, str] = {}
    start_time = time.time()
    patterns = [
        (r"FW Version:\s*([\d.]+)", "sail_fw_version", 1),
        (r"(?:Info:\s+)?Platform type:\s*(\S+)", "platform_type", 1),
        (r"(?:Info:\s+)?SOC1 Board ID corresponds to SOC Type:\s*([^\r\n]+)", "soc_type", 1),
        (r"(?:Info:\s+)?SIP1 Board ID corresponds to SIP Type:\s*([^\r\n]+)", "sip_type", 1),
        (r"Hypervisor\s+cold\s+boot[^:]*:\s*gunyah-[^\s]+\s+(perf|debug|prod)", "hypervisor", 1),
        (r"Hypervisor.*?(perf|debug)", "hypervisor", 1),
    ]
    found_patterns: set[str] = set()

    while time.time() - start_time < timeout and len(found_patterns) < len(patterns):
        try:
            expect_patterns = [pattern for pattern, key, _ in patterns if key not in found_patterns]
            if not expect_patterns:
                break
            index = sail.expect(expect_patterns, timeout=5)
            pattern_idx = 0
            for _pattern, key, group in patterns:
                if key not in found_patterns:
                    if pattern_idx == index and sail.match:
                        value = sail.match.group(group)
                        if isinstance(value, bytes):
                            value = value.decode("utf-8", errors="ignore")
                        version_info[key] = value.strip()
                        found_patterns.add(key)
                    pattern_idx += 1
        except Exception:
            if len(found_patterns) >= 2:
                break
            continue
    return version_info


def extract_main_version(serial, timeout=30, log_buffer=None) -> dict[str, str]:
    serial.logfile_read = log_buffer
    version_info: dict[str, str] = {}
    start_time = time.time()
    patterns = [
        (r"QC_IMAGE_VERSION_STRING=([^\r\n]+)", "qc_image_version", 1),
        (r"IMAGE_VARIANT_STRING=([^\r\n]+)", "image_variant", 1),
        (r"OEM_IMAGE_VERSION_STRING=([^\r\n]+)", "oem_image_version", 1),
        (r"UEFI Ver\s+:\s*([\d.]+\.BOOT\.[^\r\n]+)", "uefi_version", 1),
        (r"UEFI Ver\s*:\s*([\d.]+\.BOOT\.[^\r\n]+)", "uefi_version", 1),
        (r"UEFI Ver\s+:\s*([^\r\n]+)", "uefi_version", 1),
        (r"UEFI Ver\s*:\s*([^\r\n]+)", "uefi_version", 1),
        (r"SBL1 BUILD @ ([^\r\n]+)", "sbl1_build", 1),
        (r"Chip Name\s*:\s*([^\r\n]+)", "chip_name", 1),
        (r"Chip Ver\s*:\s*([^\r\n]+)", "chip_version", 1),
        (r"Loader Build Info:\s*([^\r\n]+)[\r\n]", "abl_build", 1),
        (r"CDT Version:\d+,Platform ID:(\d+),Major ID:(\d+),Minor ID:(\d+),Subtype:(\d+)", "cdt_platform_id", 1),
        (r"\[RM\]Resource Manager version:\s*(\S+)", "rm_version", 1),
        (r"Hypervisor\s+cold\s+boot[^:]*:\s*gunyah-[^\s]+\s+(perf|debug|prod)", "hypervisor", 1),
        (r"Hypervisor.*?(perf|debug|prod)", "hypervisor", 1),
    ]
    found_patterns: set[str] = set()

    while time.time() - start_time < timeout and len(found_patterns) < len(patterns):
        try:
            expect_patterns = [pattern for pattern, key, _ in patterns if key not in found_patterns]
            if not expect_patterns:
                break
            index = serial.expect(expect_patterns, timeout=5)
            pattern_idx = 0
            for _pattern, key, group in patterns:
                if key not in found_patterns:
                    if pattern_idx == index and serial.match:
                        value = serial.match.group(group)
                        if isinstance(value, bytes):
                            value = value.decode("utf-8", errors="ignore")
                        value = value.strip()
                        if key == "uefi_version" and (len(value) < 10 or ("BOOT" not in value and len(value) < 20)):
                            pattern_idx += 1
                            continue
                        version_info[key] = value
                        found_patterns.add(key)
                    pattern_idx += 1
        except Exception:
            if len(found_patterns) >= 3:
                break
            continue
    return version_info


def identify_firmware_variant(qc_image_version: str | None, rm_version: str | None = None) -> str | None:
    if not qc_image_version:
        return None

    known_variants = {
        "BOOT.MXF.1.2-00527-LEMANS-1": "ES21",
        "BOOT.MXF.1.2-00541-LEMANS-1": "ES22",
        "BOOT.MXF.1.2-00369-LEMANS-3": "ES14",
        "BOOT.MXF.1.2-00347-LEMANS-2": "ES13",
        "BOOT.MXF.1.2-00333-LEMANS-1": "ES12",
    }
    if qc_image_version in known_variants:
        return known_variants[qc_image_version]

    if qc_image_version == "BOOT.MXF.1.2-00568-LEMANS-2":
        rm_version_map = {
            "55198a39": "CS4",
            "ba2475df": "CS5",
        }
        if rm_version and rm_version in rm_version_map:
            return rm_version_map[rm_version]
        return "CS4/CS5"

    return "unknown"


def collect_version_info(serial, sail, power_cycle_callable, *, verbose=False, capture_dir=None) -> VersionInfo:
    result = VersionInfo()
    sail_capture = _SerialCapture()
    main_capture = _SerialCapture()

    with sail:
        with serial:
            if verbose:
                print("Power cycling device...")
            power_cycle_callable()
            if verbose:
                print("Collecting version information...")
            result.sail_versions = extract_sail_version(sail, timeout=30, log_buffer=sail_capture)
            result.main_versions = extract_main_version(serial, timeout=30, log_buffer=main_capture)
            if "hypervisor" in result.sail_versions and "hypervisor" not in result.main_versions:
                result.main_versions["hypervisor"] = result.sail_versions["hypervisor"]
            qc_image_version = result.main_versions.get("qc_image_version")
            rm_version = result.main_versions.get("rm_version")
            if qc_image_version:
                result.firmware_variant = identify_firmware_variant(qc_image_version, rm_version=rm_version)

    result.sail_raw = sail_capture.getvalue()
    result.main_raw = main_capture.getvalue()
    result.raw_output = result.sail_raw + result.main_raw

    if capture_dir:
        import os

        os.makedirs(capture_dir, exist_ok=True)
        with open(os.path.join(capture_dir, "sail.log"), "w", encoding="utf-8") as handle:
            handle.write(result.sail_raw)
        with open(os.path.join(capture_dir, "main.log"), "w", encoding="utf-8") as handle:
            handle.write(result.main_raw)

    return result


def format_version_report(versions: VersionInfo) -> str:
    lines = ["=" * 60, "VERSION INFORMATION SUMMARY", "=" * 60]
    if versions.sail_versions:
        lines.append("\nSAIL Port:")
        for key, value in versions.sail_versions.items():
            lines.append(f"  {key}: {value}")
    if versions.main_versions:
        lines.append("\nMAIN/SERIAL Port:")
        for key, value in versions.main_versions.items():
            lines.append(f"  {key}: {value}")
    lines.extend(["", "=" * 60, "FIRMWARE VARIANT IDENTIFICATION", "=" * 60])
    qc_image_version = versions.main_versions.get("qc_image_version")
    if versions.firmware_variant:
        lines.append(f"\n  Firmware Variant: {versions.firmware_variant}")
        lines.append(f"  QC Image Version: {qc_image_version}")
    else:
        lines.append(f"\n  QC Image Version: {qc_image_version or 'Not found'}")
        lines.append("  Firmware Variant: Could not be identified")
    uefi_version = versions.main_versions.get("uefi_version")
    hypervisor = versions.main_versions.get("hypervisor")
    lines.append(f"  UEFI Version: {uefi_version or 'Not found'}")
    lines.append(f"  Hypervisor: {hypervisor or 'Not found'}")
    lines.append("\n" + "=" * 60)
    return "\n".join(lines)
