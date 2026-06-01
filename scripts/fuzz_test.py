import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).parent))

import argparse
import subprocess

import pytest

import fuzz as fuzz_module
from fuzz import (
    _clean_example_args,
    _discover_fuzz_test_files,
    _discover_go_fuzz_targets,
    _discover_python_fuzz_dirs,
    _extract_falsifying_examples,
    _insert_example,
    _is_safe_example_args,
    main,
    parse_duration,
    replay_and_inject_go,
    replay_and_inject_python,
    run_go_all,
    run_hypofuzz,
    run_hypothesis_loop,
    run_python,
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

    def test_closing_paren_in_string_value(self):
        output = "Falsifying example: test_foo(value='a(b)c')\n"
        result = _extract_falsifying_examples(output)
        assert result == [("test_foo", "value='a(b)c'")]

    def test_trailing_whitespace_after_closing_paren(self):
        output = "Falsifying example: test_bar(x=1)  \n"
        result = _extract_falsifying_examples(output)
        assert result == [("test_bar", "x=1")]

    def test_multi_line_with_closing_paren_in_value(self):
        output = (
            "Falsifying example: test_qux(\n"
            "    value='a(b)c',\n"
            "    other=42,\n"
            ")"
        )
        result = _extract_falsifying_examples(output)
        assert result == [("test_qux", "value='a(b)c', other=42")]


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

    def test_empty_string_raises(self):
        with pytest.raises(argparse.ArgumentTypeError, match="must be positive"):
            parse_duration("")

    def test_whitespace_only_raises(self):
        with pytest.raises(argparse.ArgumentTypeError, match="must be positive"):
            parse_duration("  ")

    def test_all_units(self):
        assert parse_duration("1h30m45s") == 5445

    def test_malformed_input_raises_argument_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_duration("abc")

    def test_malformed_trailing_text_raises_argument_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError):
            parse_duration("30mxyz")

    def test_trailing_bare_number_after_unit_raises(self):
        with pytest.raises(argparse.ArgumentTypeError, match="ambiguous.*explicit units"):
            parse_duration("5m30")

    def test_zero_duration_raises_argument_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError, match="must be positive"):
            parse_duration("0s")

    def test_negative_duration_raises_argument_type_error(self):
        with pytest.raises(argparse.ArgumentTypeError, match="must be positive"):
            parse_duration("-5m")


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


class TestInsertExamplePreservesFile:
    def test_preserves_existing_comments_and_whitespace(self, tmp_path):
        test_file = tmp_path / "test_mod.py"
        test_file.write_text(
            "from hypothesis import given\n"
            "from hypothesis import strategies as st\n"
            "\n"
            "# important: this comment must be preserved\n"
            "\n"
            "\n"
            "class TestTarget:\n"
            "    @given(value=st.text())\n"
            "    def test_target(self, value):\n"
            "        # another comment\n"
            "        pass\n"
        )
        result = _insert_example(test_file, "test_target", "value='x'")
        assert result is True
        text = test_file.read_text()
        assert "# important: this comment must be preserved" in text
        assert "# another comment" in text

    def test_rejects_unsafe_ast_nodes(self, tmp_path):
        test_file = tmp_path / "test_mod.py"
        test_file.write_text(
            "from hypothesis import given\n"
            "from hypothesis import strategies as st\n"
            "\n"
            "class TestTarget:\n"
            "    @given(value=st.text())\n"
            "    def test_target(self, value):\n"
            "        pass\n"
        )
        result = _insert_example(test_file, "test_target", "value=__import__('os')")
        assert result is False


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


