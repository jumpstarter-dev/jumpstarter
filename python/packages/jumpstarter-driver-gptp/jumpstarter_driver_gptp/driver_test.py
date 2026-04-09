"""Comprehensive tests for the gPTP driver.

Levels:
    1. Unit tests — no system dependencies, always run.
    2. E2E tests — MockGptp over gRPC via serve(), always run.
    2.5. Stateful tests — StatefulPtp4l state machine enforcement, always run.
    3-5. Integration tests — env-gated, require Linux and/or PTP hardware.
"""

from __future__ import annotations

import os
import platform
from unittest.mock import AsyncMock, patch

import pytest
from pydantic import ValidationError

from .common import (
    GptpOffset,
    GptpParentInfo,
    GptpPortStats,
    GptpStatus,
    GptpSyncEvent,
    PortState,
)
from .conftest import PtpStateError
from .driver import (
    Gptp,
    MockGptp,
    _generate_ptp4l_config,
    _validate_extra_args,
    parse_ptp4l_log_line,
)
from jumpstarter.client.core import DriverError
from jumpstarter.common.utils import serve

# =============================================================================
# Level 1: Unit Tests (No system dependencies, always run)
# =============================================================================


class TestPtp4lLogParsing:
    """1a. Parse ptp4l log lines into structured data."""

    def test_parse_offset_line(self):
        line = "ptp4l[1234.567]: master offset   -23 s2 freq  +1234 path delay   567"
        result = parse_ptp4l_log_line(line)
        assert result is not None
        assert result.offset_ns == -23
        assert result.freq_ppb == 1234
        assert result.path_delay_ns == 567
        assert result.servo_state == "s2"

    def test_parse_port_state_change(self):
        line = "ptp4l[1234.567]: port 1: LISTENING to SLAVE on MASTER_CLOCK_SELECTED"
        result = parse_ptp4l_log_line(line)
        assert result is not None
        assert result.port_state == "SLAVE"
        assert result.event == "MASTER_CLOCK_SELECTED"

    def test_parse_init_line(self):
        line = "ptp4l[0.000]: port 1: INITIALIZING to LISTENING on INIT_COMPLETE"
        result = parse_ptp4l_log_line(line)
        assert result is not None
        assert result.port_state == "LISTENING"

    def test_parse_unrecognized_line(self):
        line = "ptp4l[0.000]: some unrecognized message"
        result = parse_ptp4l_log_line(line)
        assert result is None

    def test_parse_fault_line(self):
        line = "ptp4l[5.678]: port 1: SLAVE to FAULTY on FAULT_DETECTED"
        result = parse_ptp4l_log_line(line)
        assert result is not None
        assert result.port_state == "FAULTY"

    def test_parse_master_state(self):
        line = "ptp4l[2.000]: port 1: LISTENING to MASTER on ANNOUNCE_RECEIPT_TIMEOUT_EXPIRES"
        result = parse_ptp4l_log_line(line)
        assert result is not None
        assert result.port_state == "MASTER"

    def test_parse_large_offset(self):
        line = "ptp4l[10.000]: master offset  999999999 s0 freq  -50000 path delay  12345"
        result = parse_ptp4l_log_line(line)
        assert result is not None
        assert result.offset_ns == 999999999
        assert result.servo_state == "s0"
        assert result.freq_ppb == -50000

    def test_parse_negative_freq(self):
        line = "ptp4l[3.000]: master offset   42 s1 freq  -9999 path delay   100"
        result = parse_ptp4l_log_line(line)
        assert result is not None
        assert result.freq_ppb == -9999

    def test_parse_offset_without_master_prefix(self):
        line = "ptp4l[1.000]: offset   -100 s2 freq  +500 path delay   200"
        result = parse_ptp4l_log_line(line)
        assert result is not None
        assert result.offset_ns == -100

    def test_parse_port_with_interface_name(self):
        line = "ptp4l[1.000]: port 1 (eth0): INITIALIZING to LISTENING on INIT_COMPLETE"
        result = parse_ptp4l_log_line(line)
        assert result is not None
        assert result.port_state == "LISTENING"


