import os
import sys

import pytest
import yaml

sys.path.insert(0, os.path.dirname(__file__))
from importlib.util import module_from_spec, spec_from_file_location

_spec = spec_from_file_location(
    "generate_crd_docs",
    os.path.join(os.path.dirname(__file__), "generate-crd-docs.py"),
)
generate_crd_docs = module_from_spec(_spec)
_spec.loader.exec_module(generate_crd_docs)

flatten_properties = generate_crd_docs.flatten_properties
render_table = generate_crd_docs.render_table
process_crd = generate_crd_docs.process_crd
main = generate_crd_docs.main


def _minimal_crd(*, versions=None, spec_properties=None, status_properties=None):
    if versions is None:
        schema = {"type": "object", "properties": {}}
        if spec_properties is not None:
            schema["properties"]["spec"] = {
                "type": "object",
                "properties": spec_properties,
            }
        if status_properties is not None:
            schema["properties"]["status"] = {
                "type": "object",
                "properties": status_properties,
            }
        versions = [
            {
                "name": "v1alpha1",
                "storage": True,
                "schema": {"openAPIV3Schema": schema},
            }
        ]
    return {
        "spec": {
            "group": "test.example.com",
            "names": {"kind": "TestResource"},
            "versions": versions,
        }
    }


class TestFlattenProperties:
    def test_empty_properties_returns_empty_list(self):
        assert flatten_properties({}) == []

    def test_single_string_property(self):
        props = {"name": {"type": "string", "description": "The name"}}
        rows = flatten_properties(props)
        assert len(rows) == 1
        assert rows[0] == ("`name`", "string", "The name")

    def test_property_without_type_defaults_to_object(self):
        props = {"data": {"description": "Some data"}}
        rows = flatten_properties(props)
        assert rows[0][1] == "object"

    def test_property_without_description_defaults_to_empty(self):
        props = {"field": {"type": "integer"}}
        rows = flatten_properties(props)
        assert rows[0][2] == ""

    def test_nested_object_properties_are_flattened(self):
        props = {
            "outer": {
                "type": "object",
                "description": "Outer",
                "properties": {
                    "inner": {"type": "string", "description": "Inner"},
                },
            }
        }
        rows = flatten_properties(props)
        assert len(rows) == 2
        assert rows[0][0] == "`outer`"
        assert rows[1][0] == "`outer.inner`"

    def test_prefix_is_prepended(self):
        props = {"field": {"type": "string", "description": "A field"}}
        rows = flatten_properties(props, prefix="spec.")
        assert rows[0][0] == "`spec.field`"

    def test_depth_limit_stops_recursion_at_depth_2(self):
        props = {
            "level0": {
                "type": "object",
                "description": "L0",
                "properties": {
                    "level1": {
                        "type": "object",
                        "description": "L1",
                        "properties": {
                            "level2": {
                                "type": "object",
                                "description": "L2",
                                "properties": {
                                    "level3": {
                                        "type": "string",
                                        "description": "L3",
                                    }
                                },
                            }
                        },
                    }
                },
            }
        }
        rows = flatten_properties(props)
        paths = [r[0] for r in rows]
        assert "`level0`" in paths
        assert "`level0.level1`" in paths
        assert "`level0.level1.level2`" in paths
        assert "`level0.level1.level2.level3`" not in paths

    def test_skip_expand_keys_are_not_recursed(self):
        props = {
            "resources": {
                "type": "object",
                "description": "Resource reqs",
                "properties": {
                    "cpu": {"type": "string", "description": "CPU"},
                },
            }
        }
        rows = flatten_properties(props)
        assert len(rows) == 1
        assert rows[0][0] == "`resources`"

    def test_array_items_with_object_type_are_flattened(self):
        props = {
            "containers": {
                "type": "array",
                "description": "Container list",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Name"},
                    },
                },
            }
        }
        rows = flatten_properties(props)
        assert len(rows) == 2
        assert rows[1][0] == "`containers[].name`"

    def test_enum_values_are_formatted_with_pipes(self):
        props = {
            "mode": {
                "type": "string",
                "description": "Mode",
                "enum": ["fast", "slow"],
            }
        }
        rows = flatten_properties(props)
        assert rows[0][1] == "`fast` | `slow`"

    def test_description_truncated_at_120_chars(self):
        long_desc = "x" * 200
        props = {"field": {"type": "string", "description": long_desc}}
        rows = flatten_properties(props)
        assert len(rows[0][2]) == 120
        assert rows[0][2].endswith("...")

    def test_default_value_appended_to_description(self):
        props = {"port": {"type": "integer", "description": "Port", "default": 8080}}
        rows = flatten_properties(props)
        assert "(default: `8080`)" in rows[0][2]

    def test_properties_are_sorted_by_name(self):
        props = {
            "zebra": {"type": "string", "description": "Z"},
            "alpha": {"type": "string", "description": "A"},
        }
        rows = flatten_properties(props)
        assert rows[0][0] == "`alpha`"
        assert rows[1][0] == "`zebra`"


