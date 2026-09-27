import json
import plistlib
import socket
import socketserver
import struct
import threading
import urllib.request

import pytest

from .driver import IosDevice
from jumpstarter.common.utils import serve

LEASED_UDID = "00008110-001234567890001E"
OTHER_UDID = "00008110-001234567890002E"


def _read_exactly(sock, count):
    data = bytearray()
    while len(data) < count:
        part = sock.recv(count - len(data))
        if not part:
            raise EOFError("usbmux stream ended early")
        data.extend(part)
    return bytes(data)


def read_packet(sock):
    length, version, message, tag = struct.unpack("<IIII", _read_exactly(sock, 16))
    assert version == 1 and message == 8
    assert 16 < length <= 1024 * 1024
    return plistlib.loads(_read_exactly(sock, length - 16)), tag


def request(sock, payload, tag=23):
    data = plistlib.dumps(payload)
    sock.sendall(struct.pack("<IIII", len(data) + 16, 1, 8, tag) + data)
    response, response_tag = read_packet(sock)
    assert response_tag == tag
    return response


def packet(payload, tag=1):
    data = plistlib.dumps(payload)
    return struct.pack("<IIII", len(data) + 16, 1, 8, tag) + data


def device(udid, device_id):
    return {
        "MessageType": "Attached",
        "DeviceID": device_id,
        "Properties": {"SerialNumber": udid, "DeviceID": device_id, "ConnectionType": "USB"},
    }


class MuxHandler(socketserver.BaseRequestHandler):
    def handle(self):
        try:
            self.handle_mux()
        except (EOFError, ConnectionError, OSError):
            pass

    def handle_mux(self):  # noqa: C901
        while True:
            length, version, message, tag = struct.unpack("<IIII", _read_exactly(self.request, 16))
            if version != 1 or message != 8 or not 16 < length <= 1024 * 1024:
                return
            request = plistlib.loads(_read_exactly(self.request, length - 16))
            command = request.get("MessageType")
            if command == "ListDevices":
                result = {"DeviceList": [device(LEASED_UDID, 1), device(OTHER_UDID, 2)]}
            elif command == "ReadBUID":
                result = {"BUID": "FAKE-HOST-ID"}
            elif command == "ReadPairRecord":
                result = {"PairRecordData": plistlib.dumps({"HostID": "FAKE-PAIR-" + request["PairRecordID"]})}
            elif command == "Listen":
                self.request.sendall(packet({"MessageType": "Result", "Number": 0}, tag))
                self.request.sendall(packet(device(OTHER_UDID, 2), 0) + packet(device(LEASED_UDID, 1), 0))
                _read_exactly(self.request, 1)
                return
            elif command == "Connect":
                if request.get("DeviceID") not in (1, 2):
                    self.request.sendall(packet({"MessageType": "Result", "Number": 2}, tag))
                    return
                self.request.sendall(packet({"MessageType": "Result", "Number": 0}, tag))
                port = socket.ntohs(request["PortNumber"])
                if port == 62078:
                    self.lockdown()
                elif port == 8100:
                    self.request.recv(4096)
                    body = b'{"value":{"ready":true,"message":"mock HTTP service"}}'
                    self.request.sendall(
                        b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nConnection: close\r\n"
                        + f"Content-Length: {len(body)}\r\n\r\n".encode()
                        + body
                    )
                else:
                    while data := self.request.recv(65536):
                        self.request.sendall(data)
                return
            else:
                result = {"MessageType": "Result", "Number": 1}
            self.request.sendall(packet(result, tag))

    def lockdown(self):
        values = {"ProductVersion": "27.1", "ProductType": "iPhone18,1"}
        while True:
            length = struct.unpack(">I", _read_exactly(self.request, 4))[0]
            if not 0 < length <= 1024 * 1024:
                return
            request = plistlib.loads(_read_exactly(self.request, length))
            result = plistlib.dumps({"Request": "GetValue", "Value": values[request["Key"]]})
            self.request.sendall(struct.pack(">I", len(result)) + result)


class MuxServer(socketserver.ThreadingTCPServer):
    daemon_threads = True
    allow_reuse_address = True


@pytest.fixture
def mux_server():
    with MuxServer(("127.0.0.1", 0), MuxHandler) as server:
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            yield server.server_address
        finally:
            server.shutdown()
            thread.join(timeout=5)
            assert not thread.is_alive()


@pytest.fixture
def client(mux_server):
    with serve(
        IosDevice(
            transport="network",
            trust_mode="passthrough",
            udid=LEASED_UDID,
            usbmux_host=mux_server[0],
            usbmux_port=mux_server[1],
            forward_ports=[8100, 9100],
        )
    ) as client:
        yield client


def test_local_mode_device_scoping_and_byte_transport(client):
    info = client.info()
    assert info["present"] is True, info
    assert info["udid"] == LEASED_UDID
    assert info["os_version"] == "27.1"
    assert info["protocols"] == ["usbmux"]
    assert sorted(info["forward_ports"]) == [8100, 9100]
    with client.serve() as address:
        host, port = address.rsplit(":", 1)
        assert host == "127.0.0.1"
        endpoint = host, int(port)
        with socket.create_connection(endpoint, timeout=15) as sock:
            response = request(sock, {"MessageType": "ListDevices"})
            assert [item["Properties"]["SerialNumber"] for item in response["DeviceList"]] == [LEASED_UDID]
            response = request(sock, {"MessageType": "ReadPairRecord", "PairRecordID": LEASED_UDID})
            assert plistlib.loads(response["PairRecordData"])["HostID"] == "FAKE-PAIR-" + LEASED_UDID
            for message in (
                {"MessageType": "ReadPairRecord", "PairRecordID": OTHER_UDID},
                {"MessageType": "SavePairRecord", "PairRecordID": LEASED_UDID, "PairRecordData": b"forbidden"},
                {"MessageType": "DeletePairRecord", "PairRecordID": LEASED_UDID},
                {"MessageType": "Connect", "DeviceID": 2, "PortNumber": socket.htons(9100)},
                {"MessageType": "ListListeners"},
            ):
                assert request(sock, message)["Number"] != 0
            assert (
                request(sock, {"MessageType": "Connect", "DeviceID": 1, "PortNumber": socket.htons(9100)})["Number"]
                == 0
            )
            payload = bytes(range(256)) * 1024
            sock.sendall(payload)
            assert _read_exactly(sock, len(payload)) == payload
        with socket.create_connection(endpoint, timeout=15) as sock:
            assert request(sock, {"MessageType": "Listen"})["Number"] == 0
            event, _ = read_packet(sock)
            assert event["Properties"]["SerialNumber"] == LEASED_UDID
    with (
        client.forward(8100, local_port=0) as address,
        urllib.request.urlopen("http://" + address + "/status", timeout=15) as response,
    ):
        assert json.load(response)["value"]["ready"] is True
    with pytest.raises(ValueError, match="usbmux"), client.serve(protocol="coredevice"):
        raise AssertionError("unadvertised protocol accepted")
