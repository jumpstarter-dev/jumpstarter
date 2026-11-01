import base64
import json
import plistlib
import struct
from contextlib import asynccontextmanager

import anyio
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305

from .remotepairing import (
    PACKET_MAGIC,
    WIRE_PROTOCOL_VERSION,
    ControlChannel,
    DeviceIdentity,
    HostPairing,
    RemotePairingError,
    SrpClient,
    SrpServer,
    Tlv,
    _Channel,
    _counter_nonce,
    _hkdf,
    _nonce,
    _raw_public,
    auth_tag,
    opack_decode,
    opack_encode,
    siphash24,
    tlv8_decode,
    tlv8_encode,
)
from .usbmux import _memory_pair


@pytest.fixture
def anyio_backend():
    return "asyncio"


def _identity(name="iPhone") -> DeviceIdentity:
    return DeviceIdentity.generate(name=name, model="iPhone17,1", udid=f"UDID-{name}")


class Host(_Channel):
    """The Mac's side of the control channel: pair-verify, then encrypted requests."""

    originated_by = "host"

    def __init__(self, stream, identity: DeviceIdentity):
        super().__init__(stream)
        self.identity = identity
        self._outgoing = self._incoming = None
        self._sent = self._received = 0

    async def handshake(self) -> dict:
        await self._send_plain({"request": {"_0": {"handshake": {"_0": {"hostOptions": {}}}}}})
        return (await self._receive_plain())["response"]

    async def pairing_reply(self) -> dict[int, bytes]:
        event = (await self._receive_plain())["event"]["_0"]
        return tlv8_decode(base64.b64decode(event["pairingData"]["_0"]["data"]))

    async def verify(self, device_key: bytes) -> dict[int, bytes]:
        ephemeral = X25519PrivateKey.generate()
        host_public = _raw_public(ephemeral)
        await self._send_pairing_data(
            tlv8_encode([(Tlv.STATE, b"\x01"), (Tlv.PUBLIC_KEY, host_public)]), "verifyManualPairing"
        )
        m2 = await self.pairing_reply()
        device_public = m2[Tlv.PUBLIC_KEY]
        shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(device_public))
        cipher = ChaCha20Poly1305(_hkdf(shared, b"Pair-Verify-Encrypt-Salt", b"Pair-Verify-Encrypt-Info"))
        fields = tlv8_decode(cipher.decrypt(_nonce(b"PV-Msg02"), m2[Tlv.ENCRYPTED_DATA], None))
        Ed25519PublicKey.from_public_bytes(device_key).verify(
            fields[Tlv.SIGNATURE], device_public + fields[Tlv.IDENTIFIER] + host_public
        )
        identifier = self.identity.identifier.encode()
        signature = self.identity.signing_key.sign(host_public + identifier + device_public)
        sealed = cipher.encrypt(
            _nonce(b"PV-Msg03"), tlv8_encode([(Tlv.IDENTIFIER, identifier), (Tlv.SIGNATURE, signature)]), None
        )
        await self._send_pairing_data(
            tlv8_encode([(Tlv.STATE, b"\x03"), (Tlv.ENCRYPTED_DATA, sealed)]), "verifyManualPairing"
        )
        m4 = await self.pairing_reply()
        if Tlv.ERROR not in m4:
            self._outgoing = ChaCha20Poly1305(_hkdf(shared, None, b"ClientEncrypt-main"))
            self._incoming = ChaCha20Poly1305(_hkdf(shared, None, b"ServerEncrypt-main"))
        return m4

    async def send_encrypted(self, payload: dict) -> None:
        assert self._outgoing is not None
        sealed = self._outgoing.encrypt(_counter_nonce(self._sent), json.dumps(payload).encode(), None)
        self._sent += 1
        await self._send({"streamEncrypted": {"_0": base64.b64encode(sealed).decode()}})

    async def request(self, name: str, body: dict | None = None) -> dict:
        await self.send_encrypted({"request": {"_0": {name: body or {}}}})
        packet = await self._receive()
        assert self._incoming is not None
        sealed = base64.b64decode(packet["message"]["streamEncrypted"]["_0"])
        reply = json.loads(self._incoming.decrypt(_counter_nonce(self._received), sealed, None))
        self._received += 1
        return reply["response"]


@asynccontextmanager
async def device_channel(identity, **options):
    """Run a device-side control channel; yield the Mac's end and the channel."""
    host_stream, device_stream = _memory_pair()
    options.setdefault("on_request", _no_requests)
    channel = ControlChannel(device_stream, identity, **options)
    async with anyio.create_task_group() as tasks:
        tasks.start_soon(channel.serve)
        yield host_stream, channel
        tasks.cancel_scope.cancel()


async def _no_requests(_name, _body):
    return None


