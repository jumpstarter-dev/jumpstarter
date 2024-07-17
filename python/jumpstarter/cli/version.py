import click
import importlib.metadata
import sys
import os


def get_client_version():
    """Get the version of the Jumpstarter Python client/exporter."""
    return importlib.metadata.version('jumpstarter')


def get_cli_path():
    """Get the path of the current Jumpstarter binary."""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def version_msg():
    """Generate a human-readable version message for Jumpstarter."""
    python_version = sys.version
    jumpstarter_version = get_client_version()
    location = get_cli_path()
    return f"Jumpstarter v{jumpstarter_version} from {location} (Python {python_version})"


@click.command()
def version():
    """Get the current Jumpstarter version."""
    click.echo(version_msg())
