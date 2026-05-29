from pathlib import Path

import jsonschema
import pytest
import yaml
from hypothesis import given
from hypothesis import strategies as st

CRD_BASE_DIR = Path(__file__).resolve().parents[4] / "controller" / "deploy" / "operator" / "config" / "crd" / "bases"


def _load_crd_schema(filename: str) -> dict:
    crd_path = CRD_BASE_DIR / filename
    with crd_path.open() as f:
        crd = yaml.safe_load(f)
    return crd["spec"]["versions"][0]["schema"]["openAPIV3Schema"]


def _get_spec_schema(full_schema: dict) -> dict:
    return full_schema.get("properties", {}).get("spec", {})


LEASE_SCHEMA = _load_crd_schema("jumpstarter.dev_leases.yaml")
LEASE_SPEC_SCHEMA = _get_spec_schema(LEASE_SCHEMA)


class TestLeaseSchemaBasicValidation:
    def test_valid_lease_with_selector(self) -> None:
        instance = {
            "clientRef": {"name": "my-client"},
            "selector": {"matchLabels": {"board": "rpi4"}},
            "duration": "1h",
        }
        jsonschema.validate(instance, LEASE_SPEC_SCHEMA)

    def test_valid_lease_with_exporter_ref(self) -> None:
        instance = {
            "clientRef": {"name": "my-client"},
            "selector": {},
            "exporterRef": {"name": "my-exporter"},
            "duration": "1h",
        }
        jsonschema.validate(instance, LEASE_SPEC_SCHEMA)

    def test_missing_client_ref_rejected(self) -> None:
        instance = {
            "selector": {"matchLabels": {"board": "rpi4"}},
            "duration": "1h",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance, LEASE_SPEC_SCHEMA)

    def test_missing_selector_rejected(self) -> None:
        instance = {
            "clientRef": {"name": "my-client"},
            "duration": "1h",
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance, LEASE_SPEC_SCHEMA)


class TestLeaseCelRuleDocumentation:
    """CEL x-kubernetes-validations rules are enforced server-side by the
    Kubernetes API server. Python jsonschema cannot evaluate CEL expressions.
    These tests document the CEL rules and verify the structural schema
    constraints that jsonschema CAN enforce, while documenting what
    must be tested via integration tests against a real cluster.

    The lease CRD has these CEL rules:
    1. "one of selector or exporterRef.name is required"
       - CEL: checks matchLabels/matchExpressions size OR exporterRef.name size
    2. "tags are immutable after creation"
       - CEL: compares self.tags with oldSelf.tags (update validation)
    """

    def test_empty_selector_and_no_exporter_ref_passes_schema(self) -> None:
        """This passes jsonschema but would FAIL the CEL rule on a real cluster.
        Documents that CEL rule #1 cannot be enforced client-side."""
        instance = {
            "clientRef": {"name": "my-client"},
            "selector": {},
        }
        jsonschema.validate(instance, LEASE_SPEC_SCHEMA)

    def test_both_selector_and_exporter_ref_passes_schema(self) -> None:
        instance = {
            "clientRef": {"name": "my-client"},
            "selector": {"matchLabels": {"board": "rpi4"}},
            "exporterRef": {"name": "my-exporter"},
        }
        jsonschema.validate(instance, LEASE_SPEC_SCHEMA)


class TestLeaseTagsSchemaValidation:
    def test_valid_tags(self) -> None:
        instance = {
            "clientRef": {"name": "my-client"},
            "selector": {"matchLabels": {"board": "rpi4"}},
            "tags": {"team": "devops", "ci-job": "12345"},
        }
        jsonschema.validate(instance, LEASE_SPEC_SCHEMA)

    def test_tags_max_properties_exceeded(self) -> None:
        instance = {
            "clientRef": {"name": "my-client"},
            "selector": {"matchLabels": {"board": "rpi4"}},
            "tags": {f"key{i}": f"val{i}" for i in range(11)},
        }
        with pytest.raises(jsonschema.ValidationError, match="maxProperties"):
            jsonschema.validate(instance, LEASE_SPEC_SCHEMA)

    def test_tags_with_non_string_values_rejected(self) -> None:
        instance = {
            "clientRef": {"name": "my-client"},
            "selector": {"matchLabels": {"board": "rpi4"}},
            "tags": {"key": 123},
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance, LEASE_SPEC_SCHEMA)


