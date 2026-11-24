import click
from jumpstarter_cli_common.alias import AliasedGroup
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.exceptions import handle_exceptions_with_reauthentication
from jumpstarter_cli_common.opt import OutputMode, OutputType, opt_comma_separated, opt_output_all
from jumpstarter_cli_common.print import model_print

from .common import opt_selector
from .login import relogin_client
from .mobly import export_mobly_testbed
from jumpstarter.client.status import status_help_text


@click.group(cls=AliasedGroup)
def get():
    """
    Display one or many resources
    """


class _ExportersCommand(click.Command):
    """Command subclass that appends a dynamic status-icon legend to --help."""

    def format_epilog(self, ctx, formatter):
        formatter.write_paragraph()
        with formatter.indentation():
            formatter.write_text(status_help_text())


@get.command(name="exporters", cls=_ExportersCommand)
@opt_config(exporter=False)
@opt_selector
@opt_output_all
@opt_comma_separated(
    "with",
    {"leases", "online", "status", "ports"},
    help_text="Include fields: leases, online, status, ports (comma-separated or repeated)",
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

    With '--with ports', each exporter shows the ports that its drivers
    declare, with the direction of each port. A forward of a lease with
    members connects a 'provides' port to 'requires' ports, or two
    'connects' ports (see 'jmp create lease --forward').
    """

    include_leases = "leases" in with_options
    include_online = "online" in with_options
    include_status = "status" in with_options
    # Passed only when it is asked for, so a request without it is the one of before ports.
    ports = {"include_ports": True} if "ports" in with_options else {}
    exporters = config.list_exporters(
        filter=selector,
        include_leases=include_leases,
        include_online=include_online,
        include_status=include_status,
        include_disabled=allow_disabled,
        show_hidden_labels=show_hidden_labels,
        page_size=page_size,
        **ports,
    )

    model_print(exporters, output)


OUTPUT_MOBLY = "mobly"

opt_output_leases = click.option(
    "-o",
    "--output",
    type=click.Choice([OutputMode.JSON, OutputMode.YAML, OutputMode.NAME, OUTPUT_MOBLY]),
    default=None,
    help='Output mode. Use "-o name" for shorter output (resource/name). '
    'With NAME, "-o mobly" writes a Mobly testbed file for a lease with members (run it in jmp shell).',
)


@get.command(name="leases")
@opt_config(exporter=False)
@click.argument("name", required=False, default=None)
@opt_selector
@opt_output_leases
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
    name: str | None = None,
):
    """
    Display one or many leases

    A lease with members shows the exporter of each role, and the lease
    template that it was made from. A lease with forwards shows the state
    of each forward.
    """

    if output == OUTPUT_MOBLY and not name:
        raise click.UsageError("-o mobly needs the NAME of a lease")

    if name:
        if selector or tag_filter:
            raise click.UsageError("NAME cannot be combined with --selector or --tag-filter")
        lease = config.get_lease(name=name)
        if output == OUTPUT_MOBLY:
            if not lease.members:
                raise click.UsageError(f"-o mobly needs a lease with members: lease {name} has one exporter")
            click.echo(export_mobly_testbed(name), nl=False)
            return
        model_print(lease, output)
        return

    leases = config.list_leases(
        filter=selector, only_active=not show_all, tag_filter=tag_filter, page_size=page_size
    ).filter_by_selector(selector)

    if not all_clients:
        leases = leases.filter_by_client(config.metadata.name)

    model_print(leases, output, viewer=config.metadata.name)


@get.command(name="lease-templates")
@opt_config(exporter=False)
@opt_output_all
@click.option("--page-size", type=click.IntRange(min=1), default=100, help="Number of results per page for pagination")
@handle_exceptions_with_reauthentication(relogin_client)
def get_lease_templates(config, output: OutputType, page_size: int):
    """
    Display the lease templates

    A lease template names a set of exporters that belong together. Request
    one with 'jmp shell -t NAME' or 'jmp create lease -t NAME'. A * marks an
    optional member. AVAILABLE is the number of members that have at least one
    exporter that you can lease now; it is only a guide. FORWARDS are the
    forwards that connect the members of a lease from the template.
    """

    model_print(config.list_lease_templates(page_size=page_size), output)
