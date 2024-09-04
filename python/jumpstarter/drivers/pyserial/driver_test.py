from jumpstarter.common.utils import serve
from jumpstarter.drivers.pyserial.driver import PySerial


def test_drivers_pyserial():
    with serve(PySerial(url="loop://")) as client:
        with client.stream() as stream:
            stream.send(b"hello")
            assert "hello".startswith(stream.receive().decode("utf-8"))
