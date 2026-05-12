import socket
import struct
from unittest.mock import MagicMock, patch

from . import nftables
from .ntp_server import (
    NTP_EPOCH_OFFSET,
    NTP_PACKET_SIZE,
    NtpServer,
    _extract_transmit_timestamp,
    _to_ntp_timestamp,
    build_response,
)

_DRIVER_MODULE = "jumpstarter_driver_dut_network.driver"


class TestNtpTimestamp:
    def test_epoch_zero(self):
        sec, frac = _to_ntp_timestamp(0.0)
        assert sec == NTP_EPOCH_OFFSET
        assert frac == 0

    def test_known_value(self):
        unix_ts = 1_000_000_000.0
        sec, frac = _to_ntp_timestamp(unix_ts)
        assert sec == int(unix_ts + NTP_EPOCH_OFFSET)
        assert frac == 0

    def test_fractional_seconds(self):
        sec, frac = _to_ntp_timestamp(0.5)
        assert sec == NTP_EPOCH_OFFSET
        assert frac == 2**31


class TestExtractTransmitTimestamp:
    def test_extracts_from_valid_packet(self):
        packet = b"\x00" * 40 + struct.pack("!II", 12345, 67890)
        sec, frac = _extract_transmit_timestamp(packet)
        assert sec == 12345
        assert frac == 67890

    def test_short_packet_returns_zero(self):
        sec, frac = _extract_transmit_timestamp(b"\x00" * 10)
        assert sec == 0
        assert frac == 0


class TestBuildResponse:
    def test_response_length(self):
        resp = build_response(1e9, 1e9, (0, 0))
        assert len(resp) == NTP_PACKET_SIZE

    def test_header_fields(self):
        resp = build_response(1e9, 1e9, (0, 0))
        li_vn_mode, stratum, poll, precision = struct.unpack("!BBBb", resp[:4])
        assert li_vn_mode == (0 << 6) | (4 << 3) | 4
        assert stratum == 1
        assert poll == 6
        assert precision == -20

    def test_reference_id_is_locl(self):
        resp = build_response(1e9, 1e9, (0, 0))
        ref_id = struct.unpack("!I", resp[12:16])[0]
        assert ref_id == 0x4C4F434C

    def test_origin_matches_client_transmit(self):
        origin = (99999, 88888)
        resp = build_response(1e9, 1e9, origin)
        orig_sec, orig_frac = struct.unpack("!II", resp[24:32])
        assert orig_sec == origin[0]
        assert orig_frac == origin[1]

    def test_timestamps_are_ntp_format(self):
        unix_ts = 1_700_000_000.0
        resp = build_response(unix_ts, unix_ts, (0, 0))
        tx_sec = struct.unpack("!I", resp[40:44])[0]
        assert tx_sec == int(unix_ts + NTP_EPOCH_OFFSET)


class TestNtpServer:
    def test_start_and_stop(self):
        server = NtpServer("127.0.0.1", port=0)
        with patch.object(socket.socket, "bind"):
            server.start()
            assert server.running
            server.stop()
            assert not server.running

    def test_double_start_is_noop(self):
        server = NtpServer("127.0.0.1", port=0)
        with patch.object(socket.socket, "bind"):
            server.start()
            thread = server._thread
            server.start()
            assert server._thread is thread
            server.stop()

    def test_stop_without_start(self):
        server = NtpServer("127.0.0.1")
        server.stop()
        assert not server.running

    def test_responds_to_ntp_request(self):
        server = NtpServer("127.0.0.1", port=0)
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
        sock.close()

        server._port = port
        server.start()
        try:
            client_sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
            client_sock.settimeout(2)
            request = b"\x1b" + b"\x00" * 47
            client_sock.sendto(request, ("127.0.0.1", port))
            data, _ = client_sock.recvfrom(1024)
            client_sock.close()

            assert len(data) == NTP_PACKET_SIZE
            li_vn_mode = data[0]
            mode = li_vn_mode & 0x07
            assert mode == 4
            stratum = data[1]
            assert stratum == 1
        finally:
            server.stop()


class TestNtpRedirectRules:
    def test_ruleset_contains_expected_elements(self):
        with patch.object(nftables, "_run_nft"), \
             patch.object(nftables, "_load_ruleset") as mock_load:
            nftables.apply_ntp_redirect("br-jmp0", "192.168.100.1", "jumpstarter_br_jmp0")
            ruleset = mock_load.call_args[0][0]
            assert "udp dport 123" in ruleset
            assert "dnat to 192.168.100.1:123" in ruleset
            assert "br-jmp0" in ruleset
            assert "prerouting" in ruleset

    def test_uses_ntp_table_suffix(self):
        with patch.object(nftables, "_run_nft"), \
             patch.object(nftables, "_load_ruleset") as mock_load:
            nftables.apply_ntp_redirect("br-jmp0", "192.168.100.1", "jumpstarter_br_jmp0")
            ruleset = mock_load.call_args[0][0]
            assert "jumpstarter_br_jmp0_ntp" in ruleset

    def test_remove_ntp_redirect(self):
        with patch.object(nftables, "_run_nft") as mock_nft:
            nftables.remove_ntp_redirect("jumpstarter_br_jmp0")
            mock_nft.assert_called_once_with(
                ["delete", "table", "ip", "jumpstarter_br_jmp0_ntp"],
                check=False,
            )


