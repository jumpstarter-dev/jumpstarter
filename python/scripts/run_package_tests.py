"""Run every Python package suite with the same discovery and reports on each OS."""

from __future__ import annotations

import argparse
import json
import os
import platform
import signal
import subprocess
import sys
import tomllib
import uuid
import xml.etree.ElementTree as ET
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

WORKSPACE = Path(__file__).resolve().parents[1]

# Suites qualified on Windows. Packages join this list as their Windows support
# lands; drivers not listed here are not yet supported on Windows.
WINDOWS_PACKAGES = frozenset({
    "hatch-pin-jumpstarter",
    "jumpstarter",
    "jumpstarter-all",
    "jumpstarter-cli",
    "jumpstarter-cli-admin",
    "jumpstarter-cli-common",
    "jumpstarter-cli-driver",
    "jumpstarter-driver-composite",
    "jumpstarter-driver-power",
    "jumpstarter-imagehash",
    "jumpstarter-kubernetes",
    "jumpstarter-mcp",
    "jumpstarter-protocol",
    "jumpstarter-testing",
})


def discover_packages(workspace: Path) -> dict[str, Path]:
    packages = {}
    config = tomllib.loads((workspace / "pyproject.toml").read_text(encoding="utf-8"))["tool"]["uv"]["workspace"]
    excluded = {path.resolve() for pattern in config.get("exclude", []) for path in workspace.glob(pattern)}
    members = {path for pattern in config["members"] for path in workspace.glob(pattern)}
    for directory in sorted(members):
        manifest = directory / "pyproject.toml"
        # Example projects exercise live exporters and hardware; test packages only.
        if directory.parent.name != "packages" or directory.resolve() in excluded or not manifest.is_file():
            continue
        metadata = tomllib.loads(manifest.read_text(encoding="utf-8"))
        name = metadata["project"]["name"]
        if name in packages:
            raise ValueError(f"Duplicate package name: {name}")
        packages[name] = manifest.parent
    if not packages:
        raise ValueError(f"No Python packages found in {workspace}")
    return packages


