from __future__ import annotations

import ssl
from unittest.mock import AsyncMock, MagicMock, patch

import pytest


class TestBuildClient:
    def test_with_kubeconfig(self):
        mock_config = MagicMock()
        with (
            patch("jumpstarter_driver_kubevirt.driver.KubeConfig") as mock_kc,
            patch("jumpstarter_driver_kubevirt.driver.AsyncClient") as mock_ac,
        ):
            mock_kc.from_file.return_value = mock_config
            from jumpstarter_driver_kubevirt.driver import _build_client

            _build_client("/path/to/kubeconfig")
            mock_kc.from_file.assert_called_once_with("/path/to/kubeconfig")
            mock_ac.assert_called_once_with(config=mock_config)

    def test_without_kubeconfig(self):
        with patch("jumpstarter_driver_kubevirt.driver.AsyncClient") as mock_ac:
            from jumpstarter_driver_kubevirt.driver import _build_client

            _build_client(None)
            mock_ac.assert_called_once_with()


class TestKubevirtPower:
    def _make_power(self, **kwargs):
        from jumpstarter_driver_kubevirt.driver import KubevirtPower

        defaults = {"namespace": "test-ns", "vm_name": "test-vm"}
        defaults.update(kwargs)
        return KubevirtPower(**defaults)

    def test_resolve_vm_name(self):
        power = self._make_power(vm_name="my-vm")
        assert power._resolve_vm_name() == "my-vm"

    def test_resolve_vm_name_empty_raises(self):
        power = self._make_power(vm_name="")
        with pytest.raises(ValueError, match="vm_name not set"):
            power._resolve_vm_name()

    def test_get_client_lazy_init(self):
        power = self._make_power()
        with patch("jumpstarter_driver_kubevirt.driver._build_client") as mock_build:
            mock_client = MagicMock()
            mock_build.return_value = mock_client

            result = power._get_client()
            assert result is mock_client
            mock_build.assert_called_once_with(None)

            result2 = power._get_client()
            assert result2 is mock_client
            assert mock_build.call_count == 1

    def test_client_class_method(self):
        from jumpstarter_driver_kubevirt.driver import KubevirtPower

        assert KubevirtPower.client() == "jumpstarter_driver_kubevirt.client.KubevirtPowerClient"

    @pytest.mark.asyncio
    async def test_on(self):
        power = self._make_power()
        mock_client = AsyncMock()
        power._client = mock_client

        await power.on()

        mock_client.patch.assert_called_once()
        call_kwargs = mock_client.patch.call_args
        assert call_kwargs.kwargs["name"] == "test-vm"
        assert call_kwargs.kwargs["namespace"] == "test-ns"

    @pytest.mark.asyncio
    async def test_off_halt(self):
        power = self._make_power()
        mock_client = AsyncMock()
        power._client = mock_client

        await power.off(destroy=False)

        mock_client.patch.assert_called_once()
        call_kwargs = mock_client.patch.call_args
        assert call_kwargs.kwargs["name"] == "test-vm"

    @pytest.mark.asyncio
    async def test_off_destroy(self):
        power = self._make_power()
        mock_client = AsyncMock()
        power._client = mock_client

        await power.off(destroy=True)

        mock_client.delete.assert_called_once()
        mock_client.patch.assert_not_called()

    @pytest.mark.asyncio
    async def test_read_running(self):
        power = self._make_power()
        mock_client = AsyncMock()
        mock_vmi = MagicMock()

        def vmi_get(key, default=None):
            if key == "status":
                status = MagicMock()
                status.get.return_value = "Running"
                return status
            return default

        mock_vmi.get = vmi_get
        mock_client.get = AsyncMock(return_value=mock_vmi)
        power._client = mock_client

        readings = []
        async for reading in power.read():
            readings.append(reading)

        assert len(readings) == 1
        assert readings[0].voltage == 5.0
        assert readings[0].current == 1.0

    @pytest.mark.asyncio
    async def test_read_stopped(self):
        power = self._make_power()
        mock_client = AsyncMock()
        mock_vmi = MagicMock()

        def vmi_get(key, default=None):
            if key == "status":
                status = MagicMock()
                status.get.return_value = "Stopped"
                return status
            return default

        mock_vmi.get = vmi_get
        mock_client.get = AsyncMock(return_value=mock_vmi)
        power._client = mock_client

        readings = []
        async for reading in power.read():
            readings.append(reading)

        assert len(readings) == 1
        assert readings[0].voltage == 0.0
        assert readings[0].current == 0.0

    @pytest.mark.asyncio
    async def test_read_exception(self):
        power = self._make_power()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(side_effect=Exception("connection failed"))
        power._client = mock_client

        with pytest.raises(Exception, match="connection failed"):
            async for _ in power.read():
                pass