class TestLeaseMatchExpressionsSchema:
    def test_valid_match_expression(self) -> None:
        instance = {
            "clientRef": {"name": "my-client"},
            "selector": {
                "matchExpressions": [
                    {"key": "board", "operator": "In", "values": ["rpi4", "rpi5"]},
                ],
            },
        }
        jsonschema.validate(instance, LEASE_SPEC_SCHEMA)

    def test_match_expression_missing_key_rejected(self) -> None:
        instance = {
            "clientRef": {"name": "my-client"},
            "selector": {
                "matchExpressions": [
                    {"operator": "In", "values": ["rpi4"]},
                ],
            },
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance, LEASE_SPEC_SCHEMA)

    def test_match_expression_missing_operator_rejected(self) -> None:
        instance = {
            "clientRef": {"name": "my-client"},
            "selector": {
                "matchExpressions": [
                    {"key": "board", "values": ["rpi4"]},
                ],
            },
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance, LEASE_SPEC_SCHEMA)


class TestLeaseStatusSchema:
    def _get_status_schema(self) -> dict:
        return LEASE_SCHEMA.get("properties", {}).get("status", {})

    def test_valid_status(self) -> None:
        instance = {"ended": False}
        jsonschema.validate(instance, self._get_status_schema())

    def test_missing_ended_rejected(self) -> None:
        instance = {"beginTime": "2024-01-01T00:00:00Z"}
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance, self._get_status_schema())

    def test_condition_missing_required_fields(self) -> None:
        instance = {
            "ended": False,
            "conditions": [{"type": "Ready"}],
        }
        with pytest.raises(jsonschema.ValidationError):
            jsonschema.validate(instance, self._get_status_schema())

    def test_valid_condition(self) -> None:
        instance = {
            "ended": False,
            "conditions": [
                {
                    "type": "Ready",
                    "status": "True",
                    "reason": "LeaseAcquired",
                    "message": "Lease has been acquired",
                    "lastTransitionTime": "2024-01-01T00:00:00Z",
                },
            ],
        }
        jsonschema.validate(instance, self._get_status_schema())


class TestLeaseSchemaFuzz:
    @given(
        client_name=st.text(max_size=50),
        label_key=st.text(min_size=1, max_size=30),
        label_value=st.text(max_size=30),
    )
    def test_arbitrary_label_selectors_validate_or_reject(
        self, client_name: str, label_key: str, label_value: str
    ) -> None:
        instance = {
            "clientRef": {"name": client_name},
            "selector": {"matchLabels": {label_key: label_value}},
        }
        try:
            jsonschema.validate(instance, LEASE_SPEC_SCHEMA)
        except jsonschema.ValidationError:
            pass

    @given(data=st.dictionaries(keys=st.text(min_size=1, max_size=20), values=st.text(max_size=20), max_size=12))
    def test_arbitrary_tags_validate_or_reject(self, data: dict) -> None:
        instance = {
            "clientRef": {"name": "test-client"},
            "selector": {"matchLabels": {"board": "test"}},
            "tags": data,
        }
        try:
            jsonschema.validate(instance, LEASE_SPEC_SCHEMA)
        except jsonschema.ValidationError:
            pass

    @given(extra_fields=st.dictionaries(keys=st.text(min_size=1, max_size=20), values=st.text(max_size=20), max_size=5))
    def test_extra_fields_on_spec(self, extra_fields: dict) -> None:
        instance = {
            "clientRef": {"name": "test-client"},
            "selector": {"matchLabels": {"board": "test"}},
            **extra_fields,
        }
        try:
            jsonschema.validate(instance, LEASE_SPEC_SCHEMA)
        except jsonschema.ValidationError:
            pass
