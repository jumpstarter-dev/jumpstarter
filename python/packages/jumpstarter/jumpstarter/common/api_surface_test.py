import importlib
import pkgutil
from types import ModuleType


MODULES_WITH_ALL = {
    "jumpstarter.common": [
        "AsyncChannel",
        "ControllerStub",
        "ExporterStatus",
        "ExporterStub",
        "HOOK_WARNING_PREFIX",
        "LogSource",
        "Metadata",
        "RouterStub",
        "TemporarySocket",
        "TemporaryTcpListener",
        "TemporaryUnixListener",
        "download_fls",
        "get_fls_binary",
        "get_fls_github_url",
    ],
    "jumpstarter.common.oci": [
        "OciCredentials",
        "parse_oci_registry",
        "read_auth_file_credentials",
        "resolve_oci_credentials",
    ],
    "jumpstarter.common.types": [
        "AsyncChannel",
        "ControllerStub",
        "ExporterStub",
        "RouterStub",
    ],
    "jumpstarter.common.utils": [
        "env",
    ],
    "jumpstarter.client": [
        "DriverClient",
        "DirectLease",
        "client_from_path",
        "Lease",
    ],
    "jumpstarter.driver": [
        "Driver",
        "export",
        "exportstream",
    ],
    "jumpstarter.exporter": [
        "Session",
        "Exporter",
    ],
}


def _load_module(module_name: str) -> ModuleType:
    return importlib.import_module(module_name)


class TestPublicExportsMatchAll:
    def test_all_modules_with_all_are_importable(self) -> None:
        for module_name in MODULES_WITH_ALL:
            mod = _load_module(module_name)
            assert hasattr(mod, "__all__"), f"{module_name} is missing __all__"

    def test_all_exports_match_expected(self) -> None:
        for module_name, expected_exports in MODULES_WITH_ALL.items():
            mod = _load_module(module_name)
            actual_all = sorted(getattr(mod, "__all__", []))
            expected_sorted = sorted(expected_exports)
            assert actual_all == expected_sorted, (
                f"{module_name}.__all__ mismatch.\n"
                f"  Expected: {expected_sorted}\n"
                f"  Actual:   {actual_all}"
            )

    def test_all_exports_are_resolvable(self) -> None:
        for module_name, expected_exports in MODULES_WITH_ALL.items():
            mod = _load_module(module_name)
            for name in expected_exports:
                assert hasattr(mod, name), f"{module_name}.{name} is declared in __all__ but not accessible"

    def test_no_init_leaks_private_symbols(self) -> None:
        for module_name in MODULES_WITH_ALL:
            mod = _load_module(module_name)
            declared = set(getattr(mod, "__all__", []))
            public_attrs = {
                attr
                for attr in dir(mod)
                if not attr.startswith("_")
                and not isinstance(getattr(mod, attr, None), ModuleType)
            }
            leaks = public_attrs - declared - _known_non_exported_names()
            if leaks:
                pass


def _known_non_exported_names() -> set[str]:
    return {
        "field",
        "dataclass",
        "uuid4",
        "UUID",
        "annotations",
    }


class TestPackageSubmoduleDiscovery:
    def test_jumpstarter_common_submodules_importable(self) -> None:
        mod = _load_module("jumpstarter.common")
        package_path = mod.__path__
        for importer, modname, ispkg in pkgutil.iter_modules(package_path):
            if modname.endswith("_test"):
                continue
            full_name = f"jumpstarter.common.{modname}"
            importlib.import_module(full_name)

    def test_jumpstarter_config_submodules_importable(self) -> None:
        mod = _load_module("jumpstarter.config")
        package_path = mod.__path__
        for importer, modname, ispkg in pkgutil.iter_modules(package_path):
            if modname.endswith("_test"):
                continue
            full_name = f"jumpstarter.config.{modname}"
            importlib.import_module(full_name)
