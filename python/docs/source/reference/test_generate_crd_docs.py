"""Tests for the CRD documentation generator."""

import importlib
import importlib.util
import os

import pytest
import yaml


@pytest.fixture()
def generate_mod():
    spec = importlib.util.spec_from_file_location(
        "generate_crd_docs",
        os.path.join(os.path.dirname(__file__), "generate-crd-docs.py"),
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class TestFlattenProperties:
    def test_simple_string_property(self, generate_mod):
        props = {"name": {"type": "string", "description": "The name"}}
        rows = generate_mod.flatten_properties(props)
        assert rows == [("`name`", "string", "The name")]

    def test_nested_object_property(self, generate_mod):
        props = {
            "outer": {
                "type": "object",
                "description": "Outer object",
                "properties": {
                    "inner": {"type": "string", "description": "Inner field"},
                },
            }
        }
        rows = generate_mod.flatten_properties(props)
        assert len(rows) == 2
        assert rows[0] == ("`outer`", "object", "Outer object")
        assert rows[1] == ("`outer.inner`", "string", "Inner field")

    def test_depth_limit_stops_at_2(self, generate_mod):
        props = {
            "l0": {
                "type": "object",
                "description": "Level 0",
                "properties": {
                    "l1": {
                        "type": "object",
                        "description": "Level 1",
                        "properties": {
                            "l2": {
                                "type": "object",
                                "description": "Level 2",
                                "properties": {
                                    "l3": {
                                        "type": "string",
                                        "description": "Level 3",
                                    }
                                },
                            }
                        },
                    }
                },
            }
        }
        rows = generate_mod.flatten_properties(props)
        paths = [r[0] for r in rows]
        assert "`l0`" in paths
        assert "`l0.l1`" in paths
        assert "`l0.l1.l2`" in paths
        assert "`l0.l1.l2.l3`" not in paths

    def test_skip_expand_stops_recursion(self, generate_mod):
        props = {
            "resources": {
                "type": "object",
                "description": "Resource reqs",
                "properties": {
                    "limits": {"type": "object", "description": "Limits"},
                },
            }
        }
        rows = generate_mod.flatten_properties(props)
        assert len(rows) == 1
        assert rows[0][0] == "`resources`"

    def test_description_truncation_at_120(self, generate_mod):
        long_desc = "a" * 200
        props = {"field": {"type": "string", "description": long_desc}}
        rows = generate_mod.flatten_properties(props)
        assert len(rows[0][2]) == 120
        assert rows[0][2].endswith("...")

    def test_enum_formatting(self, generate_mod):
        props = {
            "status": {
                "type": "string",
                "description": "Status",
                "enum": ["Running", "Stopped"],
            }
        }
        rows = generate_mod.flatten_properties(props)
        assert rows[0][1] == "`Running` | `Stopped`"

    def test_default_value_appended(self, generate_mod):
        props = {
            "retries": {
                "type": "integer",
                "description": "Retry count",
                "default": 3,
            }
        }
        rows = generate_mod.flatten_properties(props)
        assert "(default: `3`)" in rows[0][2]

    def test_pipe_in_description_is_escaped(self, generate_mod):
        props = {
            "field": {
                "type": "string",
                "description": "Use A | B syntax",
            }
        }
        rows = generate_mod.flatten_properties(props)
        assert "\\|" in rows[0][2]
        assert "A \\| B" in rows[0][2]

    def test_array_items_expanded(self, generate_mod):
        props = {
            "containers": {
                "type": "array",
                "description": "Container list",
                "items": {
                    "type": "object",
                    "properties": {
                        "name": {"type": "string", "description": "Container name"},
                    },
                },
            }
        }
        rows = generate_mod.flatten_properties(props)
        assert len(rows) == 2
        assert rows[1][0] == "`containers[].name`"

    def test_prefix_applied(self, generate_mod):
        props = {"field": {"type": "string", "description": "A field"}}
        rows = generate_mod.flatten_properties(props, prefix="spec.")
        assert rows[0][0] == "`spec.field`"

    def test_empty_properties(self, generate_mod):
        rows = generate_mod.flatten_properties({})
        assert rows == []


class TestRenderTable:
    def test_empty_rows_returns_no_fields_message(self, generate_mod):
        result = generate_mod.render_table([])
        assert result == "*No fields defined.*\n"

    def test_single_row_renders_table(self, generate_mod):
        rows = [("`name`", "string", "The name")]
        result = generate_mod.render_table(rows)
        lines = result.strip().split("\n")
        assert len(lines) == 3
        assert lines[0] == "| Field | Type | Description |"
        assert lines[1] == "| --- | --- | --- |"
        assert lines[2] == "| `name` | string | The name |"

    def test_multiple_rows(self, generate_mod):
        rows = [
            ("`a`", "string", "Field A"),
            ("`b`", "integer", "Field B"),
        ]
        result = generate_mod.render_table(rows)
        lines = result.strip().split("\n")
        assert len(lines) == 4


class TestProcessCrd:
    def _make_crd_file(self, tmp_dir, crd_dict):
        path = os.path.join(tmp_dir, "test.crd.yaml")
        with open(path, "w") as f:
            yaml.dump(crd_dict, f)
        return path

    def _valid_crd(self):
        return {
            "spec": {
                "group": "jumpstarter.dev",
                "names": {"kind": "Exporter"},
                "versions": [
                    {
                        "name": "v1alpha1",
                        "schema": {
                            "openAPIV3Schema": {
                                "description": "An exporter resource",
                                "properties": {
                                    "spec": {
                                        "type": "object",
                                        "properties": {
                                            "endpoint": {
                                                "type": "string",
                                                "description": "gRPC endpoint",
                                            }
                                        },
                                    }
                                },
                            }
                        },
                    }
                ],
            }
        }

    def test_valid_crd_returns_kind_and_content(self, generate_mod, tmp_path):
        crd = self._valid_crd()
        path = self._make_crd_file(str(tmp_path), crd)
        kind, content = generate_mod.process_crd(path)
        assert kind == "Exporter"
        assert "# Exporter" in content
        assert "`jumpstarter.dev/v1alpha1`" in content
        assert "## Spec" in content

    def test_crd_missing_openapi_schema_raises_key_error(
        self, generate_mod, tmp_path
    ):
        crd = {
            "spec": {
                "group": "jumpstarter.dev",
                "names": {"kind": "Broken"},
                "versions": [{"name": "v1alpha1", "schema": {}}],
            }
        }
        path = self._make_crd_file(str(tmp_path), crd)
        with pytest.raises(KeyError):
            generate_mod.process_crd(path)

    def test_crd_without_status_omits_status_section(
        self, generate_mod, tmp_path
    ):
        crd = self._valid_crd()
        path = self._make_crd_file(str(tmp_path), crd)
        _, content = generate_mod.process_crd(path)
        assert "## Status" not in content


class TestMain:
    def test_main_skips_crd_without_schema(self, generate_mod, tmp_path, capsys):
        crd_dir = str(tmp_path / "crds")
        out_dir = str(tmp_path / "output")
        os.makedirs(crd_dir)

        valid_crd = {
            "spec": {
                "group": "jumpstarter.dev",
                "names": {"kind": "Good"},
                "versions": [
                    {
                        "name": "v1alpha1",
                        "schema": {
                            "openAPIV3Schema": {
                                "description": "Valid",
                                "properties": {},
                            }
                        },
                    }
                ],
            }
        }
        broken_crd = {
            "spec": {
                "group": "jumpstarter.dev",
                "names": {"kind": "Broken"},
                "versions": [{"name": "v1alpha1", "schema": {}}],
            }
        }

        with open(os.path.join(crd_dir, "a_good.yaml"), "w") as f:
            yaml.dump(valid_crd, f)
        with open(os.path.join(crd_dir, "b_broken.yaml"), "w") as f:
            yaml.dump(broken_crd, f)

        original_crd_dir = generate_mod.CRD_DIR
        original_out_dir = generate_mod.OUTPUT_DIR
        generate_mod.CRD_DIR = crd_dir
        generate_mod.OUTPUT_DIR = out_dir
        try:
            generate_mod.main()
        finally:
            generate_mod.CRD_DIR = original_crd_dir
            generate_mod.OUTPUT_DIR = original_out_dir

        captured = capsys.readouterr()
        assert "Skipping b_broken.yaml" in captured.out
        assert "Generated 1 CRD docs" in captured.out
        assert os.path.exists(os.path.join(out_dir, "good.md"))

    def test_main_no_crds_prints_message(self, generate_mod, tmp_path, capsys):
        empty_dir = str(tmp_path / "empty")
        os.makedirs(empty_dir)

        original_crd_dir = generate_mod.CRD_DIR
        generate_mod.CRD_DIR = empty_dir
        try:
            generate_mod.main()
        finally:
            generate_mod.CRD_DIR = original_crd_dir

        captured = capsys.readouterr()
        assert "No CRD files found" in captured.out
