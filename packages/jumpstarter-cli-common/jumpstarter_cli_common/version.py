import importlib.metadata
import os
import sys

import asyncclick as click
from pydantic import ConfigDict, Field

from .echo import echo
from .opt import OutputType, opt_output_auto
from jumpstarter.common.pydantic import SerializableBaseModel


def get_client_version():
    """Get the version of the Jumpstarter Python client/exporter"""
    return importlib.metadata.version("jumpstarter")


def get_cli_path():
    """Get the path of the current Jumpstarter CLI binary"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def version_msg():
    """Generate a human-readable version message for Jumpstarter"""
    python_version = sys.version
    jumpstarter_version = get_client_version()
    location = get_cli_path()
    return f"Jumpstarter v{jumpstarter_version} from {location} (Python {python_version})"


class JumpstarterVersion(SerializableBaseModel):
    git_version: str = Field(alias="gitVersion")
    python_version: str = Field(alias="pythonVersion")

    model_config = ConfigDict(populate_by_name=True)


def version_obj():
    return JumpstarterVersion(git_version=importlib.metadata.version("jumpstarter"), python_version=sys.version)


@click.command()
@opt_output_auto(JumpstarterVersion)
def version(output: OutputType):
    """Get the current Jumpstarter version"""
    if output:
        echo(version_obj().dump(output))
    else:
        click.echo(version_msg())
