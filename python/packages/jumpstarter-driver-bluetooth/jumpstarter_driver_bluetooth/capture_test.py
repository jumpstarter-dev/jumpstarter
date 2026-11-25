import struct

import pytest

from .capture import EPOCH, REDACTED, NotBtsnoop, decode, matches, parse_btsnoop, select, short_name

SENT, RECEIVED = 0, 1
STARTED = 1_800_000_000.0  # (the device's clock when its log starts)

RESET = bytes.fromhex("01030c00")
RESET_DONE = bytes.fromhex("040e0401030c00")
CONNECTED = bytes.fromhex("04030b00010001020304050601" + "00")  # handle 1, to 06:05:04:03:02:01
LINK_KEY = bytes.fromhex("041817" + "010203040506" + "0102030405060708090a0b0c0d0e0f10" + "04")
KEY_REPLY = bytes.fromhex("010b0416" + "010203040506" + "0102030405060708090a0b0c0d0e0f10")
PIN_REPLY = bytes.fromhex("010d0417" + "010203040506" + "04" + "31323334" + "00" * 12)
LE_ENCRYPT = bytes.fromhex("0119201c" + "0100" + "0000000000000000" + "0000" + "a1a2a3a4a5a6a7a8a9aaabacadaeafb0")
ACL = bytes.fromhex("02012005000100040041")
DISCONNECTED = bytes.fromhex("04050400010013")
VENDOR = bytes.fromhex("04ff020102")


def btsnoop(*packets: tuple[int, bytes], start: float = STARTED, datalink: int = 1002) -> bytes:
    """A snoop log as Android writes it: one record for each (direction, H4 packet), 10 ms apart."""
    data = struct.pack(">8sII", b"btsnoop\x00", 1, datalink)
    for number, (flags, packet) in enumerate(packets):
        timestamp = int((start + number * 0.01) * 1e6) + EPOCH
        data += struct.pack(">IIIIq", len(packet), len(packet), flags, 0, timestamp) + packet
    return data


CONVERSATION = btsnoop(
    (SENT, RESET), (RECEIVED, RESET_DONE), (RECEIVED, CONNECTED), (RECEIVED, LINK_KEY), (SENT, KEY_REPLY),
    (SENT, ACL), (RECEIVED, DISCONNECTED),
)  # fmt: skip


def test_a_snoop_log_is_read_record_by_record():
    records = parse_btsnoop(CONVERSATION)
    assert [record.index for record in records] == list(range(7))
    assert [record.received for record in records] == [False, True, True, True, False, False, True]
    assert records[0].data == RESET and records[-1].data == DISCONNECTED
    assert records[0].time == pytest.approx(STARTED) and records[2].time == pytest.approx(STARTED + 0.02)


def test_a_log_that_is_being_written_ends_at_its_last_whole_record():
    assert len(parse_btsnoop(CONVERSATION[:-3])) == 6
    assert parse_btsnoop(CONVERSATION[:16]) == []


def test_what_is_not_a_snoop_log_of_h4_packets_is_said_to_be_so():
    for data, why in ((b"", "too short"), (b"\x00" * 32, "not a btsnoop file"), (btsnoop(datalink=1001), "datalink")):
        with pytest.raises(NotBtsnoop, match=why):
            parse_btsnoop(data)


def test_a_packet_is_decoded_with_its_name_and_its_fields():
    reset, done, connected, *_rest, disconnected = map(decode, parse_btsnoop(CONVERSATION))
    assert (reset["type"], reset["name"], reset["direction"]) == ("command", "HCI_RESET_COMMAND", "host-to-controller")
    assert (done["type"], done["name"], done["direction"]) == (
        "event", "HCI_COMMAND_COMPLETE_EVENT", "controller-to-host",
    )  # fmt: skip
    assert connected["name"] == "HCI_CONNECTION_COMPLETE_EVENT"
    assert connected["fields"] == {
        "status": 0, "connection_handle": 1, "bd_addr": "06:05:04:03:02:01", "link_type": "ACL",
        "encryption_enabled": 0,
    }  # fmt: skip
    assert connected["index"] == 2 and connected["time"] == pytest.approx(STARTED + 0.02)
    assert disconnected["fields"] == {"status": 0, "connection_handle": 1, "reason": 0x13}


def test_keys_are_left_out_of_what_is_decoded():
    for packet, secret in ((LINK_KEY, "link_key"), (KEY_REPLY, "link_key"), (PIN_REPLY, "pin_code"),
                           (LE_ENCRYPT, "long_term_key")):  # fmt: skip
        (decoded,) = map(decode, parse_btsnoop(btsnoop((SENT, packet))))
        assert decoded["fields"][secret] == REDACTED, decoded
        assert packet[-16:].hex() not in str(decoded) and "0102030405060708" not in str(decoded)
    # What is not a key stays: whose key it is, and of what kind.
    (notified,) = map(decode, parse_btsnoop(btsnoop((RECEIVED, LINK_KEY))))
    assert notified["fields"]["bd_addr"] == "06:05:04:03:02:01"
    assert notified["fields"]["key_type"] == "UNAUTHENTICATED_COMBINATION_KEY_GENERATED_FROM_P_192"


def test_what_is_said_above_the_link_is_given_by_its_length_only():
    (acl,) = map(decode, parse_btsnoop(btsnoop((SENT, ACL))))
    assert (acl["type"], acl["name"]) == ("acl", "ACL")
    assert acl["fields"] == {"connection_handle": 1, "length": len(ACL)}


def test_a_packet_the_parser_does_not_know_is_still_a_packet():
    vendor, cut = map(decode, parse_btsnoop(btsnoop((RECEIVED, VENDOR), (RECEIVED, b"\x04"))))
    assert vendor["type"] == "event" and vendor["name"] == "HCI_VENDOR_EVENT"
    assert cut["type"] == "event" and cut["fields"]["error"]


def test_an_event_is_named_as_a_test_would_say_it():
    names = ("connection_complete", "Connection Complete", "HCI_CONNECTION_COMPLETE_EVENT", "hci_connection_complete")
    for said in names:
        assert short_name(said) == "connection_complete"
    assert short_name("HCI_RESET_COMMAND") == "reset"


def test_packets_are_found_by_name_by_field_and_by_where_they_are():
    records = parse_btsnoop(CONVERSATION)
    assert [packet["index"] for packet in select(records, "connection_complete")] == [2]
    assert [packet["index"] for packet in select(records, "disconnection complete", {"reason": 0x13})] == [6]
    assert select(records, "disconnection_complete", {"reason": 0x08}) == []
    # a field is found however deep: the status of a command is in its return parameters
    assert [packet["index"] for packet in select(records, "command_complete", {"status": "success"})] == [1]
    assert [packet["index"] for packet in select(records, match={"bd_addr": "06:05:04:03:02:01"})] == [2, 3, 4]
    assert [packet["index"] for packet in select(records, match={"connection_handle": 1}, since=3)] == [5, 6]
    assert [packet["index"] for packet in select(records, match={"type": "command"})] == [0, 4]
    assert matches(decode(records[2]), "", None) and not matches(decode(records[2]), "reset")
