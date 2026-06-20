import os
from contextlib import contextmanager

import pytest

os.environ["TERM"] = "dumb"

try:
    from jumpstarter.common.utils import serve
except ImportError:
    # some packages in the workspace does not depend on jumpstarter
    pass
else:

    def _instantiate_driver(node: dict):
        """Build a driver tree from a parsed driver-instance dict (the Rust core parsed the
        YAML; this only imports the driver classes by dotted path) — mirrors
        jumpstarter.exporter.host._instantiate_spec for doctests."""
        from jumpstarter.common.importlib import import_class

        children = {name: _instantiate_driver(child) for name, child in (node.get("children") or {}).items()}
        ref = node.get("ref")
        if ref:
            from jumpstarter_driver_composite.driver import Proxy

            return Proxy(ref=ref)
        type_ = node.get("type")
        if type_:
            driver_class = import_class(type_, [], True)
            return driver_class(
                description=node.get("description") or None,
                methods_description=node.get("methods_description") or {},
                children=children,
                **(node.get("config") or {}),
            )
        from jumpstarter_driver_composite.driver import Composite

        return Composite(children=children)

    @contextmanager
    def run(config):
        import json

        import jumpstarter_core as jc

        node = json.loads(jc.parse_yaml(config))
        with serve(_instantiate_driver(node)) as client:
            yield client

    @pytest.fixture(autouse=True)
    def jumpstarter_namespace(doctest_namespace):
        doctest_namespace["serve"] = serve
        doctest_namespace["run"] = run

    @pytest.fixture(autouse=True)
    def tmp_config_path(tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "client-config"))

    @pytest.fixture(autouse=True)
    def console_size(monkeypatch):
        monkeypatch.setenv("COLUMNS", "1024")
        monkeypatch.setenv("LINES", "1024")
