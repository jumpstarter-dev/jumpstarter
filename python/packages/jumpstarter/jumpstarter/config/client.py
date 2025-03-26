import os
from contextlib import asynccontextmanager, contextmanager
from datetime import timedelta
from pathlib import Path
from typing import ClassVar, Literal, Optional, Self

import grpc
import yaml
from anyio.from_thread import BlockingPortal, start_blocking_portal
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .common import CONFIG_PATH, ObjectMeta
from .env import JMP_DRIVERS_ALLOW, JMP_ENDPOINT, JMP_LEASE, JMP_NAME, JMP_NAMESPACE, JMP_TOKEN
from .grpc import call_credentials
from .tls import TLSConfigV1Alpha1
from jumpstarter.client.grpc import ClientService
from jumpstarter.common.exceptions import FileNotFoundError
from jumpstarter.common.grpc import aio_secure_channel, ssl_channel_credentials


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

    alias: str = Field(default="default")
    path: Path | None = Field(default=None)

    apiVersion: Literal["jumpstarter.dev/v1alpha1"] = Field(default="jumpstarter.dev/v1alpha1")
    kind: Literal["ClientConfig"] = Field(default="ClientConfig")

    metadata: ObjectMeta

    endpoint: str
    tls: TLSConfigV1Alpha1 = Field(default_factory=TLSConfigV1Alpha1)
    token: str
    grpcOptions: dict[str, str | int] | None = Field(default_factory=dict)

    drivers: ClientConfigV1Alpha1Drivers

    async def channel(self):
        credentials = grpc.composite_channel_credentials(
            await ssl_channel_credentials(self.endpoint, self.tls),
            call_credentials("Client", self.metadata, self.token),
        )

        return aio_secure_channel(self.endpoint, credentials, self.grpcOptions)

    @contextmanager
    def lease(
        self,
        selector: str | None = None,
        lease_name: str | None = None,
        duration: timedelta = timedelta(minutes=30),
    ):
        with start_blocking_portal() as portal:
            with portal.wrap_async_context_manager(self.lease_async(selector, lease_name, duration, portal)) as lease:
                yield lease

    def get_exporter(self, name: str):
        with start_blocking_portal() as portal:
            return portal.call(self.get_exporter_async, name)

    def list_exporters(
        self,
        page_size: int | None = None,
        page_token: str | None = None,
        filter: str | None = None,
    ):
        with start_blocking_portal() as portal:
            return portal.call(self.list_exporters_async, page_size, page_token, filter)

    def list_leases(self, filter: str):
        with start_blocking_portal() as portal:
            return portal.call(self.list_leases_async, filter)

    def create_lease(
        self,
        selector: str,
        duration: timedelta,
    ):
        with start_blocking_portal() as portal:
            return portal.call(self.create_lease_async, selector, duration)

    def delete_lease(
        self,
        name: str,
    ):
        with start_blocking_portal() as portal:
            return portal.call(self.delete_lease_async, name)

    def update_lease(self, name, duration: timedelta):
        with start_blocking_portal() as portal:
            return portal.call(self.update_lease_async, name, duration)

    async def get_exporter_async(self, name: str):
        svc = ClientService(channel=await self.channel(), namespace=self.metadata.namespace)
        return await svc.GetExporter(name=name)

    async def list_exporters_async(
        self,
        page_size: int | None = None,
        page_token: str | None = None,
        filter: str | None = None,
    ):
        svc = ClientService(channel=await self.channel(), namespace=self.metadata.namespace)
        return await svc.ListExporters(page_size=page_size, page_token=page_token, filter=filter)

    async def create_lease_async(
        self,
        selector: str,
        duration: timedelta,
    ):
        svc = ClientService(channel=await self.channel(), namespace=self.metadata.namespace)
        return await svc.CreateLease(
            selector=selector,
            duration=duration,
        )

    async def delete_lease_async(self, name: str):
        svc = ClientService(channel=await self.channel(), namespace=self.metadata.namespace)
        await svc.DeleteLease(
            name=name,
        )

    async def list_leases_async(self, filter: str):
        svc = ClientService(channel=await self.channel(), namespace=self.metadata.namespace)
        return await svc.ListLeases(filter=filter)

    async def update_lease_async(self, name, duration: timedelta):
        svc = ClientService(channel=await self.channel(), namespace=self.metadata.namespace)
        return await svc.UpdateLease(name=name, duration=duration)

    @asynccontextmanager
    async def lease_async(
        self,
        selector: str,
        lease_name: str | None,
        duration: timedelta,
        portal: BlockingPortal,
    ):
        from jumpstarter.client import Lease

        # if no lease_name provided, check if it is set in the environment
        lease_name = lease_name or os.environ.get(JMP_LEASE, "")
        # when no lease name is provided, release the lease on exit
        release_lease = lease_name == ""

        async with Lease(
            channel=await self.channel(),
            namespace=self.metadata.namespace,
            name=lease_name,
            selector=selector,
            duration=duration,
            portal=portal,
            allow=self.drivers.allow,
            unsafe=self.drivers.unsafe,
            release=release_lease,
            tls_config=self.tls,
            grpc_options=self.grpcOptions,
        ) as lease:
            yield lease

    @classmethod
    def from_file(cls, path: os.PathLike):
        with open(path) as f:
            v = cls.model_validate(yaml.safe_load(f))
            v.alias = os.path.basename(path).split(".")[0]
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
            metadata=ObjectMeta(
                namespace=os.environ.get(JMP_NAMESPACE),
                name=os.environ.get(JMP_NAME),
            ),
            endpoint=os.environ.get(JMP_ENDPOINT),
            token=os.environ.get(JMP_TOKEN),
            drivers=ClientConfigV1Alpha1Drivers(
                allow=allow,
                unsafe=unsafe,
            ),
        )

    @classmethod
    def _get_path(cls, alias: str) -> Path:
        """Get the regular path of a client config given an alias."""
        return (cls.CLIENT_CONFIGS_PATH / alias).with_suffix(".yaml")

    @classmethod
    def load(cls, alias: str) -> Self:
        """Load a client config by alias."""
        path = cls._get_path(alias)
        if path.exists() is False:
            raise FileNotFoundError(f"Client config '{path}' does not exist.")
        return cls.from_file(path)

    @classmethod
    def save(cls, config: Self, path: Optional[os.PathLike] = None) -> Path:
        """Saves a client config as YAML."""
        # Ensure the clients dir exists
        if path is None:
            cls.ensure_exists()
            # Set the config path before saving
            config.path = cls._get_path(config.alias)
        else:
            config.path = Path(path)
        with config.path.open(mode="w") as f:
            yaml.safe_dump(config.model_dump(mode="json", exclude={"path", "alias"}), f, sort_keys=False)
        return config.path

    @classmethod
    def dump_yaml(cls, config: Self) -> str:
        return yaml.safe_dump(config.model_dump(mode="json", exclude={"path", "alias"}), sort_keys=False)

    @classmethod
    def exists(cls, alias: str) -> bool:
        """Check if a client config exists by alias."""
        return cls._get_path(alias).exists()

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
    def delete(cls, alias: str) -> Path:
        """Delete a client config by alias."""
        path = cls._get_path(alias)
        if path.exists() is False:
            raise FileNotFoundError(f"Client config '{path}' does not exist.")
        path.unlink()
        return path


class ClientConfigListV1Alpha1(BaseModel):
    api_version: Literal["jumpstarter.dev/v1alpha1"] = Field(alias="apiVersion", default="jumpstarter.dev/v1alpha1")
    current_config: Optional[str] = Field(alias="currentConfig")
    items: list[ClientConfigV1Alpha1]
    kind: Literal["ClientConfigList"] = Field(default="ClientConfigList")

    def dump_json(self):
        return self.model_dump_json(indent=4, by_alias=True)

    def dump_yaml(self):
        return yaml.safe_dump(self.model_dump(mode="json", by_alias=True), indent=2)

    model_config = ConfigDict(arbitrary_types_allowed=True, populate_by_name=True)
