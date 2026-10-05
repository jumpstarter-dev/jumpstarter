"""Entry and exit scripts: ``j`` scripts that put a device into fastboot, and bring it out.

Each strategy is a driver script (``jumpstarter.driver.scripts``), run like a
lease hook: Bash or Python, with ``j``/``env()`` access to every driver in the
exporter. Pressing buttons, cycling power, talking to a serial console or adb,
holding a button until the device shows up: it's all ordinary script code, so
nothing here invents its own timing language.

Strategies are tried in order. After a strategy's script exits successfully,
the flasher waits up to ``wait`` seconds for the device to appear in fastboot;
a script can also wait itself (``j $JMP_DRIVER_PATH wait-present``) and keep a
button held until it does. A strategy's ``cleanup`` script always runs
afterwards, whatever happened, to release anything the script left held.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any, Literal

from pydantic import Field, field_validator

from jumpstarter.driver.scripts import DriverScript

DEFAULT_CLEANUP_TIMEOUT = 30


class EntryStrategy(DriverScript):
    """A named driver script that should leave the device in fastboot.

    Fields as for any driver script (``script``, ``exec``, ``timeout``,
    ``block_self``, ``self_path``, ``env``), plus:
    """

    name: str = Field(description="Strategy name, for `enter --strategy NAME` and in errors.")
    wait: float = Field(
        default=60.0,
        ge=0,
        description="Seconds to wait for the device in fastboot after the script exits successfully.",
    )
    cleanup: DriverScript | None = Field(
        default=None,
        description=(
            "A script that always runs after this strategy (success, failure, or timeout), "
            "e.g. to release buttons. Inline text, or a full script config."
        ),
    )

    @field_validator("cleanup", mode="before")
    @classmethod
    def _inline_cleanup(cls, value: Any) -> Any:
        if isinstance(value, str):
            return {"script": value, "timeout": DEFAULT_CLEANUP_TIMEOUT}
        return value


class ExitScript(DriverScript):
    """A driver script run once a flash job finishes, to bring the device out of fastboot.

    For what ``finally: reboot``/``continue`` can't do: switch boot-mode straps
    back, power-cycle, type ``boot`` on a console. It runs within the flash
    job's task, so a lease that ends waits for it and its session (with every
    driver) stays up while it runs.
    """

    when: Literal["success", "always"] = Field(
        default="success",
        description="Run after a successful job only (default), or after every job that finishes.",
    )


class EntryError(RuntimeError):
    """No entry strategy brought the device into fastboot."""


ScriptRunner = Callable[[DriverScript], Awaitable[None]]


class EntryRunner:
    """Tries entry strategies in order until the device is in fastboot."""

    def __init__(
        self,
        *,
        label: str,
        present: Callable[[], Awaitable[bool]],
        wait_present: Callable[[float], Awaitable[bool]],
        diagnose: Callable[[], Awaitable[str]],
        logger: logging.Logger,
        script_runner: ScriptRunner,
    ):
        self.label = label
        self.present = present
        self.wait_present = wait_present
        self.diagnose = diagnose
        self.logger = logger
        self.script_runner = script_runner

    async def enter(self, strategies: list[EntryStrategy], only: str | None = None) -> str:
        """Bring the device into fastboot; return the strategy that worked."""
        if await self.present():
            return "already-present"
        candidates = [s for s in strategies if only is None or s.name == only]
        if only is not None and not candidates:
            raise ValueError(f"unknown entry strategy {only!r}")
        if not candidates:
            raise EntryError(f"no entry strategy is configured. {await self._diagnose()}")

        errors = []
        for strategy in candidates:
            self.logger.info("Entering fastboot with strategy %r", strategy.name)
            try:
                await self.script_runner(strategy)
                if await self.wait_present(strategy.wait):
                    self.logger.info("Device %s is in fastboot (strategy %r)", self.label, strategy.name)
                    return strategy.name
                errors.append(f"{strategy.name}: device did not appear in fastboot within {strategy.wait}s")
            except Exception as exc:  # noqa: BLE001 - try the next strategy
                errors.append(f"{strategy.name}: {exc}")
                self.logger.warning("Entry strategy %r failed: %s", strategy.name, exc)
            finally:
                await self._cleanup(strategy)
        raise EntryError("could not enter fastboot: " + "; ".join(errors) + f". {await self._diagnose()}")

    async def _cleanup(self, strategy: EntryStrategy) -> None:
        if strategy.cleanup is None:
            return
        try:
            await self.script_runner(strategy.cleanup)
        except Exception:
            self.logger.warning("Cleanup for entry strategy %r failed", strategy.name, exc_info=True)

    async def _diagnose(self) -> str:
        try:
            return await self.diagnose()
        except Exception as exc:  # noqa: BLE001 - only an explanation
            return str(exc)