def run_command(command: list[str], directory: Path, environment: dict, output, timeout: float) -> int:
    """Bound a suite's lifetime, including descendants left behind by tests."""
    guard = None
    if sys.platform == "win32":
        # Use the same Rust containment primitive as the exporter. The wrapper
        # cannot launch uv/pytest until Job Object assignment has succeeded.
        from jumpstarter_core.process import ChildProcessTree

        child_command = [sys.executable, str(Path(__file__).resolve()), "--worker", *command]
    else:
        child_command = command
    with subprocess.Popen(
        child_command,
        cwd=directory,
        env=environment,
        stdout=output,
        stderr=subprocess.STDOUT,
        stdin=subprocess.PIPE if sys.platform == "win32" else subprocess.DEVNULL,
        start_new_session=sys.platform != "win32",
    ) as child:
        try:
            if sys.platform == "win32":
                guard = ChildProcessTree(int(child._handle))
                child.stdin.write(b"1")
                child.stdin.close()
            try:
                return child.wait(timeout=timeout)
            except subprocess.TimeoutExpired:
                output.write(f"\nSuite exceeded its {timeout:g}-second timeout.\n")
                output.flush()
                return 124
        finally:
            if sys.platform == "win32":
                if guard is not None:
                    guard.close()
                if child.poll() is None:
                    child.kill()
            else:
                try:
                    os.killpg(child.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            child.wait()


def run_suite(name: str, directory: Path, command: list[str], logs: Path, timeout: float = 900) -> dict:
    log = logs / f"{name}.log"
    report = logs / f"{name}.xml"
    # A retry must not report an earlier successful run's XML as current evidence.
    report.unlink(missing_ok=True)
    (logs / f"{name}.coverage.xml").unlink(missing_ok=True)
    environment = dict(os.environ, PYTHONUNBUFFERED="1", PYTHONUTF8="1")
    with log.open("w", encoding="utf-8") as output:
        output.write(f"Working directory: {directory}\nCommand: {json.dumps(command)}\n")
        output.flush()
        returncode = run_command(command, directory, environment, output, timeout)
    counts = dict.fromkeys(("tests", "failures", "errors", "skipped"), 0)
    if report.exists():
        root = ET.parse(report).getroot()
        suites = [root] if root.tag == "testsuite" else root.findall("testsuite")
        for suite in suites:
            for key in counts:
                counts[key] += int(suite.get(key, "0"))
    # pytest's no-tests exit code is expected for metadata-only packages and
    # modules with explicit platform collection gates. Errors remain failures.
    status = "passed" if returncode == 0 else "no-tests" if returncode == 5 else "failed"
    if returncode in (0, 5) and not report.exists():
        status = "failed"
    return {"package": name, "status": status, "returncode": returncode, "log": str(log), **counts}


def suite_command(directory: Path, name: str, logs: Path, python: str | None, *, isolated: bool) -> list[str]:
    if python:
        command = [python, "-m", "pytest"]
    elif isolated:
        command = ["uv", "run", "--locked", "--isolated", "--all-extras", "python", "-m", "pytest"]
    else:
        # Native binding and CI-helper tests use the fully synced workspace;
        # the native package is deliberately not a workspace member.
        command = ["uv", "run", "--project", str(WORKSPACE), "--no-sync", "python", "-m", "pytest"]
    # A positional package directory overrides pytest's configured testpaths,
    # which can accidentally collect demos that require a live exporter.
    targets = [] if isolated else [directory]
    if name == "ci-helpers":
        targets = sorted(directory.glob("*_test.py"))
    temporary_root = logs / "tmp"
    temporary_root.mkdir(exist_ok=True)
    # Avoid pytest's shared per-user base (and stale ACLs) across parallel jobs.
    # A unique child also ensures pytest never clears another run's fixtures.
    basetemp = temporary_root / f"{name}-{uuid.uuid4().hex[:8]}"
    coverage_options = ["--cov=."]
    if name == "jumpstarter-core":
        # The native suite's cwd contains only tests. Clear inherited --cov=.
        # so this report measures the installed Python facades instead.
        coverage_options = ["--cov-reset", "--cov=jumpstarter_core"]
    return command + [
        *(str(target) for target in targets),
        "-ra",
        *coverage_options,
        "-o",
        f"cache_dir={logs / 'cache' / name}",
        f"--basetemp={basetemp}",
        f"--junitxml={logs / (name + '.xml')}",
        f"--cov-report=xml:{logs / (name + '.coverage.xml')}",
        f"--cov-report=html:{logs / 'html' / name}",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--logs-dir", type=Path, required=True)
    parser.add_argument("--jobs", type=int, default=4)
    parser.add_argument(
        "--timeout",
        type=float,
        default=900,
        help="Maximum seconds per suite, including environment setup",
    )
    parser.add_argument(
        "--package",
        action="append",
        help="Run a named package; defaults to all workspace members and helper tests",
    )
    parser.add_argument("--test-python", help="Reuse an already synced interpreter for local checks; CI uses isolation")
    args = parser.parse_args(argv)
    if args.jobs < 1:
        parser.error("--jobs must be positive")
    if args.timeout <= 0:
        parser.error("--timeout must be positive")
    packages = discover_packages(WORKSPACE)
    if args.package:
        unknown = set(args.package) - packages.keys()
        if unknown:
            parser.error(f"Unknown packages: {', '.join(sorted(unknown))}")
        packages = {name: packages[name] for name in dict.fromkeys(args.package)}
    elif sys.platform == "win32":
        unsupported = sorted(packages.keys() - WINDOWS_PACKAGES)
        print(f"Not yet supported on Windows, skipping: {', '.join(unsupported)}", flush=True)
        packages = {name: directory for name, directory in packages.items() if name in WINDOWS_PACKAGES}
    logs = args.logs_dir.resolve()
    logs.mkdir(parents=True, exist_ok=True)
    (logs / "summary.json").unlink(missing_ok=True)
    suites = [(name, directory, True) for name, directory in packages.items()]
    if not args.package:
        suites += [
            ("jumpstarter-core", WORKSPACE / "native/jumpstarter-core/tests", False),
            ("ci-helpers", WORKSPACE / "scripts", False),
        ]
    results = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        pending = {
            pool.submit(
                run_suite,
                name,
                directory,
                suite_command(directory, name, logs, args.test_python, isolated=isolated),
                logs,
                args.timeout,
            ): name
            for name, directory, isolated in suites
        }
        for future in as_completed(pending):
            name = pending[future]
            try:
                result = future.result()
            except (OSError, ValueError, ET.ParseError) as error:
                result = {"package": name, "status": "failed", "error": str(error)}
            results.append(result)
            print(f"[{result['status']}] {name}", flush=True)
            if result["status"] == "failed" and "log" in result:
                print(Path(result["log"]).read_text(encoding="utf-8", errors="replace"), flush=True)
    summary = {
        "os": platform.platform(),
        "python": sys.version,
        "machine": platform.machine(),
        "isolated": args.test_python is None,
        "suites": sorted(results, key=lambda result: result["package"]),
    }
    (logs / "summary.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    failed = [result["package"] for result in results if result["status"] == "failed"]
    print(f"Completed {len(results)} suites; {len(failed)} failed. Reports: {logs}")
    return int(bool(failed))


if __name__ == "__main__":
    if sys.argv[1:2] == ["--worker"]:
        if sys.stdin.buffer.read(1) != b"1":
            raise SystemExit("Test runner startup gate closed before assignment")
        raise SystemExit(subprocess.call(sys.argv[2:], stdin=subprocess.DEVNULL))
    raise SystemExit(main())