def _make_driver(tmp_path, **overrides):
    """Create a DutNetwork driver with all system calls mocked."""
    params = {
        "interface": "eth-dut",
        "subnet": "192.168.100.0/24",
        "gateway_ip": "192.168.100.1",
        "upstream_interface": "eth-up",
        "nat_mode": "masquerade",
        "dhcp_enabled": True,
        "dhcp_range_start": "192.168.100.100",
        "dhcp_range_end": "192.168.100.200",
        "static_leases": [],
        "dns_servers": ["8.8.8.8"],
        "state_dir": str(tmp_path),
    }
    params.update(overrides)

    from .driver import DutNetwork

    with patch(f"{_DRIVER_MODULE}.sys") as mock_sys, \
         patch(f"{_DRIVER_MODULE}.shutil") as mock_shutil, \
         patch(f"{_DRIVER_MODULE}.iproute") as mock_iproute, \
         patch(f"{_DRIVER_MODULE}.nftables") as mock_nftables, \
         patch(f"{_DRIVER_MODULE}.dnsmasq") as mock_dnsmasq, \
         patch(f"{_DRIVER_MODULE}.NtpServer") as mock_ntp_cls:
        mock_sys.platform = "linux"
        mock_shutil.which.return_value = "/usr/bin/fake"
        mock_dnsmasq.state_dir_for_interface.return_value = tmp_path
        mock_dnsmasq.start.return_value = MagicMock()
        mock_iproute.detect_upstream_interface.return_value = "eth-up"
        mock_iproute.interface_exists.return_value = False
        mock_iproute.get_interface_addresses.return_value = []
        mock_iproute.get_interface_forwarding.return_value = "0"
        mock_iproute.get_interface_prefix_len.return_value = 24
        mock_nftables.ensure_filter_forward.return_value = []
        mock_nftables.list_rules.return_value = ""
        mock_nftables._table_name_for.return_value = "jumpstarter_eth_dut"
        mock_ntp_server = MagicMock()
        mock_ntp_server.running = True
        mock_ntp_cls.return_value = mock_ntp_server
        driver = DutNetwork(**params)  # type: ignore[missing-argument]

    return driver, mock_iproute, mock_nftables, mock_dnsmasq, mock_ntp_cls


class TestDriverLocalNtp:
    def test_ntp_disabled_by_default(self, tmp_path):
        driver, _, mock_nft, _, mock_ntp_cls = _make_driver(tmp_path)
        assert driver.local_ntp is False
        mock_ntp_cls.assert_not_called()
        mock_nft.apply_ntp_redirect.assert_not_called()

    def test_ntp_enabled_starts_server_and_redirect(self, tmp_path):
        driver, _, mock_nft, _, mock_ntp_cls = _make_driver(tmp_path, local_ntp=True)
        mock_ntp_cls.assert_called_once_with("192.168.100.1")
        mock_ntp_cls.return_value.start.assert_called_once()
        mock_nft.apply_ntp_redirect.assert_called_once_with(
            "eth-dut", "192.168.100.1", "jumpstarter_eth_dut",
        )

    def test_ntp_status_enabled(self, tmp_path):
        driver, _, _, _, _ = _make_driver(tmp_path, local_ntp=True)
        status = driver.ntp_status()
        assert status["enabled"] is True
        assert status["running"] is True

    def test_ntp_status_disabled(self, tmp_path):
        driver, _, _, _, _ = _make_driver(tmp_path, local_ntp=False)
        status = driver.ntp_status()
        assert status["enabled"] is False
        assert status["running"] is False

    def test_cleanup_stops_ntp(self, tmp_path):
        driver, _, _, _, _ = _make_driver(tmp_path, local_ntp=True)
        ntp_server = driver._ntp_server
        with patch(f"{_DRIVER_MODULE}.iproute"), \
             patch(f"{_DRIVER_MODULE}.nftables") as mock_nft2, \
             patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.cleanup()
            ntp_server.stop.assert_called_once()
            mock_nft2.remove_ntp_redirect.assert_called_once_with("jumpstarter_eth_dut")
        assert driver._ntp_server is None

    def test_cleanup_skips_ntp_when_disabled(self, tmp_path):
        driver, _, _, _, _ = _make_driver(tmp_path, local_ntp=False)
        with patch(f"{_DRIVER_MODULE}.iproute"), \
             patch(f"{_DRIVER_MODULE}.nftables") as mock_nft2, \
             patch(f"{_DRIVER_MODULE}.dnsmasq"):
            driver.cleanup()
            mock_nft2.remove_ntp_redirect.assert_not_called()
