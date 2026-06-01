import pathlib

import jsonschema
import yaml
from hypothesis import given
from hypothesis import strategies as st

CRD_BASE = (
    pathlib.Path(__file__).resolve().parents[4] / "controller" / "deploy" / "operator" / "config" / "crd" / "bases"
)

ARBITRARY = st.one_of(
    st.text(max_size=50),
    st.integers(),
    st.floats(allow_nan=False),
    st.none(),
    st.booleans(),
)

ARBITRARY_DICT = st.recursive(
    ARBITRARY,
    lambda children: st.one_of(
        st.lists(children, max_size=3),
        st.dictionaries(st.text(max_size=20), children, max_size=3),
    ),
    max_leaves=10,
)


def _load_crd_schema(filename: str) -> dict:
    path = CRD_BASE / filename
    with open(path) as fp:
        doc = yaml.safe_load(fp)
    return doc["spec"]["versions"][0]["schema"]["openAPIV3Schema"]


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


CRD_FILES = [
    "jumpstarter.dev_clients.yaml",
    "jumpstarter.dev_exporteraccesspolicies.yaml",
    "jumpstarter.dev_exporters.yaml",
    "jumpstarter.dev_leases.yaml",
    "operator.jumpstarter.dev_jumpstarters.yaml",
]

CRD_SCHEMAS = {name: _strip_kubernetes_extensions(_load_crd_schema(name)) for name in CRD_FILES}


class TestClientCRDRobustness:
    schema = CRD_SCHEMAS["jumpstarter.dev_clients.yaml"]

    @given(instance=st.dictionaries(st.text(max_size=20), ARBITRARY_DICT, max_size=5))
    def test_validate_never_crashes_on_arbitrary_dict(self, instance: dict) -> None:
        try:
            jsonschema.validate(instance=instance, schema=self.schema)
        except jsonschema.ValidationError:
            pass
        except jsonschema.SchemaError:
            pass
        except Exception as exc:
            raise AssertionError(f"jsonschema.validate raised unexpected {type(exc).__name__}: {exc}") from exc


class TestExporterCRDRobustness:
    schema = CRD_SCHEMAS["jumpstarter.dev_exporters.yaml"]

    @given(instance=st.dictionaries(st.text(max_size=20), ARBITRARY_DICT, max_size=5))
    def test_validate_never_crashes_on_arbitrary_dict(self, instance: dict) -> None:
        try:
            jsonschema.validate(instance=instance, schema=self.schema)
        except jsonschema.ValidationError:
            pass
        except jsonschema.SchemaError:
            pass
        except Exception as exc:
            raise AssertionError(f"jsonschema.validate raised unexpected {type(exc).__name__}: {exc}") from exc


class TestLeaseCRDRobustness:
    schema = CRD_SCHEMAS["jumpstarter.dev_leases.yaml"]

    @given(instance=st.dictionaries(st.text(max_size=20), ARBITRARY_DICT, max_size=5))
    def test_validate_never_crashes_on_arbitrary_dict(self, instance: dict) -> None:
        try:
            jsonschema.validate(instance=instance, schema=self.schema)
        except jsonschema.ValidationError:
            pass
        except jsonschema.SchemaError:
            pass
        except Exception as exc:
            raise AssertionError(f"jsonschema.validate raised unexpected {type(exc).__name__}: {exc}") from exc


class TestExporterAccessPolicyCRDRobustness:
    schema = CRD_SCHEMAS["jumpstarter.dev_exporteraccesspolicies.yaml"]

    @given(instance=st.dictionaries(st.text(max_size=20), ARBITRARY_DICT, max_size=5))
    def test_validate_never_crashes_on_arbitrary_dict(self, instance: dict) -> None:
        try:
            jsonschema.validate(instance=instance, schema=self.schema)
        except jsonschema.ValidationError:
            pass
        except jsonschema.SchemaError:
            pass
        except Exception as exc:
            raise AssertionError(f"jsonschema.validate raised unexpected {type(exc).__name__}: {exc}") from exc


class TestJumpstarterCRDRobustness:
    schema = CRD_SCHEMAS["operator.jumpstarter.dev_jumpstarters.yaml"]

    @given(instance=st.dictionaries(st.text(max_size=20), ARBITRARY_DICT, max_size=5))
    def test_validate_never_crashes_on_arbitrary_dict(self, instance: dict) -> None:
        try:
            jsonschema.validate(instance=instance, schema=self.schema)
        except jsonschema.ValidationError:
            pass
        except jsonschema.SchemaError:
            pass
        except Exception as exc:
            raise AssertionError(f"jsonschema.validate raised unexpected {type(exc).__name__}: {exc}") from exc


class TestAllCRDsRobustness:
    @given(
        instance=st.dictionaries(st.text(max_size=20), ARBITRARY_DICT, max_size=5),
        crd_name=st.sampled_from(CRD_FILES),
    )
    def test_no_crd_schema_crashes_on_arbitrary_input(self, instance: dict, crd_name: str) -> None:
        schema = CRD_SCHEMAS[crd_name]
        try:
            jsonschema.validate(instance=instance, schema=schema)
        except jsonschema.ValidationError:
            pass
        except jsonschema.SchemaError:
            pass
        except Exception as exc:
            raise AssertionError(
                f"jsonschema.validate for {crd_name} raised unexpected {type(exc).__name__}: {exc}"
            ) from exc
