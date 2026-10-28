import json
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from io import BytesIO
from pathlib import Path
from typing import Any

import click
from anyio import EndOfStream
from anyio.abc import ObjectStream

from .media import describe_sbc_format, ffmpeg_sbc_args
from jumpstarter.client import DriverClient


@dataclass(kw_only=True)
class _BytesStream(ObjectStream[bytes]):
    """Feeds an in-memory buffer to the exporter as a client resource."""

    buf: BytesIO

    async def send(self, item: bytes):
        self.buf.write(item)

    async def receive(self) -> bytes:
        item = self.buf.read(65536)
        if len(item) == 0:
            raise EndOfStream
        return item

    async def send_eof(self):
        pass

    async def aclose(self):
        pass


def _parse(raw: str) -> dict[str, Any] | list[Any] | str:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return raw


def _parse_dict(raw: str) -> dict[str, Any]:
    result = _parse(raw)
    if not isinstance(result, dict):
        raise TypeError(f"expected dict, got {type(result).__name__}: {raw!r}")
    return result


def _parse_list(raw: str) -> list[Any]:
    result = _parse(raw)
    if not isinstance(result, list):
        raise TypeError(f"expected list, got {type(result).__name__}: {raw!r}")
    return result


def _echo(obj: object) -> None:
    if isinstance(obj, (dict, list)):
        click.echo(json.dumps(obj, indent=2))
    else:
        click.echo(obj)


