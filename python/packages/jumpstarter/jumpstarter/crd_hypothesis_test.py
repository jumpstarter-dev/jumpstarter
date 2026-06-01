import pathlib

import jsonschema
import yaml
from hypothesis import given
from hypothesis import strategies as st


def _find_repo_root() -> pathlib.Path:
    candidate = pathlib.Path(__file__).resolve().parent
    while candidate != candidate.parent:
        if (candidate / "controller").is_dir() and (candidate / "python").is_dir():
            return candidate
        candidate = candidate.parent
    msg = "cannot locate repository root containing controller/ and python/ directories"
    raise FileNotFoundError(msg)


CRD_BASE = (
    _find_repo_root() / "controller" / "deploy" / "operator" / "config" / "crd" / "bases"
)

safe_text = st.text(min_size=1, max_size=30)
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


class TestNegativeLeaseCRDValidation:
    SCHEMA = _load_crd_schema("jumpstarter.dev_leases.yaml")

    def test_missing_required_selector_fails(self) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "clientRef": {"name": "test-client"},
                "duration": "1h",
            },
        }
        try:
            _validate_against_schema(instance, self.SCHEMA)
            raise AssertionError("Expected validation error for missing selector")
        except jsonschema.ValidationError:
            pass

    def test_missing_required_client_ref_fails(self) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": {"board": "rpi4"}},
                "duration": "1h",
            },
        }
        try:
            _validate_against_schema(instance, self.SCHEMA)
            raise AssertionError("Expected validation error for missing clientRef")
        except jsonschema.ValidationError:
            pass

    @given(wrong_type=st.sampled_from([42, True, ["list"], None]))
    def test_wrong_type_for_duration_fails(self, wrong_type: object) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": {}},
                "clientRef": {"name": "test-client"},
                "duration": wrong_type,
            },
        }
        try:
            _validate_against_schema(instance, self.SCHEMA)
            raise AssertionError("Expected validation error for wrong type")
        except jsonschema.ValidationError:
            pass

    @given(wrong_type=st.sampled_from([42, "string", ["list"], None]))
    def test_wrong_type_for_release_fails(self, wrong_type: object) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": {}},
                "clientRef": {"name": "test-client"},
                "release": wrong_type,
            },
        }
        try:
            _validate_against_schema(instance, self.SCHEMA)
            raise AssertionError("Expected validation error for wrong type")
        except jsonschema.ValidationError:
            pass

    def test_empty_spec_fails(self) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {},
        }
        try:
            _validate_against_schema(instance, self.SCHEMA)
            raise AssertionError("Expected validation error for empty spec")
        except jsonschema.ValidationError:
            pass


class TestNegativeLeaseStatusCRDValidation:
    SCHEMA = _load_crd_schema("jumpstarter.dev_leases.yaml")

    @given(wrong_type=st.sampled_from([42, "string", ["list"], None]))
    def test_wrong_type_for_ended_fails(self, wrong_type: object) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": {}},
                "clientRef": {"name": "test-client"},
            },
            "status": {
                "ended": wrong_type,
            },
        }
        try:
            _validate_against_schema(instance, self.SCHEMA)
            raise AssertionError("Expected validation error for wrong ended type")
        except jsonschema.ValidationError:
            pass


class TestNegativeClientCRDValidation:
    SCHEMA = _load_crd_schema("jumpstarter.dev_clients.yaml")

    @given(wrong_type=st.sampled_from([42, True, ["list"]]))
    def test_wrong_type_for_username_fails(self, wrong_type: object) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Client",
            "metadata": {},
            "spec": {"username": wrong_type},
        }
        try:
            _validate_against_schema(instance, self.SCHEMA)
            raise AssertionError("Expected validation error for wrong username type")
        except jsonschema.ValidationError:
            pass


class TestNegativeExporterCRDValidation:
    SCHEMA = _load_crd_schema("jumpstarter.dev_exporters.yaml")

    @given(wrong_type=st.sampled_from([42, True, ["list"]]))
    def test_wrong_type_for_username_fails(self, wrong_type: object) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Exporter",
            "metadata": {},
            "spec": {"username": wrong_type},
        }
        try:
            _validate_against_schema(instance, self.SCHEMA)
            raise AssertionError("Expected validation error for wrong username type")
        except jsonschema.ValidationError:
            pass


