import importlib.metadata
import os
import sys

import click
import yaml
from pydantic import BaseModel, ConfigDict, Field

from .opt import OutputMode, OutputType, opt_output_all


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


class JumpstarterVersion(BaseModel):
    git_version: str = Field(alias="gitVersion")
    python_version: str = Field(alias="pythonVersion")

    model_config = ConfigDict(populate_by_name=True)

    def dump_json(self):
        return self.model_dump_json(indent=4, by_alias=True)

    def dump_yaml(self):
        return yaml.safe_dump(self.model_dump(mode="json", by_alias=True), indent=2)


def version_obj():
    return JumpstarterVersion(git_version=importlib.metadata.version("jumpstarter"), python_version=sys.version)


@click.command()
@opt_output_all
def version(output: OutputType):
    """Get the current Jumpstarter version"""
    if output == OutputMode.JSON:
        click.echo(version_obj().dump_json())
    elif output == OutputMode.YAML:
        click.echo(version_obj().dump_yaml())
    else:
        click.echo(version_msg())
