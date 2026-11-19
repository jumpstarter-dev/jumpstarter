"""The scrcpy protocol: control messages, the packets of the video socket and the server's device messages."""

from __future__ import annotations

import socket
import struct

import pytest

from . import protocol
from .fakes import DELTA, IDR, hevc_stream, media_packet, parameter_sets, session_packet


def test_control_message_layouts():
    assert protocol.inject_keycode(protocol.ACTION_DOWN, 3) == bytes([0, 0]) + struct.pack(">iii", 3, 0, 0)
    assert protocol.inject_keycode(protocol.ACTION_DOWN, 3, repeat=1) == bytes([0, 0]) + struct.pack(">iii", 3, 1, 0)
    assert protocol.inject_text("hé") == b"\x01\x00\x00\x00\x03h\xc3\xa9"
    shifted = protocol.inject_keycode(protocol.ACTION_UP, 29, 2, protocol.META_SHIFT_ON | protocol.META_SHIFT_LEFT_ON)
    assert shifted == bytes([0, 1]) + struct.pack(">iii", 29, 2, 0x41) and len(shifted) == 14  # the meta state is last
    touch = protocol.inject_touch(protocol.ACTION_DOWN, 1, 100, 200, 540, 1200, 1.0)
    assert len(touch) == 32 and touch[0] == 2 and touch[1] == 0
    assert struct.unpack_from(">q", touch, 2)[0] == 1
    assert struct.unpack_from(">iiHHH", touch, 10) == (100, 200, 540, 1200, 0xFFFF)
    assert struct.unpack_from(">H", protocol.inject_touch(protocol.ACTION_UP, 0, 1, 1, 2, 2, 0.0), 22)[0] == 0
    assert protocol.simple(protocol.TYPE_RESET_VIDEO) == b"\x11"
    assert protocol.simple(protocol.TYPE_ROTATE_DEVICE) == b"\x0b"
    assert protocol.inject_text("x" * 400)[1:5] == struct.pack(">I", 300)  # one message carries at most 300 characters


@pytest.mark.parametrize(
    ("name", "code"),
    [
        ("HOME", 3),
        ("back", 4),
        ("KEYCODE_POWER", 26),
        ("recents", 187),
        ("APP_SWITCH", 187),
        ("volume-up", 24),
        ("A", 29),
        ("0", 7),
        (66, 66),
        (3.0, 3),  # a generic call carries every number as a float
        ("187", 187),
    ],
)
def test_keycodes(name, code):
    assert protocol.keycode_of(name) == code


@pytest.mark.parametrize("bad", ["nope", "", True, 0, 5000])
def test_unknown_keys_are_refused(bad):
    with pytest.raises(ValueError):
        protocol.keycode_of(bad)


def _pair():
    a, b = socket.socketpair()
    a.settimeout(2)
    b.settimeout(2)
    return a, b


def test_read_video_packets():
    server, client = _pair()
    sps_pps = parameter_sets(540, 1200)
    server.sendall(
        session_packet(540, 1200)
        + media_packet(0, sps_pps, config=True)
        + media_packet(1000, IDR, key=True)
        + media_packet(34000, DELTA)
    )
    assert protocol.read_packet(client) == protocol.Session(540, 1200)
    assert protocol.read_packet(client) == protocol.Config(sps_pps)
    assert protocol.read_packet(client) == protocol.Media(1000, True, IDR)
    assert protocol.read_packet(client) == protocol.Media(34000, False, DELTA)


def test_the_video_stream_of_h265_has_the_parameter_sets_in_its_config_packet_and_key_pictures_without():
    server, client = _pair()
    sets, pictures = hevc_stream()
    (key, is_key), (delta, _) = pictures[0], pictures[1]
    assert is_key
    server.sendall(
        session_packet(240, 400) + media_packet(0, sets, config=True) + media_packet(1000, key, key=True)
        + media_packet(67000, delta)
    )  # fmt: skip
    assert protocol.read_packet(client) == protocol.Session(240, 400)
    assert protocol.read_packet(client) == protocol.Config(sets)
    assert protocol.read_packet(client) == protocol.Media(1000, True, key)
    assert protocol.read_packet(client) == protocol.Media(67000, False, delta)


def test_client_resized_flag_and_closed_socket():
    server, client = _pair()
    server.sendall(struct.pack(">III", 0x80000001, 10, 20))
    assert protocol.read_packet(client) == protocol.Session(10, 20, client_resized=True)
    server.close()
    with pytest.raises(EOFError):
        protocol.read_packet(client)


def test_a_packet_of_an_absurd_size_is_refused():
    server, client = _pair()
    server.sendall(struct.pack(">QI", 5, 0xFFFFFFF0))
    with pytest.raises(protocol.ProtocolError):
        protocol.read_packet(client)


