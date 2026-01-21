import pytest

from jumpstarter_driver_ustreamer.driver import UStreamer

from jumpstarter.common.utils import serve


def test_drivers_video_ustreamer():
    try:
        instance = UStreamer()
    except FileNotFoundError:
        pytest.skip("ustreamer not available") # ty: ignore[call-non-callable]

    with serve(instance) as client:
        assert client.state().ok
        _ = client.snapshot()
