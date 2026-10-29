import socket
from dataclasses import replace

import pytest
from anyio import fail_after
from anyio.streams.buffered import BufferedByteReceiveStream

from .driver import UsbFlux, canonical_udid
from .usbmux import encode_message, receive_message
from .usbmux_test import exchange, running_mux
from jumpstarter.common.exceptions import ConfigurationError

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _driver(daemon, **kwargs):
    return UsbFlux(transport="network", udid="leased-udid", usbmux_host=daemon.host, usbmux_port=daemon.port, **kwargs)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"udid": ""},
        {"udid": "bad udid"},
        {"udid": "x", "transport": "serial"},
        {"udid": "x", "usbmux_host": "mux"},
        {"udid": "x", "usbmux_socket": ""},
        {"udid": "x", "transport": "network"},
        {"udid": "x", "transport": "network", "usbmux_host": "mux", "usbmux_port": 0},
        {"udid": "x", "transport": "network", "usbmux_host": "mux", "usbmux_port": "27015"},
        {"udid": "x", "connect_timeout": 0},
        {"udid": "x", "connect_timeout": True},
        {"udid": "x", "connect_timeout": float("inf")},
    ],
)
def test_invalid_config(kwargs):
    with pytest.raises(ConfigurationError):
        UsbFlux(**kwargs)


def test_canonical_udid_ignores_dash_and_case():
    assert canonical_udid("00008030-000000000000002E") == canonical_udid("00008030000000000000002e")


async def test_info_reports_presence_and_daemon_identity():
    async with running_mux() as (daemon, _):
        info = await _driver(daemon).info()
        assert info["present"] is True
        assert info["udid"] == "leased-udid"
        assert info["device_id"] == 7
        assert info["connection_type"] == "USB"
        assert info["protocols"] == ["usbmux"]

        absent = await replace(_driver(daemon), udid="missing-udid").info()
        assert absent["present"] is False
        assert "not present" in absent["reason"]


async def test_info_fails_closed_when_daemon_is_unreachable():
    driver = UsbFlux(
        transport="network", udid="leased-udid", usbmux_host="127.0.0.1", usbmux_port=9, connect_timeout=0.5
    )
    info = await driver.info()
    assert info["present"] is False
    assert info["reason"]


async def test_connect_usbmux_proxies_only_the_selected_device():
    async with running_mux() as (daemon, mock), _driver(daemon).connect_usbmux() as stream:
        reader = BufferedByteReceiveStream(stream)
        response = await exchange(stream, reader, {"MessageType": "ListDevices"})
        assert [item["DeviceID"] for item in response["DeviceList"]] == [7]
        response = await exchange(stream, reader, {"MessageType": "ReadPairRecord", "PairRecordID": "other-udid"})
        assert response["Number"] == 1
        assert all(request.get("MessageType") != "ReadPairRecord" for request in mock.requests)


async def test_connect_usbmux_matches_canonical_udid_form():
    async with running_mux() as (daemon, _):
        mock_udid = "0000AAAA-BBBBCCCCDDDDEEEE"
        async with running_mux() as (daemon, mock):
            mock.devices = [
                {
                    "MessageType": "Attached",
                    "DeviceID": 9,
                    "Properties": {"DeviceID": 9, "SerialNumber": mock_udid, "ConnectionType": "USB"},
                }
            ]
            driver = UsbFlux(
                transport="network",
                udid=mock_udid.replace("-", "").lower(),
                usbmux_host=daemon.host,
                usbmux_port=daemon.port,
            )
            assert (await driver.info())["udid"] == mock_udid
            async with driver.connect_usbmux() as stream:
                reader = BufferedByteReceiveStream(stream)
                response = await exchange(stream, reader, {"MessageType": "ListDevices"})
                assert [item["Properties"]["SerialNumber"] for item in response["DeviceList"]] == [mock_udid]


async def test_connect_usbmux_refuses_absent_device():
    async with running_mux() as (daemon, _):
        with pytest.raises(RuntimeError, match="not present"):
            async with replace(_driver(daemon), udid="missing-udid").connect_usbmux():
                pass


async def test_failed_connect_through_driver_stays_in_plist_mode():
    async with running_mux() as (daemon, mock), _driver(daemon).connect_usbmux() as stream:
        mock.connect_result = 3
        reader = BufferedByteReceiveStream(stream)
        response = await exchange(
            stream, reader, {"MessageType": "Connect", "DeviceID": 7, "PortNumber": socket.htons(8100)}
        )
        assert response["Number"] == 3
        await stream.send(encode_message({"MessageType": "ListDevices"}, 5))
        with fail_after(2):
            tag, response = await receive_message(reader)
        assert tag == 5 and len(response["DeviceList"]) == 1