class Recorder:
    """A stream that keeps what is sent, for calling channel steps directly."""

    def __init__(self):
        self.sent = []

    async def send(self, data):
        self.sent.append(data)

    async def receive(self, max_bytes=65536):
        raise anyio.EndOfStream

    def messages(self):
        return [json.loads(packet[len(PACKET_MAGIC) + 2 :])["message"] for packet in self.sent]

    def pairing(self):
        data = self.messages()[-1]["plain"]["_0"]["event"]["_0"]["pairingData"]["_0"]["data"]
        return tlv8_decode(base64.b64decode(data))


def test_tlv8_round_trip_splits_long_values():
    long = bytes(range(256)) * 2
    encoded = tlv8_encode([(Tlv.STATE, b"\x01"), (Tlv.PUBLIC_KEY, long), (Tlv.METHOD, b"")])
    assert encoded.count(bytes((Tlv.PUBLIC_KEY, 255))) == 2
    assert tlv8_decode(encoded) == {Tlv.STATE: b"\x01", Tlv.PUBLIC_KEY: long, Tlv.METHOD: b""}


@pytest.mark.parametrize("data", [b"\x01", b"\x01\x05abc"])
def test_tlv8_rejects_truncated_items(data):
    with pytest.raises(RemotePairingError, match="truncated TLV8"):
        tlv8_decode(data)


@pytest.mark.parametrize(
    "value",
    [
        True,
        False,
        None,
        0,
        0x27,
        1000,
        -5,
        2**40,
        "",
        "short",
        "x" * 40,
        "y" * 300,
        b"\x00" * 4,
        b"\x01" * 100,
        b"\x02" * 300,
        {"name": "iPhone", "nested": {"ecid": 12345, "key": b"\x00" * 16}},
    ],
)
def test_opack_round_trip(value):
    assert opack_decode(opack_encode(value)) == value


def test_opack_decodes_narrow_integers():
    assert opack_decode(b"\x30\xff") == -1
    assert opack_decode(b"\x31\x00\x01") == 256


@pytest.mark.parametrize(
    "data,message",
    [
        (b"", "truncated"),
        (b"\x45ab", "truncated"),
        (b"\x08\x08", "trailing"),
        (b"\xef", "terminated"),
        (b"\x05", "unsupported OPACK tag 0x5"),
    ],
)
def test_opack_rejects_malformed_data(data, message):
    with pytest.raises(RemotePairingError, match=message):
        opack_decode(data)


def test_opack_refuses_what_it_cannot_encode():
    with pytest.raises(ValueError, match="15 or more"):
        opack_encode({str(index): index for index in range(15)})
    with pytest.raises(TypeError, match="float"):
        opack_encode(1.5)


def test_siphash_matches_reference_vectors():
    key = bytes(range(16))
    assert siphash24(key, b"") == 0x726FDB47DD0E0E31
    assert siphash24(key, bytes(range(15))) == 0xA129CA6149BE45E5


def test_auth_tag_is_stable_for_an_identity():
    tag = auth_tag(bytes(16), "IDENTIFIER")
    assert tag == auth_tag(bytes(16), "IDENTIFIER") != auth_tag(bytes(16), "OTHER")
    assert len(base64.b64decode(tag)) == 6


def test_identity_survives_a_round_trip():
    identity = _identity()
    identity.ecid = 42
    identity.hosts["MAC"] = b"\x01" * 32
    restored = DeviceIdentity.from_dict(json.loads(json.dumps(identity.to_dict())))
    assert restored.to_dict() == identity.to_dict()
    assert restored.public_key == identity.public_key
    assert restored.auth_tag == identity.auth_tag


def test_identity_describes_itself_to_the_mac():
    identity = _identity()
    identity.ecid = 7
    identity.kvs = {"NULL": {"ProductVersion": "27.0"}}
    info = opack_decode(identity.pairing_info())
    assert info["accountID"] == identity.identifier
    assert info["altIRK"] == identity.alt_irk
    assert info["remotepairing_ecid"] == 7
    peer = identity.peer_info()
    assert plistlib.loads(base64.b64decode(peer["deviceKVSData"])) == identity.kvs
    assert peer["udid"] == identity.udid


def test_srp_rejects_degenerate_public_keys():
    with pytest.raises(RemotePairingError, match="invalid SRP public key"):
        SrpServer().verify(b"\x00", b"proof")
    with pytest.raises(RemotePairingError, match="invalid SRP public key"):
        SrpClient(b"salt", b"\x00", b"000000")


