"""Device side of Apple's RemotePairing control channel.

macOS reaches network iOS devices by browsing ``_remotepairing._tcp`` and
speaking a JSON protocol framed as ``RPPairing`` packets. Pairing is the
HomeKit-style SRP pair-setup (M1-M6) and X25519/Ed25519 pair-verify (M1-M4);
after pair-verify, requests are sealed with ChaCha20-Poly1305. The exporter
plays the device here with its own synthetic identity, so the Mac pairs with
the exporter and never with the real device.
"""

import base64
import hashlib
import json
import logging
import os
import plistlib
import secrets
import struct
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from anyio.abc import ByteStream
from anyio.streams.buffered import BufferedByteReceiveStream
from cryptography.exceptions import InvalidSignature, InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey, X25519PublicKey
from cryptography.hazmat.primitives.ciphers.aead import ChaCha20Poly1305
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

logger = logging.getLogger(__name__)

PACKET_MAGIC = b"RPPairing"
MAX_PACKET = 0xFFFF
WIRE_PROTOCOL_VERSION = 26
MINIMUM_WIRE_PROTOCOL_VERSION = 8
SETUP_USER = b"Pair-Setup"
SETUP_PIN = b"000000"


class Tlv:
    METHOD = 0x00
    IDENTIFIER = 0x01
    SALT = 0x02
    PUBLIC_KEY = 0x03
    PROOF = 0x04
    ENCRYPTED_DATA = 0x05
    STATE = 0x06
    ERROR = 0x07
    SIGNATURE = 0x0A
    INFO = 0x11


TLV_ERROR_AUTHENTICATION = b"\x02"


class RemotePairingError(Exception):
    pass


def tlv8_encode(items: list[tuple[int, bytes]]) -> bytes:
    out = bytearray()
    for kind, value in items:
        if not value:
            out += bytes((kind, 0))
        for offset in range(0, len(value), 255):
            chunk = value[offset : offset + 255]
            out += bytes((kind, len(chunk))) + chunk
    return bytes(out)


def tlv8_decode(data: bytes) -> dict[int, bytes]:
    """Decode TLV8, joining consecutive fragments of one item."""
    result: dict[int, bytes] = {}
    last = None
    offset = 0
    while offset < len(data):
        if offset + 2 > len(data):
            raise RemotePairingError("truncated TLV8")
        kind, length = data[offset], data[offset + 1]
        value = data[offset + 2 : offset + 2 + length]
        if len(value) != length:
            raise RemotePairingError("truncated TLV8")
        result[kind] = result[kind] + value if kind == last and kind in result else value
        last = kind
        offset += 2 + length
    return result


def opack_encode(value: Any) -> bytes:  # noqa: C901
    """Encode the OPACK subset used by pairing info: dicts, strings, bytes, ints, bools."""
    if value is True:
        return b"\x01"
    if value is False:
        return b"\x02"
    if value is None:
        return b"\x04"
    if isinstance(value, int):
        if 0 <= value <= 0x27:
            return bytes((0x08 + value,))
        if -(2**31) <= value < 2**31:
            return b"\x32" + struct.pack("<i", value)
        return b"\x33" + struct.pack("<q", value)
    if isinstance(value, str):
        encoded = value.encode()
        if len(encoded) <= 0x20:
            return bytes((0x40 + len(encoded),)) + encoded
        if len(encoded) <= 0xFF:
            return b"\x61" + bytes((len(encoded),)) + encoded
        return b"\x62" + struct.pack("<H", len(encoded)) + encoded
    if isinstance(value, bytes):
        if len(value) <= 0x20:
            return bytes((0x70 + len(value),)) + value
        if len(value) <= 0xFF:
            return b"\x91" + bytes((len(value),)) + value
        return b"\x92" + struct.pack("<H", len(value)) + value
    if isinstance(value, dict):
        if len(value) >= 0x0F:
            raise ValueError("OPACK dictionaries of 15 or more entries are not supported")
        body = b"".join(opack_encode(key) + opack_encode(item) for key, item in value.items())
        return bytes((0xE0 + len(value),)) + body
    raise TypeError(f"cannot OPACK-encode {type(value).__name__}")


def opack_decode(data: bytes) -> Any:
    value, offset = _opack_decode(data, 0)
    if offset != len(data):
        raise RemotePairingError("trailing OPACK data")
    return value


