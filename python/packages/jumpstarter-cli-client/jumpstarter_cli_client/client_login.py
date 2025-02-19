import asyncclick as click
from jumpstarter_cli_common.exceptions import async_handle_exceptions
from jumpstarter_cli_common.oidc import Config, decode_jwt_issuer, opt_client_id

from jumpstarter.common.exceptions import FileNotFoundError
from jumpstarter.config import ClientConfigV1Alpha1, ClientConfigV1Alpha1Drivers, ObjectMeta, UserConfigV1Alpha1


@click.command("login", short_help="Login")
@click.argument("alias", default=None)
@click.option(
    "-e",
    "--endpoint",
    type=str,
    help="Enter the Jumpstarter service endpoint.",
    default=None,
)
@click.option(
    "--namespace",
    type=str,
    help="Enter the Jumpstarter client namespace.",
    default=None,
)
@click.option(
    "--name",
    type=str,
    help="Enter the Jumpstarter client name.",
    default=None,
)
@click.option("--username", type=str, help="Enter the OIDC username.", default=None)
@click.option("--password", type=str, help="Enter the OIDC password.", default=None)
@click.option(
    "--issuer",
    type=str,
    help="Enter the OIDC issuer.",
    default=None,
)
@click.option(
    "--allow",
    type=str,
    help="A comma-separated list of driver client packages to load.",
    default="",
)
@click.option(
    "--unsafe", is_flag=True, help="Should all driver client packages be allowed to load (UNSAFE!).", default=None
)
@opt_client_id
@async_handle_exceptions
async def client_login(  # noqa: C901
    alias: str,
    endpoint: str,
    namespace: str,
    name: str,
    username: str | None,
    password: str | None,
    issuer: str,
    client_id: str,
    allow: str,
    unsafe: str,
):
    """Login into a jumpstarter instance"""

    if alias is not None:
        try:
            config = ClientConfigV1Alpha1.load(alias)
            issuer = decode_jwt_issuer(config.token)
        except FileNotFoundError:
            if namespace is None:
                namespace = click.prompt("Enter the Jumpstarter client namespace")
            if name is None:
                name = click.prompt("Enter the Jumpstarter client name")
            if endpoint is None:
                endpoint = click.prompt("Enter the Jumpstarter service endpoint")
            if unsafe is None:
                unsafe = click.confirm("Allow unsafe driver client imports?")
                if unsafe is False and allow == "":
                    allow = click.prompt(
                        "Enter a comma-separated list of allowed driver packages (optional)", default="", type=str
                    )

            config = ClientConfigV1Alpha1(
                alias=alias,
                metadata=ObjectMeta(namespace=namespace, name=name),
                endpoint=endpoint,
                token="",
                drivers=ClientConfigV1Alpha1Drivers(allow=allow.split(","), unsafe=unsafe),
            )
    else:
        config = UserConfigV1Alpha1.load_or_create().config.current_client
        if config is None:
            raise ValueError("no client specified")
        issuer = decode_jwt_issuer(config.token)

    if issuer is None:
        issuer = click.prompt("Enter the OIDC issuer")

    oidc = Config(issuer=issuer, client_id=client_id)

    if username is not None and password is not None:
        tokens = await oidc.password_grant(username, password)
    else:
        tokens = await oidc.authorization_code_grant()

    config.token = tokens["access_token"]

    ClientConfigV1Alpha1.save(config)
