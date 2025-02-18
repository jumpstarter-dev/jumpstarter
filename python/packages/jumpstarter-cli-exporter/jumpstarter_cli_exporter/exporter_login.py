import asyncclick as click
from jumpstarter_cli_common.oidc import Config, decode_jwt_issuer, opt_client_id

from jumpstarter.config.exporter import ExporterConfigV1Alpha1, ObjectMeta


@click.command("login", short_help="Login")
@click.argument("alias", default="default")
@click.option("-e", "--endpoint", type=str, help="Enter the Jumpstarter service endpoint.", default=None)
@click.option("--namespace", type=str, help="Enter the Jumpstarter exporter namespace.", default=None)
@click.option("--name", type=str, help="Enter the Jumpstarter exporter name.", default=None)
@click.option("--username", type=str, help="Enter the OIDC username.", default=None)
@click.option("--password", type=str, help="Enter the OIDC password.", default=None)
@click.option("--issuer", type=str, help="Enter the OIDC issuer.", default=None)
@opt_client_id
async def exporter_login(
    alias: str,
    endpoint: str,
    namespace: str,
    name: str,
    username: str | None,
    password: str | None,
    issuer: str,
    client_id: str,
):
    """Login into a jumpstarter instance"""
    try:
        config = ExporterConfigV1Alpha1.load(alias)
        issuer = decode_jwt_issuer(config.token)
    except FileNotFoundError:
        if namespace is None:
            namespace = click.prompt("Enter the Jumpstarter exporter namespace")
        if name is None:
            name = click.prompt("Enter the Jumpstarter exporter name")
        if endpoint is None:
            endpoint = click.prompt("Enter the Jumpstarter service endpoint")

        config = ExporterConfigV1Alpha1(
            alias=alias,
            metadata=ObjectMeta(namespace=namespace, name=name),
            endpoint=endpoint,
            token="",
        )

    if issuer is None:
        issuer = click.prompt("Enter the OIDC issuer")

    oidc = Config(issuer=issuer, client_id=client_id)

    if username is not None and password is not None:
        tokens = await oidc.password_grant(username, password)
    else:
        tokens = await oidc.authorization_code_grant()

    config.token = tokens["access_token"]

    ExporterConfigV1Alpha1.save(config)