class TestBuildConsoleWsUrl:
    def test_no_kubeconfig_raises(self):
        from jumpstarter_driver_kubevirt.driver import _build_console_ws_url

        with pytest.raises(ValueError, match="kubeconfig required"):
            _build_console_ws_url(None, "ns", "vm")

    def test_https_server(self):
        mock_single = MagicMock()
        mock_single.cluster.server = "https://api.example.com:6443"
        mock_single.cluster.insecure = False
        mock_single.cluster.certificate_authority_data = None
        mock_single.user.token = "my-token"
        mock_single.user.client_certificate_data = None
        mock_single.user.client_key_data = None

        mock_config = MagicMock()
        mock_config.get.return_value = mock_single

        with patch("jumpstarter_driver_kubevirt.driver.KubeConfig") as mock_kc:
            mock_kc.from_file.return_value = mock_config
            from jumpstarter_driver_kubevirt.driver import _build_console_ws_url

            ws_url, headers, ssl_ctx = _build_console_ws_url("/path/kc", "test-ns", "my-vm")

        assert ws_url.startswith("wss://")
        assert "test-ns" in ws_url
        assert "my-vm" in ws_url
        assert headers["Authorization"] == "Bearer my-token"
        assert ssl_ctx is not None
        assert ssl_ctx.check_hostname is True

    def test_https_insecure(self):
        mock_single = MagicMock()
        mock_single.cluster.server = "https://api.example.com:6443"
        mock_single.cluster.insecure = True
        mock_single.cluster.certificate_authority_data = None
        mock_single.user.token = None
        mock_single.user.client_certificate_data = None
        mock_single.user.client_key_data = None
        mock_single.user.exec = None

        mock_config = MagicMock()
        mock_config.get.return_value = mock_single

        with patch("jumpstarter_driver_kubevirt.driver.KubeConfig") as mock_kc:
            mock_kc.from_file.return_value = mock_config
            from jumpstarter_driver_kubevirt.driver import _build_console_ws_url

            ws_url, headers, ssl_ctx = _build_console_ws_url("/path/kc", "ns", "vm")

        assert ssl_ctx is not None
        assert ssl_ctx.check_hostname is False
        assert ssl_ctx.verify_mode == ssl.CERT_NONE
        assert "Authorization" not in headers

    def test_http_server(self):
        mock_single = MagicMock()
        mock_single.cluster.server = "http://localhost:8080"
        mock_single.cluster.insecure = False
        mock_single.user = None

        mock_config = MagicMock()
        mock_config.get.return_value = mock_single

        with patch("jumpstarter_driver_kubevirt.driver.KubeConfig") as mock_kc:
            mock_kc.from_file.return_value = mock_config
            from jumpstarter_driver_kubevirt.driver import _build_console_ws_url

            ws_url, headers, ssl_ctx = _build_console_ws_url("/path/kc", "ns", "vm")

        assert ws_url.startswith("ws://")
        assert ssl_ctx is None

    def test_no_current_context_raises(self):
        mock_config = MagicMock()
        mock_config.get.return_value = None

        with patch("jumpstarter_driver_kubevirt.driver.KubeConfig") as mock_kc:
            mock_kc.from_file.return_value = mock_config
            from jumpstarter_driver_kubevirt.driver import _build_console_ws_url

            with pytest.raises(ValueError, match="no current context"):
                _build_console_ws_url("/path/kc", "ns", "vm")


