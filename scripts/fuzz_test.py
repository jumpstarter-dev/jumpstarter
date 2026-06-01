import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from fuzz import _clean_example_args, _extract_falsifying_examples, _insert_example


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
