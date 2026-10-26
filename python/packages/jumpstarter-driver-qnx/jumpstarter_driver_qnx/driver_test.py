import pytest

from .driver import Qnx, QnxPower, Qvm, QvmFlasher, QvmPower
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.driver import Driver


class MockDriver(Driver):
    """Minimal mock for driver children used in init tests."""

    @classmethod
    def client(cls) -> str:
        return "jumpstarter_driver_qnx.driver_test.MockDriver"


class TestQnxDriverInit:
    """Test Qnx driver initialization and configuration validation."""

    def test_qnx_requires_power_ctl(self):
        """Qnx driver should require a power_ctl child."""
        with pytest.raises(ConfigurationError, match="power_ctl"):
            Qnx(children={"serial": MockDriver()})

    def test_qnx_requires_serial(self):
        """Qnx driver should require a serial child."""
        with pytest.raises(ConfigurationError, match="serial"):
            Qnx(children={"power_ctl": MockDriver()})

    def test_qnx_creates_power_child_with_siblings(self):
        """Qnx driver should auto-create QnxPower with power_ctl and serial children."""
        power_ctl = MockDriver()
        serial = MockDriver()
        qnx = Qnx(children={"power_ctl": power_ctl, "serial": serial})
        assert "power" in qnx.children
        assert isinstance(qnx.children["power"], QnxPower)
        assert qnx.children["power"].children["power_ctl"] is power_ctl
        assert qnx.children["power"].children["serial"] is serial

    def test_qnx_default_config(self):
        """Qnx driver should have sensible defaults."""
        qnx = Qnx(children={"power_ctl": MockDriver(), "serial": MockDriver()})
        assert qnx.boot_prompt == "Press 'M' for eSD download"
        assert qnx.boot_key == "M"
        assert qnx.final_prompt == "#"
        assert qnx.boot_timeout == 60
        assert qnx.prompt_timeout == 120

    def test_qnx_custom_config_propagated_to_power(self):
        """Qnx config should be passed to the auto-created QnxPower child."""
        qnx = Qnx(
            boot_prompt="Press 'E' for eMMC download",
            boot_key="E",
            final_prompt="$ ",
            boot_timeout=30,
            prompt_timeout=60,
            init_script="echo hello",
            children={"power_ctl": MockDriver(), "serial": MockDriver()},
        )
        power = qnx.children["power"]
        assert isinstance(power, QnxPower)
        assert power.boot_prompt == "Press 'E' for eMMC download"
        assert power.boot_key == "E"
        assert power.final_prompt == "$ "
        assert power.boot_timeout == 30
        assert power.prompt_timeout == 60
        assert power.init_script == "echo hello"


class TestQvmDriverInit:
    """Test Qvm driver initialization and configuration validation."""

    def test_qvm_requires_qnx(self):
        """Qvm driver should require a qnx child."""
        with pytest.raises(ConfigurationError, match="qnx"):
            Qvm()

    def test_qvm_creates_children_with_qnx(self):
        """Qvm driver should auto-create power/storage with shared qnx child."""
        qnx = MockDriver()
        qvm = Qvm(children={"qnx": qnx})
        assert isinstance(qvm.children["power"], QvmPower)
        assert isinstance(qvm.children["storage"], QvmFlasher)
        assert qvm.children["power"].children["qnx"] is qnx
        assert qvm.children["storage"].children["qnx"] is qnx

    def test_qvm_default_config(self):
        """Qvm driver should have sensible defaults."""
        qvm = Qvm(children={"qnx": MockDriver()})
        assert qvm.emmc_device == "/dev/emmc0"
        assert qvm.qvm_path == "/sbin/qvm"
        assert qvm.working_directory == "/tmp"
        assert qvm.qvm_start_timeout == 30
        assert qvm.flash_timeout == 1800

    def test_qvm_render_config(self):
        """Qvm driver should render config template with variable substitution."""
        qvm = Qvm(
            emmc_device="/dev/mmcblk0",
            virtio_net_mac="52:54:00:11:22:33",
            qvm_default_config="hostdev {emmc_device}\nmac {virtio_net_mac}",
            children={"qnx": MockDriver()},
        )
        assert qvm.render_config() == "hostdev /dev/mmcblk0\nmac 52:54:00:11:22:33"

    def test_qvm_render_config_empty(self):
        """Qvm driver should return empty string when no default config."""
        qvm = Qvm(children={"qnx": MockDriver()})
        assert qvm.render_config() == ""
