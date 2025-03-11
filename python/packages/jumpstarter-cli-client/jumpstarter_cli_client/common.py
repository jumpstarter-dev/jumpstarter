import asyncclick as click

from jumpstarter.config import (
    ClientConfigV1Alpha1,
    UserConfigV1Alpha1,
)

opt_context = click.option("--client", "--context", "context", help="Name of client config")

opt_selector_simple = click.option(
    "-l",
    "--selector",
    help="Selector (label query) to filter on, only supports '=', (e.g. -l key1=value1,key2=value2)."
    " Matching objects must satisfy all of the specified label constraints.",
    required=True,
)


def selector_to_labels(selector: str):
    # TODO: support complex selectors (e.g. !=)
    return dict([term.split("=") for term in selector.split(",")])


def load_context(context: str | None) -> ClientConfigV1Alpha1:
    if context:
        config = ClientConfigV1Alpha1.load(context)
    else:
        config = UserConfigV1Alpha1.load_or_create().config.current_client
    if not config:
        raise click.BadOptionUsage(
            "--context",
            "no client context specified, and no default client context set",
        )
    return config
