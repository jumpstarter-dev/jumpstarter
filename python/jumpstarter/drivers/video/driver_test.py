import pytest

from jumpstarter.common.utils import serve
from jumpstarter.drivers.video.driver import UStreamer


def test_drivers_video_ustreamer():
    try:
        instance = UStreamer(name="ustreamer")
    except FileNotFoundError:
        pytest.skip("ustreamer not available")

    with serve(instance) as client:
        assert client.state().ok
        _ = client.snapshot()
