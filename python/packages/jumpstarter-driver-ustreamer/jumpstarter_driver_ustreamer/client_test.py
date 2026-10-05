from unittest.mock import MagicMock

from click.testing import CliRunner
from jumpstarter_driver_video.client import VideoClient
from jumpstarter_driver_video.common import VideoState

from jumpstarter_driver_ustreamer.client import UStreamerClient
from jumpstarter_driver_ustreamer.common import UStreamerState


def _make_client():
    client = object.__new__(UStreamerClient)
    client.description = None
    client.methods_description = {}
    client.stack = MagicMock()
    return client


def _make_state():
    return UStreamerState.model_validate(
        {
            "ok": True,
            "result": {
                "source": {
                    "online": True,
                    "desired_fps": 30,
                    "captured_fps": 29,
                    "resolution": {"width": 1280, "height": 720},
                },
                "encoder": {"type": "CPU", "quality": 85},
            },
        }
    )


def test_state_returns_validated_model():
    client = _make_client()
    client.call = MagicMock(return_value=_make_state().model_dump(mode="python"))

    state = UStreamerClient.state(client)

    assert state == _make_state()
    client.call.assert_called_once_with("state")


def test_state_exposes_common_video_state_fields():
    """UStreamerState extends VideoState, so generic consumers can read the
    common fields while ustreamer's own detail stays available."""
    state = _make_state()

    assert isinstance(state, VideoState)
    assert state.online is True
    assert (state.width, state.height) == (1280, 720)
    assert state.fps == 29
    # ustreamer-specific detail is unchanged
    assert state.result.encoder.type == "CPU"
    assert state.result.source.desired_fps == 30


def test_ustreamer_inherits_video_client_methods():
    """UStreamerClient must expose the full VideoClient API."""
    assert issubclass(UStreamerClient, VideoClient)
    # snapshot/snapshot_bytes/stream come from VideoClient
    assert UStreamerClient.snapshot is VideoClient.snapshot
    assert UStreamerClient.snapshot_bytes is VideoClient.snapshot_bytes


def test_state_command_prints_source_and_encoder_details():
    client = _make_client()
    client.state = MagicMock(return_value=_make_state())

    result = CliRunner().invoke(client.cli(), ["state"])

    assert result.exit_code == 0
    assert "Online:     True" in result.output
    assert "Resolution: 1280x720" in result.output
    assert "FPS:        29/30" in result.output
    assert "Encoder:    CPU (quality: 85)" in result.output