class TestPtp4lConfigGeneration:
    """1b. Generate ptp4l configuration from driver fields."""

    def test_generate_gptp_config(self):
        config = _generate_ptp4l_config("eth0", 0, "gptp", "L2", "auto")
        assert "domainNumber\t\t0" in config
        assert "network_transport\tL2" in config
        assert "transportSpecific\t0x1" in config

    def test_generate_master_config(self):
        config = _generate_ptp4l_config("eth0", 0, "gptp", "L2", "master")
        assert "priority1\t\t128" in config
        assert "priority2\t\t0" in config

    def test_generate_slave_config(self):
        config = _generate_ptp4l_config("eth0", 0, "default", "UDPv4", "slave")
        assert "network_transport\tUDPv4" in config
        assert "slaveOnly\t\t1" in config

    def test_generate_ieee1588_config(self):
        config = _generate_ptp4l_config("eth0", 0, "default", "UDPv4", "auto")
        assert "transportSpecific" not in config

    def test_generate_config_custom_domain(self):
        config = _generate_ptp4l_config("eth0", 42, "gptp", "L2", "auto")
        assert "domainNumber\t\t42" in config

    def test_generate_config_udpv6(self):
        config = _generate_ptp4l_config("eth0", 0, "default", "UDPv6", "auto")
        assert "network_transport\tUDPv6" in config

    def test_generate_config_has_interface_section(self):
        config = _generate_ptp4l_config("enp3s0", 0, "gptp", "L2", "auto")
        assert "[enp3s0]" in config

    def test_generate_config_custom_priority(self):
        config = _generate_ptp4l_config("eth0", 0, "gptp", "L2", "master", priority1=50)
        assert "priority1\t\t50" in config


