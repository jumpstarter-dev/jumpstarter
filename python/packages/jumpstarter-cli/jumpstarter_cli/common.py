from datetime import datetime, timedelta
from functools import partial

import click
from pydantic import TypeAdapter, ValidationError
from pytimeparse2 import parse as parse_duration


def _opt_selector_callback(_ctx, _param, value):
    """Combine multiple selector values into a single comma-separated string."""
    return ",".join(value) if value else None


opt_selector = click.option(
    "-l",
    "--selector",
    multiple=True,
    callback=_opt_selector_callback,
    help="Selector (label query) to filter on, supports '=', '==', and '!=' (e.g. -l key1=value1,key2=value2)."
    " Matching objects must satisfy all of the specified label constraints. Can be specified multiple times.",
)

opt_exporter_name = click.option(
    "-n",
    "--name",
    "exporter_name",
    type=str,
    default=None,
    help="Target a specific exporter/device name directly.",
)


class DurationParamType(click.ParamType):
    name = "duration"

    def __init__(self, minimum: timedelta | None = None):
        super().__init__()
        self.minimum = minimum

    def _parse_string(self, value: str, param, ctx) -> timedelta:
        try:
            int_value = int(value)
            try:
                return timedelta(seconds=int_value)
            except OverflowError:
                self.fail(f"{value!r} exceeds the maximum allowed duration", param, ctx)
        except ValueError:
            pass

        try:
            seconds = parse_duration(value)
            if seconds is not None and isinstance(seconds, (int, float)):
                return timedelta(seconds=seconds)
        except (ValueError, TypeError, OverflowError):
            pass

        try:
            return TypeAdapter(timedelta).validate_python(value)
        except (ValueError, ValidationError) as exc:
            self.fail(
                f"{value!r} is not a valid duration (e.g., '30m', '3h30m', '1d', '1d3h40m', 'PT1H30M', '01:30:00')",
                param,
                ctx,
            )
            raise exc

    def convert(self, value, param, ctx):
        if isinstance(value, timedelta):
            td = value
        elif isinstance(value, int):
            try:
                td = timedelta(seconds=value)
            except OverflowError:
                self.fail(f"{value!r} exceeds the maximum allowed duration", param, ctx)
        elif isinstance(value, str):
            td = self._parse_string(value, param, ctx)
        else:
            self.fail(
                f"{value!r} is not a valid duration (e.g., '30m', '3h30m', '1d', '1d3h40m')",
                param,
                ctx,
            )

        if self.minimum is not None and td < self.minimum:
            min_seconds = int(self.minimum.total_seconds())
            self.fail(f"{value!r} must be at least {min_seconds} seconds", param, ctx)

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

See https://github.com/onegreyonewhite/pytimeparse2 for details
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