def test_device_name_and_codec_ids():
    server, client = _pair()
    server.sendall(b"Fake Phone".ljust(64, b"\x00") + struct.pack(">I", protocol.CODEC_H264))
    assert protocol.read_device_name(client) == "Fake Phone"
    assert protocol.read_codec(client) == protocol.CODEC_H264
    server.sendall(struct.pack(">I", 1))
    with pytest.raises(protocol.ProtocolError):
        protocol.read_codec(client)


def test_the_codec_ids_are_the_ascii_of_the_servers_names_for_them():
    assert protocol.CODEC_H264 == int.from_bytes(b"h264") == 0x68323634
    assert protocol.CODEC_H265 == int.from_bytes(b"h265") == 0x68323635
    assert protocol.CODEC_IDS == {"h264": protocol.CODEC_H264, "hevc": protocol.CODEC_H265}
    assert protocol.SERVER_CODECS == {"h264": "h264", "hevc": "h265"}  # the server's option is video_codec=h265
    assert protocol.codec_of(protocol.CODEC_H264) == "h264" and protocol.codec_of(protocol.CODEC_H265) == "hevc"
    assert protocol.codec_of(0x00617631) is None  # AV1: the server makes it and the interface has no use for it
    assert protocol.codec_of(0) is None


def test_the_codec_of_the_stream_is_read_for_either_codec():
    server, client = _pair()
    server.sendall(struct.pack(">I", protocol.CODEC_H265))
    assert protocol.read_codec(client) == protocol.CODEC_H265
    assert protocol.parse_codec(struct.pack(">I", protocol.CODEC_H265)) == protocol.CODEC_H265


def test_the_name_and_the_codec_are_read_from_their_bytes_too():
    assert protocol.parse_device_name(b"Fake Phone".ljust(64, b"\x00")) == "Fake Phone"
    assert protocol.parse_device_name(b"\xff" + b"\x00" * 63) == "\ufffd"
    assert protocol.parse_codec(struct.pack(">I", protocol.CODEC_H264)) == protocol.CODEC_H264
    with pytest.raises(protocol.ProtocolError):
        protocol.parse_codec(struct.pack(">I", 0))


def test_start_app_is_a_type_a_length_and_the_name():
    assert protocol.start_app("com.android.settings") == b"\x10\x14com.android.settings"
    assert protocol.start_app("?Settings")[:2] == b"\x10\x09"
    for bad in ("", "x" * 256, "a\nb", "a\x00"):
        with pytest.raises(ValueError):
            protocol.start_app(bad)


def test_set_clipboard_is_a_sequence_a_paste_flag_and_the_text():
    message = protocol.set_clipboard(7, "héllo", paste=True)
    assert struct.unpack_from(">BQ?I", message) == (9, 7, True, 6)  # six bytes: the accent is two
    assert message[14:] == "héllo".encode()
    assert protocol.set_clipboard(1, "")[14:] == b""
    with pytest.raises(ValueError):
        protocol.set_clipboard(1, "x" * (protocol.CLIPBOARD_MAX_BYTES + 1))


def test_get_clipboard_only_reads_unless_told_to_copy_or_cut():
    assert protocol.get_clipboard() == b"\x08\x00"
    assert protocol.get_clipboard(1) == b"\x08\x01"


def test_the_display_can_be_switched_on_and_off():
    assert protocol.set_display_power(True) == b"\x0a\x01"
    assert protocol.set_display_power(False) == b"\x0a\x00"


def test_the_servers_device_messages_are_read_in_any_chunking():
    wire = (
        struct.pack(">BI", 0, 6)
        + "héllo".encode()
        + struct.pack(">BQ", 1, 42)
        + struct.pack(">BHH", 2, 3, 2)
        + b"zz"  # a UHID output: read and left out
        + struct.pack(">BI", 0, 0)
    )
    expected = [protocol.DeviceClipboard("héllo"), protocol.DeviceAck(42), protocol.DeviceClipboard("")]
    for size in (1, 2, 3, 7, len(wire)):
        parser = protocol.DeviceMessages()
        got: list = []
        for at in range(0, len(wire), size):
            got += parser.feed(wire[at : at + size])
        assert got == expected, size


def test_a_device_message_of_a_type_the_protocol_does_not_have_is_an_error():
    with pytest.raises(protocol.ProtocolError):
        protocol.DeviceMessages().feed(b"\x63\x00")


def test_an_absurd_clipboard_message_is_an_error():
    with pytest.raises(protocol.ProtocolError):
        protocol.DeviceMessages().feed(struct.pack(">BI", 0, 0x7FFFFFFF))
