from .driver import MockRadio
from jumpstarter.common.utils import serve


def test_a_mock_radio_gives_back_what_goes_into_its_port():
    with serve(MockRadio()) as client:
        assert client.status() == {"running": False, "attached": False, "peer": False}
        client.wait_until_attached(timeout=5)
        (port,) = client.ports()
        assert (port.direction, port.transport, port.attachment) == ("connects", "stream", "socket")
        assert port.child == "frames"
        with client.children[port.child].stream() as stream:
            stream.send(b"hello")
            assert stream.receive() == b"hello"
        client.stop()
        assert client.status()["running"] is False
