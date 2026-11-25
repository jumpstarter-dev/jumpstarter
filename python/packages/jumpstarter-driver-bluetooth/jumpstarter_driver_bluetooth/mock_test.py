import pytest

from .mock import MockBluetooth
from jumpstarter.common.utils import serve
from jumpstarter.driver import Port


def test_a_mock_radio_declares_one_connects_port():
    radio = MockBluetooth()
    (port,) = radio.ports
    assert (port.name, port.direction, port.protocol) == ("echo", "connects", "echo")
    assert port.forwardable and port.listen is None and port.connect is None
    # as it is reported at registration, for a forward to name it
    (report,) = radio.report().ports
    assert report.name == "echo"


def test_a_mock_radio_can_be_a_service_or_the_client_of_one():
    assert MockBluetooth(direction="provides").ports[0].direction == "provides"
    requires = MockBluetooth(direction="requires", listen="127.0.0.1:7777").ports[0]
    assert (requires.direction, requires.listen) == ("requires", "127.0.0.1:7777")
    with pytest.raises(ValueError, match="listen"):
        MockBluetooth(direction="requires")


def test_the_exporter_configuration_may_name_the_port_itself():
    radio = MockBluetooth(ports=[Port(name="air", direction="connects", protocol="echo")])
    assert [port.name for port in radio.ports] == ["air"]
    with pytest.raises(ValueError, match="one port"):
        MockBluetooth(ports=[Port(name="one", direction="connects"), Port(name="two", direction="connects")])


def test_a_mock_radio_starts_stops_and_says_how_it_is():
    with serve(MockBluetooth()) as client:
        assert client.status() == {"running": False, "attached": False, "peer": False, "heard": 0, "sent": 0}
        status = client.wait_until_attached(timeout=5)
        assert status["running"] and status["attached"] and not status["peer"]
        assert client.ports() == [
            {"name": "echo", "direction": "connects", "protocol": "echo", "attachment": "socket", "transport": "stream"}
        ]
        with pytest.raises(Exception, match="nothing is joined"):
            client.exchange("hello", timeout=0.2)
        client.stop()
        assert client.status()["running"] is False


@pytest.mark.anyio
async def test_what_goes_into_a_mock_radios_port_comes_back_out():
    radio = MockBluetooth()
    await radio.start()
    async with radio.connect_port(radio.ports[0]) as air:
        assert not (await radio.status())["peer"]  # (nothing has used the path yet)
        await air.send(b"hello")
        assert await air.receive() == b"hello"
        status = await radio.status()
        assert status["peer"] and (status["heard"], status["sent"]) == (5, 5)
    assert not (await radio.status())["peer"]


@pytest.mark.anyio
async def test_a_mock_radio_that_is_off_hears_nothing():
    radio = MockBluetooth()
    async with radio.connect_port(radio.ports[0]) as air:
        await air.send(b"hello")
        assert (await radio.status())["heard"] == 0
