from datetime import datetime, timedelta
from functools import partial

import click
from pydantic import TypeAdapter, ValidationError

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
        except (ValueError, ValidationError):
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


class DateTimeParamType(click.ParamType):
    name = "datetime"

    def convert(self, value, param, ctx):
        if isinstance(value, datetime):
            dt = value
        else:
            try:
                dt = TypeAdapter(datetime).validate_python(value)
            except (ValueError, ValidationError):
                self.fail(f"{value!r} is not a valid datetime", param, ctx)

        # Normalize naive datetimes to local timezone
        if dt.tzinfo is None:
            dt = dt.astimezone()

        return dt


DATETIME = DateTimeParamType()

opt_begin_time = click.option(
    "--begin-time",
    "begin_time",
    type=DATETIME,
    default=None,
    help="""
Begin time for the lease in ISO 8601 format (e.g., 2024-01-01T12:00:00 or 2024-01-01T12:00:00Z).
If not specified, the lease tries to be acquired immediately. The lease duration always starts
at the actual time of acquisition.
""",
)
