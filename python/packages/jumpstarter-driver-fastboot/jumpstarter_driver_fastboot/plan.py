"""Planning: a ``FlashManifest`` becomes an ordered, device-independent list of operations.

Manifest vocabulary (one action per ``fastboot:`` step)::

    spec:
      fastboot:
        slot: inactive           # current (default) | inactive | a | b | all
        finally: continue        # reboot (default) | continue | stay
      steps:
        - critical: true
          fastboot:
            flash:
              - {partition: abl, file: data/abl.elf}
              - {partition: cdt, file: data/cdt-v3.bin, variant: v3}
        - fastboot: {reboot: fastboot}       # into fastbootd, for logical partitions
        - fastboot: {erase: userdata}
        - fastboot: {set_active: inactive}
        - fastboot: {oem: "device-info"}     # only commands the exporter allows

``spec.fastboot.android_info: {file: android-info.txt}`` adds the AOSP build's
own ``require`` lines to ``requires``, checked exactly as ``fastboot flashall``
checks them.

Planning never touches the device: the operations are resolved against the live
bootloader later (the driver's ``resolve_plan``), and run by the job runner,
which only speaks fastboot.
"""

from __future__ import annotations

import fnmatch
import logging
import re
import shlex
from collections.abc import Callable
from typing import Any

from .manifest import (
    FastbootOptions,
    FastbootStep,
    FlashManifest,
    ManifestError,
    ManifestStep,
    Requirement,
    normalize_path,
)

logger = logging.getLogger(__name__)

DEFAULT_CRITICAL_PARTITIONS = [
    "xbl*",
    "abl*",
    "aboot*",
    "tz*",
    "hyp*",
    "aop*",
    "devcfg*",
    "keymaster*",
    "bootloader*",
    "cdt",
    "uefi*",
    "multiimgoem*",
    "shrm*",
    "cpucp*",
]

FORBIDDEN_OEM = ("lock", "unlock", "lock_critical", "unlock_critical")

_REQUIRE = re.compile(r"(require\s+|reject\s+)?\s*(\S+)\s*=\s*(.*)")
_REQUIRE_FOR_PRODUCT = re.compile(r"require-for-product:\s*(\S+)\s+(\S+)\s*=\s*(.*)")


def parse_android_info(text: str, source: str = "android-info.txt") -> list[Requirement]:
    """``android-info.txt`` requirements, with AOSP ``fastboot``'s own rules (``ParseRequirementLine``).

    * ``require name=a|b`` (``require`` optional), ``reject name=a|b``, and
      ``require-for-product:p name=a|b`` (checked only when ``product`` is ``p``);
    * ``board`` means the ``product`` variable;
    * a value ending in ``*`` matches by prefix;
    * ``partition-exists=p`` requires the device to have the partition;
    * lines that don't parse are skipped, as ``fastboot`` does.
    """
    reqs = []
    for number, line in enumerate(text.split("\n"), start=1):
        if not line:
            continue
        product = None
        if m := _REQUIRE.fullmatch(line):
            reject, name, values = (m.group(1) or "").strip() == "reject", m.group(2), m.group(3)
        elif m := _REQUIRE_FOR_PRODUCT.fullmatch(line):
            reject, product, name, values = False, m.group(1), m.group(2), m.group(3)
        else:
            logger.warning("%s:%d: not a requirement, skipped (as fastboot does): %r", source, number, line)
            continue
        options = [option.strip() for option in values.split("|")]
        if name == "board":  # AOSP: "Work around an unfortunate name mismatch."
            name = "product"
        if name == "partition-exists":
            reqs.append(Requirement(equals=["yes", "no"], variable=f"has-slot:{options[0]}"))
        else:
            reqs.append(Requirement(equals=options, reject=reject, for_product=product, variable=name))
    return reqs


def android_info_requirements(options: FastbootOptions, read_text: Callable[[str], str]) -> list[Requirement]:
    """Requirements from the bundle's ``android-info.txt``, if ``spec.fastboot.android_info`` names one.

    ``read_text(path)`` reads a staged bundle file.
    """
    if options.android_info is None:
        return []
    return parse_android_info(read_text(options.android_info.file), options.android_info.file)


def is_critical(partition: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(partition, p) for p in patterns)


