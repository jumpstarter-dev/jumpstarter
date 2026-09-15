"""Shared fastboot subprocess helpers for the RideSX driver family.

Both the manifest-driven QDL executor and the direct RideSXDriver API
share these low-level wrappers around the ``fastboot`` CLI tool.
"""

from __future__ import annotations

import logging
import subprocess
import time
from pathlib import Path

logger = logging.getLogger(__name__)


def _build_cmd(
    args: list[str],
    *,
    device_id: str | None = None,
) -> list[str]:
    cmd = ["fastboot"]
    if device_id:
        cmd.extend(["-s", device_id])
    cmd.extend(args)
    return cmd


def run_fastboot(
    args: list[str],
    *,
    device_id: str | None = None,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Run a fastboot command and return the result.

    Raises ``RuntimeError`` on non-zero exit code.
    """
    cmd = _build_cmd(args, device_id=device_id)
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=False, timeout=timeout,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"fastboot {args[0]} failed (rc={result.returncode}): "
            f"{result.stderr or result.stdout}"
        )
    return result


def flash(
    partition: str,
    image_path: str | Path,
    *,
    device_id: str | None = None,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Flash a single partition."""
    return run_fastboot(
        ["flash", partition, str(image_path)],
        device_id=device_id,
        timeout=timeout,
    )


def erase(
    partition: str,
    *,
    device_id: str | None = None,
    timeout: int,
) -> subprocess.CompletedProcess[str]:
    """Erase a partition."""
    return run_fastboot(
        ["erase", partition],
        device_id=device_id,
        timeout=timeout,
    )


def continue_boot(
    *,
    device_id: str | None = None,
    timeout: int,
    raise_on_failure: bool = True,
) -> subprocess.CompletedProcess[str]:
    """Send the ``fastboot continue`` command.

    When *raise_on_failure* is ``False``, a non-zero exit code is logged
    as a warning instead of raising.
    """
    cmd = _build_cmd(["continue"], device_id=device_id)
    logger.info("Running: %s", " ".join(cmd))
    result = subprocess.run(
        cmd, capture_output=True, text=True, check=False, timeout=timeout,
    )
    if result.returncode != 0:
        msg = (
            f"fastboot continue failed (rc={result.returncode}): "
            f"{result.stderr or result.stdout}"
        )
        if raise_on_failure:
            raise RuntimeError(msg)
        logger.warning(msg)
    return result


def detect_device(
    *,
    max_attempts: int = 5,
    delay: float = 2.0,
    timeout: int = 10,
) -> dict[str, str | None]:
    """Poll for a fastboot device.

    Returns ``{"status": "device_found", "device_id": "<id>"}`` on success
    or ``{"status": "no_device_found", "device_id": None}`` after exhausting
    all attempts.
    """
    for attempt in range(max_attempts):
        try:
            result = subprocess.run(
                ["fastboot", "devices", "-l"],
                capture_output=True, text=True, check=True, timeout=timeout,
            )
            if result.stdout.strip():
                device_id = result.stdout.strip().split()[0]
                logger.info("Found fastboot device: %s", device_id)
                return {"status": "device_found", "device_id": device_id}
            logger.warning(
                "No fastboot devices found on attempt %d/%d",
                attempt + 1, max_attempts,
            )
        except subprocess.TimeoutExpired:
            logger.warning(
                "Fastboot command timed out on attempt %d/%d",
                attempt + 1, max_attempts,
            )
        except subprocess.CalledProcessError as e:
            raise RuntimeError(f"Fastboot command failed: {e}") from e
        except FileNotFoundError:
            raise RuntimeError("fastboot command not found") from None
        if attempt < max_attempts - 1:
            time.sleep(delay)

    logger.error("No fastboot devices found after %d attempts", max_attempts)
    return {"status": "no_device_found", "device_id": None}
