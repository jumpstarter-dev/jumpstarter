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
@click.option(
    "--allow-disabled",
    is_flag=True,
    default=False,
    help="Include disabled exporters in the listing",
)
@click.option(
    "--show-hidden-labels",
    is_flag=True,
    default=False,
    help="Show labels hidden by controller config",
)
@click.option("--page-size", type=click.IntRange(min=1), default=100, help="Number of results per page for pagination")
@handle_exceptions_with_reauthentication(relogin_client)
def get_exporters(
    config,
    selector: str | None,
    output: OutputType,
    with_options: list[str],
    allow_disabled: bool,
    show_hidden_labels: bool,
    page_size: int,
):
    """
    Display one or many exporters
    """

    include_leases = "leases" in with_options
    include_online = "online" in with_options
    include_status = "status" in with_options
    exporters = config.list_exporters(
        filter=selector,
        include_leases=include_leases,
        include_online=include_online,
        include_status=include_status,
        include_disabled=allow_disabled,
        show_hidden_labels=show_hidden_labels,
        page_size=page_size,
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
@click.option("--page-size", type=click.IntRange(min=1), default=100, help="Number of results per page for pagination")
@handle_exceptions_with_reauthentication(relogin_client)
def get_leases(
    config,
    selector: str | None,
    output: OutputType,
    show_all: bool,
    all_clients: bool,
    tag_filter: str | None,
    page_size: int,
):
    """
    Display one or many leases
    """

    leases = config.list_leases(
        filter=selector, only_active=not show_all, tag_filter=tag_filter, page_size=page_size
    ).filter_by_selector(selector)

    if not all_clients:
        leases = leases.filter_by_client(config.metadata.name)

    model_print(leases, output)