class TestIsSafeExampleArgs:
    def test_simple_keyword_arg(self):
        assert _is_safe_example_args("value=42") is True

    def test_string_keyword_arg(self):
        assert _is_safe_example_args("value='hello'") is True

    def test_multiple_keyword_args(self):
        assert _is_safe_example_args("a=1, b='x'") is True

    def test_empty_string_value(self):
        assert _is_safe_example_args("operator=''") is True

    def test_nested_call_rejected(self):
        assert _is_safe_example_args("value=__import__('os')") is False

    def test_lambda_rejected(self):
        assert _is_safe_example_args("value=lambda: 1") is False

    def test_fstring_rejected(self):
        assert _is_safe_example_args("value=f'{__import__(\"os\")}'") is False

    def test_list_comprehension_rejected(self):
        assert _is_safe_example_args("value=[x for x in range(10)]") is False

    def test_syntax_error_rejected(self):
        assert _is_safe_example_args("value=)(") is False

    def test_plain_literal_list(self):
        assert _is_safe_example_args("value=[1, 2, 3]") is True

    def test_none_value(self):
        assert _is_safe_example_args("value=None") is True

    def test_boolean_value(self):
        assert _is_safe_example_args("value=True") is True

    def test_tuple_value(self):
        assert _is_safe_example_args("value=(1, 2)") is True

    def test_dict_value(self):
        assert _is_safe_example_args("value={'a': 1}") is True

    def test_set_value(self):
        assert _is_safe_example_args("value={1, 2, 3}") is True

    def test_negative_number(self):
        assert _is_safe_example_args("value=-1") is True

    def test_binary_bytes(self):
        assert _is_safe_example_args("value=b'hello'") is True

    def test_arithmetic_rejected(self):
        assert _is_safe_example_args("value=1+2") is True

    def test_generator_expression_rejected(self):
        assert _is_safe_example_args("value=(x for x in range(10))") is False

    def test_set_comprehension_rejected(self):
        assert _is_safe_example_args("value={x for x in range(10)}") is False

    def test_dict_comprehension_rejected(self):
        assert _is_safe_example_args("value={k: v for k, v in {}.items()}") is False

    def test_await_rejected(self):
        assert _is_safe_example_args("value=await foo()") is False

    def test_attribute_access_rejected(self):
        assert _is_safe_example_args("value=os.path") is False

    def test_subscript_rejected(self):
        assert _is_safe_example_args("value=x[0]") is False

    def test_variable_reference_rejected(self):
        assert _is_safe_example_args("value=some_var") is False

    def test_starred_rejected(self):
        assert _is_safe_example_args("value=*[1,2]") is False


