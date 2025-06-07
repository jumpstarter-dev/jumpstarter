import os
import subprocess
from unittest.mock import MagicMock, call, patch

import pytest

from jumpstarter_driver_android.driver.adb import AdbServer

from jumpstarter.common.exceptions import ConfigurationError


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run")
def test_start_server(mock_subprocess_run: MagicMock, _: MagicMock):
    mock_subprocess_run.side_effect = [
        MagicMock(stdout="ADB version", stderr="", returncode=0),
        MagicMock(stdout="ADB server started", stderr="", returncode=0),
    ]

    adb_server = AdbServer()
    port = adb_server.start_server()

    assert port == 5037
    mock_subprocess_run.assert_has_calls(
        [
            call(["/usr/bin/adb", "version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
            call(
                ["/usr/bin/adb", "start-server"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={"ANDROID_ADB_SERVER_PORT": "5037", **dict(os.environ)},
            ),
        ]
    )


@patch("shutil.which", return_value="/usr/bin/adb")
@patch("subprocess.run")
def test_kill_server(mock_subprocess_run: MagicMock, _: MagicMock):
    mock_subprocess_run.side_effect = [
        MagicMock(stdout="ADB version", stderr="", returncode=0),
        MagicMock(stdout="ADB server stopped", stderr="", returncode=0),
    ]

    adb_server = AdbServer()
    port = adb_server.kill_server()

    assert port == 5037
    mock_subprocess_run.assert_has_calls(
        [
            call(["/usr/bin/adb", "version"], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True),
            call(
                ["/usr/bin/adb", "kill-server"],
                check=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                env={"ANDROID_ADB_SERVER_PORT": "5037", **dict(os.environ)},
            ),
        ]
    )


@patch("shutil.which", return_value=None)
def test_missing_adb_executable(_: MagicMock) -> None:
    with pytest.raises(ConfigurationError):
        AdbServer()


def test_invalid_port():
    with pytest.raises(ConfigurationError):
        AdbServer(port=-1)

    with pytest.raises(ConfigurationError):
        AdbServer(port=70000)

    with pytest.raises(ConfigurationError):
        AdbServer(port="not_an_int")
