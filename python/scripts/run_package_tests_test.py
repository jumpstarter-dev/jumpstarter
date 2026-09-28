import json
import sys

import pytest
import run_package_tests as runner


def test_discovers_new_packages_but_not_examples(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[tool.uv.workspace]\nmembers = ["packages/*", "examples/*"]\n',
        encoding="utf-8",
    )
    package = tmp_path / "packages/new-driver"
    package.mkdir(parents=True)
    (package / "pyproject.toml").write_text('[project]\nname = "new-driver"\n', encoding="utf-8")
    example = tmp_path / "examples/hardware-example"
    example.mkdir(parents=True)
    (example / "pyproject.toml").write_text('[project]\nname = "hardware-example"\n', encoding="utf-8")
    assert runner.discover_packages(tmp_path) == {"new-driver": package}


def test_windows_packages_exist():
    assert runner.WINDOWS_PACKAGES <= runner.discover_packages(runner.WORKSPACE).keys()


def test_timeout_is_reported_as_failure(tmp_path):
    result = runner.run_suite(
        "slow",
        tmp_path,
        [sys.executable, "-c", "import time; time.sleep(60)"],
        tmp_path,
        timeout=0.2,
    )
    assert result["status"] == "failed"
    assert result["returncode"] == 124
    assert "timeout" in (tmp_path / "slow.log").read_text(encoding="utf-8")


def test_package_discovery_preserves_configured_testpaths(tmp_path):
    (tmp_path / "pyproject.toml").write_text('[tool.pytest.ini_options]\ntestpaths = ["tests"]\n', encoding="utf-8")
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_unit.py").write_text("def test_unit():\n    assert True\n", encoding="utf-8")
    demo = tmp_path / "demo"
    demo.mkdir()
    (demo / "test_device.py").write_text('raise RuntimeError("requires a live device")\n', encoding="utf-8")
    command = runner.suite_command(tmp_path, "example", tmp_path, sys.executable, isolated=True)
    result = runner.run_suite("example", tmp_path, command, tmp_path)
    assert result["status"] == "passed", (tmp_path / "example.log").read_text(encoding="utf-8")
    assert result["tests"] == 1


@pytest.mark.parametrize("exit_code, status", [(0, "passed"), (5, "no-tests"), (1, "failed"), (2, "failed")])
def test_suite_exit_and_reports(tmp_path, exit_code, status):
    report = tmp_path / "example.xml"
    command = [
        sys.executable,
        "-c",
        (
            "from pathlib import Path; import sys; "
            f'Path({str(report)!r}).write_text(\'<testsuites><testsuite tests="2" skipped="1"/>\''
            "'</testsuites>'); "
            f"sys.exit({exit_code})"
        ),
    ]
    result = runner.run_suite("example", tmp_path, command, tmp_path)
    assert result["status"] == status
    assert result["tests"] == 2
    assert result["skipped"] == 1


def test_stale_report_does_not_hide_a_broken_invocation(tmp_path):
    (tmp_path / "example.xml").write_text('<testsuite tests="100"/>', encoding="utf-8")
    result = runner.run_suite("example", tmp_path, [sys.executable, "-c", "pass"], tmp_path)
    assert result["status"] == "failed"
    assert result["tests"] == 0


def test_all_results_are_recorded_after_a_failure(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "discover_packages", lambda _: {"one": tmp_path, "two": tmp_path})
    monkeypatch.setattr(runner, "suite_command", lambda *args, **kwargs: [])

    def run(name, *args):
        if name == "one":
            raise OSError("cannot start test process")
        return {"package": name, "status": "passed"}

    monkeypatch.setattr(runner, "run_suite", run)
    assert runner.main(["--logs-dir", str(tmp_path), "--package", "one", "--package", "two"]) == 1
    results = json.loads((tmp_path / "summary.json").read_text(encoding="utf-8"))["suites"]
    assert [result["status"] for result in results] == ["failed", "passed"]
