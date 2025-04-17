import asyncclick as click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.version import version

from .create import create
from .delete import delete
from .get import get
from .import_res import import_res
from .install import install


@click.group(cls=AliasedGroup)
def admin():
    """Jumpstarter Kubernetes cluster admin CLI tool"""


admin.add_command(get)
admin.add_command(create)
admin.add_command(delete)
admin.add_command(install)
admin.add_command(import_res)
admin.add_command(version)

if __name__ == "__main__":
    admin()