def _opack_decode(data: bytes, offset: int) -> tuple[Any, int]:  # noqa: C901
    if offset >= len(data):
        raise RemotePairingError("truncated OPACK data")
    tag = data[offset]
    offset += 1
    if tag == 0x01:
        return True, offset
    if tag == 0x02:
        return False, offset
    if tag == 0x04:
        return None, offset
    if 0x08 <= tag <= 0x2F:
        return tag - 0x08, offset
    sizes = {0x30: "<b", 0x31: "<h", 0x32: "<i", 0x33: "<q"}
    if tag in sizes:
        width = struct.calcsize(sizes[tag])
        return struct.unpack_from(sizes[tag], data, offset)[0], offset + width
    if 0x40 <= tag <= 0x60 or 0x70 <= tag <= 0x90:
        length = tag - (0x40 if tag <= 0x60 else 0x70)
    elif tag in (0x61, 0x62, 0x63, 0x64, 0x91, 0x92, 0x93, 0x94):
        width = 1 << ((tag & 0x0F) - 1)
        length = int.from_bytes(data[offset : offset + width], "little")
        offset += width
    elif 0xE0 <= tag <= 0xEF:
        count = tag - 0xE0
        if count == 0x0F:
            raise RemotePairingError("terminated OPACK dictionaries are not supported")
        result = {}
        for _ in range(count):
            key, offset = _opack_decode(data, offset)
            item, offset = _opack_decode(data, offset)
            result[key] = item
        return result, offset
    else:
        raise RemotePairingError(f"unsupported OPACK tag {tag:#x}")
    raw = data[offset : offset + length]
    if len(raw) != length:
        raise RemotePairingError("truncated OPACK data")
    return (raw.decode() if tag < 0x70 else raw), offset + length


def _rotl(value: int, bits: int) -> int:
    return ((value << bits) | (value >> (64 - bits))) & 0xFFFFFFFFFFFFFFFF


def siphash24(key: bytes, message: bytes) -> int:
    k0, k1 = struct.unpack("<QQ", key)
    v = [k0 ^ 0x736F6D6570736575, k1 ^ 0x646F72616E646F6D, k0 ^ 0x6C7967656E657261, k1 ^ 0x7465646279746573]

    def rounds(count):
        for _ in range(count):
            v[0] = (v[0] + v[1]) & 0xFFFFFFFFFFFFFFFF
            v[1] = _rotl(v[1], 13) ^ v[0]
            v[0] = _rotl(v[0], 32)
            v[2] = (v[2] + v[3]) & 0xFFFFFFFFFFFFFFFF
            v[3] = _rotl(v[3], 16) ^ v[2]
            v[0] = (v[0] + v[3]) & 0xFFFFFFFFFFFFFFFF
            v[3] = _rotl(v[3], 21) ^ v[0]
            v[2] = (v[2] + v[1]) & 0xFFFFFFFFFFFFFFFF
            v[1] = _rotl(v[1], 17) ^ v[2]
            v[2] = _rotl(v[2], 32)

    tail = len(message) % 8
    for (word,) in struct.iter_unpack("<Q", message[: len(message) - tail]):
        v[3] ^= word
        rounds(2)
        v[0] ^= word
    last = (len(message) & 0xFF) << 56 | int.from_bytes(message[len(message) - tail :], "little")
    v[3] ^= last
    rounds(2)
    v[0] ^= last
    v[2] ^= 0xFF
    rounds(4)
    return v[0] ^ v[1] ^ v[2] ^ v[3]


def auth_tag(alt_irk: bytes, identifier: str) -> str:
    """The Bonjour ``authTag`` that lets a paired Mac recognize this identity."""
    output = struct.pack("<Q", siphash24(alt_irk, identifier.encode()))
    return base64.b64encode(bytes(reversed(output[:6]))).decode()


# RFC 5054 3072-bit group, as used by HomeKit-style pair-setup.
SRP_PRIME = int(
    "FFFFFFFFFFFFFFFFC90FDAA22168C234C4C6628B80DC1CD129024E088A67CC74020BBEA63B139B22514A08798E3404DD"
    "EF9519B3CD3A431B302B0A6DF25F14374FE1356D6D51C245E485B576625E7EC6F44C42E9A637ED6B0BFF5CB6F406B7ED"
    "EE386BFB5A899FA5AE9F24117C4B1FE649286651ECE45B3DC2007CB8A163BF0598DA48361C55D39A69163FA8FD24CF5F"
    "83655D23DCA3AD961C62F356208552BB9ED529077096966D670C354E4ABC9804F1746C08CA18217C32905E462E36CE3B"
    "E39E772C180E86039B2783A2EC07A28FB5C55DF06F4C52C9DE2BCBF6955817183995497CEA956AE515D2261898FA0510"
    "15728E5A8AAAC42DAD33170D04507A33A85521ABDF1CBA64ECFB850458DBEF0A8AEA71575D060C7DB3970F85A6E1E4C7"
    "ABF5AE8CDB0933D71E8C94E04A25619DCEE3D2261AD2EE6BF12FFA06D98A0864D87602733EC86A64521F2B18177B200C"
    "BBE117577A615D6C770988C0BAD946E208E24FA074E5AB3143DB5BFCE0FD108E4B82D120A93AD2CAFFFFFFFFFFFFFFFF",
    16,
)
SRP_GENERATOR = 5
_SRP_LENGTH = (SRP_PRIME.bit_length() + 7) // 8