def test_srp_client_and_server_agree_only_on_the_same_code():
    server = SrpServer()
    client = SrpClient(server.salt, server.public.to_bytes(384, "big"), b"000000")
    client.verify(server.verify(client.public.to_bytes(384, "big"), client.proof))
    assert client.session_key == server.session_key

    wrong = SrpClient(server.salt, server.public.to_bytes(384, "big"), b"123456")
    with pytest.raises(RemotePairingError, match="did not match"):
        server.verify(wrong.public.to_bytes(384, "big"), wrong.proof)
    with pytest.raises(RemotePairingError, match="check the pairing code"):
        wrong.verify(b"\x00" * 64)


@pytest.mark.anyio
async def test_device_pairs_with_a_mac_offering_pair_nearby_device():
    # The stand-in pairs as the controller; the accessory side here plays the Mac.
    device, mac = _identity("device"), _identity("mac")
    paired = []

    async def on_paired():
        paired.append(dict(mac.hosts))

    async def code():
        return " 000000\n"

    async with device_channel(mac, allow_pair_setup=True, on_paired=on_paired) as (stream, channel):
        host = await HostPairing(stream, device, code).pair()
    assert host == mac.identifier
    assert device.hosts == {mac.identifier: mac.public_key}
    assert paired == [{device.identifier: device.public_key}]
    assert channel.host_identifier == device.identifier
    assert channel.encryption_key is not None


@pytest.mark.anyio
async def test_pairing_with_the_wrong_code_is_rejected():
    async def code():
        return "123456"

    async with device_channel(_identity("mac"), allow_pair_setup=True) as (stream, _channel):
        with pytest.raises(RemotePairingError, match="rejected pairing"):
            await HostPairing(stream, _identity("device"), code).pair()


@pytest.mark.anyio
async def test_pair_setup_is_refused_unless_allowed():
    async def code():
        raise AssertionError("no code is asked for")

    async with device_channel(_identity("mac")) as (stream, _channel):
        with pytest.raises(RemotePairingError, match="rejected pairing"):
            await HostPairing(stream, _identity("device"), code).pair()


@pytest.mark.anyio
async def test_mac_that_rejects_pairing_is_reported():
    device_stream, mac_stream = _memory_pair()
    mac = _Channel(mac_stream)

    async def code():
        return "000000"

    async def reject():
        await mac._receive()
        await mac._send_plain({"response": {"_1": {"handshake": {"_0": {}}}}})
        await mac._receive()
        await mac._send_plain({"event": {"_0": {"awaitingUserConsent": {}}}})
        error = {"wrappedError": {"userInfo": {"NSLocalizedDescription": "The user declined"}}}
        await mac._send_plain({"event": {"_0": {"pairingRejectedWithError": error}}})

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(reject)
        with pytest.raises(RemotePairingError, match="The user declined"):
            await HostPairing(device_stream, _identity(), code).pair()


@pytest.mark.anyio
async def test_mac_must_answer_the_handshake():
    device_stream, mac_stream = _memory_pair()
    mac = _Channel(mac_stream)

    async def answer():
        await mac._receive()
        await mac._send_plain({"response": {"_1": {}}})

    async with anyio.create_task_group() as tasks:
        tasks.start_soon(answer)
        with pytest.raises(RemotePairingError, match="did not answer"):
            await HostPairing(device_stream, _identity(), lambda: None).pair()  # ty: ignore[invalid-argument-type]


@pytest.mark.anyio
async def test_handshake_advertises_pairing_options():
    async with device_channel(_identity()) as (stream, _channel):
        response = await Host(stream, _identity("mac")).handshake()
    handshake = response["_1"]["handshake"]["_0"]
    assert handshake["wireProtocolVersion"] == WIRE_PROTOCOL_VERSION
    assert handshake["deviceOptions"]["allowsPairSetup"] is False
    assert response["forRequestIdentifier"] == 0


@pytest.mark.anyio
async def test_paired_mac_verifies_and_makes_encrypted_requests():
    device, mac = _identity("device"), _identity("mac")
    device.hosts[mac.identifier] = mac.public_key
    device.ecid = 99
    requests, saved = [], []

    async def on_request(name, body):
        requests.append((name, body))
        return {"port": 50000} if name == "createListener" else None

    async def on_paired():
        saved.append(dict(device.hosts))

    async with device_channel(device, on_request=on_request, on_paired=on_paired) as (stream, channel):
        host = Host(stream, mac)
        assert await host.verify(device.public_key) == {Tlv.STATE: b"\x04"}
        assert channel.host_identifier == mac.identifier

        peer = await host.request("peerInfo")
        assert peer["_1"]["peerInfo"]["_0"]["ecid"] == 99
        assert await host.request("ping") == {"_1": {"ping": {}}, "forRequestIdentifier": 3}
        assert (await host.request("createRemoteUnlockKey"))["_1"] == {"createRemoteUnlockKey": {"hostKey": ""}}
        listener = await host.request("createListener", {"transportProtocolType": "tcp"})
        assert listener["_1"] == {"createListener": {"port": 50000}}
        unsupported = await host.request("startTunnel")
        assert unsupported["_1"]["errorExtended"]["_0"]["code"] == 1
        assert requests == [("createListener", {"transportProtocolType": "tcp"}), ("startTunnel", {})]

        await host.request("unpair")
        assert device.hosts == {} and saved == [{}]


