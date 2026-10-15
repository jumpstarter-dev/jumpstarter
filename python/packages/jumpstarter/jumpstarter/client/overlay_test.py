"""Tests for the client-only exporter overlay."""

from contextlib import ExitStack
from unittest.mock import patch

import pytest
from anyio.from_thread import start_blocking_portal
from jumpstarter_driver_composite.client import CompositeClient
from pydantic.dataclasses import dataclass

from jumpstarter.client.base import DriverClient
from jumpstarter.client.core import DriverError
from jumpstarter.client.overlay import (
    apply_client_overlay,
    apply_client_overlay_from_path,
    build_client_overlay,
    prepare_client_overlay,
    validate_client_overlay,
)
from jumpstarter.common.exceptions import ConfigurationError
from jumpstarter.config.env import JMP_CLIENT_OVERLAY, JMP_DRIVERS_ALLOW, JUMPSTARTER_HOST
from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance
from jumpstarter.driver import Driver
from jumpstarter.utils.env import env_async

_DRIVER = "jumpstarter.client.overlay_test.OverlaySampleDriver"


class OverlaySampleDriver(Driver):
    @classmethod
    def client(cls) -> str:
        return "jumpstarter.client.overlay_test.OverlaySampleClient"


class BrokenClientDriver(Driver):
    @classmethod
    def client(cls) -> str:
        raise NotImplementedError("no client")


@dataclass(kw_only=True)
class OverlaySampleClient(DriverClient):
    host: str | None = None


def _export(nodes: dict) -> dict[str, ExporterConfigV1Alpha1DriverInstance]:
    return {name: ExporterConfigV1Alpha1DriverInstance.model_validate(node) for name, node in nodes.items()}


def _sample_export() -> dict[str, ExporterConfigV1Alpha1DriverInstance]:
    return _export({
        "board": {
            "type": _DRIVER,
            "description": "board",
            "methods_description": {"on": "turn on"},
            "config": {"host": "lab", "port": 7},
            "children": {
                "inner": {"type": _DRIVER},
                "serial": {"ref": "group.leaf"},
            },
        },
        "group": {
            "children": {
                "leaf": {"type": _DRIVER},
            },
        },
        "alias": {"ref": "board"},
        "leaf_alias": {"ref": "group.leaf"},
        "chain": {"ref": "alias"},
    })


@pytest.fixture
def portal_and_stack():
    with start_blocking_portal() as portal, ExitStack() as stack:
        yield portal, stack


def test_build_includes_children_config_and_shared_refs(portal_and_stack):
    portal, stack = portal_and_stack
    root = build_client_overlay(_sample_export(), portal, stack)

    board = root.children["board"]
    leaf = root.children["group"].children["leaf"]
    assert isinstance(board, OverlaySampleClient)
    assert isinstance(root.children["group"], CompositeClient)
    assert isinstance(board.children["inner"], OverlaySampleClient)
    assert board.host == "lab"
    assert board.description == "board"
    assert board.methods_description == {"on": "turn on"}
    assert board.labels["jumpstarter.dev/name"] == "board"
    assert root.children["alias"] is board
    assert root.children["chain"] is board
    assert root.children["leaf_alias"] is leaf
    assert board.children["serial"] is leaf


def test_validate_rejects_empty_missing_invalid_and_cyclic_refs():
    with pytest.raises(ConfigurationError, match="empty"):
        validate_client_overlay({})
    with pytest.raises(ConfigurationError, match="nonexistent"):
        validate_client_overlay(_export({"alias": {"ref": "missing"}}))
    with pytest.raises(ConfigurationError, match="invalid"):
        validate_client_overlay(_export({"alias": {"ref": ""}}))
    with pytest.raises(ConfigurationError, match="cycle"):
        validate_client_overlay(_export({"a": {"ref": "b"}, "b": {"ref": "a"}}))


def test_validate_rejects_driver_without_client_class():
    with pytest.raises(ConfigurationError, match="did not provide a client class"):
        validate_client_overlay(_export({
            "broken": {"type": "jumpstarter.client.overlay_test.BrokenClientDriver"},
        }))


def test_server_side_calls_are_not_supported(portal_and_stack):
    portal, stack = portal_and_stack
    power = build_client_overlay(_export({"power": {"type": _DRIVER}}), portal, stack).children["power"]

    with pytest.raises(DriverError, match="not supported"):
        power.call("on")
    with pytest.raises(DriverError, match="not supported"):
        list(power.streamingcall("read"))
    with pytest.raises(DriverError, match="not supported"), power.stream():
        pass


def test_apply_replaces_existing_client_and_warns(portal_and_stack, capsys):
    portal, stack = portal_and_stack
    session = build_client_overlay(_export({
        "power": {"type": _DRIVER, "description": "remote"},
        "kept": {"type": _DRIVER},
    }), portal, stack)
    original = session.children["power"]
    kept = session.children["kept"]

    apply_client_overlay(session, _export({
        "power": {"type": _DRIVER, "description": "local"},
        "extra": {"type": _DRIVER},
    }), portal, stack)

    assert session.children["power"] is not original
    assert session.children["power"].description == "local"
    assert session.children["kept"] is kept
    assert isinstance(session.children["extra"], OverlaySampleClient)
    err = capsys.readouterr().err
    assert "Warning: client overlay replaced existing client 'power'" in err
    assert "extra" not in err
    assert "kept" not in err


def test_prepare_client_overlay_uses_only_the_export_section(tmp_path, portal_and_stack):
    portal, stack = portal_and_stack
    path = tmp_path / "exporter.yaml"
    path.write_text(
        f"""apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: lab
endpoint: "jumpstarter.example:443"
token: "secret"
hooks:
  beforeLease:
    script: "echo hi"
export:
  power:
    type: "{_DRIVER}"
    config:
      host: "bench"
""",
        encoding="utf-8",
    )

    resolved = prepare_client_overlay(str(path))
    assert resolved == str(path.resolve())
    session = build_client_overlay(_export({"kept": {"type": _DRIVER}}), portal, stack)
    apply_client_overlay_from_path(session, resolved, portal, stack)
    assert set(session.children) == {"kept", "power"}
    power = session.children["power"]
    assert isinstance(power, OverlaySampleClient)
    assert power.host == "bench"


@pytest.mark.anyio
async def test_env_async_applies_overlay_from_env(monkeypatch, tmp_path):
    overlay = tmp_path / "overlay.yaml"
    overlay.write_text("placeholder", encoding="utf-8")
    monkeypatch.setenv(JUMPSTARTER_HOST, "sock")
    monkeypatch.setenv(JMP_DRIVERS_ALLOW, "UNSAFE")
    monkeypatch.setenv(JMP_CLIENT_OVERLAY, str(overlay))
    client = type("Client", (), {"children": {}, "close": lambda self: None})()

    class _Connected:
        async def __aenter__(self):
            return client

        async def __aexit__(self, *args):
            return None

    with (
        patch("jumpstarter.utils.env.client_from_path", return_value=_Connected()),
        patch("jumpstarter.utils.env.apply_client_overlay_from_path") as apply,
    ):
        async with env_async(portal=None, stack=None) as got:
            assert got is client

    apply.assert_called_once_with(client, str(overlay), None, None)
