from datetime import timedelta
from functools import partial

import asyncclick as click
from pydantic import TypeAdapter

from jumpstarter.config import (
    ClientConfigV1Alpha1,
    UserConfigV1Alpha1,
)

opt_selector = click.option(
    "-l",
    "--selector",
    help="Selector (label query) to filter on, supports '=', '==', and '!=' (e.g. -l key1=value1,key2=value2)."
    " Matching objects must satisfy all of the specified label constraints.",
)

opt_selector_simple = click.option(
    "-l",
    "--selector",
    help="Selector (label query) to filter on, only supports '=', (e.g. -l key1=value1,key2=value2)."
    " Matching objects must satisfy all of the specified label constraints.",
    required=True,
)


class ClientParamType(click.ParamType):
    name = "client"

    def convert(self, value, param, ctx):
        if isinstance(value, ClientConfigV1Alpha1):
            return value

        if isinstance(value, bool):  # hack to allow loading the default config
            config = UserConfigV1Alpha1.load_or_create().config.current_client
            if config is None:
                self.fail("no client config specified, and no default client config set", param, ctx)
            return config
        else:
            return ClientConfigV1Alpha1.load(value)


class DurationParamType(click.ParamType):
    name = "duration"

    def convert(self, value, param, ctx):
        if isinstance(value, timedelta):
            return value

        try:
            return TypeAdapter(timedelta).validate_python(value)
        except ValueError:
            self.fail(f"{value!r} is not a valid duration", param, ctx)


DURATION = DurationParamType()
CLIENT = ClientParamType()

opt_config = click.option("--client", "config", type=CLIENT, default=False, help="Name of client config")
opt_duration_partial = partial(
    click.option,
    "--duration",
    "duration",
    type=DURATION,
    help="""
Accepted duration formats:

\b
PnYnMnDTnHnMnS - ISO 8601 duration format
HH:MM:SS - time in hours, minutes, seconds
D days, HH:MM:SS - time prefixed by X days
D d, HH:MM:SS - time prefixed by X d

See https://docs.rs/speedate/latest/speedate/ for details
""",
)
