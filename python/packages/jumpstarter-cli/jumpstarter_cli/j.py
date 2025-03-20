import sys

import asyncclick as click
from jumpstarter_cli_common.exceptions import handle_exceptions

from jumpstarter.common.utils import env


def j():
    with env() as client:

        @handle_exceptions
        def cli():
            client.cli()(standalone_mode=False)

        try:
            cli()
        except click.ClickException as e:
            e.show()
            sys.exit(1)
