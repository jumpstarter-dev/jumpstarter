import logging
from uuid import UUID

import pytest
import yarl
from anyio import EndOfStream

from .base import Driver
from jumpstarter.common.ports import Port


def test_make_url_preserves_percent_encoding():
    """_make_url must preserve %XX sequences that appear in presigned URLs.

    Without encoded=True, yarl.URL normalizes %40 to @, breaking S3/CloudFront
    signatures which are computed over the percent-encoded form.
    """
    url = "https://cdn.example.com/path%40encoded/file.bin?X-Amz-Signature=abc123"

    broken = yarl.URL(url)
    assert "%40" not in str(broken), "sanity: yarl.URL() should decode %40"

    preserved = Driver._make_url(url)
    assert str(preserved) == url


def test_make_url_preserves_multiple_encoded_sequences():
    url = "https://cdn.example.com/a%40b%2Fc%23d?token=xyz"

    preserved = Driver._make_url(url)
    assert str(preserved) == url
    assert "%40" in str(preserved)
    assert "%2F" in str(preserved)
    assert "%23" in str(preserved)


def test_strip_sensitive_headers_same_origin():
    headers = {
        "Authorization": "Bearer tok",
        "x-amz-security-token": "sess",
        "Content-Type": "application/octet-stream",
    }
    result = Driver._strip_sensitive_headers(
        headers, "https://s3.amazonaws.com/a", "https://s3.amazonaws.com/b"
    )
    assert result == headers


def test_strip_sensitive_headers_cross_origin():
    headers = {
        "Authorization": "Bearer tok",
        "x-amz-security-token": "sess",
        "x-amz-server-side-encryption-customer-key": "secret",
        "Content-Type": "application/octet-stream",
        "Host": "s3.amazonaws.com",
    }
    result = Driver._strip_sensitive_headers(headers, "https://s3.amazonaws.com/a", "https://cdn.example.com/b")
    assert "Authorization" not in result
    assert "x-amz-security-token" not in result
    assert "x-amz-server-side-encryption-customer-key" not in result
    assert result["Content-Type"] == "application/octet-stream"
    assert result["Host"] == "s3.amazonaws.com"


def test_strip_sensitive_headers_different_port():
    headers = {"Authorization": "Bearer tok", "Accept": "*/*"}
    result = Driver._strip_sensitive_headers(headers, "https://s3.amazonaws.com:443/a", "https://s3.amazonaws.com:8443/b")
    assert "Authorization" not in result
    assert result["Accept"] == "*/*"


def test_strip_sensitive_headers_scheme_change():
    headers = {"Authorization": "Bearer tok", "Accept": "*/*"}
    result = Driver._strip_sensitive_headers(
        headers, "https://s3.amazonaws.com/a", "http://s3.amazonaws.com/a"
    )
    assert "Authorization" not in result
    assert result["Accept"] == "*/*"


def test_strip_sensitive_headers_cloud_providers():
    headers = {"x-ms-blob-type": "BlockBlob", "x-goog-encryption-key": "key", "Accept": "*/*"}
    result = Driver._strip_sensitive_headers(headers, "https://a.blob.core.windows.net/c", "https://evil.com/c")
    assert "x-ms-blob-type" not in result
    assert "x-goog-encryption-key" not in result
    assert result["Accept"] == "*/*"


def test_strip_sensitive_headers_cookie_and_proxy_auth():
    headers = {"Cookie": "session=abc", "Proxy-Authorization": "Basic xyz", "Accept": "*/*"}
    result = Driver._strip_sensitive_headers(headers, "https://s3.amazonaws.com/a", "https://evil.com/b")
    assert "Cookie" not in result
    assert "Proxy-Authorization" not in result
    assert result["Accept"] == "*/*"


def test_redact_url_with_query_params():
    url = "https://s3.amazonaws.com/bucket/key?X-Amz-Credential=AKIA123&X-Amz-Signature=abc"
    assert Driver._redact_url(url) == "https://s3.amazonaws.com/bucket/key?[REDACTED]"


def test_redact_url_without_query_params():
    url = "https://s3.amazonaws.com/bucket/key"
    assert Driver._redact_url(url) == url


def test_driver_package_import_does_not_cycle():
    """Driver packages import jumpstarter.driver first; session must not pull fanout back.

    Regression: session.py imported fanout, which did `from jumpstarter.driver import
    export` while driver/__init__.py was still loading Driver from base.py.
    Isolated driver tests then failed with:
      ImportError: cannot import name 'export' from partially initialized module
      'jumpstarter.driver'
    """
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from jumpstarter.driver import Driver, export, exportstream",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "level, expected", [(None, logging.INFO), ("DEBUG", logging.DEBUG), ("WARNING", logging.WARNING)]
)
def test_propagate_log_level_recurses_into_children(level, expected, caplog):
    class _TestDriver(Driver):
        @classmethod
        def client(cls) -> str:
            return "test.client"

    grandchild = _TestDriver(uuid=UUID(int=3))
    child = _TestDriver(uuid=UUID(int=2), children={"grandchild": grandchild})
    parent = _TestDriver(uuid=UUID(int=1), log_level=level, children={"child": child})

    try:
        parent.propagate_log_level()

        for driver in (parent, child, grandchild):
            assert driver.logger.level == expected
        with caplog.at_level(logging.DEBUG):
            grandchild.logger.debug("debug message")
            grandchild.logger.info("info message")
            grandchild.logger.warning("warning message")
        assert [(record.name, record.levelno) for record in caplog.records] == [
            ("driver._TestDriver", severity)
            for severity in (logging.DEBUG, logging.INFO, logging.WARNING)
            if severity >= expected
        ]
    finally:
        parent.close()

    for driver in (parent, child, grandchild):
        assert driver._logger_filter not in driver.logger.filters


