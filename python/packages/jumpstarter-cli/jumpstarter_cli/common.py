from datetime import datetime, timedelta
from functools import partial

import click
from pydantic import TypeAdapter, ValidationError
from pytimeparse2 import parse as parse_duration


def _opt_selector_callback(ctx, param, value):
    return ",".join(value) if value else None

opt_selector = click.option(
    "-l",
    "--selector",
    multiple=True,
    callback=_opt_selector_callback,
    help="Selector (label query) to filter on, supports '=', '==', and '!=' (e.g. -l key1=value1,key2=value2)."
    " Matching objects must satisfy all of the specified label constraints. Can be specified multiple times.",
)


class DurationParamType(click.ParamType):
    name = "duration"

    def __init__(self, minimum: timedelta | None = None):
        super().__init__()
        self.minimum = minimum

    def convert(self, value, param, ctx):
        if isinstance(value, timedelta):
            td = value
        elif isinstance(value, int):
            # Integer as seconds (backward compatibility)
            td = timedelta(seconds=value)
        elif isinstance(value, str):
            # Try parsing as plain integer first (backward compatibility)
            try:
                int_value = int(value)
                td = timedelta(seconds=int_value)
            except ValueError:
                # Parse with pytimeparse2 first (supports human-readable formats)
                td = None
                try:
                    seconds = parse_duration(value)
                    if seconds is not None:
                        td = timedelta(seconds=seconds)
                except (ValueError, TypeError):
                    pass

                # Fall back to pydantic/speedate for ISO 8601 and other formats
                if td is None:
                    try:
                        td = TypeAdapter(timedelta).validate_python(value)
                    except (ValueError, ValidationError):
                        self.fail(
                            (
                                f"{value!r} is not a valid duration "
                                "(e.g., '30m', '3h30m', '1d', '1d3h40m', 'PT1H30M', '01:30:00')"
                            ),
                            param,
                            ctx,
                        )
        else:
            self.fail(
                f"{value!r} is not a valid duration (e.g., '30m', '3h30m', '1d', '1d3h40m')",
                param,
                ctx,
            )

        # Validate minimum if specified
        if self.minimum is not None and td < self.minimum:
            min_seconds = int(self.minimum.total_seconds())
            self.fail(
                f"{value!r} must be at least {min_seconds} seconds", param, ctx
            )

        return td


DURATION = DurationParamType()

opt_duration_partial = partial(
    click.option,
    "--duration",
    "duration",
    type=DURATION,
    help="""
Accepted duration formats:

\b
Human-readable: 30m, 3h30m, 1d, 1d3h40m, etc.
ISO 8601: PT1H30M, P1DT2H30M, etc.
Time format: 01:30:00, 2 days, 01:30:00, etc.

See https://github.com/wroberts/pytimeparse2 for details
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


ACQUISITION_TIMEOUT = DurationParamType(minimum=timedelta(seconds=5))

opt_acquisition_timeout = partial(
    click.option,
    "--acquisition-timeout",
    "acquisition_timeout",
    type=ACQUISITION_TIMEOUT,
    default=None,
    help=(
        "Override acquisition timeout (e.g., '30m', '3h30m', '1d', '1d3h40m', "
        "or seconds as integer). Must be >= 5 seconds."
    ),
)

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
