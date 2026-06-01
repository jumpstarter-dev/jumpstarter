import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

import argparse

import pytest

from fuzz import (
    _clean_example_args,
    _discover_fuzz_test_files,
    _discover_go_fuzz_targets,
    _discover_python_fuzz_dirs,
    _extract_falsifying_examples,
    _insert_example,
    parse_duration,
)


class TestCleanExampleArgs:
    def test_strips_self_parameter(self):
        raw = "\n    self=<foo.Bar object at 0x7f123>,\n    value='hello',\n"
        assert _clean_example_args(raw) == "value='hello'"

    def test_strips_self_with_nested_angles(self):
        raw = (
            "\n    self=<mod.Class object at 0xabc>,\n"
            "    key='k', value=42,\n"
        )
        assert _clean_example_args(raw) == "key='k', value=42"

    def test_preserves_args_without_self(self):
        raw = "value=42"
        assert _clean_example_args(raw) == "value=42"

    def test_single_arg_after_self(self):
        raw = "\n    self=<X object at 0x1>,\n    operator='',\n"
        assert _clean_example_args(raw) == "operator=''"

    def test_empty_string_value(self):
        raw = "value=''"
        assert _clean_example_args(raw) == "value=''"

    def test_value_containing_parens(self):
        raw = "value='hello(world)'"
        assert _clean_example_args(raw) == "value='hello(world)'"

    def test_multiple_args(self):
        raw = "\n    self=<T object at 0x1>,\n    a=1, b='x',\n"
        assert _clean_example_args(raw) == "a=1, b='x'"


class TestExtractFalsifyingExamples:
    def test_single_line(self):
        output = "Falsifying example: test_foo(value=0)"
        result = _extract_falsifying_examples(output)
        assert result == [("test_foo", "value=0")]

    def test_multi_line_with_self(self):
        output = (
            "Falsifying example: test_bar(\n"
            "    self=<mod.Cls object at 0x123>,\n"
            "    value='86400000000000',\n"
            ")"
        )
        result = _extract_falsifying_examples(output)
        assert result == [("test_bar", "value='86400000000000'")]

    def test_multi_line_with_e_prefix(self):
        output = (
            "E           Falsifying example: test_baz(\n"
            "E               self=<mod.Cls object at 0x123>,\n"
            "E               operator='',\n"
            "E           )"
        )
        result = _extract_falsifying_examples(output)
        assert result == [("test_baz", "operator=''")]

    def test_multiple_failures(self):
        output = (
            "Falsifying example: test_a(x=1)\n"
            "some other output\n"
            "Falsifying example: test_b(y='z')\n"
        )
        result = _extract_falsifying_examples(output)
        assert result == [("test_a", "x=1"), ("test_b", "y='z'")]

    def test_deduplicates(self):
        output = (
            "Falsifying example: test_a(x=1)\n"
            "Falsifying example: test_a(x=1)\n"
        )
        result = _extract_falsifying_examples(output)
        assert result == [("test_a", "x=1")]

    def test_no_matches(self):
        output = "all tests passed\n"
        result = _extract_falsifying_examples(output)
        assert result == []

    def test_value_with_parens_in_string(self):
        output = "Falsifying example: test_foo(value='a(b)c')"
        result = _extract_falsifying_examples(output)
        assert result == [("test_foo", "value='a(b)c'")]

    def test_indented_e_prefix(self):
        output = (
            "    E           Falsifying example: test_x(\n"
            "    E               self=<A object at 0x1>,\n"
            "    E               val=99,\n"
            "    E           )"
        )
        result = _extract_falsifying_examples(output)
        assert result == [("test_x", "val=99")]


class TestParseDuration:
    def test_minutes_only(self):
        assert parse_duration("30m") == 1800

    def test_hours_only(self):
        assert parse_duration("2h") == 7200

    def test_hours_and_minutes(self):
        assert parse_duration("1h30m") == 5400

    def test_seconds_only_with_suffix(self):
        assert parse_duration("90s") == 90

    def test_bare_number(self):
        assert parse_duration("90") == 90

    def test_empty_string(self):
        assert parse_duration("") == 0

    def test_whitespace_only(self):
        assert parse_duration("  ") == 0

    def test_all_units(self):
        assert parse_duration("1h30m45s") == 5445

    def test_malformed_input_raises_argument_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_duration("abc")

    def test_malformed_trailing_text_raises_argument_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_duration("30mxyz")