class TestRenderTable:
    def test_empty_rows_returns_no_fields_message(self):
        result = render_table([])
        assert result == "*No fields defined.*\n"

    def test_single_row_renders_markdown_table(self):
        rows = [("`name`", "string", "The name")]
        result = render_table(rows)
        lines = result.strip().split("\n")
        assert len(lines) == 3
        assert lines[0] == "| Field | Type | Description |"
        assert lines[1] == "| --- | --- | --- |"
        assert "| `name` | string | The name |" in lines[2]

    def test_multiple_rows_render_correctly(self):
        rows = [
            ("`a`", "string", "First"),
            ("`b`", "integer", "Second"),
        ]
        result = render_table(rows)
        lines = result.strip().split("\n")
        assert len(lines) == 4

    def test_pipe_characters_in_description_are_escaped(self):
        rows = [("`field`", "string", "value is A | B")]
        result = render_table(rows)
        lines = result.strip().split("\n")
        assert lines[2] == r"| `field` | string | value is A \| B |"


class TestProcessCrd:
    def test_minimal_crd_produces_kind_and_heading(self, tmp_path):
        crd = _minimal_crd()
        filepath = tmp_path / "test.yaml"
        filepath.write_text(yaml.dump(crd), encoding="utf-8")

        kind, content = process_crd(str(filepath))
        assert kind == "TestResource"
        assert "# TestResource" in content
        assert "`test.example.com/v1alpha1`" in content

    def test_crd_with_spec_properties(self, tmp_path):
        crd = _minimal_crd(
            spec_properties={"replicas": {"type": "integer", "description": "Replica count"}}
        )
        filepath = tmp_path / "test.yaml"
        filepath.write_text(yaml.dump(crd), encoding="utf-8")

        _, content = process_crd(str(filepath))
        assert "## Spec" in content
        assert "replicas" in content

    def test_storage_version_is_preferred(self, tmp_path):
        versions = [
            {
                "name": "v1alpha1",
                "storage": False,
                "schema": {
                    "openAPIV3Schema": {"type": "object", "properties": {}},
                },
            },
            {
                "name": "v1beta1",
                "storage": True,
                "schema": {
                    "openAPIV3Schema": {"type": "object", "properties": {}},
                },
            },
        ]
        crd = _minimal_crd(versions=versions)
        filepath = tmp_path / "test.yaml"
        filepath.write_text(yaml.dump(crd), encoding="utf-8")

        _, content = process_crd(str(filepath))
        assert "v1beta1" in content
        assert "v1alpha1" not in content

    def test_fallback_to_first_version_when_no_storage_flag(self, tmp_path):
        versions = [
            {
                "name": "v1",
                "schema": {
                    "openAPIV3Schema": {"type": "object", "properties": {}},
                },
            },
        ]
        crd = _minimal_crd(versions=versions)
        filepath = tmp_path / "test.yaml"
        filepath.write_text(yaml.dump(crd), encoding="utf-8")

        _, content = process_crd(str(filepath))
        assert "v1" in content


class TestMain:
    def test_exits_with_error_when_no_crds_found(self, tmp_path):
        with pytest.raises(SystemExit) as exc_info:
            main(crd_dir=str(tmp_path))
        assert exc_info.value.code == 1

    def test_generates_output_files(self, tmp_path):
        crd_dir = tmp_path / "crds_in"
        crd_dir.mkdir()
        output_dir = tmp_path / "crds_out"

        crd = _minimal_crd(
            spec_properties={"field": {"type": "string", "description": "A field"}}
        )
        (crd_dir / "test_crd.yaml").write_text(yaml.dump(crd), encoding="utf-8")

        main(crd_dir=str(crd_dir), output_dir=str(output_dir))

        generated = list(output_dir.iterdir())
        assert len(generated) == 1
        assert generated[0].name == "testresource.md"
        content = generated[0].read_text(encoding="utf-8")
        assert "# TestResource" in content
