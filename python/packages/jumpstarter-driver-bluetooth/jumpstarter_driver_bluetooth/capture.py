"""What a device's Bluetooth said on its HCI, read from a btsnoop file.

btsnoop is what Android's own snoop log is written in, and what Wireshark opens: a 16-byte header, then one
record for each HCI packet. The packets are decoded with bumble's HCI parser into small dictionaries, for a
test to look for an event in. Keys are left out of those: a link key in a test report is a link key given away.
The file itself has them, as any snoop log does.
"""

from __future__ import annotations

import dataclasses
import enum
import logging
import struct
from typing import Any, NamedTuple

from bumble import hci

MAGIC = b"btsnoop\x00"
HEADER = struct.Struct(">8sII")  # magic, version, datalink
RECORD = struct.Struct(">IIIIq")  # original length, included length, flags, drops, timestamp
VERSION = 1
DATALINK_H4 = 1002  # each record's data starts with the H4 packet type
# A btsnoop timestamp is microseconds since midnight, January 1st, 0 AD; this is 1970 on that clock.
EPOCH = 0x00DCDDB30F2F8000

REDACTED = "<redacted>"
# Fields that are, or give away, a key: by name, wherever bumble's parser puts them.
SECRETS = {"key", "pin_code", "passkey", "c_192", "c_256", "r_192", "r_256"}
SECRET_ENDINGS = ("_key", "_keys", "_irk")
TYPES = {0x01: "command", 0x02: "acl", 0x03: "sco", 0x04: "event", 0x05: "iso"}


class NotBtsnoop(ValueError):
    """What was read is not a btsnoop file of HCI packets."""


class Record(NamedTuple):
    index: int  # its place in the file
    time: float  # seconds since 1970, by the device's clock
    received: bool  # from the controller to the host
    data: bytes  # one H4 packet


def parse_btsnoop(data: bytes) -> list[Record]:
    """The records of a btsnoop file. A last record that is cut short (the log is being written) is left out."""
    if len(data) < HEADER.size:
        raise NotBtsnoop("too short to be a btsnoop file")
    magic, version, datalink = HEADER.unpack_from(data)
    if magic != MAGIC or version != VERSION:
        raise NotBtsnoop("not a btsnoop file (version 1)")
    if datalink != DATALINK_H4:
        raise NotBtsnoop(f"a btsnoop file of datalink {datalink}, not of H4 packets ({DATALINK_H4})")
    records, at = [], HEADER.size
    while at + RECORD.size <= len(data):
        _original, included, flags, _drops, timestamp = RECORD.unpack_from(data, at)
        at += RECORD.size
        if at + included > len(data):
            break
        records.append(Record(len(records), (timestamp - EPOCH) / 1e6, bool(flags & 1), data[at : at + included]))
        at += included
    return records


def _secret(name: str) -> bool:
    return name in SECRETS or name.endswith(SECRET_ENDINGS)


def _plain(value: Any) -> Any:
    """A decoded field as something JSON can carry, and a person can read."""
    if isinstance(value, hci.Address):
        return value.to_string(False)
    if isinstance(value, enum.Enum):
        return value.name
    if isinstance(value, (bytes, bytearray)):
        return bytes(value).hex()
    if isinstance(value, (list, tuple)):
        return [_plain(item) for item in value]
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return _fields(value)
    if isinstance(value, (bool, int, float, str)) or value is None:
        return value
    return str(value)


def _fields(packet: Any) -> dict:
    if dataclasses.is_dataclass(packet):
        names = [field.name for field in dataclasses.fields(packet)]
    else:
        names = list(vars(packet))
    return {
        name: REDACTED if _secret(name) else _plain(getattr(packet, name))
        for name in names
        if not name.startswith("_") and hasattr(packet, name)
    }


def decode(record: Record) -> dict:
    """One record as a dictionary: where and when, what kind of packet, its name and its fields."""
    kind = TYPES.get(record.data[0], "other") if record.data else "other"
    packet = {
        "index": record.index,
        "time": round(record.time, 6),
        "direction": "controller-to-host" if record.received else "host-to-controller",
        "type": kind,
        "name": kind.upper(),
        "fields": {},
    }
    if kind in ("acl", "sco", "iso"):
        # What a device says above the link is not HCI's to explain, and may be a key (SMP): only whose it is.
        if len(record.data) >= 3:
            packet["fields"] = {"connection_handle": int.from_bytes(record.data[1:3], "little") & 0x0FFF}
        packet["fields"]["length"] = len(record.data)
        return packet
    bumble_log = logging.getLogger("bumble.hci")
    quiet, bumble_log.disabled = bumble_log.disabled, True  # (it logs what it cannot parse; that is said here)
    try:
        parsed = hci.HCI_Packet.from_bytes(record.data)
        packet["name"] = str(getattr(parsed, "name", type(parsed).__name__))
        packet["fields"] = _fields(parsed)
    except Exception as error:  # noqa: BLE001 - a packet the parser does not know is still a packet
        packet["fields"] = {"length": len(record.data), "error": str(error) or type(error).__name__}
    finally:
        bumble_log.disabled = quiet
    return packet


def short_name(name: str) -> str:
    """A packet's name as a test may say it: ``connection_complete`` for ``HCI_CONNECTION_COMPLETE_EVENT``."""
    name = name.strip().lower().replace(" ", "_").replace("-", "_")
    name = name.removeprefix("hci_")
    for ending in ("_event", "_command"):
        name = name.removesuffix(ending)
    return name


def _find(where: Any, name: str) -> list:
    """Every value a packet has under that name, however deep (a command's return parameters are fields too)."""
    found = []
    if isinstance(where, dict):
        for key, value in where.items():
            if key == name:
                found.append(value)
            found += _find(value, name)
    elif isinstance(where, list):
        for item in where:
            found += _find(item, name)
    return found


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return float(int(str(value), 0))  # ("19", "0x13")
    except ValueError:
        return None


def _same(value: Any, wanted: Any) -> bool:
    """A field is what a test asked for. A number that has crossed from a client is a float (1.0 for a
    connection handle of 1); and a status or a reason, which is a number on the wire, may be asked for by its
    name: ``SUCCESS``, ``PAGE_TIMEOUT``, ``REMOTE_USER_TERMINATED_CONNECTION``."""
    if str(value).lower() == str(wanted).lower():
        return True
    number, asked = _number(value), _number(wanted)
    if number is not None and asked is not None:
        return number == asked
    if isinstance(value, int) and not isinstance(value, bool) and isinstance(wanted, str):
        said = wanted.strip().lower().replace(" ", "_").removeprefix("hci_").removesuffix("_error")
        return hci.HCI_Constant.error_name(value).lower().removesuffix("_error") == said
    return False


def matches(packet: dict, event: str = "", match: dict | None = None) -> bool:
    if event and short_name(packet["name"]) != short_name(event):
        return False
    for name, wanted in (match or {}).items():
        if not any(_same(value, wanted) for value in _find(packet, name)):
            return False
    return True


def select(records: list[Record], event: str = "", match: dict | None = None, since: int = 0) -> list[dict]:
    """The decoded packets from ``since`` on that have that name and those fields."""
    # (a number that has crossed to a client and back is a float)
    return [packet for packet in map(decode, records[int(since) :]) if matches(packet, event, match)]
