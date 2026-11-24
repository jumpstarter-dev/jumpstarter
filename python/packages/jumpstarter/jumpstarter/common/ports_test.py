import pytest
from jumpstarter_protocol.jumpstarter.v1 import common_pb2
from pydantic import ValidationError

from .ports import Port, PortDirection, coerce_ports, duplicate_port_names, parse_port_address


def test_port_report_has_the_properties_and_no_local_address():
    port = Port(name="controller", direction="requires", listen="127.0.0.1:7300", protocol="hci-h4")
    report = port.to_report()

    assert report == common_pb2.PortReport(
        name="controller",
        direction=common_pb2.PORT_DIRECTION_REQUIRES,
        protocol="hci-h4",
        attachment=common_pb2.PORT_ATTACHMENT_SOCKET,
        transport=common_pb2.PORT_TRANSPORT_STREAM,
    )
    assert b"7300" not in report.SerializeToString()


def test_port_report_without_a_protocol_leaves_the_field_unset():
    report = Port(name="rootcanal", direction="provides").to_report()
    assert not report.HasField("protocol")


def test_port_report_of_a_port_that_a_forward_cannot_use():
    port = Port(name="someip-sd", direction="connects", transport="datagram")
    assert not port.forwardable
    assert port.to_report().transport == common_pb2.PORT_TRANSPORT_DATAGRAM

    port = Port(name="can0", direction="provides", attachment="wired")
    assert not port.forwardable
    assert port.to_report().attachment == common_pb2.PORT_ATTACHMENT_WIRED


@pytest.mark.parametrize("name", ["rootcanal", "rootcanal-link", "a", "can0", "a" * 63])
def test_port_name_is_a_dns_label(name):
    assert Port(name=name, direction="provides").name == name


@pytest.mark.parametrize("name", ["", "Rootcanal", "root_canal", "-uart", "uart-", "a.b", "a" * 64, "uart "])
def test_port_name_that_is_not_a_dns_label_is_rejected(name):
    with pytest.raises(ValidationError, match="DNS label"):
        Port(name=name, direction="provides")


def test_port_must_have_a_direction():
    with pytest.raises(ValidationError):
        Port.model_validate({"name": "uart"})
    with pytest.raises(ValidationError):
        Port.model_validate({"name": "uart", "direction": "tx"})


def test_port_rejects_an_unknown_key():
    with pytest.raises(ValidationError):
        Port.model_validate({"name": "uart", "direction": "provides", "address": "127.0.0.1:1"})


def test_requires_port_must_have_listen():
    with pytest.raises(ValidationError, match="must have 'listen'"):
        Port(name="controller", direction="requires")
    with pytest.raises(ValidationError, match="not 'connect'"):
        Port(name="controller", direction="requires", listen="127.0.0.1:1", connect="127.0.0.1:2")


def test_requires_port_that_a_forward_cannot_use_needs_no_address():
    assert Port(name="antenna", direction="requires", attachment="wireless").listen is None


def test_provides_port_has_no_listen():
    with pytest.raises(ValidationError, match="not 'listen'"):
        Port(name="rootcanal", direction="provides", listen="127.0.0.1:7300")


def test_connects_port_has_one_local_mode():
    assert Port(name="uart", direction="connects", connect="127.0.0.1:4001").connect_address == ("127.0.0.1", 4001)
    assert Port(name="uart", direction="connects", listen="127.0.0.1:4001").listen_address == ("127.0.0.1", 4001)
    with pytest.raises(ValidationError, match="not the two"):
        Port(name="uart", direction="connects", listen="127.0.0.1:4001", connect="127.0.0.1:4001")


@pytest.mark.parametrize(
    ("address", "expected"),
    [
        ("127.0.0.1:7300", ("127.0.0.1", 7300)),
        ("localhost:1", ("localhost", 1)),
        ("[::1]:7300", ("::1", 7300)),
    ],
)
def test_parse_port_address(address, expected):
    assert parse_port_address(address) == expected


@pytest.mark.parametrize("address", ["7300", "127.0.0.1", "127.0.0.1:", ":7300", "127.0.0.1:0", "h:70000", "h:x"])
def test_invalid_port_address_is_rejected(address):
    with pytest.raises(ValidationError, match="host:port"):
        Port(name="uart", direction="connects", connect=address)


def test_direction_proto_round_trip():
    for direction in PortDirection:
        assert PortDirection.from_proto(direction.to_proto()) is direction
    with pytest.raises(ValueError):
        PortDirection.from_proto(common_pb2.PORT_DIRECTION_UNSPECIFIED)


def test_coerce_ports_accepts_mappings_and_ports():
    port = Port(name="a", direction="provides")
    assert coerce_ports([port, {"name": "b", "direction": "provides"}]) == [port, Port(name="b", direction="provides")]
    assert coerce_ports(None) == []


def test_duplicate_port_names():
    ports = [Port(name=n, direction="provides") for n in ("a", "b", "a", "a", "b", "c")]
    assert duplicate_port_names(ports) == ["a", "b"]


def test_protocol_tag_length():
    assert Port(name="a", direction="provides", protocol="p" * 63).protocol == "p" * 63
    with pytest.raises(ValidationError, match="protocol tag"):
        Port(name="a", direction="provides", protocol="p" * 64)