def check_oem(command: str, allowed: list[str]) -> None:
    words = shlex.split(command)
    if words and words[0] in FORBIDDEN_OEM:
        raise ManifestError(f"'oem {command}' is never allowed from a bundle")
    if command not in allowed:
        raise ManifestError(f"'oem {command}' is not in the exporter's allowed_oem_commands")


class _Planner:
    """Turns steps into operations, tracking the fastboot mode the device will be in."""

    def __init__(self, *, variant: str | None, critical_partitions: list[str], allowed_oem_commands: list[str]):
        self.variant = variant
        self.critical_partitions = critical_partitions
        self.allowed_oem_commands = allowed_oem_commands
        self.variant_used = False
        self.mode = "bootloader"

    def selects(self, variant: str | list[str] | None) -> bool:
        """Whether an entry with this ``variant:`` applies to the exporter's board."""
        if variant is None:
            return True
        self.variant_used = True
        return self.variant in ([variant] if isinstance(variant, str) else variant)

    def plan_step(self, step: ManifestStep) -> list[dict[str, Any]]:
        """Each operation records the fastboot mode it needs (``bootloader`` or ``userspace``).

        So the runner can verify the mode before every step and resume correctly
        after an interruption.
        """
        body: FastbootStep = step.body
        mode = self.mode
        if body.flash is not None:
            return [
                {
                    "op": "flash",
                    "partition": entry.partition,
                    "file": normalize_path(entry.file),
                    "critical": entry.critical or is_critical(entry.partition, self.critical_partitions),
                    "mode": mode,
                    "describe": f"flash {entry.partition}",
                }
                for entry in body.flash
                if self.selects(entry.variant)
            ]
        if body.erase is not None:
            partitions = [body.erase] if isinstance(body.erase, str) else body.erase
            return [
                {
                    "op": "erase",
                    "partition": partition,
                    "critical": is_critical(partition, self.critical_partitions),
                    "mode": mode,
                    "describe": f"erase {partition}",
                }
                for partition in partitions
            ]
        if body.set_active is not None:
            return [{"op": "set_active", "slot": body.set_active, "critical": True, "mode": mode,
                     "describe": f"set_active {body.set_active}"}]
        if body.reboot is not None:
            self.mode = "userspace" if body.reboot == "fastboot" else "bootloader"
            return [{"op": "mode", "target": self.mode, "mode": self.mode, "writes": False,
                     "describe": f"reboot {body.reboot}"}]
        assert body.oem is not None
        check_oem(body.oem, self.allowed_oem_commands)
        return [{"op": "oem", "command": body.oem, "mode": mode, "describe": f"oem {body.oem}"}]


def build_plan(
    manifest: FlashManifest,
    *,
    files: set[str],
    variant: str | None,
    wipe: bool,
    critical_partitions: list[str],
    allowed_oem_commands: list[str],
) -> list[dict[str, Any]]:
    """Turn a manifest into an ordered, device-independent plan. Steps run exactly in manifest order.

    An operation is a JSON-able dict with at least ``op`` and ``describe``, plus
    ``name`` (its step's) and ``critical``. ``file`` names a bundle file, to which
    the driver attaches the staged blob before the job starts; ``writes: False``
    marks operations that don't change the device (mode switches).
    """
    missing = [ref for ref in manifest.files if ref not in files]
    if missing:
        raise ManifestError(f"file {missing[0]!r} is not in the bundle")
    planner = _Planner(variant=variant, critical_partitions=critical_partitions,
                       allowed_oem_commands=allowed_oem_commands)
    plan: list[dict[str, Any]] = []
    for step in manifest.steps:
        if step.when == "wipe" and not wipe:
            continue
        if step.action == "sleep":
            plan.append(
                {"op": "sleep", "seconds": step.body, "name": step.label, "critical": False,
                 "writes": False, "describe": f"sleep {step.body:g}s"}
            )
            continue
        for op in planner.plan_step(step):
            op = {"name": step.label, **op}
            op["critical"] = bool(op.get("critical")) or step.critical
            plan.append(op)
    if planner.variant_used and variant is None:
        raise ManifestError("manifest selects files by variant, but the exporter has no 'variant' configured")
    if not any(op.get("writes", True) for op in plan):
        raise ManifestError("the manifest resolves to nothing to do")
    return plan