@pytest.mark.anyio
async def test_unpaired_mac_fails_pair_verify():
    device = _identity("device")
    async with device_channel(device) as (stream, channel):
        reply = await Host(stream, _identity("mac")).verify(device.public_key)
        assert reply == {Tlv.STATE: b"\x04", Tlv.ERROR: b"\x02"}
        assert channel.encryption_key is None


@pytest.mark.anyio
async def test_channel_ignores_messages_it_does_not_understand(caplog):
    async with device_channel(_identity()) as (stream, _channel):
        host = Host(stream, _identity("mac"))
        await host._send({"unknown": {}})
        await host._send_plain({"request": {"_0": {"somethingElse": {}}}})
        await host._send_pairing_data(b"", "unknownKind")
        await host._send_plain({"event": {"_0": {"pairVerifyFailed": {}}}})
        await host._send_plain({"event": {"_0": {"somethingHappened": {}}}})
        response = await host.handshake()
    assert response["forRequestIdentifier"] == 5
    assert "ignoring RemotePairing message ['unknown']" in caplog.text
    assert "unsupported plain RemotePairing request ['somethingElse']" in caplog.text
    assert "unsupported pairing kind 'unknownKind'" in caplog.text
    assert "unsupported RemotePairing event ['somethingHappened']" in caplog.text


@pytest.mark.anyio
async def test_encrypted_event_is_logged_without_a_response(caplog):
    device, mac = _identity("device"), _identity("mac")
    device.hosts[mac.identifier] = mac.public_key
    async with device_channel(device) as (stream, _channel):
        host = Host(stream, mac)
        await host.verify(device.public_key)
        await host.send_encrypted({"event": {"_0": {}}})
        assert (await host.request("ping"))["_1"] == {"ping": {}}
    assert "encrypted RemotePairing event" in caplog.text


@pytest.mark.anyio
async def test_out_of_order_or_unpaired_steps_are_errors():
    channel = ControlChannel(Recorder(), _identity(), on_request=_no_requests)  # ty: ignore[invalid-argument-type]
    with pytest.raises(RemotePairingError, match="pair-verify state"):
        await channel._pair_verify({Tlv.STATE: b"\x03"})
    with pytest.raises(RemotePairingError, match="before pairing"):
        await channel._encrypted(b"sealed")
    channel.allow_pair_setup = True
    with pytest.raises(RemotePairingError, match="pair-setup state"):
        await channel._pair_setup({Tlv.STATE: b"\x03"}, {})


@pytest.mark.anyio
async def test_bad_pair_setup_messages_are_refused():
    recorder = Recorder()
    channel = ControlChannel(recorder, _identity(), on_request=_no_requests, allow_pair_setup=True)  # ty: ignore[invalid-argument-type]
    await channel._pair_setup({Tlv.STATE: b"\x01"}, {"sendingHost": "Mac"})
    assert recorder.messages()[0]["plain"]["_0"]["event"]["_0"] == {"awaitingUserConsent": {}}
    assert recorder.pairing()[Tlv.STATE] == b"\x02"

    await channel._pair_setup({Tlv.STATE: b"\x03"}, {})
    assert recorder.pairing() == {Tlv.STATE: b"\x04", Tlv.ERROR: b"\x02"}
    assert channel._setup is None

    channel._setup = SrpServer()
    channel._setup.session_key = b"\x00" * 64
    await channel._pair_setup({Tlv.STATE: b"\x05", Tlv.ENCRYPTED_DATA: b"not sealed"}, {})
    assert recorder.pairing() == {Tlv.STATE: b"\x06", Tlv.ERROR: b"\x02"}
    assert channel.host_identifier is None


@pytest.mark.anyio
async def test_framing_rejects_foreign_or_oversized_packets():
    stream, other = _memory_pair()
    channel = _Channel(stream)
    await other.send(b"NotPairing")
    with pytest.raises(RemotePairingError, match="not a RemotePairing packet"):
        await channel._receive()

    stream, other = _memory_pair()
    channel = _Channel(stream)
    await other.send(PACKET_MAGIC + struct.pack(">H", 2) + b"[]")
    with pytest.raises(RemotePairingError, match="not an object"):
        await channel._receive()

    await other.send(PACKET_MAGIC + struct.pack(">H", 2) + b"{}")
    with pytest.raises(RemotePairingError, match="expected a plain"):
        await channel._receive_plain()

    with pytest.raises(RemotePairingError, match="too large"):
        await channel._send({"blob": "x" * 70000})
