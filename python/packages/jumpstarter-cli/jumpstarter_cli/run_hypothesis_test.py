import click
import pytest
from hypothesis import given
from hypothesis import strategies as st

from .run import _parse_listener_bind


class TestParseListenerBindValidInputs:
    def test_port_only(self) -> None:
        host, port = _parse_listener_bind("8080")
        assert host == "0.0.0.0"
        assert port == 8080

    def test_host_and_port(self) -> None:
        host, port = _parse_listener_bind("127.0.0.1:8080")
        assert host == "127.0.0.1"
        assert port == 8080

    def test_empty_host_defaults(self) -> None:
        host, port = _parse_listener_bind(":8080")
        assert host == "0.0.0.0"
        assert port == 8080

    def test_port_boundaries(self) -> None:
        _, port = _parse_listener_bind("1")
        assert port == 1
        _, port = _parse_listener_bind("65535")
        assert port == 65535


class TestParseListenerBindInvalidInputs:
    def test_port_zero_rejected(self) -> None:
        with pytest.raises(click.BadParameter, match="between 1 and 65535"):
            _parse_listener_bind("0")

    def test_port_too_high_rejected(self) -> None:
        with pytest.raises(click.BadParameter, match="between 1 and 65535"):
            _parse_listener_bind("65536")

    def test_non_integer_port_rejected(self) -> None:
        with pytest.raises(click.BadParameter, match="port must be an integer"):
            _parse_listener_bind("abc")

    def test_negative_port_rejected(self) -> None:
        with pytest.raises(click.BadParameter):
            _parse_listener_bind("-1")


class TestParseListenerBindFuzz:
    @given(value=st.text(min_size=0, max_size=200))
    def test_never_crashes(self, value: str) -> None:
        try:
            host, port = _parse_listener_bind(value)
            assert isinstance(host, str)
            assert isinstance(port, int)
            assert 1 <= port <= 65535
        except click.BadParameter:
            pass
        except ValueError:
            pass

    @given(port=st.integers(min_value=1, max_value=65535))
    def test_valid_port_always_parses(self, port: int) -> None:
        host, parsed_port = _parse_listener_bind(str(port))
        assert parsed_port == port
        assert host == "0.0.0.0"

    @given(
        host=st.text(min_size=1, max_size=50).filter(lambda s: ":" not in s),
        port=st.integers(min_value=1, max_value=65535),
    )
    def test_host_colon_port_preserves_host(self, host: str, port: int) -> None:
        value = f"{host}:{port}"
        parsed_host, parsed_port = _parse_listener_bind(value)
        assert parsed_port == port
        expected_host = host.strip() or "0.0.0.0"
        assert parsed_host == expected_host

    @given(value=st.text(min_size=0, max_size=100))
    def test_ipv6_ambiguity_no_crash(self, value: str) -> None:
        try:
            _parse_listener_bind(value)
        except (click.BadParameter, ValueError):
            pass

    @given(
        colons=st.integers(min_value=2, max_value=8),
        segments=st.lists(st.text(min_size=0, max_size=5), min_size=2, max_size=9),
    )
    def test_multiple_colons_no_crash(self, colons: int, segments: list[str]) -> None:
        value = ":".join(segments[: colons + 1])
        try:
            _parse_listener_bind(value)
        except (click.BadParameter, ValueError):
            pass
