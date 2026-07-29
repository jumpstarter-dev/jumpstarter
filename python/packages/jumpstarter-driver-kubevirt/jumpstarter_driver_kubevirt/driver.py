from __future__ import annotations

import asyncio
import base64
import logging
import ssl
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from urllib.parse import urlparse

import websockets
from jumpstarter_driver_composite.driver import CompositeInterface
from jumpstarter_driver_network.driver import NetworkInterface
from jumpstarter_driver_network.streams.websocket import WebsocketClientStream
from jumpstarter_driver_power.driver import PowerInterface, PowerReading
from websockets.exceptions import InvalidStatus

from jumpstarter.driver import Driver, export, exportstream

if TYPE_CHECKING:
    from lightkube import AsyncClient

try:
    from lightkube import AsyncClient
    from lightkube.config.kubeconfig import KubeConfig
    from lightkube.core.exceptions import ApiError
    from lightkube.generic_resource import create_namespaced_resource
    from lightkube.models.meta_v1 import ObjectMeta
    from lightkube.types import PatchType

    _HAS_LIGHTKUBE = True
except ImportError:
    _HAS_LIGHTKUBE = False

logger = logging.getLogger(__name__)

VirtualMachine = create_namespaced_resource(
    group="kubevirt.io",
    version="v1",
    kind="VirtualMachine",
    plural="virtualmachines",
)

VirtualMachineInstance = create_namespaced_resource(
    group="kubevirt.io",
    version="v1",
    kind="VirtualMachineInstance",
    plural="virtualmachineinstances",
)

DataVolume = create_namespaced_resource(
    group="cdi.kubevirt.io",
    version="v1beta1",
    kind="DataVolume",
    plural="datavolumes",
)


def _build_client(kubeconfig_path: str | None) -> AsyncClient:
    if kubeconfig_path:
        config = KubeConfig.from_file(kubeconfig_path)
        return AsyncClient(config=config)
    return AsyncClient()


@dataclass(kw_only=True)
class _KubevirtBase:
    namespace: str = ""
    kubeconfig: str | None = None
    vm_name: str = ""
    _client: AsyncClient | None = field(init=False, default=None, repr=False)

    def _get_client(self) -> AsyncClient:
        if self._client is None:
            self._client = _build_client(self.kubeconfig)
        return self._client

    def _resolve_vm_name(self) -> str:
        if self.vm_name:
            return self.vm_name
        raise ValueError("vm_name not set in driver config")


@dataclass(kw_only=True)
class KubevirtPower(_KubevirtBase, PowerInterface, Driver):

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_kubevirt.client.KubevirtPowerClient"

    @export
    async def on(self) -> None:
        client = self._get_client()
        name = self._resolve_vm_name()

        patch = {"spec": {"runStrategy": "Always"}}
        await client.patch(
            VirtualMachine,
            name=name,
            namespace=self.namespace,
            obj=patch,
            patch_type=PatchType.MERGE,
        )
        logger.info("started VM %s/%s", self.namespace, name)

    @export
    async def off(self, destroy: bool = False) -> None:
        client = self._get_client()
        name = self._resolve_vm_name()

        if destroy:
            await client.delete(
                VirtualMachine,
                name=name,
                namespace=self.namespace,
            )
            logger.info("destroyed VM %s/%s", self.namespace, name)
        else:
            patch = {"spec": {"runStrategy": "Halted"}}
            await client.patch(
                VirtualMachine,
                name=name,
                namespace=self.namespace,
                obj=patch,
                patch_type=PatchType.MERGE,
            )
            logger.info("stopped VM %s/%s", self.namespace, name)

    @export
    async def read(self) -> AsyncGenerator[PowerReading, None]:
        client = self._get_client()
        name = self._resolve_vm_name()

        try:
            vmi = await client.get(
                VirtualMachineInstance,
                name=name,
                namespace=self.namespace,
            )
            phase = vmi.get("status", {}).get("phase", "Unknown")
            if phase == "Running":
                yield PowerReading(voltage=5.0, current=1.0)
            else:
                yield PowerReading(voltage=0.0, current=0.0)
        except ApiError as e:
            if e.status.code == 404:
                yield PowerReading(voltage=0.0, current=0.0)
            else:
                logger.warning("failed to read VMI %s/%s: %s", self.namespace, name, e)
                raise
        except Exception:
            logger.warning("unexpected error reading VMI %s/%s", self.namespace, name, exc_info=True)
            raise


