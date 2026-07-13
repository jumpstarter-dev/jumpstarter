import signal
from unittest.mock import MagicMock, patch

import pytest

from jumpstarter_driver_ustreamer.driver import UStreamer, _get_preexec_fn

from jumpstarter.common.utils import serve


def test_drivers_video_ustreamer():
    try:
        instance = UStreamer()
    except FileNotFoundError:
        pytest.skip("ustreamer not available")  # ty: ignore[call-non-callable]

    with serve(instance) as client:
        assert client.state().ok
        _ = client.snapshot()


def test_get_preexec_fn_non_linux():
    with patch("jumpstarter_driver_ustreamer.driver._IS_LINUX", False):
        assert _get_preexec_fn() is None


def test_get_preexec_fn_sets_pdeathsig_on_linux():
    with (
        patch("jumpstarter_driver_ustreamer.driver._IS_LINUX", True),
        patch("jumpstarter_driver_ustreamer.driver.ctypes.CDLL") as mock_cdll,
    ):
        libc = mock_cdll.return_value
        libc.prctl.return_value = 0

        preexec_fn = _get_preexec_fn()

        assert preexec_fn is not None
        assert callable(preexec_fn)
        preexec_fn()

        mock_cdll.assert_called_once_with("libc.so.6", use_errno=True)
        libc.prctl.assert_called_once_with(1, signal.SIGTERM, 0, 0, 0)


def test_get_preexec_fn_raises_when_prctl_fails():
    with (
        patch("jumpstarter_driver_ustreamer.driver._IS_LINUX", True),
        patch("jumpstarter_driver_ustreamer.driver.ctypes.CDLL") as mock_cdll,
        patch("jumpstarter_driver_ustreamer.driver.ctypes.get_errno", return_value=22),
    ):
        mock_cdll.return_value.prctl.return_value = 1

        preexec_fn = _get_preexec_fn()

        assert preexec_fn is not None
        with pytest.raises(OSError, match="prctl\\(PR_SET_PDEATHSIG\\) failed") as exc_info:
            preexec_fn()

        exc = exc_info.value
        assert isinstance(exc, OSError)
        assert exc.errno == 22


def test_ustreamer_passes_preexec_fn_to_popen():
    mock_proc = MagicMock()
    with (
        patch("jumpstarter_driver_ustreamer.driver.Popen", return_value=mock_proc) as mock_popen,
        patch("jumpstarter_driver_ustreamer.driver._get_preexec_fn", return_value=MagicMock(name="preexec")) as mock_fn,
    ):
        instance = UStreamer(executable="/usr/bin/ustreamer", args={"device": "/dev/video0"})
        mock_popen.assert_called_once()
        assert mock_popen.call_args.kwargs["preexec_fn"] is mock_fn.return_value
        instance.close()
        mock_proc.terminate.assert_called_once()
