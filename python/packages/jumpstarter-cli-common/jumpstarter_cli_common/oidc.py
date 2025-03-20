import json
import os
from dataclasses import dataclass
from typing import ClassVar

import aiohttp
import asyncclick as click
import truststore
from aiohttp import web
from anyio import create_memory_object_stream
from anyio.to_thread import run_sync
from authlib.integrations.requests_client import OAuth2Session
from joserfc.jws import extract_compact
from yarl import URL

# if we are running in MacOS avoid injecting system certificates to avoid
# https://github.com/jumpstarter-dev/jumpstarter/issues/362
# also allow to force the system certificates injection with
# JUMPSTARTER_FORCE_SYSTEM_CERTS=1
if os.uname().sysname != "Darwin" or os.environ.get("JUMPSTARTER_FORCE_SYSTEM_CERTS") == "1":
    truststore.inject_into_ssl()


opt_client_id = click.option("--client-id", "client_id", type=str, default="jumpstarter-cli", help="OIDC client id")
opt_connector_id = click.option(
    "--connector-id", "connector_id", type=str, help="OIDC token exchange connector id (Dex specific)"
)


@dataclass(kw_only=True)
class Config:
    issuer: str
    client_id: str
    scope: ClassVar[list[str]] = ["openid", "profile"]

    async def configuration(self):
        async with aiohttp.ClientSession() as session:
            async with session.get(
                URL(self.issuer).joinpath(".well-known", "openid-configuration"),
                raise_for_status=True,
            ) as response:
                return await response.json()

    def client(self, **kwargs):
        return OAuth2Session(client_id=self.client_id, scope=self.scope, **kwargs)

    async def token_exchange_grant(self, token: str, **kwargs):
        config = await self.configuration()

        client = self.client()

        return await run_sync(
            lambda: client.fetch_token(
                config["token_endpoint"],
                grant_type="urn:ietf:params:oauth:grant-type:token-exchange",
                requested_token_type="urn:ietf:params:oauth:token-type:access_token",
                subject_token_type="urn:ietf:params:oauth:token-type:id_token",
                subject_token=token,
                **kwargs,
            )
        )

    async def password_grant(self, username: str, password: str):
        config = await self.configuration()

        client = self.client()

        return await run_sync(
            lambda: client.fetch_token(
                config["token_endpoint"], grant_type="password", username=username, password=password
            )
        )

    async def authorization_code_grant(self):
        config = await self.configuration()

        tx, rx = create_memory_object_stream()

        async def callback(request):
            await tx.send(str(request.url))
            return web.Response(text="Login successful, you can close this page")

        app = web.Application()
        app.add_routes([web.get("/callback", callback)])

        runner = web.AppRunner(app, access_log=None)
        await runner.setup()

        site = web.TCPSite(runner, "localhost", 0)
        await site.start()

        redirect_uri = "http://localhost:%d/callback" % site._server.sockets[0].getsockname()[1]

        client = self.client(redirect_uri=redirect_uri)

        uri, state = client.create_authorization_url(config["authorization_endpoint"])

        print("Please open the URL in browser: ", uri)

        authorization_response = await rx.receive()

        await site.stop()
        await runner.cleanup()

        return await run_sync(
            lambda: client.fetch_token(config["token_endpoint"], authorization_response=authorization_response)
        )


def decode_jwt(token: str):
    return json.loads(extract_compact(token.encode()).payload)


def decode_jwt_issuer(token: str):
    return decode_jwt(token).get("iss")
