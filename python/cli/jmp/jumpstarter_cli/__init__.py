import asyncclick as click
from jumpstarter_admin_cli import admin
from jumpstarter_cli_common import AliasedGroup, version
from jumpstarter_client_cli import client
from jumpstarter_exporter_cli import exporter


@click.group(cls=AliasedGroup)
def jmp():
    """The Jumpstarter CLI"""

jmp.add_command(client)
jmp.add_command(exporter)
jmp.add_command(admin)
jmp.add_command(version)

if __name__ == "__main__":
    jmp()
