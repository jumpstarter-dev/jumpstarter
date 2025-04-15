from functools import partial

import asyncclick as click

echo = partial(click.echo, nl=False)
