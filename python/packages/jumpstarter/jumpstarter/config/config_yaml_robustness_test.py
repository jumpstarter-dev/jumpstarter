import yaml
from hypothesis import given
from hypothesis import strategies as st
from pydantic import ValidationError

from jumpstarter.config.exporter import (
    ExporterConfigV1Alpha1,
    ExporterConfigV1Alpha1DriverInstance,
)


def _yaml_safe_load_or_error(raw: str):
    try:
        return yaml.safe_load(raw)
    except yaml.YAMLError:
        return None


YAML_SCALARS = st.one_of(
    st.text(max_size=200),
    st.integers(min_value=-(10**9), max_value=10**9),
    st.floats(allow_nan=False, allow_infinity=False),
    st.booleans(),
    st.none(),
)

YAML_DICTS = st.dictionaries(
    keys=st.text(min_size=1, max_size=20),
    values=YAML_SCALARS,
    max_size=10,
)

YAML_NESTED_DICTS = st.dictionaries(
    keys=st.text(min_size=1, max_size=10),
    values=st.dictionaries(
        keys=st.text(min_size=1, max_size=10),
        values=YAML_SCALARS,
        max_size=5,
    ),
    max_size=5,
)


class TestExporterConfigFromStrRobustness:
    @given(raw=st.text(max_size=500))
    def test_random_text_never_crashes(self, raw: str) -> None:
        try:
            parsed = yaml.safe_load(raw)
            ExporterConfigV1Alpha1.model_validate(parsed)
        except (yaml.YAMLError, ValidationError, TypeError, ValueError):
            pass

    @given(raw=st.binary(max_size=500))
    def test_binary_content_never_crashes(self, raw: bytes) -> None:
        try:
            decoded = raw.decode("utf-8", errors="replace")
            parsed = yaml.safe_load(decoded)
            ExporterConfigV1Alpha1.model_validate(parsed)
        except (
            yaml.YAMLError,
            ValidationError,
            TypeError,
            ValueError,
            UnicodeDecodeError,
        ):
            pass

    @given(data=YAML_DICTS)
    def test_random_dicts_never_crash(self, data: dict) -> None:
        try:
            ExporterConfigV1Alpha1.model_validate(data)
        except (ValidationError, TypeError, ValueError):
            pass

    @given(data=YAML_NESTED_DICTS)
    def test_nested_dicts_never_crash(self, data: dict) -> None:
        try:
            ExporterConfigV1Alpha1.model_validate(data)
        except (ValidationError, TypeError, ValueError):
            pass


class TestDriverInstanceFromStrRobustness:
    @given(raw=st.text(max_size=500))
    def test_random_text_never_crashes(self, raw: str) -> None:
        try:
            parsed = yaml.safe_load(raw)
            ExporterConfigV1Alpha1DriverInstance.model_validate(parsed)
        except (yaml.YAMLError, ValidationError, TypeError, ValueError):
            pass

    @given(data=YAML_DICTS)
    def test_random_dicts_never_crash(self, data: dict) -> None:
        try:
            ExporterConfigV1Alpha1DriverInstance.model_validate(data)
        except (ValidationError, TypeError, ValueError):
            pass


class TestYamlBombRobustness:
    def test_anchor_expansion_does_not_crash(self) -> None:
        yaml_bomb = "a: &anchor\n  b: c\n" + "\n".join(f"x{i}: *anchor" for i in range(100))
        try:
            parsed = yaml.safe_load(yaml_bomb)
            ExporterConfigV1Alpha1.model_validate(parsed)
        except (yaml.YAMLError, ValidationError, TypeError, ValueError):
            pass

    def test_deeply_nested_yaml_does_not_crash(self) -> None:
        depth = 50
        yaml_str = ""
        for i in range(depth):
            yaml_str += " " * (i * 2) + f"level{i}:\n"
        yaml_str += " " * (depth * 2) + "value: leaf"
        try:
            parsed = yaml.safe_load(yaml_str)
            ExporterConfigV1Alpha1.model_validate(parsed)
        except (
            yaml.YAMLError,
            ValidationError,
            TypeError,
            ValueError,
            RecursionError,
        ):
            pass

    def test_large_string_value_does_not_crash(self) -> None:
        large_name = "x" * 10000
        yaml_str = (
            f"apiVersion: jumpstarter.dev/v1alpha1\n"
            f"kind: ExporterConfig\n"
            f"metadata:\n  name: {large_name}\n  namespace: test"
        )
        try:
            parsed = yaml.safe_load(yaml_str)
            ExporterConfigV1Alpha1.model_validate(parsed)
        except (yaml.YAMLError, ValidationError, TypeError, ValueError):
            pass

    def test_many_keys_does_not_crash(self) -> None:
        lines = [f"key{i}: value{i}" for i in range(500)]
        yaml_str = "\n".join(lines)
        try:
            parsed = yaml.safe_load(yaml_str)
            ExporterConfigV1Alpha1.model_validate(parsed)
        except (yaml.YAMLError, ValidationError, TypeError, ValueError):
            pass


class TestYamlEdgeCases:
    def test_null_document(self) -> None:
        try:
            parsed = yaml.safe_load("")
            ExporterConfigV1Alpha1.model_validate(parsed)
        except (yaml.YAMLError, ValidationError, TypeError, ValueError):
            pass

    def test_list_instead_of_dict(self) -> None:
        try:
            parsed = yaml.safe_load("[1, 2, 3]")
            ExporterConfigV1Alpha1.model_validate(parsed)
        except (yaml.YAMLError, ValidationError, TypeError, ValueError):
            pass

    def test_scalar_instead_of_dict(self) -> None:
        try:
            parsed = yaml.safe_load("42")
            ExporterConfigV1Alpha1.model_validate(parsed)
        except (yaml.YAMLError, ValidationError, TypeError, ValueError):
            pass

    def test_valid_structure_wrong_api_version(self) -> None:
        yaml_str = "apiVersion: wrong/v1\nkind: ExporterConfig\nmetadata:\n  name: test\n  namespace: test"
        try:
            parsed = yaml.safe_load(yaml_str)
            ExporterConfigV1Alpha1.model_validate(parsed)
        except (yaml.YAMLError, ValidationError, TypeError, ValueError):
            pass

    def test_valid_structure_wrong_kind(self) -> None:
        yaml_str = "apiVersion: jumpstarter.dev/v1alpha1\nkind: WrongKind\nmetadata:\n  name: test\n  namespace: test"
        try:
            parsed = yaml.safe_load(yaml_str)
            ExporterConfigV1Alpha1.model_validate(parsed)
        except (yaml.YAMLError, ValidationError, TypeError, ValueError):
            pass

    @given(
        value=st.one_of(
            st.integers(),
            st.floats(allow_nan=False),
            st.booleans(),
            st.lists(st.text(max_size=10), max_size=5),
        )
    )
    def test_metadata_wrong_type_never_crashes(self, value) -> None:
        data = {
            "apiVersion": "jumpstarter.dev/v1alpha1",
            "kind": "ExporterConfig",
            "metadata": value,
        }
        try:
            ExporterConfigV1Alpha1.model_validate(data)
        except (ValidationError, TypeError, ValueError):
            pass