class TestInsertExample:
    def test_inserts_before_multiline_given(self, tmp_path):
        test_file = tmp_path / "test_mod.py"
        test_file.write_text(
            "from hypothesis import given\n"
            "from hypothesis import strategies as st\n"
            "\n"
            "\n"
            "class TestOther:\n"
            "    @given(x=st.text())\n"
            "    def test_other(self, x):\n"
            "        pass\n"
            "\n"
            "\n"
            "class TestTarget:\n"
            "    @given(\n"
            "        operator=st.text().filter(lambda s: s not in ('a', 'b')),\n"
            "    )\n"
            "    def test_target(self, operator):\n"
            "        pass\n"
        )
        result = _insert_example(test_file, "test_target", "operator=''")
        assert result is True
        text = test_file.read_text()
        lines = text.splitlines()
        target_idx = next(i for i, l in enumerate(lines) if "def test_target" in l)
        assert "@example(operator='')" in lines[target_idx - 4]
        assert "@given(" in lines[target_idx - 3]
        other_idx = next(i for i, l in enumerate(lines) if "def test_other" in l)
        assert "@example" not in lines[other_idx - 1]

    def test_inserts_before_single_line_given(self, tmp_path):
        test_file = tmp_path / "test_mod.py"
        test_file.write_text(
            "from hypothesis import given\n"
            "from hypothesis import strategies as st\n"
            "\n"
            "\n"
            "class TestFoo:\n"
            "    @given(value=st.text())\n"
            "    def test_foo(self, value):\n"
            "        pass\n"
        )
        result = _insert_example(test_file, "test_foo", "value='x'")
        assert result is True
        text = test_file.read_text()
        lines = text.splitlines()
        foo_idx = next(i for i, l in enumerate(lines) if "def test_foo" in l)
        assert "@example(value='x')" in lines[foo_idx - 2]
        assert "@given(value=st.text())" in lines[foo_idx - 1]


class TestDiscoverPythonFuzzDirs:
    @pytest.fixture(autouse=True)
    def _project_root(self, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)

    def test_includes_core_packages(self):
        dirs = _discover_python_fuzz_dirs()
        assert "packages/jumpstarter/jumpstarter" in dirs
        assert "packages/jumpstarter-cli/jumpstarter_cli" in dirs
        assert "packages/jumpstarter-kubernetes/jumpstarter_kubernetes" in dirs

    def test_includes_driver_packages_with_robustness_tests(self):
        dirs = _discover_python_fuzz_dirs()
        driver_dirs = [d for d in dirs if "driver" in d]
        assert len(driver_dirs) > 0, "should discover driver packages with fuzz tests"

    def test_returns_sorted_list(self):
        dirs = _discover_python_fuzz_dirs()
        assert dirs == sorted(dirs)

    def test_all_dirs_exist(self):
        dirs = _discover_python_fuzz_dirs()
        for d in dirs:
            assert (Path("python") / d).is_dir(), f"{d} does not exist"


class TestDiscoverGoFuzzTargets:
    @pytest.fixture(autouse=True)
    def _project_root(self, monkeypatch):
        monkeypatch.chdir(Path(__file__).resolve().parent.parent)

    def test_discovers_known_targets(self):
        targets = _discover_go_fuzz_targets()
        names = [name for name, _ in targets]
        assert "FuzzParseLabelSelector" in names
        assert "FuzzBearerTokenExtraction" in names
        assert "FuzzMatchLabels" in names

    def test_returns_sorted_list(self):
        targets = _discover_go_fuzz_targets()
        assert targets == sorted(targets)

    def test_all_names_start_with_fuzz(self):
        targets = _discover_go_fuzz_targets()
        for name, _ in targets:
            assert name.startswith("Fuzz"), f"{name} does not start with Fuzz"

    def test_all_packages_are_relative(self):
        targets = _discover_go_fuzz_targets()
        for _, pkg in targets:
            assert pkg.startswith("./"), f"{pkg} is not a relative package path"
            assert pkg.endswith("/"), f"{pkg} does not end with /"

    def test_returns_empty_when_no_controller_dir(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        assert _discover_go_fuzz_targets() == []
