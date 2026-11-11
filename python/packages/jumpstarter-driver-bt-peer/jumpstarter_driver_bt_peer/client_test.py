"""The local half: transcoding, tag probing and their timeouts."""

import subprocess
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from .client import _probe_metadata, _transcode_to_sbc, transcode_timeout_for
from .media import PROBE_TIMEOUT, TRANSCODE_BASE_TIMEOUT


def _completed(stdout: str = "", returncode: int = 0):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr="")


# -- probing tags --------------------------------------------------------


def test_probe_returns_nothing_without_ffprobe():
    with patch("jumpstarter_driver_bt_peer.client.shutil.which", return_value=None):
        assert _probe_metadata(Path("song.mp3")) == {}


def test_probe_reads_tags_and_duration():
    payload = '{"format": {"duration": "128.04", "tags": {"TITLE": "T", "artist": "A"}}}'
    with (
        patch("jumpstarter_driver_bt_peer.client.shutil.which", return_value="/usr/bin/ffprobe"),
        patch("jumpstarter_driver_bt_peer.client.subprocess.run", return_value=_completed(payload)),
    ):
        assert _probe_metadata(Path("song.mp3")) == {
            "title": "T",
            # Tag keys are case-insensitive in the containers ffprobe reads.
            "artist": "A",
            "album": "",
            "duration_ms": 128040,
        }


def test_probe_is_bounded_and_a_timeout_is_not_fatal():
    """A wedged ffprobe must cost the DUT a title, not the track.

    play() probes before it transcodes, so an unbounded call here would hang
    the whole command before any audio was even encoded.
    """
    with (
        patch("jumpstarter_driver_bt_peer.client.shutil.which", return_value="/usr/bin/ffprobe"),
        patch(
            "jumpstarter_driver_bt_peer.client.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffprobe", timeout=PROBE_TIMEOUT),
        ) as run,
    ):
        assert _probe_metadata(Path("song.mp3")) == {}

    assert run.call_args.kwargs["timeout"] == PROBE_TIMEOUT


@pytest.mark.parametrize(
    "payload",
    ["not json at all", '{"format": {"duration": "unknown"}}', "{}"],
)
def test_probe_survives_unusable_output(payload):
    with (
        patch("jumpstarter_driver_bt_peer.client.shutil.which", return_value="/usr/bin/ffprobe"),
        patch("jumpstarter_driver_bt_peer.client.subprocess.run", return_value=_completed(payload)),
    ):
        assert _probe_metadata(Path("song.mp3")).get("duration_ms", 0) == 0


def test_probe_returns_nothing_when_ffprobe_fails():
    with (
        patch("jumpstarter_driver_bt_peer.client.shutil.which", return_value="/usr/bin/ffprobe"),
        patch(
            "jumpstarter_driver_bt_peer.client.subprocess.run",
            return_value=_completed(returncode=1),
        ),
    ):
        assert _probe_metadata(Path("song.mp3")) == {}


# -- transcoding ---------------------------------------------------------


def test_transcode_timeout_scales_with_the_input(tmp_path):
    small = tmp_path / "small.mp3"
    small.write_bytes(b"x")
    assert transcode_timeout_for(small) == pytest.approx(TRANSCODE_BASE_TIMEOUT, abs=1)

    big = tmp_path / "big.mp3"
    big.write_bytes(b"x" * (8 * 1024 * 1024))
    assert transcode_timeout_for(big) > transcode_timeout_for(small)


def test_transcode_timeout_of_a_missing_file_is_the_base():
    assert transcode_timeout_for(Path("/nonexistent/song.mp3")) == TRANSCODE_BASE_TIMEOUT


def test_transcode_explains_a_missing_ffmpeg():
    import click

    with (
        patch("jumpstarter_driver_bt_peer.client.shutil.which", return_value=None),
        pytest.raises(click.ClickException, match="ffmpeg is needed"),
    ):
        _transcode_to_sbc(Path("song.mp3"), Path("song.sbc"))


def test_transcode_reports_a_timeout_rather_than_hanging(tmp_path):
    import click

    source = tmp_path / "song.mp3"
    source.write_bytes(b"x")

    with (
        patch("jumpstarter_driver_bt_peer.client.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch(
            "jumpstarter_driver_bt_peer.client.subprocess.run",
            side_effect=subprocess.TimeoutExpired(cmd="ffmpeg", timeout=60),
        ),
        pytest.raises(click.ClickException, match="did not finish converting"),
    ):
        _transcode_to_sbc(source, tmp_path / "song.sbc")


def test_transcode_surfaces_ffmpeg_stderr(tmp_path):
    import click

    source = tmp_path / "song.mp3"
    source.write_bytes(b"x")
    failed = MagicMock(returncode=1, stderr="Invalid data found when processing input")

    with (
        patch("jumpstarter_driver_bt_peer.client.shutil.which", return_value="/usr/bin/ffmpeg"),
        patch("jumpstarter_driver_bt_peer.client.subprocess.run", return_value=failed),
        pytest.raises(click.ClickException, match="Invalid data found"),
    ):
        _transcode_to_sbc(source, tmp_path / "song.sbc")
