"""Tests for the proto_gen generator entrypoint."""

from .proto_gen import _discover_interfaces, _is_interface_class, _proto_path_for, main


class TestDiscovery:
    """Interface discovery over imported driver modules."""

    def test_power_module_interfaces(self):
        found = _discover_interfaces(["jumpstarter_driver_power.driver"])
        names = {cls.__name__ for cls in found.values()}
        # The abstract interfaces are discovered; the concrete drivers and the
        # imported Driver base are not.
        assert names == {"PowerInterface", "VirtualPowerInterface"}

    def test_concrete_driver_excluded(self):
        from jumpstarter_driver_power.driver import MockPower, PowerInterface

        assert _is_interface_class(PowerInterface, PowerInterface.__module__)
        assert not _is_interface_class(MockPower, MockPower.__module__)

    def test_dedupes_across_packages(self):
        found = _discover_interfaces(
            ["jumpstarter_driver_power.driver", "jumpstarter_driver_power.driver"]
        )
        assert len(found) == 2


class TestProtoPath:
    """Nested output path mirrors the descriptor package."""

    def test_power_path(self):
        from jumpstarter_driver_power.driver import PowerInterface

        from .descriptor_builder import build_file_descriptor

        fd = build_file_descriptor(PowerInterface)
        assert _proto_path_for(fd) == "jumpstarter/interfaces/power/v1/power.proto"


class TestGenerateCli:
    """The argparse entrypoint."""

    def test_generate_to_file(self, tmp_path, capsys):
        output = tmp_path / "power.proto"
        rc = main(
            [
                "generate",
                "--interface",
                "jumpstarter_driver_power.driver.PowerInterface",
                "--output",
                str(output),
            ]
        )
        assert rc == 0
        content = output.read_text()
        assert 'syntax = "proto3";' in content
        assert "service PowerInterface {" in content

    def test_generate_all_nested_layout(self, tmp_path):
        rc = main(
            [
                "generate-all",
                "--output-dir",
                str(tmp_path),
                "--import-package",
                "jumpstarter_driver_power.driver",
            ]
        )
        assert rc == 0
        power = tmp_path / "jumpstarter" / "interfaces" / "power" / "v1" / "power.proto"
        assert power.exists()
        assert "service PowerInterface {" in power.read_text()