class TestAdversarialLabels:
    LEASE_SCHEMA = _load_crd_schema("jumpstarter.dev_leases.yaml")

    @given(
        key=st.text(min_size=1, max_size=100),
        value=st.text(min_size=0, max_size=100),
    )
    def test_full_unicode_labels_validated(self, key: str, value: str) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": {key: value}},
                "duration": "1h",
                "clientRef": {"name": "test-client"},
            },
        }
        try:
            _validate_against_schema(instance, self.LEASE_SCHEMA)
        except jsonschema.ValidationError:
            pass

    def test_boundary_62_char_label_key(self) -> None:
        key = "a" * 62
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": {key: "val"}},
                "duration": "1h",
                "clientRef": {"name": "test-client"},
            },
        }
        _validate_against_schema(instance, self.LEASE_SCHEMA)

    def test_boundary_63_char_label_key(self) -> None:
        key = "a" * 63
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": {key: "val"}},
                "duration": "1h",
                "clientRef": {"name": "test-client"},
            },
        }
        _validate_against_schema(instance, self.LEASE_SCHEMA)

    def test_boundary_64_char_label_key(self) -> None:
        key = "a" * 64
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": {key: "val"}},
                "duration": "1h",
                "clientRef": {"name": "test-client"},
            },
        }
        _validate_against_schema(instance, self.LEASE_SCHEMA)

    def test_boundary_62_char_label_value(self) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": {"key": "v" * 62}},
                "duration": "1h",
                "clientRef": {"name": "test-client"},
            },
        }
        _validate_against_schema(instance, self.LEASE_SCHEMA)

    def test_boundary_63_char_label_value(self) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": {"key": "v" * 63}},
                "duration": "1h",
                "clientRef": {"name": "test-client"},
            },
        }
        _validate_against_schema(instance, self.LEASE_SCHEMA)

    def test_boundary_64_char_label_value(self) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": {"key": "v" * 64}},
                "duration": "1h",
                "clientRef": {"name": "test-client"},
            },
        }
        _validate_against_schema(instance, self.LEASE_SCHEMA)

    @given(
        count=st.integers(min_value=0, max_value=20),
    )
    def test_many_label_pairs_validated(self, count: int) -> None:
        labels = {f"key-{i}": f"val-{i}" for i in range(count)}
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": labels},
                "duration": "1h",
                "clientRef": {"name": "test-client"},
            },
        }
        _validate_against_schema(instance, self.LEASE_SCHEMA)

    @given(
        key=st.text(min_size=0, max_size=200),
        value=st.text(min_size=0, max_size=200),
    )
    def test_arbitrary_label_no_crash(self, key: str, value: str) -> None:
        instance = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "Lease",
            "metadata": {},
            "spec": {
                "selector": {"matchLabels": {key: value}},
                "duration": "1h",
                "clientRef": {"name": "test-client"},
            },
        }
        try:
            _validate_against_schema(instance, self.LEASE_SCHEMA)
        except jsonschema.ValidationError:
            pass


class TestJumpstarterCRDSchema:
    SCHEMA = _load_crd_schema("operator.jumpstarter.dev_jumpstarters.yaml")

    @given(
        base_domain=st.from_regex(r"[a-z0-9]([a-z0-9\-\.]*[a-z0-9])?", fullmatch=True).filter(lambda s: len(s) <= 63),
    )
    def test_valid_jumpstarter_validates(self, base_domain: str) -> None:
        instance = {
            "apiVersion": "operator.jumpstarter.dev/v1alpha1",
            "kind": "Jumpstarter",
            "metadata": {},
            "spec": {"baseDomain": base_domain},
        }
        _validate_against_schema(instance, self.SCHEMA)

    def test_empty_spec_validates(self) -> None:
        instance = {
            "apiVersion": "operator.jumpstarter.dev/v1alpha1",
            "kind": "Jumpstarter",
            "metadata": {},
            "spec": {},
        }
        _validate_against_schema(instance, self.SCHEMA)

    @given(wrong_type=st.sampled_from([42, True, ["list"]]))
    def test_wrong_type_for_base_domain_fails(self, wrong_type: object) -> None:
        instance = {
            "apiVersion": "operator.jumpstarter.dev/v1alpha1",
            "kind": "Jumpstarter",
            "metadata": {},
            "spec": {"baseDomain": wrong_type},
        }
        try:
            _validate_against_schema(instance, self.SCHEMA)
            raise AssertionError("Expected validation error for wrong baseDomain type")
        except jsonschema.ValidationError:
            pass