class TestKubevirtConsole:
    def test_client_class_method(self):
        from jumpstarter_driver_kubevirt.driver import KubevirtConsole

        assert KubevirtConsole.client() == "jumpstarter_driver_kubevirt.client.KubevirtConsoleClient"

    def test_resolve_vm_name(self):
        from jumpstarter_driver_kubevirt.driver import KubevirtConsole

        console = KubevirtConsole(namespace="ns", vm_name="my-vm")
        assert console._resolve_vm_name() == "my-vm"

    def test_resolve_vm_name_empty_raises(self):
        from jumpstarter_driver_kubevirt.driver import KubevirtConsole

        console = KubevirtConsole(namespace="ns", vm_name="")
        with pytest.raises(ValueError, match="vm_name not set"):
            console._resolve_vm_name()

    @pytest.mark.asyncio
    async def test_connect_success(self):
        from jumpstarter_driver_kubevirt.driver import KubevirtConsole

        console = KubevirtConsole(namespace="test-ns", vm_name="my-vm", kubeconfig="/path/kc")
        mock_stream = MagicMock()

        with (
            patch("jumpstarter_driver_kubevirt.driver._build_console_ws_url") as mock_build_url,
            patch("jumpstarter_driver_kubevirt.driver.websockets") as mock_ws,
            patch("jumpstarter_driver_kubevirt.driver.WebsocketClientStream") as mock_wss,
        ):
            mock_build_url.return_value = ("wss://example.com/console", {}, None)
            mock_websocket = AsyncMock()
            mock_ws_cm = AsyncMock()
            mock_ws_cm.__aenter__ = AsyncMock(return_value=mock_websocket)
            mock_ws_cm.__aexit__ = AsyncMock(return_value=False)
            mock_ws.connect.return_value = mock_ws_cm

            mock_stream_cm = AsyncMock()
            mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_stream)
            mock_stream_cm.__aexit__ = AsyncMock(return_value=False)
            mock_wss.return_value = mock_stream_cm

            async with console.connect() as stream:
                assert stream is mock_stream

    @pytest.mark.asyncio
    async def test_connect_retry_on_not_running(self):
        from websockets.exceptions import InvalidStatus

        from jumpstarter_driver_kubevirt.driver import KubevirtConsole

        console = KubevirtConsole(namespace="test-ns", vm_name="my-vm", kubeconfig="/path/kc")
        mock_stream = MagicMock()

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.body = b"VMI is not running"

        error = InvalidStatus(mock_response)
        counter = [0]

        def make_connect_cm(*_args, **_kwargs):
            counter[0] += 1
            if counter[0] <= 2:
                cm = MagicMock()
                cm.__aenter__ = AsyncMock(side_effect=error)
                cm.__aexit__ = AsyncMock(return_value=False)
                return cm
            cm = MagicMock()
            cm.__aenter__ = AsyncMock(return_value=AsyncMock())
            cm.__aexit__ = AsyncMock(return_value=False)
            return cm

        with (
            patch("jumpstarter_driver_kubevirt.driver._build_console_ws_url") as mock_build_url,
            patch("jumpstarter_driver_kubevirt.driver.websockets") as mock_ws,
            patch("jumpstarter_driver_kubevirt.driver.WebsocketClientStream") as mock_wss,
            patch("jumpstarter_driver_kubevirt.driver.asyncio.sleep", new_callable=AsyncMock) as mock_sleep,
        ):
            mock_build_url.return_value = ("wss://example.com/console", {}, None)
            mock_ws.connect = make_connect_cm

            mock_stream_cm = MagicMock()
            mock_stream_cm.__aenter__ = AsyncMock(return_value=mock_stream)
            mock_stream_cm.__aexit__ = AsyncMock(return_value=False)
            mock_wss.return_value = mock_stream_cm

            async with console.connect():
                pass

            assert mock_sleep.call_count == 2


class TestKubevirtStorage:
    def _make_storage(self, **kwargs):
        from jumpstarter_driver_kubevirt.driver import KubevirtStorage

        defaults = {"namespace": "test-ns", "vm_name": "test-vm"}
        defaults.update(kwargs)
        return KubevirtStorage(**defaults)

    def test_client_class_method(self):
        from jumpstarter_driver_kubevirt.driver import KubevirtStorage

        assert KubevirtStorage.client() == "jumpstarter_driver_kubevirt.client.KubevirtStorageClient"

    def test_resolve_vm_name(self):
        storage = self._make_storage(vm_name="my-vm")
        assert storage._resolve_vm_name() == "my-vm"

    def test_resolve_vm_name_empty_raises(self):
        storage = self._make_storage(vm_name="")
        with pytest.raises(ValueError, match="vm_name not set"):
            storage._resolve_vm_name()

    def test_get_client_lazy_init(self):
        storage = self._make_storage()
        with patch("jumpstarter_driver_kubevirt.driver._build_client") as mock_build:
            mock_client = MagicMock()
            mock_build.return_value = mock_client

            result = storage._get_client()
            assert result is mock_client

            result2 = storage._get_client()
            assert result2 is mock_client
            assert mock_build.call_count == 1

    @pytest.mark.asyncio
    async def test_flash_delete_then_create(self):
        storage = self._make_storage()
        mock_client = AsyncMock()
        storage._client = mock_client

        await storage.flash("http://example.com/image.qcow2")

        mock_client.delete.assert_called_once()
        mock_client.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_flash_delete_404_still_creates(self):
        from lightkube.core.exceptions import ApiError

        storage = self._make_storage()
        mock_client = AsyncMock()

        error = ApiError.__new__(ApiError)
        error.status = MagicMock()
        error.status.code = 404

        mock_client.delete = AsyncMock(side_effect=error)
        storage._client = mock_client

        await storage.flash("http://example.com/image.qcow2")

        mock_client.create.assert_called_once()

    @pytest.mark.asyncio
    async def test_flash_delete_other_error_raises(self):
        from lightkube.core.exceptions import ApiError

        storage = self._make_storage()
        mock_client = AsyncMock()

        error = ApiError.__new__(ApiError)
        error.status = MagicMock()
        error.status.code = 500

        mock_client.delete = AsyncMock(side_effect=error)
        storage._client = mock_client

        with pytest.raises(ApiError):
            await storage.flash("http://example.com/image.qcow2")


class TestKubevirt:
    def test_client_class_method(self):
        from jumpstarter_driver_kubevirt.driver import Kubevirt

        assert Kubevirt.client() == "jumpstarter_driver_kubevirt.client.KubevirtClient"
