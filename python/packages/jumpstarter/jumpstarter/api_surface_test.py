import ast
import importlib
import pkgutil
import types
from pathlib import Path
from types import ModuleType

import pytest

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
                f"{module_name}.__all__ mismatch.\n  Expected: {expected_sorted}\n  Actual:   {actual_all}"
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
            allowed = _allowed_leaks().get(module_name, set())
            public_attrs = {
                attr
                for attr in dir(mod)
                if not attr.startswith("_") and not isinstance(getattr(mod, attr, None), ModuleType)
            }
            leaks = public_attrs - declared - _non_local_names(mod, module_name) - allowed
            assert not leaks, f"{module_name} leaks undeclared public symbols: {sorted(leaks)}"


def _allowed_leaks() -> dict[str, set[str]]:
    return {
        "jumpstarter.common.utils": {
            "launch_shell",
            "lease_ending_handler",
            "serve",
            "serve_async",
        },
    }


def _non_local_names(mod: ModuleType, module_name: str) -> set[str]:
    non_local = set()
    for attr in dir(mod):
        if attr.startswith("_"):
            continue
        obj = getattr(mod, attr, None)
        defining_module = getattr(obj, "__module__", None)
        if defining_module is None:
            if not callable(obj):
                non_local.add(attr)
        elif defining_module != module_name and not defining_module.startswith(module_name + "."):
            non_local.add(attr)
    return non_local


ZERO_USAGE_ALLOWLIST: set[str] = {
    "jumpstarter.common:AsyncChannel",
    "jumpstarter.common:ControllerStub",
    "jumpstarter.common:ExporterStub",
    "jumpstarter.common:RouterStub",
    "jumpstarter.common:download_fls",
    "jumpstarter.common:get_fls_binary",
    "jumpstarter.common:get_fls_github_url",
    "jumpstarter.common.oci:parse_oci_registry",
    "jumpstarter.common.oci:read_auth_file_credentials",
    "jumpstarter.common.types:AsyncChannel",
    "jumpstarter.common.types:ControllerStub",
    "jumpstarter.common.types:ExporterStub",
    "jumpstarter.common.types:RouterStub",
}


def _collect_imported_names_from_tree(tree: ast.AST, file_path: str) -> dict[str, set[str]]:
    usage: dict[str, set[str]] = {}
    direct_imports = _collect_direct_imports(tree)
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module:
            for alias in node.names:
                key = f"{node.module}:{alias.name}"
                usage.setdefault(key, set()).add(file_path)
        elif isinstance(node, ast.Attribute):
            resolved = _resolve_attribute_chain(node)
            if resolved is not None:
                root_name, attr_parts = resolved
                if root_name in direct_imports:
                    module_name = direct_imports[root_name]
                    dotted_suffix = ".".join(attr_parts[:-1])
                    full_module = f"{module_name}.{dotted_suffix}" if dotted_suffix else module_name
                    symbol = attr_parts[-1]
                    key = f"{full_module}:{symbol}"
                    usage.setdefault(key, set()).add(file_path)
    return usage


def _collect_imported_names(scan_root: Path) -> dict[str, set[str]]:
    usage: dict[str, set[str]] = {}
    for py_file in scan_root.rglob("*.py"):
        if py_file.name.endswith("_test.py"):
            continue
        try:
            tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        except SyntaxError:
            continue
        file_usage = _collect_imported_names_from_tree(tree, str(py_file))
        for key, files in file_usage.items():
            usage.setdefault(key, set()).update(files)
    return usage


def _collect_direct_imports(tree: ast.AST) -> dict[str, str]:
    result: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    result[alias.asname] = alias.name
                else:
                    top_level = alias.name.split(".")[0]
                    result[top_level] = top_level
    return result


def _resolve_attribute_chain(node: ast.Attribute) -> tuple[str, list[str]] | None:
    attrs: list[str] = [node.attr]
    current = node.value
    while isinstance(current, ast.Attribute):
        attrs.append(current.attr)
        current = current.value
    if isinstance(current, ast.Name):
        attrs.reverse()
        return current.id, attrs
    return None


def _packages_root() -> Path:
    return Path(__file__).resolve().parent.parent.parent


