from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from .self import self
from .self_update import _determine_install_dir, _fetch_install_script


@pytest.mark.parametrize(
    "jmp_path,expected_install_dir",
    [
        ("", None),
        (None, None),
        ("/home/user/.local/jumpstarter/bin/jmp", Path("/home/user/.local/jumpstarter")),
    ],
)
@patch("shutil.which")
def test__determine_install_dir(which_mock, jmp_path, expected_install_dir):
    which_mock.return_value = jmp_path
    install_dir = _determine_install_dir()
    assert install_dir == expected_install_dir

@patch("urllib.request.urlopen")
def test__fetch_install_script(urlopen_mock):
    response = MagicMock()
    response.read.return_value = b"#!/bin/sh"
    urlopen_mock.return_value.__enter__.return_value = response
    script = _fetch_install_script()
    assert script == "#!/bin/sh"

@pytest.mark.parametrize(
    "source,install_dir,expected_cmd",
    [
        (None, None, ["bash", "-s", "-"]),
        (None, "/home/user/.local/jumpstarter", ["bash", "-s", "-", "--dir", "/home/user/.local/jumpstarter"]),
        ("main", None, ["bash", "-s", "-", "--source", "main"]),
        ("main", "/home/user/.local/jumpstarter", [
            "bash", "-s", "-",
            "--dir", "/home/user/.local/jumpstarter",
            "--source", "main"]
        ),
    ],
)
@patch("jumpstarter_cli.self_update._determine_install_dir")
@patch("jumpstarter_cli.self_update._fetch_install_script")
@patch("subprocess.run")
def test_self_update(
    subprocess_mock,
    fetch_install_script_mock,
    determine_install_dir_mock,
    source,
    install_dir,
    expected_cmd
):
    determine_install_dir_mock.return_value = install_dir
    fetch_install_script_mock.return_value = "#!/bin/sh"

    CliRunner().invoke(self, ["update", "--source", source])

    subprocess_mock.assert_called_once_with(
        expected_cmd,
        input="#!/bin/sh",
        check=True,
    )
