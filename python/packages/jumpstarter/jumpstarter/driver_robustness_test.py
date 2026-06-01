import importlib

import pytest
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from jumpstarter.common.exceptions import JumpstarterException
from jumpstarter.testing_strategies import arbitrary as ARBITRARY

EXPECTED_EXCEPTIONS = (
    TypeError,
    ValueError,
    ValidationError,
    OSError,
    RuntimeError,
    FileNotFoundError,
    NotImplementedError,
    ImportError,
    AttributeError,
    KeyError,
    JumpstarterException,
)

DRIVER_TARGETS = [
    ("jumpstarter_driver_adb", "jumpstarter_driver_adb.driver", "AdbServer"),
    ("jumpstarter_driver_androidemulator", "jumpstarter_driver_androidemulator.driver", "AndroidEmulator"),
    ("jumpstarter_driver_ble", "jumpstarter_driver_ble.driver", "AsyncBleConfig"),
    ("jumpstarter_driver_can", "jumpstarter_driver_can.driver", "Can"),
    ("jumpstarter_driver_composite", "jumpstarter_driver_composite.driver", "Composite"),
    ("jumpstarter_driver_corellium", "jumpstarter_driver_corellium.driver", "Corellium"),
    ("jumpstarter_driver_doip", "jumpstarter_driver_doip.driver", "DoIP"),
    ("jumpstarter_driver_dutlink", "jumpstarter_driver_dutlink.driver", "DutlinkConfig"),
    ("jumpstarter_driver_dut_network", "jumpstarter_driver_dut_network.driver", "DutNetwork"),
    ("jumpstarter_driver_energenie", "jumpstarter_driver_energenie.driver", "EnerGenie"),
    ("jumpstarter_driver_esp32", "jumpstarter_driver_esp32.driver", "Esp32Flasher"),
    ("jumpstarter_driver_flashers", "jumpstarter_driver_flashers.driver", "TIJ784S4Flasher"),
    ("jumpstarter_driver_gpiod", "jumpstarter_driver_gpiod.driver", "DigitalOutput"),
    ("jumpstarter_driver_http", "jumpstarter_driver_http.driver", "HttpServer"),
    ("jumpstarter_driver_http_power", "jumpstarter_driver_http_power.driver", "HttpPower"),
    ("jumpstarter_driver_iscsi", "jumpstarter_driver_iscsi.driver", "ISCSI"),
    ("jumpstarter_driver_mitmproxy", "jumpstarter_driver_mitmproxy.driver", "MitmproxyDriver"),
    ("jumpstarter_driver_network", "jumpstarter_driver_network.driver", "TcpNetwork"),
    ("jumpstarter_driver_noyito_relay", "jumpstarter_driver_noyito_relay.driver", "NoyitoPowerSerial"),
    ("jumpstarter_driver_opendal", "jumpstarter_driver_opendal.driver", "Opendal"),
    ("jumpstarter_driver_pi_pico", "jumpstarter_driver_pi_pico.driver", "PiPicoFlasher"),
    ("jumpstarter_driver_power", "jumpstarter_driver_power.driver", "MockPower"),
    ("jumpstarter_driver_probe_rs", "jumpstarter_driver_probe_rs.driver", "ProbeRs"),
    ("jumpstarter_driver_pyserial", "jumpstarter_driver_pyserial.driver", "PySerial"),
    ("jumpstarter_driver_qemu", "jumpstarter_driver_qemu.driver", "Qemu"),
    ("jumpstarter_driver_renode", "jumpstarter_driver_renode.driver", "RenodeFlasher"),
    ("jumpstarter_driver_ridesx", "jumpstarter_driver_ridesx.driver", "RideSXDriver"),
    ("jumpstarter_driver_sdwire", "jumpstarter_driver_sdwire.driver", "SDWire"),
    ("jumpstarter_driver_shell", "jumpstarter_driver_shell.driver", "Shell"),
    ("jumpstarter_driver_snmp", "jumpstarter_driver_snmp.driver", "SNMPServer"),
    ("jumpstarter_driver_someip", "jumpstarter_driver_someip.driver", "SomeIp"),
    ("jumpstarter_driver_ssh", "jumpstarter_driver_ssh.driver", "SSHWrapper"),
    ("jumpstarter_driver_ssh_mitm", "jumpstarter_driver_ssh_mitm.driver", "SSHMITM"),
    ("jumpstarter_driver_ssh_mount", "jumpstarter_driver_ssh_mount.driver", "SSHMount"),
    ("jumpstarter_driver_stlink_msd", "jumpstarter_driver_stlink_msd.driver", "StlinkMsdFlasher"),
    ("jumpstarter_driver_tasmota", "jumpstarter_driver_tasmota.driver", "TasmotaPower"),
    ("jumpstarter_driver_tftp", "jumpstarter_driver_tftp.driver", "Tftp"),
    ("jumpstarter_driver_tmt", "jumpstarter_driver_tmt.driver", "TMT"),
    ("jumpstarter_driver_uboot", "jumpstarter_driver_uboot.driver", "UbootConsole"),
    ("jumpstarter_driver_uds", "jumpstarter_driver_uds.driver", "UdsInterface"),
    ("jumpstarter_driver_uds_can", "jumpstarter_driver_uds_can.driver", "UdsCan"),
    ("jumpstarter_driver_uds_doip", "jumpstarter_driver_uds_doip.driver", "UdsDoip"),
    ("jumpstarter_driver_ustreamer", "jumpstarter_driver_ustreamer.driver", "UStreamer"),
    ("jumpstarter_driver_vnc", "jumpstarter_driver_vnc.driver", "Vnc"),
    ("jumpstarter_driver_xcp", "jumpstarter_driver_xcp.driver", "Xcp"),
    ("jumpstarter_driver_yepkit", "jumpstarter_driver_yepkit.driver", "Ykush"),
]


def _resolve_class(package: str, module_path: str, class_name: str) -> type:
    pytest.importorskip(package)
    mod = importlib.import_module(module_path)
    return getattr(mod, class_name)


def _is_driver_defined_error(exc: Exception, module_path: str) -> bool:
    exc_module = type(exc).__module__ or ""
    driver_package = module_path.rsplit(".", 1)[0]
    return exc_module.startswith(driver_package)


@pytest.mark.parametrize(
    "package, module_path, class_name",
    DRIVER_TARGETS,
    ids=[t[2] for t in DRIVER_TARGETS],
)
class TestDriverConstructorRobustness:
    @given(kwargs=st.dictionaries(st.text(max_size=10), ARBITRARY, max_size=5))
    def test_constructor_never_crashes(
        self, package: str, module_path: str, class_name: str, kwargs: dict
    ) -> None:
        cls = _resolve_class(package, module_path, class_name)
        try:
            cls(**kwargs)
        except EXPECTED_EXCEPTIONS:
            pass
        except Exception as exc:
            if _is_driver_defined_error(exc, module_path):
                pass
            else:
                raise AssertionError(
                    f"{class_name}(**kwargs) raised unexpected "
                    f"{type(exc).__name__}: {exc}"
                ) from exc
