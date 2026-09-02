import shutil
import subprocess
import urllib.request
from pathlib import Path

import click
from jumpstarter_cli_common.exceptions import handle_exceptions


def _determine_install_dir() -> Path | None:
    jmp_path = shutil.which("jmp")
    if jmp_path:
        return Path(jmp_path).parent.parent
    return None

def _fetch_install_script() -> str:
    INSTALL_SCRIPT_URL = "https://raw.githubusercontent.com/jumpstarter-dev/jumpstarter/main/python/install.sh"
    with urllib.request.urlopen(INSTALL_SCRIPT_URL) as response:
        return response.read().decode("utf-8")


@click.command("update")
@click.option(
    "--source",
    type=str,
    help="Overwrites the current installation source. Available: latest, rc, main and release-x.x",
    default=None,
)
@handle_exceptions
def self_update(source: str):
    """
    Update jumpstarter
    """
    install_dir = _determine_install_dir()
    script = _fetch_install_script()

    cmd = ["bash", "-s", "-"]
    if install_dir:
        cmd.extend(["--dir", f"{install_dir}"])
    if source:
        cmd.extend(["--source", source])

    subprocess.run(cmd, input=script, check=True)
