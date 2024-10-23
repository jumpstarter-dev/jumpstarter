import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import ClassVar, Literal, Optional, Self

import grpc
import yaml
from anyio.from_thread import BlockingPortal, start_blocking_portal
from pydantic import BaseModel, Field, ValidationError

from jumpstarter.client import Lease
from jumpstarter.common import MetadataFilter
from jumpstarter.common.grpc import aio_secure_channel, ssl_channel_credentials
from jumpstarter.v1 import jumpstarter_pb2, jumpstarter_pb2_grpc

from .common import CONFIG_PATH
from .env import JMP_DRIVERS_ALLOW, JMP_ENDPOINT, JMP_TOKEN


def _allow_from_env():
    allow = os.environ.get(JMP_DRIVERS_ALLOW)
    match allow:
        case None:
            return [], False
        case "UNSAFE":
            return [], True
        case _:
            return allow.split(","), False


class ClientConfigV1Alpha1Drivers(BaseModel):
    allow: list[str] = Field(default_factory=[])
    unsafe: bool = Field(default=False)


class ClientConfigV1Alpha1(BaseModel):
    CLIENT_CONFIGS_PATH: ClassVar[Path] = CONFIG_PATH / "clients"

    name: str = Field(default="default", exclude=True)
    path: str | None = Field(default=None, exclude=True)

    apiVersion: Literal["jumpstarter.dev/v1alpha1"] = Field(default="jumpstarter.dev/v1alpha1")
    kind: Literal["ClientConfig"] = Field(default="ClientConfig")

    endpoint: str
    token: str

    drivers: ClientConfigV1Alpha1Drivers

    async def channel(self):
        credentials = grpc.composite_channel_credentials(
            ssl_channel_credentials(self.endpoint),
            grpc.access_token_call_credentials(self.token),
        )

        return aio_secure_channel(self.endpoint, credentials)

    @contextmanager
    def lease(self, metadata_filter: MetadataFilter, lease_name: str | None):
        with start_blocking_portal() as portal:
            with portal.wrap_async_context_manager(self.lease_async(metadata_filter, lease_name, portal)) as lease:
                yield lease

    def list_leases(self):
        with start_blocking_portal() as portal:
            return portal.call(self.list_leases_async)

    def release_lease(self, name):
        with start_blocking_portal() as portal:
            portal.call(self.release_lease_async, name)

    async def list_leases_async(self):
        controller = jumpstarter_pb2_grpc.ControllerServiceStub(await self.channel())
        return (await controller.ListLeases(jumpstarter_pb2.ListLeasesRequest())).names

    async def release_lease_async(self, name):
        controller = jumpstarter_pb2_grpc.ControllerServiceStub(await self.channel())
        await controller.ReleaseLease(jumpstarter_pb2.ReleaseLeaseRequest(name=name))

    @asynccontextmanager
    async def lease_async(self, metadata_filter: MetadataFilter, lease_name: str | None, portal: BlockingPortal):
        async with Lease(
            channel=await self.channel(),
            lease_name=lease_name,
            metadata_filter=metadata_filter,
            portal=portal,
            allow=self.drivers.allow,
            unsafe=self.drivers.unsafe,
        ) as lease:
            yield lease

    @classmethod
    def from_file(cls, filepath):
        with open(filepath) as f:
            v = cls.model_validate(yaml.safe_load(f))
            v.name = os.path.basename(filepath).split(".")[0]
            v.path = filepath
            return v

    @classmethod
    def ensure_exists(cls):
        """Check if the clients config dir exists, otherwise create it."""
        os.makedirs(cls.CLIENT_CONFIGS_PATH, exist_ok=True)

    @classmethod
    def try_from_env(cls):
        try:
            return cls.from_env()
        except ValidationError:
            return None

    @classmethod
    def from_env(cls):
        allow, unsafe = _allow_from_env()
        return cls(
            endpoint=os.environ.get(JMP_ENDPOINT),
            token=os.environ.get(JMP_TOKEN),
            drivers=ClientConfigV1Alpha1Drivers(
                allow=allow,
                unsafe=unsafe,
            ),
        )

    @classmethod
    def _get_path(cls, name: str) -> Path:
        """Get the regular path of a client config given a name."""

        return cls.CLIENT_CONFIGS_PATH / f"{name}.yaml"

    @classmethod
    def load(cls, name: str) -> Self:
        """Load a client config by name."""
        path = cls._get_path(name)
        if os.path.exists(path) is False:
            raise FileNotFoundError(f"Client config '{path}' does not exist.")

        return cls.from_file(path)

    @classmethod
    def save(cls, config: Self, path: Optional[str] = None):
        """Saves a client config as YAML."""
        # Ensure the clients dir exists
        if path is None:
            cls.ensure_exists()

        with open(path or cls._get_path(config.name), "w") as f:
            yaml.safe_dump(config.model_dump(mode="json"), f, sort_keys=False)

    @classmethod
    def exists(cls, name: str) -> bool:
        """Check if a client config exists by name."""
        return os.path.exists(cls._get_path(name))

    @classmethod
    def list(cls) -> list[Self]:
        """List the available client configs."""
        if os.path.exists(cls.CLIENT_CONFIGS_PATH) is False:
            # Return an empty list if the dir does not exist
            return []

        results = os.listdir(cls.CLIENT_CONFIGS_PATH)
        # Only accept YAML files in the list
        files = filter(lambda x: x.endswith(".yaml"), results)

        def make_config(file: str):
            path = cls.CLIENT_CONFIGS_PATH / file
            return cls.from_file(path)

        return list(map(make_config, files))

    @classmethod
    def delete(cls, name: str):
        """Delete a client config by name."""
        path = cls._get_path(name)
        if os.path.exists(path) is False:
            raise FileNotFoundError(f"Client config '{path}' does not exist.")
        os.unlink(path)