def _build_console_ws_url(
    kubeconfig_path: str | None, namespace: str, vm_name: str
) -> tuple[str, dict, ssl.SSLContext | None]:
    """Build WebSocket URL, headers, and SSL context for KubeVirt console.

    Returns (ws_url, extra_headers, ssl_context).
    Supports bearer token and client certificate auth from kubeconfig.
    Raises ValueError for unsupported auth methods (exec-based, auth-provider).
    """
    if not kubeconfig_path:
        raise ValueError("kubeconfig required for console WebSocket connection")

    config = KubeConfig.from_file(kubeconfig_path)
    single = config.get()
    if single is None:
        raise ValueError("no current context in kubeconfig")

    server = single.cluster.server.rstrip("/")
    parsed = urlparse(server)
    ws_scheme = "wss" if parsed.scheme == "https" else "ws"
    ws_url = (
        f"{ws_scheme}://{parsed.netloc}/apis/subresources.kubevirt.io/v1/"
        f"namespaces/{namespace}/virtualmachineinstances/{vm_name}/console"
    )

    headers = {}
    has_auth = False

    if single.user and single.user.token:
        headers["Authorization"] = f"Bearer {single.user.token}"
        has_auth = True

    ssl_ctx = None
    if ws_scheme == "wss":
        ssl_ctx = ssl.create_default_context()
        if single.cluster.insecure:
            ssl_ctx.check_hostname = False
            ssl_ctx.verify_mode = ssl.CERT_NONE

        ca_data_raw = getattr(single.cluster, "certificate_authority_data", None)
        if ca_data_raw:
            ca_pem = base64.b64decode(ca_data_raw).decode()
            ssl_ctx.load_verify_locations(cadata=ca_pem)

        cert_data_raw = getattr(single.user, "client_certificate_data", None) if single.user else None
        key_data_raw = getattr(single.user, "client_key_data", None) if single.user else None
        if cert_data_raw and key_data_raw:
            import tempfile

            with tempfile.NamedTemporaryFile(suffix=".crt") as cert_file, \
                 tempfile.NamedTemporaryFile(suffix=".key") as key_file:
                cert_file.write(base64.b64decode(cert_data_raw))
                cert_file.flush()
                key_file.write(base64.b64decode(key_data_raw))
                key_file.flush()
                ssl_ctx.load_cert_chain(cert_file.name, key_file.name)
            has_auth = True

    if not has_auth:
        if single.user and getattr(single.user, "exec", None):
            raise ValueError(
                "kubeconfig uses exec-based auth which is not supported for console WebSocket connections; "
                "use a kubeconfig with a bearer token or client certificate instead"
            )
        logger.warning("no auth credentials found in kubeconfig — console connection may fail")

    return ws_url, headers, ssl_ctx


@dataclass(kw_only=True)
class KubevirtConsole(_KubevirtBase, NetworkInterface, Driver):
    console_max_retries: int = 30
    console_retry_interval: float = 2.0

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_kubevirt.client.KubevirtConsoleClient"

    @exportstream
    @asynccontextmanager
    async def connect(self):
        name = self._resolve_vm_name()
        ws_url, headers, ssl_ctx = _build_console_ws_url(
            self.kubeconfig, self.namespace, name,
        )

        timeout = self.console_max_retries * self.console_retry_interval
        for attempt in range(self.console_max_retries):
            try:
                self.logger.info("Connecting console WebSocket to %s", ws_url)
                async with websockets.connect(
                    ws_url,
                    additional_headers=headers,
                    ssl=ssl_ctx,
                    subprotocols=["plain.kubevirt.io"],
                ) as websocket:
                    async with WebsocketClientStream(conn=websocket) as stream:
                        yield stream
                    self.logger.info("Console WebSocket disconnected")
                    return
            except InvalidStatus as e:
                if e.response.status_code == 400:
                    self.logger.info(
                        "VMI not ready (HTTP 400), retrying in %.1fs (attempt %d/%d)",
                        self.console_retry_interval, attempt + 1, self.console_max_retries,
                    )
                    await asyncio.sleep(self.console_retry_interval)
                else:
                    raise

        raise TimeoutError(f"VMI {name} did not become ready for console within {timeout:.0f}s")


@dataclass(kw_only=True)
class KubevirtStorage(_KubevirtBase, Driver):

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_kubevirt.client.KubevirtStorageClient"

    @export
    async def flash(self, image_url: str, disk_size: str = "20Gi", access_modes: list[str] | None = None) -> None:
        client = self._get_client()
        name = self._resolve_vm_name()
        dv_name = f"{name}-root"

        if access_modes is None:
            access_modes = ["ReadWriteOnce"]

        try:
            await client.delete(DataVolume, name=dv_name, namespace=self.namespace)
            logger.info("deleted existing DataVolume %s/%s for re-flash", self.namespace, dv_name)
        except ApiError as e:
            if e.status.code != 404:
                raise

        dv = DataVolume(
            metadata=ObjectMeta(
                name=dv_name,
                namespace=self.namespace,
            ),
            spec={
                "source": {
                    "http": {
                        "url": image_url,
                    },
                },
                "storage": {
                    "accessModes": access_modes,
                    "resources": {
                        "requests": {
                            "storage": disk_size,
                        },
                    },
                },
            },
        )

        await client.create(dv)
        logger.info("created DataVolume %s/%s from %s (size=%s)", self.namespace, dv_name, image_url, disk_size)


@dataclass(kw_only=True)
class Kubevirt(CompositeInterface, Driver):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_kubevirt.client.KubevirtClient"
