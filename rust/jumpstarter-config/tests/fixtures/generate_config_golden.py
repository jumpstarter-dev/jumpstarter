#!/usr/bin/env python3
"""Generate golden config fixtures using the REAL Python config save logic.

For each representative config we emit the exact YAML Python writes
(`configs/<name>.yaml`) and Python's own reload→re-save of it
(`configs/<name>.py-roundtrip.yaml`). The Rust tests then assert they can parse
every fixture, round-trip it stably, and that parsing Python's reload yields the
same data — a differential check against the real implementation with Python
needed only at generation time.

Run from the repo root with the project venv:

    python/.venv/bin/python \\
        rust/jumpstarter-config/tests/fixtures/generate_config_golden.py
"""

import os

import yaml

from jumpstarter.config.client import (
    ClientConfigV1Alpha1,
    ClientConfigV1Alpha1Drivers,
    ClientConfigV1Alpha1Lease,
)
from jumpstarter.config.common import ObjectMeta
from jumpstarter.config.exporter import (
    ExporterConfigV1Alpha1,
    ExporterConfigV1Alpha1DriverInstance,
    HookInstanceConfigV1Alpha1,
)
from jumpstarter.config.tls import TLSConfigV1Alpha1
from jumpstarter.config.user import UserConfigV1Alpha1, UserConfigV1Alpha1Config

OUT_DIR = os.path.join(os.path.dirname(__file__), "configs")


def client_yaml(c: ClientConfigV1Alpha1) -> str:
    payload = c.model_dump(mode="json", exclude={"path", "alias"}, exclude_none=True)
    default_leases = ClientConfigV1Alpha1Lease().model_dump(mode="json", exclude_none=True)
    if payload.get("leases") == default_leases:
        payload.pop("leases", None)
    return yaml.safe_dump(payload, sort_keys=False)


def exporter_yaml(e: ExporterConfigV1Alpha1) -> str:
    return yaml.safe_dump(
        e.model_dump(mode="json", by_alias=True, exclude={"alias", "path"}), sort_keys=False
    )


def user_yaml(u: UserConfigV1Alpha1) -> str:
    return yaml.safe_dump(u.model_dump(mode="json", by_alias=True), sort_keys=False)


def build() -> dict[str, str]:
    out: dict[str, str] = {}

    out["client_full"] = client_yaml(
        ClientConfigV1Alpha1(
            metadata=ObjectMeta(namespace="lab", name="client-1"),
            endpoint="grpc.example.com:8082",
            token="tok-123",
            tls=TLSConfigV1Alpha1(ca="CERTDATA", insecure=True),
            grpcOptions={"grpc.max_receive_message_length": 16777216, "grpc.primary_user_agent": "jmp"},
            drivers=ClientConfigV1Alpha1Drivers(allow=["jumpstarter_driver_*", "mypkg.*"], unsafe=False),
        )
    )

    out["client_minimal"] = client_yaml(
        ClientConfigV1Alpha1(
            metadata=ObjectMeta(namespace="ns", name="min"),
            endpoint="e:1",
            token="t",
        )
    )

    out["client_leases_and_refresh"] = client_yaml(
        ClientConfigV1Alpha1(
            metadata=ObjectMeta(namespace="lab", name="cli"),
            endpoint="grpc:8082",
            token="acc",
            refresh_token="ref-token",
            leases=ClientConfigV1Alpha1Lease(acquisition_timeout=3600),
        )
    )

    exp = ExporterConfigV1Alpha1(
        metadata=ObjectMeta(namespace="lab", name="exp-1"),
        endpoint="grpc.example.com:8082",
        token="etok",
        grpcOptions={"grpc.keepalive_time_ms": 30000},
        description="lab exporter",
        export={
            "power": ExporterConfigV1Alpha1DriverInstance.from_str(
                "type: jumpstarter_driver_power.driver.MockPower\n"
                "description: power port\n"
                "config:\n  voltage: 5\n  enabled: true\n  rate: 0.5\n  tags: [a, b]"
            ),
            "bucket": ExporterConfigV1Alpha1DriverInstance.from_str(
                "children:\n"
                "  serial:\n"
                "    type: jumpstarter_driver_pyserial.driver.PySerial\n"
                "    config:\n      url: loop://\n"
                "    methods_description:\n      read: reads bytes"
            ),
            "alias_ref": ExporterConfigV1Alpha1DriverInstance.from_str("ref: power"),
        },
    )
    exp.hooks.before_lease = HookInstanceConfigV1Alpha1(
        script="j power on", timeout=30, on_failure="endLease"
    )
    exp.hooks.after_lease = HookInstanceConfigV1Alpha1(
        exec="/bin/bash", script="j power off", on_failure="warn"
    )
    out["exporter_tree"] = exporter_yaml(exp)

    out["exporter_minimal"] = exporter_yaml(
        ExporterConfigV1Alpha1(metadata=ObjectMeta(namespace="ns", name="exp"))
    )

    out["user_set"] = user_yaml(
        UserConfigV1Alpha1(config=UserConfigV1Alpha1Config(current_client=None))
    )
    # Set current-client to a plain alias by going through the serialized form.
    u = UserConfigV1Alpha1.model_validate(
        {"apiVersion": "jumpstarter.dev/v1alpha1", "kind": "UserConfig", "config": {"current-client": None}}
    )
    out["user_empty"] = user_yaml(u)

    return out


def reload_and_resave(name: str, text: str) -> str:
    data = yaml.safe_load(text)
    kind = data["kind"]
    if kind == "ClientConfig":
        return client_yaml(ClientConfigV1Alpha1.model_validate(data))
    if kind == "ExporterConfig":
        return exporter_yaml(ExporterConfigV1Alpha1.model_validate(data))
    if kind == "UserConfig":
        return user_yaml(UserConfigV1Alpha1.model_validate(data))
    raise ValueError(f"unknown kind {kind}")


def main() -> None:
    os.makedirs(OUT_DIR, exist_ok=True)
    fixtures = build()
    for name, text in fixtures.items():
        with open(os.path.join(OUT_DIR, f"{name}.yaml"), "w", encoding="utf-8") as f:
            f.write(text)
        with open(os.path.join(OUT_DIR, f"{name}.py-roundtrip.yaml"), "w", encoding="utf-8") as f:
            f.write(reload_and_resave(name, text))
    print(f"wrote {len(fixtures)} fixtures (+ py-roundtrip) to {OUT_DIR}")


if __name__ == "__main__":
    main()