def test_propagate_log_level_child_explicit_overrides_parent():
    class _TestDriver(Driver):
        @classmethod
        def client(cls) -> str:
            return "test.client"

    grandchild = _TestDriver(uuid=UUID(int=3))
    child = _TestDriver(uuid=UUID(int=2), log_level="WARNING", children={"grandchild": grandchild})
    parent = _TestDriver(uuid=UUID(int=1), log_level="DEBUG", children={"child": child})

    try:
        parent.propagate_log_level()

        assert parent.logger.level == logging.DEBUG
        assert child.logger.level == logging.WARNING
        assert grandchild.logger.level == logging.WARNING
    finally:
        parent.close()


class _ProxyTestDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "test.client"


@pytest.mark.anyio
async def test_presigned_get_uses_proxy_from_environment(http_proxy):
    driver = _ProxyTestDriver(uuid=UUID(int=1))
    try:
        received = b""
        async with driver._resource_from_presigned({}, "http://package.invalid/image.bin", "GET", 10) as stream:
            try:
                while True:
                    received += await stream.receive()
            except EndOfStream:
                pass
    finally:
        driver.close()

    assert received == b'{"proxied": true}'
    assert http_proxy.requests == [("GET", "package.invalid")]


# Ports (JEP-0016)


class _PortsDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.DriverClient"


def test_driver_without_ports_reports_none():
    report = _PortsDriver().report()
    assert list(report.ports) == []


def test_driver_reports_its_ports_without_the_local_addresses():
    from jumpstarter_protocol.jumpstarter.v1 import common_pb2

    driver = _PortsDriver(
        # A plain mapping is validated as a port.
        ports=[  # ty: ignore[invalid-argument-type]
            {"name": "rootcanal", "direction": "provides", "connect": "127.0.0.1:7300", "protocol": "hci-h4"},
            Port(name="controller", direction="requires", listen="127.0.0.1:7301"),
        ]
    )
    report = driver.report(name="bt")

    assert [(p.name, p.direction) for p in report.ports] == [
        ("rootcanal", common_pb2.PORT_DIRECTION_PROVIDES),
        ("controller", common_pb2.PORT_DIRECTION_REQUIRES),
    ]
    assert report.ports[0].protocol == "hci-h4"
    assert b"730" not in report.SerializeToString()
    # The driver keeps the addresses for the exporter.
    assert driver.ports[0].connect_address == ("127.0.0.1", 7300)


def test_driver_class_can_declare_default_ports():
    from dataclasses import dataclass, field

    @dataclass(kw_only=True)
    class Rootcanal(_PortsDriver):
        hci_port: int = 7300
        ports: list[Port] = field(default_factory=lambda: [Port(name="rootcanal", direction="provides")])

        def port_address(self, port):
            return ("127.0.0.1", self.hci_port)

    driver = Rootcanal()
    assert [p.name for p in driver.report().ports] == ["rootcanal"]
    # The exporter configuration replaces the default.
    configured = Rootcanal(ports=[Port(name="hci", direction="provides")])
    assert [p.name for p in configured.report().ports] == ["hci"]


def test_driver_rejects_a_duplicate_port_name():
    with pytest.raises(ValueError, match="duplicate port name"):
        _PortsDriver(
            ports=[
                Port(name="uart", direction="connects", connect="127.0.0.1:4001"),
                Port(name="uart", direction="connects", connect="127.0.0.1:4002"),
            ]
        )


@pytest.mark.parametrize("direction", ["provides", "connects"])
def test_driver_rejects_a_port_without_a_local_address(direction):
    with pytest.raises(ValueError, match="must have"):
        _PortsDriver(ports=[Port(name="svc", direction=direction)])


def test_driver_accepts_a_reported_port_that_a_forward_cannot_use():
    driver = _PortsDriver(ports=[Port(name="someip-sd", direction="connects", transport="datagram")])
    assert len(driver.report().ports) == 1


@pytest.mark.anyio
async def test_connect_port_connects_to_the_connect_address():
    from jumpstarter.common import TemporaryTcpListener

    async def handler(stream):
        async with stream:
            await stream.send(b"service")

    async with TemporaryTcpListener(handler) as address:
        port = Port(name="svc", direction="provides", connect=f"{address[0]}:{address[1]}")
        driver = _PortsDriver(ports=[port])
        async with driver.connect_port(driver.ports[0]) as stream:
            assert await stream.receive() == b"service"
        # The optional method for a closed stream does nothing by default.
        assert await driver.forward_stream_closed(port) is None