class TestExternalUsageCounting:
    def test_exported_symbols_have_external_usage(self) -> None:
        scan_root = _packages_root()
        usage = _collect_imported_names(scan_root)
        zero_usage_symbols: list[str] = []
        for module_name, symbols in MODULES_WITH_ALL.items():
            defining_module = _load_module(module_name)
            defining_file = getattr(defining_module, "__file__", None)
            for symbol in symbols:
                key = f"{module_name}:{symbol}"
                if key in ZERO_USAGE_ALLOWLIST:
                    continue
                import_sites = usage.get(key, set())
                if defining_file:
                    import_sites = import_sites - {defining_file}
                if not import_sites:
                    zero_usage_symbols.append(key)
        assert not zero_usage_symbols, (
            f"Exported symbols with zero external imports (candidates for review): {sorted(zero_usage_symbols)}"
        )


TRACKED_PACKAGES = [
    "jumpstarter.common",
    "jumpstarter.client",
    "jumpstarter.config",
    "jumpstarter.driver",
    "jumpstarter.exporter",
]


class TestModulesWithAllDiscovery:
    def test_all_modules_defining_all_are_tracked(self) -> None:
        untracked: list[str] = []
        for package_name in TRACKED_PACKAGES:
            pkg = _load_module(package_name)
            for _importer, modname, _ispkg in pkgutil.iter_modules(getattr(pkg, "__path__", [])):
                if modname.endswith("_test"):
                    continue
                full_name = f"{package_name}.{modname}"
                try:
                    mod = importlib.import_module(full_name)
                except ImportError:
                    continue
                if hasattr(mod, "__all__") and full_name not in MODULES_WITH_ALL:
                    untracked.append(full_name)
        assert not untracked, f"Modules defining __all__ not tracked in MODULES_WITH_ALL: {sorted(untracked)}"


class TestPackageSubmoduleDiscovery:
    @pytest.mark.parametrize("package_name", TRACKED_PACKAGES)
    def test_submodules_importable(self, package_name: str) -> None:
        mod = _load_module(package_name)
        package_path = getattr(mod, "__path__", None)
        if package_path is None:
            return
        for _importer, modname, _ispkg in pkgutil.iter_modules(package_path):
            if modname.endswith("_test"):
                continue
            full_name = f"{package_name}.{modname}"
            importlib.import_module(full_name)


class TestCollectImportedNames:
    def test_dotted_import_attribute_access_resolves_correctly(self) -> None:
        source = "import jumpstarter.common.oci\njumpstarter.common.oci.parse_oci_registry()\n"
        tree = ast.parse(source)
        scan_root = Path("/nonexistent")
        usage = _collect_imported_names_from_tree(tree, str(scan_root / "test.py"))
        assert "jumpstarter.common.oci:parse_oci_registry" in usage

    def test_aliased_import_attribute_access_resolves_correctly(self) -> None:
        source = "import jumpstarter.common.oci as oci\noci.parse_oci_registry()\n"
        tree = ast.parse(source)
        usage = _collect_imported_names_from_tree(tree, "test.py")
        assert "jumpstarter.common.oci:parse_oci_registry" in usage

    def test_from_import_records_usage(self) -> None:
        source = "from jumpstarter.common.oci import parse_oci_registry\n"
        tree = ast.parse(source)
        usage = _collect_imported_names_from_tree(tree, "test.py")
        assert "jumpstarter.common.oci:parse_oci_registry" in usage

    def test_assignment_aliased_import_not_tracked(self) -> None:
        source = "import jumpstarter.common\nm = jumpstarter.common\nm.Metadata\n"
        tree = ast.parse(source)
        usage = _collect_imported_names_from_tree(tree, "test.py")
        assert "jumpstarter.common:Metadata" not in usage


class TestNonLocalNamesFilter:
    def test_prefix_match_does_not_over_match(self) -> None:
        mod = types.ModuleType("fake")
        local_func = types.FunctionType(compile("0", "", "eval"), {})
        local_func.__module__ = "jumpstarter.common.utils"
        foreign_func = types.FunctionType(compile("0", "", "eval"), {})
        foreign_func.__module__ = "jumpstarter.commonx"
        mod.local_func = local_func
        mod.foreign_func = foreign_func
        result = _non_local_names(mod, "jumpstarter.common")
        assert "foreign_func" in result
        assert "local_func" not in result