class TestRunHypofuzz:
    def test_returns_true_on_timeout(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.wait.side_effect = subprocess.TimeoutExpired(cmd="uv", timeout=10)
        mock_proc.pid = 12345
        with (
            patch("fuzz.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("fuzz.os.getpgid", return_value=12345),
            patch("fuzz.os.killpg"),
            patch("fuzz._discover_python_fuzz_dirs", return_value=["packages/pkg/mod"]),
            patch("fuzz.Path.mkdir"),
        ):
            mock_proc.wait.side_effect = [
                subprocess.TimeoutExpired(cmd="uv", timeout=10),
                None,
            ]
            result = run_hypofuzz(10)
        assert result is True
        assert mock_popen.called

    def test_returns_true_on_clean_exit(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.wait.return_value = None
        mock_proc.returncode = 0
        with (
            patch("fuzz.subprocess.Popen", return_value=mock_proc),
            patch("fuzz._discover_python_fuzz_dirs", return_value=["packages/pkg/mod"]),
            patch("fuzz.Path.mkdir"),
            patch("fuzz.time.monotonic", side_effect=[0.0, 120.0]),
        ):
            result = run_hypofuzz(10)
        assert result is True

    def test_retries_on_early_crash(self, tmp_path):
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.wait.return_value = None
        mock_proc.stderr = MagicMock()
        mock_proc.stderr.read.return_value = b"crash output"
        call_count = [0]

        def side_effect_monotonic():
            val = call_count[0]
            call_count[0] += 1
            return float(val)

        with (
            patch("fuzz.subprocess.Popen", return_value=mock_proc) as mock_popen,
            patch("fuzz._discover_python_fuzz_dirs", return_value=["packages/pkg/mod"]),
            patch("fuzz.Path.mkdir"),
            patch("fuzz.time.monotonic", side_effect=side_effect_monotonic),
        ):
            result = run_hypofuzz(120)
        assert result is False
        assert mock_popen.call_count == 3


class TestRunHypothesisLoop:
    def test_runs_all_test_files(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        call_count = [0]

        def mock_monotonic():
            val = call_count[0]
            call_count[0] += 1
            return float(val * 10)

        with (
            patch("fuzz.subprocess.run", return_value=mock_result) as mock_run,
            patch("fuzz._discover_fuzz_test_files", return_value=["pkg/test_a.py", "pkg/test_b.py"]),
            patch("fuzz.time.monotonic", side_effect=mock_monotonic),
        ):
            result = run_hypothesis_loop(60)
        assert result is True
        assert mock_run.call_count >= 2

    def test_records_failures(self):
        mock_result = MagicMock()
        mock_result.returncode = 1
        call_count = [0]

        def mock_monotonic():
            val = call_count[0]
            call_count[0] += 1
            return float(val * 10)

        with (
            patch("fuzz.subprocess.run", return_value=mock_result),
            patch("fuzz._discover_fuzz_test_files", return_value=["pkg/hypothesis_test.py"]),
            patch("fuzz.time.monotonic", side_effect=mock_monotonic),
        ):
            result = run_hypothesis_loop(60)
        assert result is False

    def test_stops_when_budget_exhausted(self):
        mock_result = MagicMock()
        mock_result.returncode = 0
        call_count = [0]

        def mock_monotonic():
            val = call_count[0]
            call_count[0] += 1
            if val < 3:
                return 0.0
            return 100.0

        with (
            patch("fuzz.subprocess.run", return_value=mock_result) as mock_run,
            patch("fuzz._discover_fuzz_test_files", return_value=["a.py", "b.py", "c.py"]),
            patch("fuzz.time.monotonic", side_effect=mock_monotonic),
        ):
            result = run_hypothesis_loop(30)
        assert result is True
        assert mock_run.call_count <= 2


class TestRunPython:
    def test_calls_hypofuzz_then_hypothesis_loop(self):
        with (
            patch("fuzz.run_hypofuzz") as mock_hypofuzz,
            patch("fuzz.run_hypothesis_loop") as mock_loop,
            patch("fuzz.replay_and_inject_python", return_value=0),
            patch("fuzz.Path.exists", return_value=False),
            patch("fuzz.time.monotonic", side_effect=[0.0, 30.0]),
        ):
            run_python(120)
        mock_hypofuzz.assert_called_once()
        mock_loop.assert_called_once()

    def test_skips_hypothesis_loop_when_no_time_left(self):
        with (
            patch("fuzz.run_hypofuzz") as mock_hypofuzz,
            patch("fuzz.run_hypothesis_loop") as mock_loop,
            patch("fuzz.replay_and_inject_python", return_value=0),
            patch("fuzz.Path.exists", return_value=False),
            patch("fuzz.time.monotonic", side_effect=[0.0, 120.0]),
        ):
            run_python(120)
        mock_hypofuzz.assert_called_once()
        mock_loop.assert_not_called()

    def test_cleans_hypothesis_database(self, tmp_path):
        db_path = tmp_path / "examples"
        db_path.mkdir()
        (db_path / "data.json").write_text("{}")
        with (
            patch("fuzz.run_hypofuzz"),
            patch("fuzz.run_hypothesis_loop"),
            patch("fuzz.replay_and_inject_python", return_value=0),
            patch("fuzz.time.monotonic", side_effect=[0.0, 120.0]),
            patch("fuzz.Path.__truediv__", return_value=db_path),
        ):
            run_python(120)


class TestRunGoAll:
    def test_runs_all_targets(self):
        with (
            patch("fuzz.run_go_target", return_value=True) as mock_target,
            patch("fuzz._discover_go_fuzz_targets", return_value=[
                ("FuzzA", "./pkg/a/"), ("FuzzB", "./pkg/b/"),
            ]),
        ):
            result = run_go_all(120)
        assert result is True
        assert mock_target.call_count == 2

    def test_continues_past_failure(self):
        with (
            patch("fuzz.run_go_target", return_value=False) as mock_target,
            patch("fuzz._discover_go_fuzz_targets", return_value=[
                ("FuzzA", "./pkg/a/"), ("FuzzB", "./pkg/b/"),
            ]),
        ):
            result = run_go_all(120)
        assert result is False
        assert mock_target.call_count == 2

    def test_returns_true_when_no_targets(self):
        with patch("fuzz._discover_go_fuzz_targets", return_value=[]):
            result = run_go_all(120)
        assert result is True


class TestReplayAndInjectPython:
    def test_returns_zero_when_no_regressions(self):
        mock_result = MagicMock()
        mock_result.stdout = "all passed"
        mock_result.stderr = ""
        with (
            patch("fuzz.subprocess.run", return_value=mock_result),
            patch("fuzz._discover_python_fuzz_dirs", return_value=["packages/pkg/mod"]),
        ):
            count = replay_and_inject_python()
        assert count == 0

    def test_injects_found_regressions(self, tmp_path):
        test_file = tmp_path / "hypothesis_test.py"
        test_file.write_text(
            "from hypothesis import given\n"
            "from hypothesis import strategies as st\n"
            "\n"
            "class TestFoo:\n"
            "    @given(x=st.integers())\n"
            "    def test_bar(self, x):\n"
            "        assert x >= 0\n"
        )
        mock_result = MagicMock()
        mock_result.stdout = "Falsifying example: test_bar(x=-1)"
        mock_result.stderr = ""
        with (
            patch("fuzz.subprocess.run", return_value=mock_result),
            patch("fuzz._discover_python_fuzz_dirs", return_value=["packages/pkg/mod"]),
            patch("fuzz._find_test_file", return_value=test_file),
        ):
            count = replay_and_inject_python()
        assert count == 1
        assert "@example(x=-1)" in test_file.read_text()


class TestReplayAndInjectGo:
    def test_returns_zero_when_no_corpus(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "controller").mkdir()
        count = replay_and_inject_go()
        assert count == 0

    def test_injects_seed_from_corpus_file(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        controller = tmp_path / "controller"
        pkg_dir = controller / "pkg"
        testdata_dir = pkg_dir / "testdata"
        corpus_dir = testdata_dir / "fuzz" / "FuzzTarget"
        corpus_dir.mkdir(parents=True)
        corpus_file = corpus_dir / "entry1"
        corpus_file.write_text("go test fuzz v1\n[]byte(\"hello\")\n")
        fuzz_file = testdata_dir / "target_fuzz_test.go"
        fuzz_file.write_text(
            'package pkg\n\n'
            'import "testing"\n\n'
            'func FuzzTarget(f *testing.F) {\n'
            '\tf.Fuzz(func(t *testing.T, data []byte) {\n'
            '\t})\n'
            '}\n'
        )
        count = replay_and_inject_go()
        assert count == 1
        assert 'f.Add([]byte("hello"))' in fuzz_file.read_text()


class TestMain:
    def test_python_only_mode(self):
        with (
            patch("fuzz.run_python") as mock_run,
            patch("fuzz._discover_go_fuzz_targets", return_value=[]),
            patch("sys.argv", ["fuzz.py", "--python-only", "--time", "60s"]),
        ):
            result = main()
        assert result == 0
        mock_run.assert_called_once_with(60)

    def test_go_only_mode(self):
        with (
            patch("fuzz.run_go_all", return_value=True) as mock_go,
            patch("fuzz.replay_and_inject_go", return_value=0),
            patch("fuzz._discover_go_fuzz_targets", return_value=[
                ("FuzzA", "./pkg/"),
            ]),
            patch("sys.argv", ["fuzz.py", "--go-only", "--time", "60s"]),
        ):
            result = main()
        assert result == 0
        mock_go.assert_called_once_with(60)

    def test_list_go_targets(self, capsys):
        with (
            patch("fuzz._discover_go_fuzz_targets", return_value=[
                ("FuzzA", "./pkg/a/"), ("FuzzB", "./pkg/b/"),
            ]),
            patch("sys.argv", ["fuzz.py", "--list-go-targets"]),
        ):
            result = main()
        assert result == 0
        output = capsys.readouterr().out
        assert "FuzzA" in output
        assert "FuzzB" in output

    def test_single_go_target(self):
        with (
            patch("fuzz.run_go_target", return_value=True) as mock_target,
            patch("fuzz.replay_and_inject_go", return_value=0),
            patch("fuzz._discover_go_fuzz_targets", return_value=[
                ("FuzzA", "./pkg/a/"),
            ]),
            patch("sys.argv", ["fuzz.py", "--go-target", "FuzzA", "--time", "30s"]),
        ):
            result = main()
        assert result == 0
        mock_target.assert_called_once_with("FuzzA", "./pkg/a/", 30)

    def test_unknown_go_target_returns_error(self):
        with (
            patch("fuzz._discover_go_fuzz_targets", return_value=[
                ("FuzzA", "./pkg/a/"),
            ]),
            patch("sys.argv", ["fuzz.py", "--go-target", "FuzzMissing", "--time", "30s"]),
        ):
            result = main()
        assert result == 1

    def test_default_mode_runs_both(self):
        with (
            patch("fuzz.run_python") as mock_python,
            patch("fuzz.run_go_target", return_value=True) as mock_go_target,
            patch("fuzz.replay_and_inject_go", return_value=0),
            patch("fuzz._discover_go_fuzz_targets", return_value=[
                ("FuzzA", "./pkg/a/"),
            ]),
            patch("sys.argv", ["fuzz.py", "--time", "60s"]),
        ):
            result = main()
        assert result == 0
        mock_python.assert_called_once()
        mock_go_target.assert_called_once()

    def test_max_examples_per_test_flag(self):
        with (
            patch("fuzz.run_python") as mock_run,
            patch("fuzz._discover_go_fuzz_targets", return_value=[]),
            patch("sys.argv", ["fuzz.py", "--python-only", "--time", "60s", "--max-examples-per-test", "5"]),
        ):
            result = main()
        assert result == 0
        assert fuzz_module.MAX_EXAMPLES_PER_TEST == 5
        mock_run.assert_called_once_with(60)

    def test_max_examples_per_test_defaults_to_one(self):
        fuzz_module.MAX_EXAMPLES_PER_TEST = 1
        with (
            patch("fuzz.run_python") as mock_run,
            patch("fuzz._discover_go_fuzz_targets", return_value=[]),
            patch("sys.argv", ["fuzz.py", "--python-only", "--time", "60s"]),
        ):
            result = main()
        assert result == 0
        assert fuzz_module.MAX_EXAMPLES_PER_TEST == 1
