"""Tests for the proto_gen generator entrypoint."""

import pytest

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


class TestRegistryEmission:
    """--registry-out: the static driver-type → interface map for proto-only device codegen."""

    def _registry(self, tmp_path, packages):
        registry_file = tmp_path / "registry" / "python.yaml"
        rc = main(
            [
                "generate-all",
                "--output-dir",
                str(tmp_path / "proto"),
                *[arg for pkg in packages for arg in ("--import-package", pkg)],
                "--registry-out",
                str(registry_file),
            ]
        )
        assert rc == 0
        return registry_file.read_text()

    def test_registry_is_interface_keyed_with_driver_entries(self, tmp_path):
        content = self._registry(tmp_path, ["jumpstarter_driver_power.driver"])
        # The interface is the entry (source of truth), drivers listed under it.
        assert "  - name: jumpstarter.interfaces.power.v1.PowerInterface" in content
        assert "    proto: jumpstarter/interfaces/power/v1/power.proto" in content
        assert "      - name: jumpstarter_driver_power.driver.MockPower" in content
        assert "          python: jumpstarter_driver_power.client.PowerClient" in content
        # No quoted map keys anywhere — plain scalars only.
        assert '"' not in content

    def test_proto_first_driver_is_a_bare_entry_without_generated_default_client(self, tmp_path):
        """NativeMockPower's advertised client is its _generated default — it lists as a
        bare-string driver entry (device codegen emits its own typed client)."""
        content = self._registry(
            tmp_path,
            ["jumpstarter_driver_power.driver", "jumpstarter_driver_power.driver_native"],
        )
        assert "      - jumpstarter_driver_power.driver_native.NativeMockPower" in content
        assert "_generated" not in content

    def test_registry_parses_as_yaml_with_expected_shape(self, tmp_path):
        yaml = pytest.importorskip("yaml")
        data = yaml.safe_load(self._registry(tmp_path, ["jumpstarter_driver_power.driver"]))
        assert data["version"] == 1
        power = next(
            e for e in data["interfaces"]
            if e["name"] == "jumpstarter.interfaces.power.v1.PowerInterface"
        )
        assert power["proto"] == "jumpstarter/interfaces/power/v1/power.proto"
        mock = next(
            d for d in power["drivers"]
            if isinstance(d, dict) and d["name"] == "jumpstarter_driver_power.driver.MockPower"
        )
        assert mock["clients"]["python"] == "jumpstarter_driver_power.client.PowerClient"
