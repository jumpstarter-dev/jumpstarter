from datetime import timedelta
from functools import partial

import click
from pydantic import TypeAdapter

opt_selector = click.option(
    "-l",
    "--selector",
    help="Selector (label query) to filter on, supports '=', '==', and '!=' (e.g. -l key1=value1,key2=value2)."
    " Matching objects must satisfy all of the specified label constraints.",
)


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
