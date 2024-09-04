from itertools import islice
from random import randbytes
from threading import Semaphore

import can
import isotp
import pytest

from jumpstarter.common.utils import serve
from jumpstarter_driver_can.driver import Can


def test_client_can_send_recv(request):
    with (
        serve(Can(channel=request.node.name, interface="virtual")) as client1,
        serve(Can(channel=request.node.name, interface="virtual")) as client2,
        client1,
        client2,
    ):
        client1.send(can.Message(data=b"hello"))

        assert client2.recv().data == b"hello"

        with pytest.raises(NotImplementedError):
            # not implemented on virtual bus
            client1.flush_tx_buffer()


def test_client_can_property(request):
    driver = Can(channel=request.node.name, interface="virtual")
    with serve(driver) as client, client:
        assert client.channel_info == driver.bus.channel_info
        assert client.state == driver.bus.state
        assert client.protocol == driver.bus.protocol

        with pytest.raises(NotImplementedError):
            # not implemented on virtual bus
            client.state = can.BusState.PASSIVE


def test_client_can_iterator(request):
    with (
        serve(Can(channel=request.node.name, interface="virtual")) as client1,
        serve(Can(channel=request.node.name, interface="virtual")) as client2,
        client1,
        client2,
    ):
        client1.send(can.Message(data=b"a"))
        client1.send(can.Message(data=b"b"))
        client1.send(can.Message(data=b"c"))

        assert [msg.data for msg in islice(client2, 3)] == [b"a", b"b", b"c"]


def test_client_can_filter(request):
    with (
        serve(Can(channel=request.node.name, interface="virtual")) as client1,
        serve(Can(channel=request.node.name, interface="virtual")) as client2,
        client1,
        client2,
    ):
        client2.set_filters([{"can_id": 0x1, "can_mask": 0x1, "extended": True}])

        client1.send(can.Message(arbitration_id=0x0, data=b"a"))
        client1.send(can.Message(arbitration_id=0x1, data=b"b"))
        client1.send(can.Message(arbitration_id=0x2, data=b"c"))

        assert client2.recv().data == b"b"


def test_client_can_notifier(request):
    with (
        serve(Can(channel=request.node.name, interface="virtual")) as client1,
        serve(Can(channel=request.node.name, interface="virtual")) as client2,
        client1,
        client2,
    ):
        sem = Semaphore(0)

        def listener(msg):
            assert msg.data == b"hello"
            sem.release()

        notifier = can.Notifier(client2, [listener])

        client1.send(can.Message(data=b"hello"))

        sem.acquire()
        notifier.stop()


def test_client_can_redirect(request):
    with (
        serve(Can(channel=request.node.name, interface="virtual")) as client1,
        serve(Can(channel=request.node.name, interface="virtual")) as client2,
        client1,
        client2,
    ):
        bus3 = can.interface.Bus(request.node.name + "_inner", interface="virtual")
        bus4 = can.interface.Bus(request.node.name + "_inner", interface="virtual")

        notifier = can.Notifier(client2, [can.RedirectReader(bus3)])

        client1.send(can.Message(data=b"hello"))

        assert bus4.recv().data == b"hello"

        notifier.stop()


@pytest.mark.parametrize(
    "msgs, expected",
    [
        ([can.Message(data=b"a"), can.Message(data=b"b")], [(1, b"a"), (1, b"b"), (1, b"a"), (1, b"b")]),
        (can.Message(data=b"a"), [(1, b"a"), (1, b"a"), (1, b"a"), (1, b"a")]),
    ],
)
def test_client_can_send_periodic_local(request, msgs, expected):
    with (
        serve(Can(channel=request.node.name, interface="virtual")) as client1,
        serve(Can(channel=request.node.name, interface="virtual")) as client2,
        client1,
        client2,
    ):

        def modifier_callback(msg):
            msg.arbitration_id = 1

        client1.send_periodic(
            msgs=msgs,
            period=0.1,
            duration=1,
            store_task=True,
            modifier_callback=modifier_callback,
        )

        assert [(msg.arbitration_id, msg.data) for msg in islice(client2, 4)] == expected


@pytest.mark.parametrize(
    "msgs, expected",
    [
        ([can.Message(data=b"a"), can.Message(data=b"b")], [(0, b"a"), (0, b"b"), (0, b"a"), (0, b"b")]),
        (can.Message(data=b"a"), [(0, b"a"), (0, b"a"), (0, b"a"), (0, b"a")]),
    ],
)
def test_client_can_send_periodic_remote(request, msgs, expected):
    with (
        serve(Can(channel=request.node.name, interface="virtual")) as client1,
        serve(Can(channel=request.node.name, interface="virtual")) as client2,
        client1,
        client2,
    ):
        client1.send_periodic(
            msgs=msgs,
            period=0.1,
            duration=1,
            store_task=True,
        )

        assert [(msg.arbitration_id, msg.data) for msg in islice(client2, 4)] == expected


@pytest.mark.parametrize("tx_data_length", [8, 64])
@pytest.mark.parametrize("blocking_send", [False, True])
def test_client_can_isotp(request, tx_data_length, blocking_send):
    with (
        serve(Can(channel=request.node.name, interface="virtual")) as client1,
        serve(Can(channel=request.node.name, interface="virtual")) as client2,
        client1,
        client2,
    ):
        notifier1 = can.Notifier(client1, [])
        notifier2 = can.Notifier(client2, [])

        params = {
            "max_frame_size": 2048,
            "tx_data_length": tx_data_length,
            "blocking_send": blocking_send,
        }

        transport1 = isotp.NotifierBasedCanStack(
            client1,
            notifier1,
            address=isotp.Address(rxid=1, txid=2),
            params=params,
        )
        transport2 = isotp.NotifierBasedCanStack(
            client2,
            notifier2,
            address=isotp.Address(rxid=2, txid=1),
            params=params,
        )

        transport1.start()
        transport2.start()

        message = randbytes(params["max_frame_size"])

        transport1.send(message, send_timeout=10)
        assert transport2.recv(block=True, timeout=10) == message

        transport1.stop()
        transport2.stop()

        notifier1.stop()
        notifier2.stop()
