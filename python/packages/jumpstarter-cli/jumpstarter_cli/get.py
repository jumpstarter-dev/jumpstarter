import click
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions_with_reauthentication
from jumpstarter_cli_common.opt import OutputType, opt_output_all
from jumpstarter_cli_common.print import model_print

from .common import opt_selector
from .login import relogin_client


def parse_with(ctx: click.Context, param: click.Parameter, value: str | tuple[str, ...] | None) -> list[str]:
    """Parse comma-separated values or repeated flags into a validated, normalized list.

    Supports both "--with a,b" and "--with a --with b" forms.
    Normalizes by stripping whitespace, lowercasing, deduplicating while preserving order.
    Validates against allowed fields and raises click.BadParameter on invalid tokens.
    """
    allowed_fields = {"leases", "online"}

    if not value:
        return []

    # Handle both single string and tuple (from multiple flag usage)
    if isinstance(value, str):
        items = [value]
    else:
        items = list(value)

    # Split CSV entries, normalize, and collect all tokens
    all_tokens = []
    for item in items:
        # Split on comma and process each token
        for token in item.split(','):
            token = token.strip().lower()
            if token:  # Drop empty tokens
                all_tokens.append(token)

    # Deduplicate while preserving order
    seen = set()
    unique_tokens = []
    for token in all_tokens:
        if token not in seen:
            seen.add(token)
            unique_tokens.append(token)

    # Validate each token against allowed fields
    for token in unique_tokens:
        if token not in allowed_fields:
            allowed_list = ", ".join(sorted(allowed_fields))
            raise click.BadParameter(
                f"Invalid field '{token}'. Allowed values are: {allowed_list}",
                ctx=ctx,
                param=param
            )

    return unique_tokens


@click.group()
def get():
    """
    Display one or many resources
    """


@get.command(name="exporters")
@opt_config(exporter=False)
@opt_selector
@opt_output_all
@click.option("--with", "with_options", callback=parse_with, multiple=True, help="fields: leases, online")
@handle_exceptions_with_reauthentication(relogin_client)
def get_exporters(config, selector: str | None, output: OutputType, with_options: list[str]):
    """
    Display one or many exporters
    """

    include_leases = "leases" in with_options
    include_online = "online" in with_options
    exporters = config.list_exporters(filter=selector, include_leases=include_leases, include_online=include_online)

    model_print(exporters, output)


@get.command(name="leases")
@opt_config(exporter=False)
@opt_selector
@opt_output_all
@handle_exceptions_with_reauthentication(relogin_client)
def get_leases(config, selector: str | None, output: OutputType):
    """
    Display one or many leases
    """

    leases = config.list_leases(filter=selector)

    model_print(leases, output)
