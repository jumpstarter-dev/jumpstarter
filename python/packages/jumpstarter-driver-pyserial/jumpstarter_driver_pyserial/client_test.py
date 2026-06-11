import threading
from unittest.mock import MagicMock

from .driver import PySerial
from jumpstarter.common.utils import serve


def test_find_power_client_no_root():
    with serve(PySerial(url="loop://")) as client:
        assert client._find_power_client() is None


def test_find_power_client_with_cycle():
    power = MagicMock(spec=["cycle", "children"])
    power.children = {}
    root = MagicMock(spec=["children"])
    root.children = {"power": power}

    with serve(PySerial(url="loop://")) as client:
        object.__setattr__(client, "root", root)
        assert client._find_power_client() is power


def test_make_power_cycle_calls_cycle():
    called = threading.Event()
    power = MagicMock()
    power.cycle = MagicMock(side_effect=lambda: called.set())

    with serve(PySerial(url="loop://")) as client:
        cycle_fn = client._make_power_cycle(power)
        client.portal.call(cycle_fn)
        assert called.is_set()
