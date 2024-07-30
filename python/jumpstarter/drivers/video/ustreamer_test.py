import pytest

from jumpstarter.common.utils import serve
from jumpstarter.drivers.video.ustreamer import UStreamer


def test_drivers_video_ustreamer():
    try:
        instance = UStreamer(name="ustreamer")
    except FileNotFoundError:
        pytest.skip("ustreamer not available")

    with serve(instance) as client:
        assert not client.state().online  # no active client
        _ = client.snapshot()
