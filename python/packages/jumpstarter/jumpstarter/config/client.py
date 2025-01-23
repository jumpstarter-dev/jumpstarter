import os
from contextlib import asynccontextmanager, contextmanager
from pathlib import Path
from typing import ClassVar, Literal, Optional, Self

import grpc
import yaml
from anyio.from_thread import BlockingPortal, start_blocking_portal
from jumpstarter_protocol import jumpstarter_pb2, jumpstarter_pb2_grpc
from pydantic import BaseModel, Field, ValidationError

from jumpstarter.common import MetadataFilter
from jumpstarter.common.grpc import aio_secure_channel, ssl_channel_credentials

from .common import CONFIG_PATH
from .env import JMP_DRIVERS_ALLOW, JMP_ENDPOINT, JMP_LEASE, JMP_TOKEN
from .tls import TLSConfigV1Alpha1


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
    path: Path | None = Field(default=None, exclude=True)

    apiVersion: Literal["jumpstarter.dev/v1alpha1"] = Field(default="jumpstarter.dev/v1alpha1")
    kind: Literal["ClientConfig"] = Field(default="ClientConfig")

    endpoint: str
    tls: TLSConfigV1Alpha1 = Field(default_factory=TLSConfigV1Alpha1)
    token: str

    drivers: ClientConfigV1Alpha1Drivers

    async def channel(self):
        credentials = grpc.composite_channel_credentials(
            ssl_channel_credentials(self.endpoint, self.tls),
            grpc.access_token_call_credentials(self.token),
        )

        return aio_secure_channel(self.endpoint, credentials)

    @contextmanager
    def lease(self, metadata_filter: MetadataFilter, lease_name: str | None = None):
        with start_blocking_portal() as portal:
            with portal.wrap_async_context_manager(
                self.lease_async(metadata_filter, lease_name, portal)) as lease:
                yield lease

    def request_lease(self, metadata_filter: MetadataFilter):
        with start_blocking_portal() as portal:
            return portal.call(self.request_lease_async, metadata_filter, portal)

    def list_leases(self):
        with start_blocking_portal() as portal:
            return portal.call(self.list_leases_async)

    def release_lease(self, name):
        with start_blocking_portal() as portal:
            portal.call(self.release_lease_async, name)

    async def request_lease_async(self, metadata_filter: MetadataFilter, portal:BlockingPortal):
        # dynamically import to avoid circular imports
        from jumpstarter.client import Lease
        lease = Lease(
            channel=await self.channel(),
            name=None,
            metadata_filter=metadata_filter,
            portal=portal,
            allow=self.drivers.allow,
            unsafe=self.drivers.unsafe,
            tls_config=self.tls,
        )
        return await lease.request_async()

    async def list_leases_async(self):
        controller = jumpstarter_pb2_grpc.ControllerServiceStub(await self.channel())
        return (await controller.ListLeases(jumpstarter_pb2.ListLeasesRequest())).names

    async def release_lease_async(self, name):
        controller = jumpstarter_pb2_grpc.ControllerServiceStub(await self.channel())
        await controller.ReleaseLease(jumpstarter_pb2.ReleaseLeaseRequest(name=name))

    @asynccontextmanager
    async def lease_async(self, metadata_filter: MetadataFilter, lease_name: str | None, portal: BlockingPortal):
        from jumpstarter.client import Lease

        # if no lease_name provided, check if it is set in the environment
        lease_name = lease_name or os.environ.get(JMP_LEASE, "")
        # when no lease name is provided, release the lease on exit
        release_lease = lease_name == ""

        async with Lease(
            channel=await self.channel(),
            name=lease_name,
            metadata_filter=metadata_filter,
            portal=portal,
            allow=self.drivers.allow,
            unsafe=self.drivers.unsafe,
            release=release_lease,
            tls_config=self.tls,
        ) as lease:
            yield lease

    @classmethod
    def from_file(cls, path: os.PathLike):
        with open(path) as f:
            v = cls.model_validate(yaml.safe_load(f))
            v.name = os.path.basename(path).split(".")[0]
            v.path = Path(path)
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
        return (cls.CLIENT_CONFIGS_PATH / name).with_suffix(".yaml")

    @classmethod
    def load(cls, name: str) -> Self:
        """Load a client config by name."""
        path = cls._get_path(name)
        if path.exists() is False:
            raise FileNotFoundError(f"Client config '{path}' does not exist.")
        return cls.from_file(path)

    @classmethod
    def save(cls, config: Self, path: Optional[os.PathLike] = None):
        """Saves a client config as YAML."""
        # Ensure the clients dir exists
        if path is None:
            cls.ensure_exists()
            # Set the config path before saving
            config.path = cls._get_path(config.name)
        else:
            config.path = Path(path)
        with config.path.open(mode="w") as f:
            yaml.safe_dump(config.model_dump(mode="json"), f, sort_keys=False)

    @classmethod
    def dump_yaml(cls, config: Self) -> str:
        return yaml.safe_dump(config.model_dump(mode="json"), sort_keys=False)

    @classmethod
    def exists(cls, name: str) -> bool:
        """Check if a client config exists by name."""
        return cls._get_path(name).exists()

    @classmethod
    def list(cls) -> list[Self]:
        """List the available client configs."""
        if cls.CLIENT_CONFIGS_PATH.exists() is False:
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
        if path.exists() is False:
            raise FileNotFoundError(f"Client config '{path}' does not exist.")
        path.unlink()