def _int_bytes(value: int) -> bytes:
    return value.to_bytes(max(1, (value.bit_length() + 7) // 8), "big")


def _srp_hash(*parts: bytes | int) -> bytes:
    digest = hashlib.sha512()
    for part in parts:
        digest.update(_int_bytes(part) if isinstance(part, int) else part)
    return digest.digest()


def _pad(value: int) -> bytes:
    return value.to_bytes(_SRP_LENGTH, "big")


_SRP_K = int.from_bytes(_srp_hash(_int_bytes(SRP_PRIME), _pad(SRP_GENERATOR)), "big")
_SRP_GROUP = int.from_bytes(_srp_hash(SRP_PRIME), "big") ^ int.from_bytes(_srp_hash(SRP_GENERATOR), "big")


def _srp_x(salt: bytes, user: bytes, password: bytes) -> int:
    return int.from_bytes(_srp_hash(salt, hashlib.sha512(user + b":" + password).digest()), "big")


def _srp_keypair(make_public: Callable[[int], int]) -> tuple[int, int]:
    # A full-length public value keeps its padded and minimal encodings equal.
    while True:
        private = secrets.randbits(256)
        public = make_public(private)
        if public.bit_length() > (_SRP_LENGTH - 1) * 8:
            return private, public


def _srp_proof(user: bytes, salt: bytes, a: int, b: int, key: bytes) -> bytes:
    return _srp_hash(_SRP_GROUP, _srp_hash(user), salt, a, b, key)


class SrpServer:
    """SRP-6a verifier side for the fixed ``Pair-Setup``/``000000`` credential.

    Hashing follows the conventions iOS uses for pair-setup: SHA-512, padded
    ``k`` and ``u``, and minimal big-endian integers everywhere else.
    """

    def __init__(self, user: bytes = SETUP_USER, password: bytes = SETUP_PIN):
        self.user = user
        # A leading zero would change the salt's minimal integer encoding.
        self.salt = b"\x01" + secrets.token_bytes(15)
        self._verifier = pow(SRP_GENERATOR, _srp_x(self.salt, user, password), SRP_PRIME)
        self._private, self.public = _srp_keypair(
            lambda b: (_SRP_K * self._verifier + pow(SRP_GENERATOR, b, SRP_PRIME)) % SRP_PRIME
        )
        self.session_key: bytes | None = None

    def verify(self, client_public: bytes, client_proof: bytes) -> bytes:
        """Check the client's M1 proof and return the server's M2 proof."""
        a = int.from_bytes(client_public, "big")
        if a % SRP_PRIME == 0:
            raise RemotePairingError("invalid SRP public key")
        u = int.from_bytes(_srp_hash(_pad(a), _pad(self.public)), "big")
        shared = pow(a * pow(self._verifier, u, SRP_PRIME), self._private, SRP_PRIME)
        key = _srp_hash(shared)
        if not secrets.compare_digest(_srp_proof(self.user, self.salt, a, self.public, key), client_proof):
            raise RemotePairingError("pair-setup proof did not match")
        self.session_key = key
        return _srp_hash(a, client_proof, key)


class SrpClient:
    """SRP-6a client side, used when the device initiates pairing with a Mac."""

    def __init__(self, salt: bytes, server_public: bytes, password: bytes, user: bytes = SETUP_USER):
        b = int.from_bytes(server_public, "big")
        if b % SRP_PRIME == 0:
            raise RemotePairingError("invalid SRP public key")
        a, self.public = _srp_keypair(lambda a: pow(SRP_GENERATOR, a, SRP_PRIME))
        u = int.from_bytes(_srp_hash(_pad(self.public), _pad(b)), "big")
        x = _srp_x(salt, user, password)
        shared = pow((b - _SRP_K * pow(SRP_GENERATOR, x, SRP_PRIME)) % SRP_PRIME, a + u * x, SRP_PRIME)
        self.session_key = _srp_hash(shared)
        self.proof = _srp_proof(user, salt, self.public, b, self.session_key)
        self._expected = _srp_hash(self.public, self.proof, self.session_key)

    def verify(self, server_proof: bytes) -> None:
        if not secrets.compare_digest(self._expected, server_proof):
            raise RemotePairingError("the Mac's pair-setup proof did not match; check the pairing code")


def _hkdf(key: bytes, salt: bytes | None, info: bytes) -> bytes:
    return HKDF(algorithm=hashes.SHA512(), length=32, salt=salt, info=info).derive(key)


def _nonce(label: bytes) -> bytes:
    return b"\x00" * (12 - len(label)) + label


def _counter_nonce(counter: int) -> bytes:
    return struct.pack("<Q", counter) + b"\x00" * 4


def _raw_public(key: Ed25519PrivateKey | X25519PrivateKey) -> bytes:
    return key.public_key().public_bytes_raw()


@dataclass
class DeviceIdentity:
    """The exporter's stand-in RemotePairing identity for one leased device."""

    identifier: str
    signing_key: Ed25519PrivateKey
    alt_irk: bytes
    name: str
    model: str
    udid: str
    hosts: dict[str, bytes] = field(default_factory=dict)
    # Lockdown values by domain ("NULL" is the default domain), refreshed from
    # the real device; the Mac shows these as the device's properties.
    kvs: dict[str, dict] = field(default_factory=dict)
    # CoreDevice ignores network devices that do not report an ECID.
    ecid: int = 0

    @classmethod
    def generate(cls, *, name: str, model: str, udid: str) -> "DeviceIdentity":
        import uuid

        return cls(
            identifier=str(uuid.uuid4()).upper(),
            signing_key=Ed25519PrivateKey.generate(),
            alt_irk=os.urandom(16),
            name=name,
            model=model,
            udid=udid,
        )

    @property
    def public_key(self) -> bytes:
        return _raw_public(self.signing_key)

    @property
    def auth_tag(self) -> str:
        return auth_tag(self.alt_irk, self.identifier)

    def to_dict(self) -> dict:
        return {
            "identifier": self.identifier,
            "signing_key": self.signing_key.private_bytes_raw().hex(),
            "alt_irk": self.alt_irk.hex(),
            "name": self.name,
            "model": self.model,
            "udid": self.udid,
            "ecid": self.ecid,
            "hosts": {host: key.hex() for host, key in self.hosts.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "DeviceIdentity":
        return cls(
            identifier=data["identifier"],
            signing_key=Ed25519PrivateKey.from_private_bytes(bytes.fromhex(data["signing_key"])),
            alt_irk=bytes.fromhex(data["alt_irk"]),
            name=data["name"],
            model=data["model"],
            udid=data["udid"],
            hosts={host: bytes.fromhex(key) for host, key in data.get("hosts", {}).items()},
            ecid=data.get("ecid", 0),
        )

    def peer_info(self) -> dict:
        return {
            "identifier": self.identifier,
            "name": self.name,
            "model": self.model,
            "udid": self.udid,
            "ecid": self.ecid,
            "deviceKVSData": base64.b64encode(plistlib.dumps(self.kvs, fmt=plistlib.FMT_BINARY)).decode(),
            "deviceKVSIncludesSensitiveInfo": False,
        }

    def pairing_info(self) -> bytes:
        """The OPACK identity payload a Mac stores with its pairing record."""
        return opack_encode(
            {
                "accountID": self.identifier,
                "altIRK": self.alt_irk,
                "name": self.name,
                "model": self.model,
                "remotepairing_udid": self.udid,
                "remotepairing_ecid": self.ecid,
            }
        )


RequestHandler = Callable[[str, dict], Awaitable[dict | None]]


class _Channel:
    """``RPPairing`` framing shared by both ends of the protocol."""

    originated_by = "device"

    def __init__(self, stream: ByteStream, reader: BufferedByteReceiveStream | None = None):
        self.stream = stream
        self.reader = reader or BufferedByteReceiveStream(stream)
        self.sequence = 0

    async def _receive(self) -> dict:
        magic = await self.reader.receive_exactly(len(PACKET_MAGIC))
        if magic != PACKET_MAGIC:
            raise RemotePairingError("not a RemotePairing packet")
        (length,) = struct.unpack(">H", await self.reader.receive_exactly(2))
        message = json.loads(await self.reader.receive_exactly(length))
        if not isinstance(message, dict):
            raise RemotePairingError("RemotePairing packet is not an object")
        return message

    async def _send(self, message: dict) -> None:
        body = json.dumps(
            {"message": message, "originatedBy": self.originated_by, "sequenceNumber": self.sequence},
            separators=(",", ":"),
        ).encode()
        if len(body) > MAX_PACKET:
            raise RemotePairingError("RemotePairing packet too large")
        self.sequence += 1
        await self.stream.send(PACKET_MAGIC + struct.pack(">H", len(body)) + body)

    async def _send_plain(self, payload: dict) -> None:
        await self._send({"plain": {"_0": payload}})

    async def _receive_plain(self) -> dict:
        message = (await self._receive()).get("message", {})
        if "plain" not in message:
            raise RemotePairingError(f"expected a plain RemotePairing message, got {sorted(message)}")
        return message["plain"].get("_0", {})

    async def _send_pairing_data(self, data: bytes, kind: str, *, new_session: bool = False) -> None:
        await self._send_plain(
            {
                "event": {
                    "_0": {
                        "pairingData": {
                            "_0": {
                                "data": base64.b64encode(data).decode(),
                                "kind": kind,
                                "startNewSession": new_session,
                            }
                        }
                    }
                }
            }
        )


class ControlChannel(_Channel):
    """One host connection to the device side of the RemotePairing protocol.

    ``on_request`` receives encrypted requests other than the pairing
    bookkeeping handled here, such as ``createListener``; it returns the
    response body or ``None`` for an unsupported request. ``on_paired`` runs
    after the set of paired hosts changes, so the caller can persist it.
    """

    def __init__(
        self,
        stream: ByteStream,
        identity: DeviceIdentity,
        *,
        on_request: RequestHandler,
        on_paired: Callable[[], Awaitable[None]] | None = None,
        allow_pair_setup: bool = False,
        reader: BufferedByteReceiveStream | None = None,
    ):
        super().__init__(stream, reader)
        self.identity = identity
        self.on_request = on_request
        self.on_paired = on_paired
        self.allow_pair_setup = allow_pair_setup
        self.encryption_key: bytes | None = None
        self.host_identifier: str | None = None
        self._incoming = None
        self._outgoing = None
        self._incoming_counter = 0
        self._outgoing_counter = 0
        self._setup: SrpServer | None = None
        self._verify: tuple[X25519PrivateKey, bytes, bytes] | None = None
        self._request_id = 0

    async def serve(self) -> None:
        while True:
            packet = await self._receive()
            message = packet.get("message", {})
            # Responses name the request they answer by its sequence number.
            self._request_id = packet.get("sequenceNumber", 0)
            if "plain" in message:
                await self._plain(message["plain"].get("_0", {}))
            elif "streamEncrypted" in message:
                await self._encrypted(base64.b64decode(message["streamEncrypted"]["_0"]))
            else:
                logger.warning("ignoring RemotePairing message %s", sorted(message))

    async def _plain(self, payload: dict) -> None:
        if "request" in payload:
            request = payload["request"].get("_0", {})
            if "handshake" in request:
                host = request["handshake"].get("_0", {})
                logger.debug("RemotePairing handshake from host: %s", host)
                await self._send_plain(
                    {
                        "response": {
                            "_1": {"handshake": {"_0": self._handshake()}},
                            "forRequestIdentifier": self._request_id,
                        }
                    }
                )
                return
            logger.warning("unsupported plain RemotePairing request %s", sorted(request))
            return
        event = payload.get("event", {}).get("_0", {})
        if "pairingData" in event:
            pairing = event["pairingData"].get("_0", {})
            data = tlv8_decode(base64.b64decode(pairing.get("data", "")))
            kind = pairing.get("kind")
            if kind == "verifyManualPairing":
                await self._pair_verify(data)
            elif kind == "setupManualPairing":
                await self._pair_setup(data, pairing)
            else:
                logger.warning("unsupported pairing kind %r", kind)
            return
        if "pairVerifyFailed" in event:
            logger.info("host reported that pair-verify failed")
            self._verify = None
            return
        logger.warning("unsupported RemotePairing event %s", sorted(event))

    def _handshake(self) -> dict:
        return {
            "wireProtocolVersion": WIRE_PROTOCOL_VERSION,
            "minimumSupportedWireProtocolVersion": MINIMUM_WIRE_PROTOCOL_VERSION,
            "deviceOptions": {
                "allowsIncomingTunnelConnections": True,
                "allowsPromptlessAutomationPairingUpgrade": False,
                "allowsSharingSensitiveInfo": True,
                "allowsPinlessPairing": self.allow_pair_setup,
                "allowsFreePairing": False,
                "allowsPairSetup": self.allow_pair_setup,
            },
        }

    async def _pair_verify(self, data: dict[int, bytes]) -> None:
        state = data.get(Tlv.STATE)
        if state == b"\x01":
            host_public = data.get(Tlv.PUBLIC_KEY, b"")
            ephemeral = X25519PrivateKey.generate()
            shared = ephemeral.exchange(X25519PublicKey.from_public_bytes(host_public))
            self._verify = (ephemeral, host_public, shared)
            device_public = _raw_public(ephemeral)
            identifier = self.identity.identifier.encode()
            signature = self.identity.signing_key.sign(device_public + identifier + host_public)
            sealed = ChaCha20Poly1305(_hkdf(shared, b"Pair-Verify-Encrypt-Salt", b"Pair-Verify-Encrypt-Info")).encrypt(
                _nonce(b"PV-Msg02"),
                tlv8_encode([(Tlv.IDENTIFIER, identifier), (Tlv.SIGNATURE, signature)]),
                None,
            )
            await self._send_pairing_data(
                tlv8_encode([(Tlv.STATE, b"\x02"), (Tlv.PUBLIC_KEY, device_public), (Tlv.ENCRYPTED_DATA, sealed)]),
                "verifyManualPairing",
            )
        elif state == b"\x03" and self._verify is not None:
            ephemeral, host_public, shared = self._verify
            self._verify = None
            try:
                plain = ChaCha20Poly1305(
                    _hkdf(shared, b"Pair-Verify-Encrypt-Salt", b"Pair-Verify-Encrypt-Info")
                ).decrypt(_nonce(b"PV-Msg03"), data.get(Tlv.ENCRYPTED_DATA, b""), None)
                fields = tlv8_decode(plain)
                host = fields[Tlv.IDENTIFIER].decode()
                key = self.identity.hosts.get(host)
                if key is None:
                    raise RemotePairingError(f"host {host} is not paired with this device")
                Ed25519PublicKey.from_public_bytes(key).verify(
                    fields[Tlv.SIGNATURE], host_public + fields[Tlv.IDENTIFIER] + _raw_public(ephemeral)
                )
            except (InvalidTag, InvalidSignature, KeyError, UnicodeDecodeError, RemotePairingError) as error:
                logger.info("pair-verify rejected: %s", error)
                await self._send_pairing_data(
                    tlv8_encode([(Tlv.STATE, b"\x04"), (Tlv.ERROR, TLV_ERROR_AUTHENTICATION)]), "verifyManualPairing"
                )
                return
            self.host_identifier = host
            self._start_encryption(shared)
            logger.info("pair-verify succeeded for host %s", host)
            await self._send_pairing_data(tlv8_encode([(Tlv.STATE, b"\x04")]), "verifyManualPairing")
        else:
            raise RemotePairingError(f"unexpected pair-verify state {state!r}")

    async def _pair_setup(self, data: dict[int, bytes], pairing: dict) -> None:
        state = data.get(Tlv.STATE)
        if not self.allow_pair_setup:
            await self._send_pairing_data(
                tlv8_encode([(Tlv.STATE, b"\x02"), (Tlv.ERROR, TLV_ERROR_AUTHENTICATION)]), "setupManualPairing"
            )
            return
        if state == b"\x01":
            logger.info("pair-setup requested by %s", pairing.get("sendingHost"))
            self._setup = SrpServer()
            await self._send_plain({"event": {"_0": {"awaitingUserConsent": {}}}})
            await self._send_pairing_data(
                tlv8_encode(
                    [
                        (Tlv.STATE, b"\x02"),
                        (Tlv.SALT, self._setup.salt),
                        (Tlv.PUBLIC_KEY, _int_bytes(self._setup.public)),
                    ]
                ),
                "setupManualPairing",
            )
        elif state == b"\x03" and self._setup is not None:
            try:
                proof = self._setup.verify(data[Tlv.PUBLIC_KEY], data[Tlv.PROOF])
            except (KeyError, RemotePairingError) as error:
                logger.info("pair-setup proof rejected: %s", error)
                self._setup = None
                await self._send_pairing_data(
                    tlv8_encode([(Tlv.STATE, b"\x04"), (Tlv.ERROR, TLV_ERROR_AUTHENTICATION)]), "setupManualPairing"
                )
                return
            await self._send_pairing_data(tlv8_encode([(Tlv.STATE, b"\x04"), (Tlv.PROOF, proof)]), "setupManualPairing")
        elif state == b"\x05" and self._setup is not None and self._setup.session_key is not None:
            await self._pair_setup_exchange(data, self._setup.session_key)
            self._setup = None
        else:
            raise RemotePairingError(f"unexpected pair-setup state {state!r}")

    async def _pair_setup_exchange(self, data: dict[int, bytes], key: bytes) -> None:
        cipher = ChaCha20Poly1305(_hkdf(key, b"Pair-Setup-Encrypt-Salt", b"Pair-Setup-Encrypt-Info"))
        try:
            fields = tlv8_decode(cipher.decrypt(_nonce(b"PS-Msg05"), data.get(Tlv.ENCRYPTED_DATA, b""), None))
            host = fields[Tlv.IDENTIFIER]
            host_key = fields[Tlv.PUBLIC_KEY]
            material = _hkdf(key, b"Pair-Setup-Controller-Sign-Salt", b"Pair-Setup-Controller-Sign-Info")
            Ed25519PublicKey.from_public_bytes(host_key).verify(fields[Tlv.SIGNATURE], material + host + host_key)
            if Tlv.INFO in fields:
                logger.debug("host pairing info: %s", opack_decode(fields[Tlv.INFO]))
        except (InvalidTag, InvalidSignature, KeyError, ValueError, RemotePairingError) as error:
            logger.info("pair-setup exchange rejected: %s", error)
            await self._send_pairing_data(
                tlv8_encode([(Tlv.STATE, b"\x06"), (Tlv.ERROR, TLV_ERROR_AUTHENTICATION)]), "setupManualPairing"
            )
            return
        identity = self.identity
        identifier = identity.identifier.encode()
        material = _hkdf(key, b"Pair-Setup-Accessory-Sign-Salt", b"Pair-Setup-Accessory-Sign-Info")
        sealed = cipher.encrypt(
            _nonce(b"PS-Msg06"),
            tlv8_encode(
                [
                    (Tlv.IDENTIFIER, identifier),
                    (Tlv.PUBLIC_KEY, identity.public_key),
                    (Tlv.SIGNATURE, identity.signing_key.sign(material + identifier + identity.public_key)),
                    (Tlv.INFO, identity.pairing_info()),
                ]
            ),
            None,
        )
        identity.hosts[host.decode()] = host_key
        self.host_identifier = host.decode()
        self._start_encryption(key)
        logger.info("pair-setup completed for host %s", self.host_identifier)
        if self.on_paired is not None:
            await self.on_paired()
        await self._send_pairing_data(
            tlv8_encode([(Tlv.STATE, b"\x06"), (Tlv.ENCRYPTED_DATA, sealed)]), "setupManualPairing"
        )

    def _start_encryption(self, key: bytes) -> None:
        self.encryption_key = key
        self._incoming = ChaCha20Poly1305(_hkdf(key, None, b"ClientEncrypt-main"))
        self._outgoing = ChaCha20Poly1305(_hkdf(key, None, b"ServerEncrypt-main"))
        self._incoming_counter = self._outgoing_counter = 0

    async def _send_encrypted(self, payload: dict) -> None:
        assert self._outgoing is not None
        sealed = self._outgoing.encrypt(_counter_nonce(self._outgoing_counter), json.dumps(payload).encode(), None)
        self._outgoing_counter += 1
        await self._send({"streamEncrypted": {"_0": base64.b64encode(sealed).decode()}})

    async def _encrypted(self, sealed: bytes) -> None:
        if self._incoming is None:
            raise RemotePairingError("encrypted message before pairing")
        plain = json.loads(self._incoming.decrypt(_counter_nonce(self._incoming_counter), sealed, None))
        self._incoming_counter += 1
        if "request" not in plain:
            logger.info("encrypted RemotePairing event: %s", plain)
            return
        request = plain["request"].get("_0", {})
        for name, body in request.items():
            response = await self._handle(name, body if isinstance(body, dict) else {})
            if response is None:
                logger.warning("unsupported RemotePairing request %r: %s", name, body)
                response = {
                    "errorExtended": {
                        "_0": {
                            "domain": "com.apple.dt.remotepairing",
                            "code": 1,
                            "userInfo": {"NSLocalizedDescription": f"{name} is not supported by this device"},
                        }
                    }
                }
            else:
                response = {name: response}
            await self._send_encrypted({"response": {"_1": response, "forRequestIdentifier": self._request_id}})

    async def _handle(self, name: str, body: dict) -> dict | None:
        logger.info("RemotePairing request %s", name)
        if name == "createRemoteUnlockKey":
            return {"hostKey": ""}
        if name == "peerInfo":
            return {"_0": self.identity.peer_info()}
        if name == "ping":
            return {}
        if name == "unpair":
            if self.host_identifier is not None:
                self.identity.hosts.pop(self.host_identifier, None)
                if self.on_paired is not None:
                    await self.on_paired()
            return {}
        return await self.on_request(name, body)


class HostPairing(_Channel):
    """Device-initiated pair-setup with a Mac that offers "Pair Nearby Device".

    The Mac listens as ``_remotepairing-pairable-host._tcp`` and shows a
    six-digit code; the device drives SRP pair-setup as the controller, so its
    messages are ``originatedBy: "host"``. ``code`` is awaited only after the
    Mac has accepted the request and shown its code.
    """

    originated_by = "host"

    def __init__(
        self,
        stream: ByteStream,
        identity: DeviceIdentity,
        code: Callable[[], Awaitable[str]],
        reader: BufferedByteReceiveStream | None = None,
    ):
        super().__init__(stream, reader)
        self.identity = identity
        self.code = code

    async def _pairing_data(self) -> dict[int, bytes]:
        while True:
            event = (await self._receive_plain()).get("event", {}).get("_0", {})
            if "pairingData" in event:
                data = tlv8_decode(base64.b64decode(event["pairingData"]["_0"]["data"]))
                if Tlv.ERROR in data:
                    raise RemotePairingError(f"the Mac rejected pairing (error {data[Tlv.ERROR].hex()})")
                return data
            if "pairingRejectedWithError" in event:
                error = event["pairingRejectedWithError"].get("wrappedError", {}).get("userInfo", {})
                raise RemotePairingError(error.get("NSLocalizedDescription", "the Mac rejected pairing"))
            logger.debug("pairing event while waiting for data: %s", sorted(event))

    async def pair(self) -> str:
        """Pair with the Mac and return its host identifier."""
        await self._send_plain(
            {
                "request": {
                    "_0": {
                        "handshake": {
                            "_0": {
                                "hostOptions": {"attemptPairVerify": False},
                                "wireProtocolVersion": WIRE_PROTOCOL_VERSION,
                            }
                        }
                    }
                }
            }
        )
        handshake = (await self._receive_plain()).get("response", {}).get("_1", {}).get("handshake", {}).get("_0")
        if not isinstance(handshake, dict):
            raise RemotePairingError("the Mac did not answer the RemotePairing handshake")
        logger.debug("pairable host handshake: %s", handshake)

        await self._send_pairing_data(
            tlv8_encode([(Tlv.METHOD, b"\x00"), (Tlv.STATE, b"\x01")]), "setupManualPairing", new_session=True
        )
        m2 = await self._pairing_data()
        srp = SrpClient(m2[Tlv.SALT], m2[Tlv.PUBLIC_KEY], (await self.code()).strip().encode())
        await self._send_pairing_data(
            tlv8_encode([(Tlv.STATE, b"\x03"), (Tlv.PUBLIC_KEY, _pad(srp.public)), (Tlv.PROOF, srp.proof)]),
            "setupManualPairing",
        )
        srp.verify((await self._pairing_data())[Tlv.PROOF])

        key = srp.session_key
        cipher = ChaCha20Poly1305(_hkdf(key, b"Pair-Setup-Encrypt-Salt", b"Pair-Setup-Encrypt-Info"))
        identity = self.identity
        identifier = identity.identifier.encode()
        material = _hkdf(key, b"Pair-Setup-Controller-Sign-Salt", b"Pair-Setup-Controller-Sign-Info")
        sealed = cipher.encrypt(
            _nonce(b"PS-Msg05"),
            tlv8_encode(
                [
                    (Tlv.IDENTIFIER, identifier),
                    (Tlv.PUBLIC_KEY, identity.public_key),
                    (Tlv.SIGNATURE, identity.signing_key.sign(material + identifier + identity.public_key)),
                    (Tlv.INFO, identity.pairing_info()),
                ]
            ),
            None,
        )
        await self._send_pairing_data(
            tlv8_encode([(Tlv.STATE, b"\x05"), (Tlv.ENCRYPTED_DATA, sealed)]), "setupManualPairing"
        )
        m6 = await self._pairing_data()
        try:
            fields = tlv8_decode(cipher.decrypt(_nonce(b"PS-Msg06"), m6[Tlv.ENCRYPTED_DATA], None))
            host, host_key = fields[Tlv.IDENTIFIER], fields[Tlv.PUBLIC_KEY]
            material = _hkdf(key, b"Pair-Setup-Accessory-Sign-Salt", b"Pair-Setup-Accessory-Sign-Info")
            Ed25519PublicKey.from_public_bytes(host_key).verify(fields[Tlv.SIGNATURE], material + host + host_key)
        except (InvalidTag, InvalidSignature, KeyError, ValueError) as error:
            raise RemotePairingError(f"the Mac's pairing identity is invalid: {error!r}") from error
        if Tlv.INFO in fields:
            logger.debug("Mac pairing info: %s", opack_decode(fields[Tlv.INFO]))
        identity.hosts[host.decode()] = host_key
        return host.decode()
