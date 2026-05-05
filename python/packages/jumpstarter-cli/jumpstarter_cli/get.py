import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions_with_reauthentication
from jumpstarter_cli_common.opt import OutputType, opt_comma_separated, opt_output_all
from jumpstarter_cli_common.print import model_print

from .common import opt_selector
from .login import relogin_client


@click.group(cls=AliasedGroup)
def get():
    """
    Display one or many resources
    """


@get.command(name="exporters")
@opt_config(exporter=False)
@opt_selector
@opt_output_all
@opt_comma_separated(
    "with",
    {"leases", "online", "status"},
    help_text="Include fields: leases, online, status (comma-separated or repeated)",
)
@handle_exceptions_with_reauthentication(relogin_client)
def get_exporters(config, selector: str | None, output: OutputType, with_options: list[str]):
    """
    Display one or many exporters
    """

    include_leases = "leases" in with_options
    include_online = "online" in with_options
    include_status = "status" in with_options
    exporters = config.list_exporters(
        filter=selector, include_leases=include_leases, include_online=include_online, include_status=include_status
    )

    model_print(exporters, output)


@get.command(name="leases")
@opt_config(exporter=False)
@opt_selector
@opt_output_all
@click.option("-a", "--all", "show_all", is_flag=True, default=False, help="Include expired leases")
@click.option("-A", "--all-clients", "all_clients", is_flag=True, default=False, help="Include leases from all clients")
@click.option(
    "--tag-filter",
    "tag_filter",
    type=str,
    default=None,
    help="Filter leases by tags (label selector syntax, e.g. build=1234)",
)
@handle_exceptions_with_reauthentication(relogin_client)
def get_leases(
    config, selector: str | None, output: OutputType, show_all: bool, all_clients: bool, tag_filter: str | None
):
    """
    Display one or many leases
    """

    leases = config.list_leases(filter=selector, only_active=not show_all, tag_filter=tag_filter).filter_by_selector(
        selector
    )

    if not all_clients:
        leases = leases.filter_by_client(config.metadata.name)

    model_print(leases, output)
