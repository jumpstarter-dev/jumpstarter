import pathlib

import jsonschema
import yaml
from hypothesis import given
from hypothesis import strategies as st

CRD_BASE = (
    pathlib.Path(__file__).resolve().parents[4] / "controller" / "deploy" / "operator" / "config" / "crd" / "bases"
)

safe_text = st.text(
    alphabet=st.characters(categories=("L", "N"), max_codepoint=0x7E),
    min_size=1,
    max_size=30,
)
safe_key = st.from_regex(r"[a-zA-Z][a-zA-Z0-9._-]{0,20}", fullmatch=True)
safe_value = st.from_regex(r"[a-zA-Z0-9][a-zA-Z0-9._-]{0,20}", fullmatch=True)
label_maps = st.dictionaries(safe_key, safe_value, max_size=3)


def _load_crd_schema(filename: str) -> dict:
    path = CRD_BASE / filename
    with open(path) as fp:
        doc = yaml.safe_load(fp)
    return doc["spec"]["versions"][0]["schema"]["openAPIV3Schema"]


def _validate_against_schema(instance: dict, schema: dict) -> None:
    cleaned = _strip_kubernetes_extensions(schema)
    jsonschema.validate(instance=instance, schema=cleaned)


def _strip_kubernetes_extensions(schema: dict) -> dict:
    result = {}
    for key, value in schema.items():
        if key.startswith("x-kubernetes"):
            continue
        if isinstance(value, dict):
            result[key] = _strip_kubernetes_extensions(value)
        elif isinstance(value, list):
            result[key] = [_strip_kubernetes_extensions(item) if isinstance(item, dict) else item for item in value]
        else:
            result[key] = value
    return result


class TestClientCRDSchema:
    SCHEMA = _load_crd_schema("jumpstarter.dev_clients.yaml")

    @given(username=safe_text)
    def test_valid_client_validates(self, username: str) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Client",
            "metadata": {},
            "spec": {"username": username},
        }
        _validate_against_schema(instance, self.SCHEMA)

    @given(username=safe_text, endpoint=safe_text)
    def test_client_with_status_validates(self, username: str, endpoint: str) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Client",
            "metadata": {},
            "spec": {"username": username},
            "status": {"endpoint": endpoint, "credential": {"name": "my-secret"}},
        }
        _validate_against_schema(instance, self.SCHEMA)

    def test_client_with_extra_spec_field_fails(self) -> None:
        schema = _strip_kubernetes_extensions(self.SCHEMA)
        spec_schema = schema.get("properties", {}).get("spec", {})
        additional = spec_schema.get("additionalProperties", True)
        if additional is False:
            instance = {
                "apiVersion": "jumpstarter.dev/v1alpha1",
                "kind": "Client",
                "spec": {"username": "test", "nonexistent": "value"},
            }
            try:
                jsonschema.validate(instance=instance, schema=schema)
                raise AssertionError("Expected validation error for extra field")
            except jsonschema.ValidationError:
                pass


class TestExporterCRDSchema:
    SCHEMA = _load_crd_schema("jumpstarter.dev_exporters.yaml")

    @given(username=safe_text)
    def test_valid_exporter_validates(self, username: str) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Exporter",
            "metadata": {},
            "spec": {"username": username},
        }
        _validate_against_schema(instance, self.SCHEMA)

    @given(
        username=safe_text,
        status_val=st.sampled_from(["Available", "Offline", "BeforeLeaseHook", "LeaseReady"]),
    )
    def test_exporter_with_status_validates(self, username: str, status_val: str) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Exporter",
            "metadata": {},
            "spec": {"username": username},
            "status": {
                "exporterStatus": status_val,
                "endpoint": "grpc://localhost:8080",
            },
        }
        _validate_against_schema(instance, self.SCHEMA)


class TestLeaseCRDSchema:
    SCHEMA = _load_crd_schema("jumpstarter.dev_leases.yaml")

    @given(match_labels=label_maps, duration=st.sampled_from(["30m", "1h", "2h30m", "24h"]))
    def test_valid_lease_validates(self, match_labels: dict[str, str], duration: str) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": match_labels},
                "duration": duration,
                "clientRef": {"name": "test-client"},
            },
        }
        _validate_against_schema(instance, self.SCHEMA)

    @given(
        match_labels=label_maps,
        tags=label_maps,
    )
    def test_lease_with_tags_validates(self, match_labels: dict[str, str], tags: dict[str, str]) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": match_labels},
                "duration": "1h",
                "clientRef": {"name": "test-client"},
                "tags": tags,
            },
        }
        _validate_against_schema(instance, self.SCHEMA)

    @given(
        key=safe_key,
        operator=st.sampled_from(["In", "NotIn", "Exists", "DoesNotExist"]),
        values=st.lists(safe_value, max_size=3),
    )
    def test_lease_with_match_expressions_validates(self, key: str, operator: str, values: list[str]) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {
                    "matchExpressions": [{"key": key, "operator": operator, "values": values}],
                },
                "duration": "1h",
                "clientRef": {"name": "test-client"},
            },
        }
        _validate_against_schema(instance, self.SCHEMA)

    @given(match_labels=label_maps)
    def test_lease_with_release_flag_validates(self, match_labels: dict[str, str]) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": match_labels},
                "duration": "1h",
                "clientRef": {"name": "test-client"},
                "release": True,
            },
        }
        _validate_against_schema(instance, self.SCHEMA)


class TestExporterAccessPolicyCRDSchema:
    SCHEMA = _load_crd_schema("jumpstarter.dev_exporteraccesspolicies.yaml")

    @given(
        match_labels=label_maps,
        client_name=safe_text,
    )
    def test_valid_policy_validates(self, match_labels: dict[str, str], client_name: str) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "ExporterAccessPolicy",
            "metadata": {},
            "spec": {
                "exporterSelector": {"matchLabels": match_labels},
                "policies": [{"clientRef": {"name": client_name}}],
            },
        }
        _validate_against_schema(instance, self.SCHEMA)


class TestCRDFilesExist:
    def test_all_expected_crd_files_present(self) -> None:
        expected = [
            "jumpstarter.dev_clients.yaml",
            "jumpstarter.dev_exporteraccesspolicies.yaml",
            "jumpstarter.dev_exporters.yaml",
            "jumpstarter.dev_leases.yaml",
            "operator.jumpstarter.dev_jumpstarters.yaml",
        ]
        for filename in expected:
            assert (CRD_BASE / filename).exists(), f"Missing CRD file: {filename}"

    def test_all_crds_have_openapi_schema(self) -> None:
        for crd_file in CRD_BASE.glob("*.yaml"):
            with open(crd_file) as fp:
                doc = yaml.safe_load(fp)
            schema = doc["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
            assert "properties" in schema, f"{crd_file.name} missing properties in schema"

    def test_all_crds_have_spec_property(self) -> None:
        for crd_file in CRD_BASE.glob("*.yaml"):
            with open(crd_file) as fp:
                doc = yaml.safe_load(fp)
            schema = doc["spec"]["versions"][0]["schema"]["openAPIV3Schema"]
            assert "spec" in schema["properties"], f"{crd_file.name} missing spec in properties"
