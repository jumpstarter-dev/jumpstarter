import asyncclick as click
from jumpstarter_cli_common.config import opt_config
from jumpstarter_cli_common.oidc import Config, decode_jwt_issuer, opt_oidc
from jumpstarter_cli_common.opt import confirm_insecure_tls, opt_insecure_tls_config, opt_nointeractive

from jumpstarter.config.client import ClientConfigV1Alpha1, ClientConfigV1Alpha1Drivers
from jumpstarter.config.common import ObjectMeta
from jumpstarter.config.exporter import ExporterConfigV1Alpha1


@click.command("login", short_help="Login")
@click.option("-e", "--endpoint", type=str, help="Enter the Jumpstarter service endpoint.", default=None)
@click.option("--namespace", type=str, help="Enter the Jumpstarter exporter namespace.", default=None)
@click.option("--name", type=str, help="Enter the Jumpstarter exporter name.", default=None)
@opt_oidc
# client specific
# TODO: warn if used with exporter
@click.option(
    "--allow",
    type=str,
    help="A comma-separated list of driver client packages to load.",
    default="",
)
@click.option(
    "--unsafe", is_flag=True, help="Should all driver client packages be allowed to load (UNSAFE!).", default=None
)
# end client specific
@opt_insecure_tls_config
@opt_nointeractive
@opt_config(allow_missing=True)
async def login(  # noqa: C901
    config,
    endpoint: str,
    namespace: str,
    name: str,
    username: str | None,
    password: str | None,
    token: str | None,
    issuer: str,
    client_id: str,
    connector_id: str,
    unsafe,
    insecure_tls_config: bool,
    nointeractive: bool,
    allow,
):
    """Login into a jumpstarter instance"""

    confirm_insecure_tls(insecure_tls_config, nointeractive)

    match config:
        case ClientConfigV1Alpha1():
            issuer = decode_jwt_issuer(config.token)
        case ExporterConfigV1Alpha1():
            issuer = decode_jwt_issuer(config.token)
        case (kind, value):
            if namespace is None:
                if nointeractive:
                    raise click.UsageError("Namespace is required in non-interactive mode.")
                namespace = click.prompt("Enter the Jumpstarter exporter namespace")
            if name is None:
                if nointeractive:
                    raise click.UsageError("Name is required in non-interactive mode.")
                name = click.prompt("Enter the Jumpstarter exporter name")
            if endpoint is None:
                if nointeractive:
                    raise click.UsageError("Endpoint is required in non-interactive mode.")
                endpoint = click.prompt("Enter the Jumpstarter service endpoint")

            if kind.startswith("client"):
                if unsafe is None:
                    unsafe = click.confirm("Allow unsafe driver client imports?")
                    if unsafe is False and allow == "":
                        if nointeractive:
                            raise click.UsageError("Allowed driver packages are required in non-interactive mode.")
                        allow = click.prompt(
                            "Enter a comma-separated list of allowed driver packages (optional)", default="", type=str
                        )

            if kind.startswith("client"):
                config = ClientConfigV1Alpha1(
                    alias=value if kind == "client" else "default",
                    metadata=ObjectMeta(namespace=namespace, name=name),
                    endpoint=endpoint,
                    token="",
                    drivers=ClientConfigV1Alpha1Drivers(allow=allow.split(","), unsafe=unsafe),
                )

            if kind.startswith("exporter"):
                config = ExporterConfigV1Alpha1(
                    alias=value if kind == "exporter" else "default",
                    metadata=ObjectMeta(namespace=namespace, name=name),
                    endpoint=endpoint,
                    token="",
                )

    if issuer is None:
        if nointeractive:
            raise click.UsageError("Issuer is required in non-interactive mode.")
        issuer = click.prompt("Enter the OIDC issuer")

    oidc = Config(issuer=issuer, client_id=client_id)

    if token is not None:
        kwargs = {"connector_id": connector_id} if connector_id is not None else {}
        tokens = await oidc.token_exchange_grant(token, **kwargs)
    elif username is not None and password is not None:
        tokens = await oidc.password_grant(username, password)
    else:
        tokens = await oidc.authorization_code_grant()

    config.token = tokens["access_token"]
    config.tls.insecure = insecure_tls_config

    match kind:
        case "client":
            ClientConfigV1Alpha1.save(config)
        case "client_config":
            ClientConfigV1Alpha1.save(config, value)
        case "exporter":
            ExporterConfigV1Alpha1.save(config)
        case "exporter_config":
            ExporterConfigV1Alpha1.save(config, value)
