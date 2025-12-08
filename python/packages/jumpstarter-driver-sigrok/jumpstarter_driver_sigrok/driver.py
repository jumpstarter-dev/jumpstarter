from __future__ import annotations

import asyncio
import subprocess
from base64 import b64encode
from dataclasses import dataclass, field
from pathlib import Path
from shutil import which
from tempfile import TemporaryDirectory

from .common import CaptureConfig, DecoderConfig, OutputFormat
from jumpstarter.driver import Driver, export


def find_sigrok_cli() -> str | None:
    """Find sigrok-cli executable in PATH.

    Returns:
        Path to executable or None if not found
    """
    return which("sigrok-cli")


@dataclass(kw_only=True)
class Sigrok(Driver):
    """Sigrok driver wrapping sigrok-cli for logic analyzer and oscilloscope support."""

    driver: str = "demo"
    conn: str | None = None
    executable: str | None = field(default_factory=find_sigrok_cli)
    channels: dict[str, str] = field(default_factory=dict)

    def __post_init__(self):
        if hasattr(super(), "__post_init__"):
            super().__post_init__()

    def _ensure_executable(self):
        """Ensure sigrok-cli is available."""
        if self.executable is None:
            raise FileNotFoundError(
                "sigrok-cli executable not found in PATH. "
                "Please install sigrok-cli to use this driver."
            )

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_sigrok.client.SigrokClient"

    # --- Public API -----------------------------------------------------

    @export
    def scan(self) -> str:
        """List devices for the configured driver."""
        self._ensure_executable()
        assert self.executable is not None
        cmd = [self.executable, "--driver", self.driver, "--scan"]
        result = subprocess.run(cmd, capture_output=True, text=True, check=True)
        return result.stdout

    @export
    def get_driver_info(self) -> dict:
        return {
            "driver": self.driver,
            "conn": self.conn,
            "channels": self.channels,
        }

    @export
    def get_channel_map(self) -> dict[int, str]:
        return self.channels

    @export
    def list_output_formats(self) -> list[str]:
        return OutputFormat.all()

    @export
    def capture(self, config: CaptureConfig | dict) -> dict:
        """One-shot capture; returns dict with base64-encoded binary data."""
        self._ensure_executable()
        cfg = CaptureConfig.model_validate(config)
        cmd, outfile, tmpdir = self._build_capture_command(cfg)

        try:
            self.logger.debug("running sigrok-cli: %s", " ".join(cmd))
            subprocess.run(cmd, check=True)

            data = outfile.read_bytes()
            # Return as dict with base64-encoded data (reliable for JSON transport)
            return {
                "data_b64": b64encode(data).decode("ascii"),
                "output_format": cfg.output_format,
                "sample_rate": cfg.sample_rate,
                "channel_map": self.channels,
                "triggers": cfg.triggers,
                "decoders": [d.model_dump() for d in cfg.decoders] if cfg.decoders else None,
            }
        finally:
            tmpdir.cleanup()

    @export
    async def capture_stream(self, config: CaptureConfig | dict):
        """Streaming capture; yields chunks of binary data from sigrok-cli stdout."""
        self._ensure_executable()
        cfg = CaptureConfig.model_validate(config)
        cmd = self._build_stream_command(cfg)

        self.logger.debug("streaming sigrok-cli: %s", " ".join(cmd))
        process = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        try:
            if process.stdout is None:
                raise RuntimeError("sigrok-cli stdout not available")

            # Stream data in chunks
            while True:
                chunk = await process.stdout.read(4096)
                if not chunk:
                    break
                yield chunk
        finally:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=5)
            except asyncio.TimeoutError:
                process.kill()

    # --- Command builders -----------------------------------------------

    def _build_capture_command(self, cfg: CaptureConfig) -> tuple[list[str], Path, TemporaryDirectory]:
        tmpdir = TemporaryDirectory()
        outfile = Path(tmpdir.name) / f"capture.{cfg.output_format}"

        cmd: list[str] = self._base_driver_args()
        cmd += self._channel_args(cfg.channels)
        cmd += self._config_args(cfg)
        cmd += self._trigger_args(cfg)
        cmd += self._decoder_args(cfg)
        cmd += ["-O", cfg.output_format, "-o", str(outfile)]

        return cmd, outfile, tmpdir

    def _build_stream_command(self, cfg: CaptureConfig) -> list[str]:
        cmd: list[str] = self._base_driver_args()
        cmd += self._channel_args(cfg.channels)
        cmd += self._config_args(cfg, continuous=True)
        cmd += self._trigger_args(cfg)
        cmd += self._decoder_args(cfg)
        cmd += ["-O", cfg.output_format, "-o", "-"]
        return cmd

    def _base_driver_args(self) -> list[str]:
        assert self.executable is not None
        if self.conn:
            return [self.executable, "-d", f"{self.driver}:conn={self.conn}"]
        return [self.executable, "-d", self.driver]

    def _channel_args(self, selected_names: list[str] | None) -> list[str]:
        """Build channel selection/renaming args for sigrok-cli.

        Args:
            selected_names: Optional list of semantic names to include

        Returns:
            List of args like ["-C", "D0=vcc,D1=cs,D2=miso"]
        """
        if not self.channels:
            return []

        # Filter channels if specific names requested
        if selected_names:
            selected_lower = {name.lower() for name in selected_names}
            filtered = {dev: user for dev, user in self.channels.items() if user.lower() in selected_lower}
        else:
            filtered = self.channels

        # Build channel map: device_name=user_name
        channel_map = ",".join(f"{dev}={user}" for dev, user in filtered.items())
        return ["-C", channel_map] if channel_map else []

    def _config_args(self, cfg: CaptureConfig, *, continuous: bool = False) -> list[str]:
        parts = [f"samplerate={cfg.sample_rate}"]
        if cfg.pretrigger is not None:
            parts.append(f"pretrigger={cfg.pretrigger}")

        args: list[str] = []
        if parts:
            args += ["-c", ",".join(parts)]

        # sigrok-cli requires one of: --samples, --frames, --time, or --continuous
        # If samples is explicitly specified, use that even for streaming
        if cfg.samples is not None:
            args.extend(["--samples", str(cfg.samples)])
        elif continuous:
            args.append("--continuous")
        else:
            # Default to 1000 samples if not specified
            args.extend(["--samples", "1000"])

        return args

    def _trigger_args(self, cfg: CaptureConfig) -> list[str]:
        if not cfg.triggers:
            return []
        trigger_parts = []
        for channel, condition in cfg.triggers.items():
            resolved = self._resolve_channel(channel)
            trigger_parts.append(f"{resolved}={condition}")
        return ["--triggers", ",".join(trigger_parts)]

    def _decoder_args(self, cfg: CaptureConfig) -> list[str]:
        if not cfg.decoders:
            return []

        args: list[str] = []
        for decoder in self._flatten_decoders(cfg.decoders):
            pin_map = self._resolve_decoder_channels(decoder)
            segments = [decoder.name]

            for pin_name, channel_name in pin_map.items():
                segments.append(f"{pin_name}={self._resolve_channel(channel_name)}")

            if decoder.options:
                for key, value in decoder.options.items():
                    segments.append(f"{key}={value}")

            args += ["-P", ":".join(segments)]

            if decoder.annotations:
                args += ["-A", f"{decoder.name}=" + ",".join(decoder.annotations)]

        return args

    def _flatten_decoders(self, decoders: list[DecoderConfig]) -> list[DecoderConfig]:
        flat: list[DecoderConfig] = []
        for decoder in decoders:
            flat.append(decoder)
            if decoder.stack:
                flat.extend(self._flatten_decoders(decoder.stack))
        return flat

    def _resolve_decoder_channels(self, decoder: DecoderConfig) -> dict[str, str]:
        if decoder.channels:
            return decoder.channels

        # Best-effort auto-mapping based on common decoder pin names
        defaults = {
            "spi": ["clk", "mosi", "miso", "cs"],
            "i2c": ["scl", "sda"],
            "uart": ["rx", "tx"],
        }
        pins = defaults.get(decoder.name.lower())
        if not pins:
            return {}

        resolved: dict[str, str] = {}
        available_lower = {name.lower(): name for name in self.channels.values()}
        for pin in pins:
            if pin in available_lower:
                resolved[pin] = available_lower[pin]
        return resolved

    def _resolve_channel(self, name_or_dn: str) -> str:
        """Resolve a user-friendly channel name to device channel name.

        Args:
            name_or_dn: User-friendly name (e.g., "clk", "mosi") or device name (e.g., "D0")

        Returns:
            Device channel name (e.g., "D0", "D1")
        """
        candidate = name_or_dn.strip()

        # If already a device channel name, return as-is
        if candidate in self.channels:
            return candidate

        # Search for user-friendly name in channel values
        for dev_name, user_name in self.channels.items():
            if user_name.lower() == candidate.lower():
                return dev_name

        raise ValueError(f"Channel '{name_or_dn}' not found in channel map {self.channels}")