def _transcode_to_sbc(source: Path, destination: Path) -> None:
    """Re-encode any ffmpeg-readable audio into the peer's SBC configuration.

    Transcoding happens here rather than on the exporter so the exporter image
    does not need ffmpeg; the upload is the encoded SBC.
    """
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise click.ClickException(
            f"ffmpeg is needed to convert {source.name} to SBC ({describe_sbc_format()}). "
            "Install it with 'brew install ffmpeg', or pass an already-encoded .sbc file."
        )

    result = subprocess.run(
        [ffmpeg, *ffmpeg_sbc_args(str(source), str(destination))],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise click.ClickException(f"ffmpeg could not convert {source.name}:\n{result.stderr.strip()}")


class BtPeerClient(DriverClient):
    """Client for the Bluetooth peer driver."""

    def start_peer(self, config_json: str = "{}") -> dict[str, Any]:
        return _parse_dict(self.call("start_peer", config_json))

    def stop_peer(self) -> dict[str, Any]:
        return _parse_dict(self.call("stop_peer"))

    def wait_connection(self, timeout: int = 30) -> dict[str, Any]:
        return _parse_dict(self.call("wait_connection", timeout))

    def wait_disconnection(self, timeout: int = 30) -> dict[str, Any]:
        return _parse_dict(self.call("wait_disconnection", timeout))

    def get_events(self, since: str = "0") -> list[Any]:
        return _parse_list(self.call("get_events", since))

    def get_address(self) -> str:
        return self.call("get_address")

    def pair(self, handle: int = 0) -> dict[str, Any]:
        return _parse_dict(self.call("pair", handle))

    def connect_to(self, address: str, timeout: int = 30) -> dict[str, Any]:
        return _parse_dict(self.call("connect_to", address, timeout))

    def get_connections(self) -> list[Any]:
        return _parse_list(self.call("get_connections"))

    def play(self, path: str | Path, loop: bool = False) -> dict[str, Any]:
        """Stream a local audio file to the DUT, transcoding if needed."""
        source = Path(path).expanduser().resolve()

        if source.suffix.lower() == ".sbc":
            encoded = source.read_bytes()
        else:
            with tempfile.TemporaryDirectory(prefix="bt-peer-") as workdir:
                destination = Path(workdir) / f"{source.stem}.sbc"
                _transcode_to_sbc(source, destination)
                encoded = destination.read_bytes()

        with (
            self.portal.wrap_async_context_manager(_BytesStream(buf=BytesIO(encoded))) as stream,
            self.portal.wrap_async_context_manager(self.resource_async(stream)) as handle,
        ):
            return _parse_dict(self.call("play", handle, source.name, loop))

    def pause(self) -> dict[str, Any]:
        return _parse_dict(self.call("pause"))

    def resume(self) -> dict[str, Any]:
        return _parse_dict(self.call("resume"))

    def stop_playback(self) -> dict[str, Any]:
        return _parse_dict(self.call("stop_playback"))

    def playback_status(self) -> dict[str, Any]:
        return _parse_dict(self.call("playback_status"))

    def cli(self):  # noqa: C901
        @click.group()
        def bt_peer():
            """Bluetooth peer device (bumble)."""

        @bt_peer.command("start")
        @click.argument("config", default="{}")
        def start_cmd(config: str):
            """Start the BT peer device.

            CONFIG is JSON: {"name": "...", "classic_enabled": true, "class_of_device": 123}
            """
            try:
                parsed = json.loads(config)
            except json.JSONDecodeError as exc:
                raise click.BadParameter(
                    f"CONFIG must be valid JSON: {exc.msg}",
                    param_hint="CONFIG",
                ) from exc
            if not isinstance(parsed, dict):
                raise click.BadParameter(
                    "CONFIG must be a JSON object",
                    param_hint="CONFIG",
                )
            _echo(self.start_peer(config))

        @bt_peer.command("stop")
        def stop_cmd():
            """Stop the BT peer device."""
            _echo(self.stop_peer())

        @bt_peer.command("wait-connection")
        @click.option("--timeout", "-t", default=30, help="Timeout in seconds")
        def wait_connection_cmd(timeout: int):
            """Wait for an incoming connection."""
            _echo(self.wait_connection(timeout))

        @bt_peer.command("wait-disconnection")
        @click.option("--timeout", "-t", default=30, help="Timeout in seconds")
        def wait_disconnection_cmd(timeout: int):
            """Wait for a disconnection."""
            _echo(self.wait_disconnection(timeout))

        @bt_peer.command("events")
        @click.option("--since", default="0", help="Timestamp filter")
        def events_cmd(since: str):
            """Show events since timestamp."""
            _echo(self.get_events(since))

        @bt_peer.command("address")
        def address_cmd():
            """Show the peer's Bluetooth address."""
            click.echo(self.get_address())

        @bt_peer.command("pair")
        @click.option("--handle", "-h", default=0, help="Connection handle")
        def pair_cmd(handle: int):
            """Authenticate and encrypt a connection."""
            _echo(self.pair(handle))

        @bt_peer.command("connect")
        @click.argument("address")
        @click.option("--timeout", "-t", default=30, help="Timeout in seconds")
        def connect_cmd(address: str, timeout: int):
            """Connect to a remote device (e.g. the CVD)."""
            _echo(self.connect_to(address, timeout))

        @bt_peer.command("connections")
        def connections_cmd():
            """Show active connections."""
            _echo(self.get_connections())

        @bt_peer.command("play")
        @click.argument(
            "audio_file",
            type=click.Path(exists=True, dir_okay=False, resolve_path=True, path_type=Path),
        )
        @click.option("--loop", is_flag=True, help="Repeat the track until stopped")
        def play_cmd(audio_file: Path, loop: bool):
            """Stream an audio file to the DUT over A2DP.

            Anything ffmpeg can read is transcoded locally to the SBC
            configuration the peer advertises. A .sbc file is sent as-is.
            """
            _echo(self.play(audio_file, loop))

        @bt_peer.command("pause")
        def pause_cmd():
            """Suspend the stream, keeping the track and its position."""
            _echo(self.pause())

        @bt_peer.command("resume")
        def resume_cmd():
            """Restart a stream suspended by pause."""
            _echo(self.resume())

        @bt_peer.command("stop-playback")
        def stop_playback_cmd():
            """Stop the current track and suspend the stream."""
            _echo(self.stop_playback())

        @bt_peer.command("playback")
        def playback_cmd():
            """Show what is currently streaming."""
            _echo(self.playback_status())

        return bt_peer
