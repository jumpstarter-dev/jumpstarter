from __future__ import annotations

import asyncio
import base64
import logging
import ssl
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import ClassVar
from urllib.parse import urlparse

import websockets
from jumpstarter_driver_composite.driver import CompositeInterface
from jumpstarter_driver_network.driver import NetworkInterface
from jumpstarter_driver_network.streams.websocket import WebsocketClientStream
from jumpstarter_driver_power.driver import PowerInterface, PowerReading
from lightkube import AsyncClient
from lightkube.config.kubeconfig import KubeConfig
from lightkube.core.exceptions import ApiError
from websockets import Subprotocol
from websockets.exceptions import InvalidStatus

from .storage import Disk, Flash
from .vm import VirtualMachine, VirtualMachineInstance, VMControl, operation, require_idle
from jumpstarter.driver import Driver, export, exportstream

logger = logging.getLogger(__name__)


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

    def _control(self) -> VMControl:
        return VMControl(self._get_client(), self.namespace, self._resolve_vm_name())

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
        control = self._control()
        vm = await control.get()
        require_idle(vm)
        await control.patch(vm, spec={"runStrategy": "Always"})
        logger.info("started VM %s/%s", self.namespace, self.vm_name)

    @export
    async def off(self, destroy: bool = False) -> None:
        control = self._control()
        vm = await control.get()
        if destroy:
            pending = operation(vm)
            if not pending or pending["phase"] != "destroying":
                require_idle(vm)
                await control.patch(vm, state={"phase": "destroying"}, spec={"runStrategy": "Halted"})
            await control.client.delete(VirtualMachine, name=control.name, namespace=control.namespace)
        else:
            require_idle(vm)
            await control.patch(vm, spec={"runStrategy": "Halted"})
        logger.info("stopped VM %s/%s", self.namespace, self.vm_name)

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
    if parsed.scheme != "https":
        raise ValueError("kubeconfig cluster server must use HTTPS for console connections")
    ws_url = (
        f"wss://{parsed.netloc}/apis/subresources.kubevirt.io/v1/"
        f"namespaces/{namespace}/virtualmachineinstances/{vm_name}/console"
    )

    headers = {}
    has_auth = False

    if single.user and single.user.token:
        headers["Authorization"] = f"Bearer {single.user.token}"
        has_auth = True

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

        with (
            tempfile.NamedTemporaryFile(suffix=".crt") as cert_file,
            tempfile.NamedTemporaryFile(suffix=".key") as key_file,
        ):
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
            self.kubeconfig,
            self.namespace,
            name,
        )

        timeout = self.console_max_retries * self.console_retry_interval
        for attempt in range(self.console_max_retries):
            try:
                self.logger.info("Connecting console WebSocket to %s", ws_url)
                async with websockets.connect(
                    ws_url,
                    additional_headers=headers,
                    ssl=ssl_ctx,
                    subprotocols=[Subprotocol("plain.kubevirt.io")],
                ) as websocket:
                    async with WebsocketClientStream(conn=websocket) as stream:
                        yield stream
                    self.logger.info("Console WebSocket disconnected")
                    return
            except InvalidStatus as e:
                if e.response.status_code == 400:
                    self.logger.info(
                        "VMI not ready (HTTP 400), retrying in %.1fs (attempt %d/%d)",
                        self.console_retry_interval,
                        attempt + 1,
                        self.console_max_retries,
                    )
                    await asyncio.sleep(self.console_retry_interval)
                else:
                    raise

        raise TimeoutError(f"VMI {name} did not become ready for console within {timeout:.0f}s")


@dataclass(kw_only=True)
class KubevirtStorage(_KubevirtBase, Driver):
    driver_type: ClassVar[str] = "storage"
    disks: dict[str, Disk] = field(default_factory=lambda: {"root": Disk()})
    upload_proxy_url: str = ""
    upload_ca: str = ""
    flash_timeout: float = 1800
    shutdown_timeout: float = 120
    poll_interval: float = 1
    _lock: asyncio.Lock = field(init=False, default_factory=asyncio.Lock, repr=False)

    def __post_init__(self):
        super().__post_init__()
        self.disks = {
            name: Disk(**config) if isinstance(config, dict) else config for name, config in self.disks.items()
        }

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_kubevirt.client.KubevirtStorageClient"

    def _flash(self):
        return Flash(
            self._control(),
            self.disks,
            self.upload_proxy_url,
            self.upload_ca,
            self.flash_timeout,
            self.shutdown_timeout,
            self.poll_interval,
        )

    @export
    async def begin_flash(self, targets: list[str]) -> str:
        async with self._lock:
            return await self._flash().begin(targets)

    @export
    async def prepare_flash(self, operation_id: str, target: str) -> None:
        async with self._lock:
            await self._flash().prepare(operation_id, target)

    @export
    async def stage_flash(self, operation_id: str, target: str, handle) -> None:
        async with self._lock:
            await self._flash().stage(
                operation_id, target, lambda: self.resource(handle, timeout=int(self.flash_timeout))
            )

    @export
    async def commit_flash(self, operation_id: str) -> None:
        async with self._lock:
            await self._flash().commit(operation_id)

    @export
    async def recover_flash(self, operation_id: str | None = None) -> bool:
        async with self._lock:
            return await self._flash().recover(operation_id)


@dataclass(kw_only=True)
class Kubevirt(CompositeInterface, Driver):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_kubevirt.client.KubevirtClient"
