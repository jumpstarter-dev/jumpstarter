from unittest.mock import MagicMock, patch

from jumpstarter_driver_qualcomm.client import (
    QualcommFlasherClient,
    _load_manifest_source,
)

MANIFEST_YAML = """
name: "SA8775P ES22 AWE Firmware"
data:
  folder: "r00002.2a_AWE"
steps:
  - sleep: 1
"""


def test_load_manifest_source_from_https_url():
    response = MagicMock()
    response.read.return_value = MANIFEST_YAML.encode("utf-8")
    response.__enter__.return_value = response

    with patch("jumpstarter_driver_qualcomm.client.urlopen", return_value=response) as urlopen:
        manifest = _load_manifest_source("https://example.com/manifests/es22.yaml")

    urlopen.assert_called_once_with("https://example.com/manifests/es22.yaml")
    assert manifest["name"] == "SA8775P ES22 AWE Firmware"
    assert manifest["data"]["folder"] == "r00002.2a_AWE"


def test_flash_stream_uses_http_adapter_for_firmware_url():
    client = MagicMock()
    client.flash_stream = QualcommFlasherClient.flash_stream.__get__(client, QualcommFlasherClient)
    client._iter_flash_status = MagicMock(return_value=iter([]))

    with patch("jumpstarter_driver_qualcomm.client._http_url_adapter") as http_adapter:
        http_adapter.return_value.__enter__.return_value = {"url": "https://example.com/fw.tar.xz"}
        http_adapter.return_value.__exit__.return_value = None

        list(
            client.flash_stream(
                "https://example.com/firmware/sx4-r00021.1a.tar.xz",
                manifest={"name": "test", "data": {"folder": "fw"}, "steps": []},
            )
        )

    http_adapter.assert_called_once_with(
        client=client,
        url="https://example.com/firmware/sx4-r00021.1a.tar.xz",
        mode="rb",
    )
    client._iter_flash_status.assert_called_once()


def test_flash_stream_uses_local_adapter_for_file_path():
    client = MagicMock()
    client.flash_stream = QualcommFlasherClient.flash_stream.__get__(client, QualcommFlasherClient)
    client._iter_flash_status = MagicMock(return_value=iter([]))

    with (
        patch("jumpstarter_driver_qualcomm.client._local_file_adapter") as local_adapter,
        patch("jumpstarter_driver_qualcomm.client._http_url_adapter") as http_adapter,
    ):
        local_adapter.return_value.__enter__.return_value = "local-handle"
        local_adapter.return_value.__exit__.return_value = None

        list(client.flash_stream("./firmware.tar.xz"))

    local_adapter.assert_called_once()
    http_adapter.assert_not_called()


def test_flash_stream_loads_manifest_from_https_url():
    client = MagicMock()
    client.flash_stream = QualcommFlasherClient.flash_stream.__get__(client, QualcommFlasherClient)
    client._iter_flash_status = MagicMock(return_value=iter([]))

    response = MagicMock()
    response.read.return_value = MANIFEST_YAML.encode("utf-8")
    response.__enter__.return_value = response

    with (
        patch("jumpstarter_driver_qualcomm.client.urlopen", return_value=response),
        patch("jumpstarter_driver_qualcomm.client._http_url_adapter") as http_adapter,
    ):
        http_adapter.return_value.__enter__.return_value = {"url": "https://example.com/fw.tar.xz"}
        http_adapter.return_value.__exit__.return_value = None

        list(
            client.flash_stream(
                "https://example.com/fw.tar.xz",
                manifest="https://example.com/es22.yaml",
            )
        )

    client._iter_flash_status.assert_called_once()
    assert client._iter_flash_status.call_args.kwargs["manifest"]["name"] == "SA8775P ES22 AWE Firmware"