class TestHwTimestampingDetection:
    """1c. Detect hardware timestamping support (async)."""

    async def test_detect_hw_timestamping(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (
            b"Capabilities:\n  hardware-transmit\n  hardware-receive\n  hardware-raw-clock\n",
            b"",
        )
        with patch("jumpstarter_driver_gptp.driver.asyncio.create_subprocess_exec", return_value=mock_proc):
            driver = Gptp(interface="eth0")
            assert await driver._supports_hw_timestamping() is True

    async def test_detect_sw_only_timestamping(self):
        mock_proc = AsyncMock()
        mock_proc.communicate.return_value = (
            b"Capabilities:\n  software-transmit\n  software-receive\n",
            b"",
        )
        with patch("jumpstarter_driver_gptp.driver.asyncio.create_subprocess_exec", return_value=mock_proc):
            driver = Gptp(interface="eth0")
            assert await driver._supports_hw_timestamping() is False

    async def test_detect_timestamping_ethtool_missing(self):
        with patch(
            "jumpstarter_driver_gptp.driver.asyncio.create_subprocess_exec",
            side_effect=FileNotFoundError("ethtool not found"),
        ):
            driver = Gptp(interface="eth0")
            assert await driver._supports_hw_timestamping() is False


class TestPydanticModels:
    """1d. Pydantic model validation."""

    def test_gptp_status_model(self):
        status = GptpStatus(
            port_state="SLAVE",
            clock_class=248,
            clock_accuracy=0x21,
            offset_ns=-23,
            mean_delay_ns=567,
            gm_identity="aa:bb:cc:ff:fe:dd:ee:ff",
        )
        assert status.port_state == PortState.SLAVE
        assert status.offset_ns == -23

    def test_gptp_status_from_enum(self):
        status = GptpStatus(port_state=PortState.MASTER)
        assert status.port_state == PortState.MASTER

    def test_gptp_status_invalid_port_state(self):
        with pytest.raises(ValueError):
            GptpStatus(port_state="INVALID_STATE")

    def test_gptp_offset_model(self):
        offset = GptpOffset(
            offset_from_master_ns=-42,
            mean_path_delay_ns=123,
            timestamp=1234567890.0,
        )
        assert offset.offset_from_master_ns == -42

    def test_gptp_sync_event(self):
        event = GptpSyncEvent(
            event_type="sync",
            offset_ns=100.0,
            port_state=PortState.SLAVE,
        )
        assert event.event_type == "sync"

    def test_gptp_sync_event_invalid_type(self):
        with pytest.raises(ValidationError):
            GptpSyncEvent(event_type="invalid")

    def test_gptp_port_stats(self):
        stats = GptpPortStats(sync_count=10, followup_count=10)
        assert stats.sync_count == 10

    def test_gptp_parent_info(self):
        info = GptpParentInfo(
            grandmaster_identity="11:22:33:ff:fe:44:55:66",
            grandmaster_priority1=0,
        )
        assert info.grandmaster_priority1 == 0


class TestDriverConfigValidation:
    """1e. Driver configuration validation."""

    def test_gptp_valid_config(self):
        driver = Gptp(interface="eth0")
        assert driver.interface == "eth0"
        assert driver.domain == 0
        assert driver.profile == "gptp"

    def test_gptp_invalid_profile(self):
        with pytest.raises(ValueError, match="profile"):
            Gptp(interface="eth0", profile="invalid_profile")

    def test_gptp_invalid_transport(self):
        with pytest.raises(ValueError, match="transport"):
            Gptp(interface="eth0", transport="SCTP")

    def test_gptp_invalid_role(self):
        with pytest.raises(ValueError, match="role"):
            Gptp(interface="eth0", role="observer")

    def test_gptp_invalid_interface_name(self):
        with pytest.raises(ValueError, match="Invalid interface name"):
            Gptp(interface="eth0]\\nmalicious")

    def test_gptp_interface_too_long(self):
        with pytest.raises(ValueError, match="Invalid interface name"):
            Gptp(interface="a" * 20)

    def test_gptp_valid_interface_names(self):
        for name in ("eth0", "enp3s0", "ens0f0.100", "br-lan", "wlan0"):
            d = Gptp(interface=name)
            assert d.interface == name

    def test_gptp_denied_extra_args(self):
        with pytest.raises(ValueError, match="denied argument"):
            Gptp(interface="eth0", ptp4l_extra_args=["-f", "/etc/shadow"])

    def test_gptp_denied_extra_args_config(self):
        with pytest.raises(ValueError, match="denied argument"):
            Gptp(interface="eth0", ptp4l_extra_args=["--config=/etc/shadow"])

    def test_gptp_denied_extra_args_uds(self):
        with pytest.raises(ValueError, match="denied argument"):
            Gptp(interface="eth0", ptp4l_extra_args=["--uds_address", "/tmp/evil"])

    def test_gptp_allowed_extra_args(self):
        d = Gptp(interface="eth0", ptp4l_extra_args=["--summary_interval", "1"])
        assert d.ptp4l_extra_args == ["--summary_interval", "1"]


class TestExtraArgsValidation:
    """1f. Extra args denylist validation."""

    def test_validate_extra_args_accepts_safe(self):
        _validate_extra_args(["--summary_interval", "1", "-l", "6"])

    def test_validate_extra_args_rejects_config(self):
        with pytest.raises(ValueError, match="-f"):
            _validate_extra_args(["-f", "/etc/shadow"])

    def test_validate_extra_args_rejects_interface(self):
        with pytest.raises(ValueError, match="-i"):
            _validate_extra_args(["-i", "lo"])

    def test_validate_extra_args_rejects_equals_form(self):
        with pytest.raises(ValueError, match="--config"):
            _validate_extra_args(["--config=/tmp/evil.cfg"])


# =============================================================================
# Level 2: E2E Tests with MockGptp (No system dependencies, always run)
# =============================================================================


class TestMockGptpLifecycle:
    """2a. MockGptp simulated driver tests."""

    def test_mock_gptp_lifecycle(self):
        with serve(MockGptp()) as client:
            client.start()
            status = client.status()
            assert status.port_state == PortState.SLAVE
            assert abs(status.offset_ns) < 1000
            assert client.is_synchronized() is True
            client.stop()

    def test_mock_gptp_streaming(self):
        with serve(MockGptp()) as client:
            client.start()
            events = []
            for event in client.monitor():
                events.append(event)
                if len(events) >= 3:
                    break
            assert len(events) == 3
            assert all(e.event_type == "sync" for e in events)
            client.stop()

    def test_mock_gptp_get_offset(self):
        with serve(MockGptp()) as client:
            client.start()
            offset = client.get_offset()
            assert isinstance(offset.offset_from_master_ns, float)
            assert isinstance(offset.mean_path_delay_ns, float)
            assert offset.timestamp > 0
            client.stop()

    def test_mock_gptp_get_port_stats(self):
        with serve(MockGptp()) as client:
            client.start()
            stats = client.get_port_stats()
            assert stats.sync_count == 42
            client.stop()

    def test_mock_gptp_get_clock_identity(self):
        with serve(MockGptp()) as client:
            client.start()
            identity = client.get_clock_identity()
            assert "ff:fe" in identity
            client.stop()

    def test_mock_gptp_get_parent_info(self):
        with serve(MockGptp()) as client:
            client.start()
            parent = client.get_parent_info()
            assert parent.grandmaster_identity != ""
            assert parent.grandmaster_priority1 == 128
            client.stop()

    def test_mock_gptp_set_priority_forces_master(self):
        with serve(MockGptp()) as client:
            client.start()
            assert client.status().port_state == PortState.SLAVE
            client.set_priority1(0)
            assert client.status().port_state == PortState.MASTER
            client.stop()


class TestMockGptpErrorPaths:
    """2c. Error path tests."""

    def test_status_before_start(self):
        with serve(MockGptp()) as client:
            with pytest.raises(DriverError, match="not started"):
                client.status()

    def test_double_start(self):
        with serve(MockGptp()) as client:
            client.start()
            with pytest.raises(DriverError, match="already running"):
                client.start()

    def test_stop_before_start(self):
        with serve(MockGptp()) as client:
            with pytest.raises(DriverError, match="not started"):
                client.stop()

    def test_get_offset_before_start(self):
        with serve(MockGptp()) as client:
            with pytest.raises(DriverError, match="not started"):
                client.get_offset()

    def test_is_synchronized_before_start(self):
        with serve(MockGptp()) as client:
            with pytest.raises(DriverError, match="not started"):
                client.is_synchronized()

    def test_set_priority_before_start(self):
        with serve(MockGptp()) as client:
            with pytest.raises(DriverError, match="not started"):
                client.set_priority1(0)


class TestClientCli:
    """2d. Client CLI tests."""

    def test_cli_interface(self):
        with serve(MockGptp()) as client:
            cli = client.cli()
            assert hasattr(cli, "commands")
            expected = {"start", "stop", "status", "offset", "monitor", "set-priority"}
            assert expected.issubset(set(cli.commands.keys()))

    def test_cli_status_command(self):
        with serve(MockGptp()) as client:
            client.start()
            cli = client.cli()
            assert cli.commands["status"].name == "status"
            client.stop()


# =============================================================================
# Level 2.5: Stateful Tests (No system dependencies, always run)
# =============================================================================


class TestStatefulPortStateTransitions:
    """2.5a. PTP port state machine transitions."""

    def test_stateful_normal_sync_lifecycle(self, stateful_client):
        client, ptp = stateful_client
        client.start()
        assert ptp._port_state == "LISTENING"

        ptp.simulate_sync_convergence()
        status = client.status()
        assert status.port_state == PortState.SLAVE
        assert ptp._servo_state == "s2"
        assert client.is_synchronized() is True

        client.stop()
        assert ptp._started is False

    def test_stateful_init_to_master(self, stateful_client):
        client, ptp = stateful_client
        client.start()
        ptp._transition_to("MASTER")
        status = client.status()
        assert status.port_state == PortState.MASTER

    def test_stateful_invalid_transition_rejected(self, stateful_ptp4l):
        ptp = stateful_ptp4l
        ptp.start()
        with pytest.raises(PtpStateError, match="Invalid transition"):
            ptp._transition_to("UNCALIBRATED")

    def test_stateful_full_state_cycle(self, stateful_client):
        """Walk through: start -> LISTENING -> SLAVE -> FAULTY -> recovery -> SLAVE -> stop."""
        client, ptp = stateful_client
        client.start()
        assert ptp._port_state == "LISTENING"

        ptp.simulate_sync_convergence()
        assert ptp._port_state == "SLAVE"
        assert ptp._servo_state == "s2"

        ptp.simulate_fault()
        assert ptp._port_state == "FAULTY"
        assert ptp._servo_state == "s0"
        assert client.is_synchronized() is False

        ptp.simulate_recovery_from_fault()
        assert ptp._port_state == "SLAVE"
        assert ptp._servo_state == "s1"

        client.stop()


class TestStatefulOperationOrdering:
    """2.5b. Operation ordering enforcement."""

    def test_stateful_operations_before_start_raise(self, stateful_client):
        client, ptp = stateful_client
        with pytest.raises(DriverError):
            client.status()
        with pytest.raises(DriverError):
            client.get_offset()
        with pytest.raises(DriverError):
            client.is_synchronized()

    def test_stateful_double_start_raises(self, stateful_client):
        client, ptp = stateful_client
        client.start()
        with pytest.raises(DriverError):
            client.start()

    def test_stateful_stop_before_start_raises(self, stateful_client):
        client, ptp = stateful_client
        with pytest.raises(DriverError):
            client.stop()

    def test_stateful_set_priority_before_start_raises(self, stateful_client):
        client, ptp = stateful_client
        with pytest.raises(DriverError):
            client.set_priority1(0)


class TestStatefulPriorityBmca:
    """2.5c. Priority / BMCA role changes."""

    def test_stateful_priority_forces_master(self, stateful_client):
        client, ptp = stateful_client
        client.start()
        ptp.simulate_sync_convergence()
        assert ptp._port_state == "SLAVE"

        client.set_priority1(0)
        assert ptp._port_state == "MASTER"
        assert ptp._priority1 == 0

    def test_stateful_priority_keeps_slave(self, stateful_client):
        client, ptp = stateful_client
        client.start()
        ptp.simulate_sync_convergence()
        client.set_priority1(255)
        assert ptp._port_state == "SLAVE"
        assert ptp._priority1 == 255


class TestStatefulFaultRecovery:
    """2.5d. Fault recovery and resilience."""

    def test_stateful_fault_clears_sync(self, stateful_client):
        client, ptp = stateful_client
        client.start()
        ptp.simulate_sync_convergence()
        assert client.is_synchronized() is True

        ptp.simulate_fault()
        assert client.is_synchronized() is False
        status = client.status()
        assert status.port_state == PortState.FAULTY

    def test_stateful_recovery_restores_sync_capability(self, stateful_client):
        client, ptp = stateful_client
        client.start()
        ptp.simulate_sync_convergence()
        ptp.simulate_fault()
        ptp.simulate_recovery_from_fault()

        assert ptp._port_state == "SLAVE"
        assert ptp._servo_state == "s1"
        assert client.is_synchronized() is False

    def test_stateful_multiple_fault_recovery_cycles(self, stateful_client):
        client, ptp = stateful_client
        client.start()
        for _ in range(3):
            ptp.simulate_sync_convergence()
            assert ptp._port_state == "SLAVE"
            ptp.simulate_fault()
            assert ptp._port_state == "FAULTY"
            ptp.simulate_recovery_from_fault()
        assert ptp._port_state == "SLAVE"


class TestStatefulRestartReset:
    """2.5e. Restart (Stop + Start) state reset."""

    def test_stateful_restart_resets_state(self, stateful_client):
        client, ptp = stateful_client
        client.start()
        ptp.simulate_sync_convergence()
        assert ptp._servo_state == "s2"
        assert ptp._port_state == "SLAVE"

        client.stop()
        client.start()
        assert ptp._port_state == "LISTENING"
        assert ptp._servo_state == "s0"
        assert client.is_synchronized() is False

    def test_stateful_restart_clears_priority(self, stateful_client):
        client, ptp = stateful_client
        client.start()
        client.set_priority1(0)
        client.stop()
        client.start()
        assert ptp._priority1 == 128


class TestStatefulCallLog:
    """2.5f. Call log / audit trail."""

    def test_stateful_call_log_records_operations(self, stateful_client):
        client, ptp = stateful_client
        client.start()
        client.set_priority1(50)
        client.stop()
        assert ptp._call_log == ["start", "set_priority1(50)", "stop"]

    def test_stateful_full_workflow_log(self, stateful_client):
        client, ptp = stateful_client
        client.start()
        ptp.simulate_sync_convergence()
        _ = client.status()
        _ = client.get_offset()
        client.set_priority1(0)
        client.stop()
        assert "start" in ptp._call_log
        assert "set_priority1(0)" in ptp._call_log
        assert "stop" in ptp._call_log


# =============================================================================
# Level 3-5: Integration Tests (Env-gated)
# =============================================================================

_RUN_INTEGRATION = (
    os.environ.get("GPTP_INTEGRATION_TESTS", "0") == "1"
    and platform.system() == "Linux"
)

_RUN_HW_TESTS = os.environ.get("GPTP_HW_TESTS", "0") == "1"


@pytest.mark.skipif(not _RUN_INTEGRATION, reason="GPTP_INTEGRATION_TESTS not set or not Linux")
class TestSoftwareTimestampingIntegration:
    """Level 3: Real ptp4l with software timestamping on veth pairs.

    Both interfaces stay in the root namespace so ptp4l can bind to them
    directly from the test process.
    """

    @pytest.fixture
    def veth_pair(self):
        """Create a veth pair in the root namespace for PTP testing."""
        import subprocess as sp
        cmds = [
            "ip link add veth-m type veth peer name veth-s",
            "ip addr add 10.99.0.1/24 dev veth-m",
            "ip addr add 10.99.0.2/24 dev veth-s",
            "ip link set veth-m up",
            "ip link set veth-s up",
        ]
        for cmd in cmds:
            sp.run(cmd.split(), check=True)
        yield ("veth-m", "veth-s")
        sp.run("ip link del veth-m".split(), check=False)

    @pytest.fixture
    def ptp_master(self, veth_pair):
        """Start a ptp4l master on veth-m."""
        import subprocess as sp
        import time
        master_iface, _ = veth_pair
        proc = sp.Popen(
            ["ptp4l", "-i", master_iface, "-S", "-m",
             "--masterOnly=1", "--domainNumber=0"],
            stdout=sp.PIPE, stderr=sp.STDOUT,
        )
        time.sleep(2)
        yield proc
        proc.terminate()
        proc.wait(timeout=5)

    def test_gptp_real_sync_software_timestamping(self, veth_pair, ptp_master):
        import time
        _, slave_iface = veth_pair
        driver = Gptp(
            interface=slave_iface, domain=0, profile="default",
            transport="UDPv4", role="slave", sync_system_clock=False,
        )
        with serve(driver) as client:
            client.start()
            time.sleep(10)
            status = client.status()
            assert status.port_state == PortState.SLAVE
            offset = client.get_offset()
            assert abs(offset.offset_from_master_ns) < 10_000_000
            assert client.is_synchronized() is True
            client.stop()


@pytest.mark.skipif(not _RUN_HW_TESTS, reason="GPTP_HW_TESTS not set")
class TestHardwareTimestampingIntegration:
    """Level 4: Real ptp4l with hardware timestamping."""

    def test_gptp_hw_timestamping_sub_microsecond(self):
        import time
        iface = os.environ.get("GPTP_TEST_INTERFACE", "eth0")
        driver = Gptp(
            interface=iface, domain=0, profile="gptp",
            transport="L2", role="slave", sync_system_clock=True,
        )
        with serve(driver) as client:
            client.start()
            time.sleep(30)
            offset = client.get_offset()
            assert abs(offset.offset_from_master_ns) < 1000
            client.stop()

    def test_gptp_hw_master_role(self):
        import time
        iface = os.environ.get("GPTP_TEST_INTERFACE", "eth0")
        driver = Gptp(
            interface=iface, domain=0, profile="gptp",
            transport="L2", role="master", sync_system_clock=False,
        )
        with serve(driver) as client:
            client.start()
            time.sleep(10)
            status = client.status()
            assert status.port_state == PortState.MASTER
            client.stop()
