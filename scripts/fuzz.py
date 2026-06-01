#!/usr/bin/env python3
"""Fuzz test runner that dispatches Python and Go fuzz targets within a time budget.

Both languages follow the same regression model:
1. Fuzzer runs, writes discoveries to a working directory (.hypothesis/ or testdata/fuzz/)
2. After fuzzing, replay failures and inject them as source-level regression tests
   - Python: @example() decorators on @given test functions
   - Go: f.Add() seed corpus entries in Fuzz* functions
3. Working directories stay gitignored; regression tests live in committed source

This means `git diff` after a fuzz run shows exactly what was found, and
`pytest` / `go test` replay those regressions on every future run.
"""

import argparse
import ast
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from pathlib import Path

_FUZZ_FUNC_RE = re.compile(r"^func (Fuzz\w+)\(f \*testing\.F\)", re.MULTILINE)


def _discover_go_fuzz_targets() -> list[tuple[str, str]]:
    controller_dir = Path("controller")
    if not controller_dir.is_dir():
        return []
    targets: list[tuple[str, str]] = []
    for fuzz_file in sorted(controller_dir.rglob("*_fuzz_test.go")):
        pkg = "./" + str(fuzz_file.parent.relative_to(controller_dir)) + "/"
        for match in _FUZZ_FUNC_RE.finditer(fuzz_file.read_text()):
            targets.append((match.group(1), pkg))
    return sorted(targets)

PYTEST_FILTER = "hypothesis_test or robustness_test"

FUZZ_TEST_PATTERNS = ("hypothesis_test", "robustness_test")

MAX_EXAMPLES_PER_TEST = 1  # default: keep one regression per test to minimize noise; override via --max-examples-per-test

HYPOFUZZ_STARTUP_GRACE_SECONDS = 60


def _discover_python_fuzz_dirs() -> list[str]:
    packages_dir = Path("python/packages")
    if not packages_dir.is_dir():
        return []
    dirs: list[str] = []
    for pkg in sorted(packages_dir.iterdir()):
        if not pkg.is_dir():
            continue
        for src_dir in pkg.iterdir():
            if not src_dir.is_dir() or src_dir.name.startswith("."):
                continue
            has_fuzz = any(
                any(pat in f.name for pat in FUZZ_TEST_PATTERNS)
                for f in src_dir.rglob("*_test.py")
            )
            if has_fuzz:
                dirs.append(str(src_dir.relative_to("python")))
    return sorted(dirs)


def parse_duration(value: str) -> int:
    total = 0
    rest = value.strip()
    has_unit = False
    for suffix, multiplier in [("h", 3600), ("m", 60), ("s", 1)]:
        if suffix in rest:
            has_unit = True
            parts = rest.split(suffix, 1)
            try:
                total += int(parts[0]) * multiplier
            except ValueError:
                raise argparse.ArgumentTypeError(
                    f"invalid duration: {value!r} (expected format like 30m, 2h, 1h30m, or 90)"
                )
            rest = parts[1]
    if rest.strip():
        if has_unit:
            raise argparse.ArgumentTypeError(
                f"invalid duration: {value!r} (trailing bare number after unit suffix is ambiguous, use explicit units like 5m30s)"
            )
        try:
            total += int(rest)
        except ValueError:
            raise argparse.ArgumentTypeError(
                f"invalid duration: {value!r} (expected format like 30m, 2h, 1h30m, or 90)"
            )
    if total <= 0:
        raise argparse.ArgumentTypeError(
            f"duration must be positive, got {total}s from {value!r}"
        )
    return total


# --- Python fuzzing ---


def run_hypofuzz(seconds: int) -> bool:
    print(f"Python fuzz (HypoFuzz): coverage-guided for {seconds}s", flush=True)
    workers = max(1, (os.cpu_count() or 2) - 2)
    db_path = Path("python") / ".hypothesis" / "examples"
    db_path.mkdir(parents=True, exist_ok=True)

    max_attempts = 3
    startup_grace = HYPOFUZZ_STARTUP_GRACE_SECONDS
    for attempt in range(max_attempts):
        is_final_attempt = attempt == max_attempts - 1
        start = time.monotonic()
        proc = subprocess.Popen(
            [
                "uv", "run", "hypothesis", "fuzz",
                "--no-dashboard",
                "-n", str(workers),
                "--",
                *_discover_python_fuzz_dirs(),
                "-k", PYTEST_FILTER,
                "--no-cov",
            ],
            cwd="python",
            stderr=subprocess.PIPE if is_final_attempt else subprocess.DEVNULL,
            start_new_session=True,
        )
        try:
            proc.wait(timeout=seconds)
        except subprocess.TimeoutExpired:
            try:
                pgid = os.getpgid(proc.pid)
                os.killpg(pgid, signal.SIGINT)
                try:
                    proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    os.killpg(pgid, signal.SIGKILL)
                    proc.wait()
            except (ProcessLookupError, OSError):
                proc.wait()
            print(f"HypoFuzz: stopped after {seconds}s (budget exhausted)")
            return True

        elapsed = time.monotonic() - start
        if proc.returncode != 0 and elapsed < startup_grace and not is_final_attempt:
            print(f"HypoFuzz: crashed during startup (code {proc.returncode}), retrying ({attempt + 1}/{max_attempts})")
            continue

        if proc.returncode == 0:
            print("HypoFuzz: completed (no more tests to fuzz)")
        else:
            print(f"HypoFuzz: exited with code {proc.returncode} (found failures)")
            if is_final_attempt and proc.stderr:
                stderr_output = proc.stderr.read().decode(errors="replace").strip()
                if stderr_output:
                    print(f"HypoFuzz stderr:\n{stderr_output}", file=sys.stderr)
        return True

    return True


def _discover_fuzz_test_files() -> list[str]:
    files = []
    for d in _discover_python_fuzz_dirs():
        base = Path("python") / d
        for p in base.rglob("*_test.py"):
            name = p.name
            if any(pat in name for pat in FUZZ_TEST_PATTERNS):
                files.append(str(p.relative_to("python")))
    return sorted(files)


def run_hypothesis_loop(seconds: int) -> bool:
    deadline = time.monotonic() + seconds
    test_files = _discover_fuzz_test_files()
    passed = 0
    failed_files = []

    print(f"Python fuzz (Hypothesis loop): {seconds}s, {len(test_files)} test files", flush=True)
    while time.monotonic() < deadline:
        passed += 1
        remaining = int(deadline - time.monotonic())
        if remaining <= 0:
            break
        print(f"--- pass {passed} ({remaining}s remaining) ---", flush=True)
        for tf in test_files:
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                break
            try:
                result = subprocess.run(
                    [
                        "uv", "run", "pytest",
                        tf,
                        "--maxfail=1", "--no-cov", "-q",
                    ],
                    cwd="python",
                    env={**os.environ, "HYPOTHESIS_PROFILE": "fuzz"},
                    timeout=remaining + 30,
                )
            except subprocess.TimeoutExpired:
                break
            if result.returncode not in (0, 5):
                short = Path(tf).name
                if short not in failed_files:
                    failed_files.append(short)
                    print(f"  FAILURE in {short}")

    print(f"Hypothesis loop: {passed} passes, {len(failed_files)} files with failures")
    if failed_files:
        for f in failed_files:
            print(f"  - {f}")
    return True


def _find_test_file(func_name: str) -> Path | None:
    for d in _discover_python_fuzz_dirs():
        base = Path("python") / d
        for p in base.rglob("*_test.py"):
            try:
                text = p.read_text()
            except OSError:
                continue
            if re.search(rf"def {re.escape(func_name)}\(", text):
                return p
    return None


def _count_existing_examples(text: str, func_name: str) -> int:
    before_func = text.split(f"def {func_name}(")[0] if f"def {func_name}(" in text else ""
    lines = before_func.splitlines()
    count = 0
    for line in reversed(lines):
        stripped = line.strip()
        if stripped.startswith("@example("):
            count += 1
        elif stripped.startswith("@") or stripped == "" or stripped.startswith("#"):
            continue
        else:
            break
    return count


_SAFE_AST_NODES = (
    ast.Expression,
    ast.keyword,
    ast.Constant,
    ast.List,
    ast.Tuple,
    ast.Dict,
    ast.Set,
    ast.UnaryOp,
    ast.USub,
    ast.UAdd,
    ast.BinOp,
    ast.Add,
    ast.Sub,
    ast.Mult,
    ast.Div,
    ast.Mod,
    ast.Pow,
    ast.FloorDiv,
    ast.Load,
)


def _is_safe_example_args(example_args: str) -> bool:
    try:
        tree = ast.parse(f"f({example_args})", mode="eval")
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            if not (isinstance(node.func, ast.Name) and node.func.id == "f"):
                return False
            continue
        if isinstance(node, ast.Name):
            if node.id != "f":
                return False
            continue
        if not isinstance(node, _SAFE_AST_NODES):
            return False
    return True


def _insert_example(path: Path, func_name: str, example_args: str) -> bool:
    if not _is_safe_example_args(example_args):
        print(f"  skipping unsafe example args for {func_name}: {example_args!r}")
        return False

    text = path.read_text()
    decorator = f"@example({example_args})"

    if decorator in text:
        return False

    if _count_existing_examples(text, func_name) >= MAX_EXAMPLES_PER_TEST:
        return False

    def_pattern = re.compile(rf"^([ \t]*)def {re.escape(func_name)}\(", re.MULTILINE)
    def_match = def_pattern.search(text)
    if not def_match:
        return False

    indent = def_match.group(1)
    before = text[:def_match.start()]
    lines_before = before.rstrip("\n").splitlines()

    given_start = None
    depth = 0
    for i in range(len(lines_before) - 1, -1, -1):
        stripped = lines_before[i].strip()
        depth += stripped.count(")") - stripped.count("(")
        if depth <= 0:
            if stripped.startswith("@given"):
                given_start = i
            break

    if given_start is not None:
        insert_offset = sum(len(l) + 1 for l in lines_before[:given_start])
        text = text[:insert_offset] + f"{indent}{decorator}\n" + text[insert_offset:]
    else:
        text = text[:def_match.start()] + f"{indent}{decorator}\n" + text[def_match.start():]

    has_example_import = bool(
        re.search(r"from hypothesis import\b.*\bexample\b", text)
    )
    if not has_example_import:
        import_match = re.search(r"from hypothesis import (.+)", text)
        if import_match:
            text = text.replace(
                import_match.group(0),
                f"from hypothesis import example, {import_match.group(1)}",
                1,
            )
        else:
            text = f"from hypothesis import example\n{text}"

    path.write_text(text)
    return True


def _clean_example_args(raw_args: str) -> str:
    cleaned = re.sub(r"\s*self=<[^>]*>,?\s*", " ", raw_args)
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned.strip(", ")
    return cleaned


def _extract_falsifying_examples(output: str) -> list[tuple[str, str]]:
    stripped = re.sub(r"^[ \t]*E {2,}", "", output, flags=re.MULTILINE)
    example_re = re.compile(
        r"Falsifying example: (\w+)\((.*?)\)\s*(?:\n|\Z)",
        re.DOTALL,
    )
    seen = set()
    results = []
    for m in example_re.finditer(stripped):
        func_name = m.group(1)
        raw_args = m.group(2)
        cleaned = _clean_example_args(raw_args)
        if not cleaned:
            continue
        key = (func_name, cleaned)
        if key not in seen:
            seen.add(key)
            results.append(key)
    return results


def replay_and_inject_python() -> int:
    print("\n=== Replaying Hypothesis database for regressions ===", flush=True)
    result = subprocess.run(
        [
            "uv", "run", "pytest",
            *_discover_python_fuzz_dirs(),
            "-k", PYTEST_FILTER,
            "--no-cov", "-v", "--tb=long",
        ],
        cwd="python",
        capture_output=True,
        text=True,
    )

    output = result.stdout + "\n" + result.stderr
    regressions = _extract_falsifying_examples(output)

    if not regressions:
        print("No regressions found in Hypothesis database.")
        return 0

    injected = 0
    for func_name, example_args in regressions:
        path = _find_test_file(func_name)
        if not path:
            print(f"  could not find file for {func_name}, skipping")
            continue
        if _insert_example(path, func_name, example_args):
            injected += 1
            print(f"  added @example({example_args}) to {path}::{func_name}")
        else:
            existing = _count_existing_examples(path.read_text(), func_name)
            if existing >= MAX_EXAMPLES_PER_TEST:
                print(f"  {func_name} already has {existing} @example decorators (max {MAX_EXAMPLES_PER_TEST}), skipping")
            else:
                print(f"  @example already present for {func_name}")

    if injected:
        print(f"\n{injected} regression @example(s) injected into test files.")
        print("Review with 'git diff python/', then commit.")
    return injected


def run_python(seconds: int) -> bool:
    start = time.monotonic()
    run_hypofuzz(min(seconds, max(60, seconds // 3)))
    elapsed = int(time.monotonic() - start)
    remaining = seconds - elapsed
    if remaining > 30:
        run_hypothesis_loop(remaining)

    replay_and_inject_python()

    db_path = Path("python") / ".hypothesis" / "examples"
    if db_path.exists():
        shutil.rmtree(db_path)
    return True


# --- Go fuzzing ---


def run_go_target(name: str, pkg: str, seconds: int) -> bool:
    print(f"Go fuzz: {name} in {pkg} for {seconds}s", flush=True)
    result = subprocess.run(
        [
            "go", "test",
            "-run=^$",
            f"-fuzz={name}",
            pkg,
            f"-fuzztime={seconds}s",
        ],
        cwd="controller",
    )
    if result.returncode != 0:
        print(f"FAILURE: {name} found a crash")
        return False
    return True


def run_go_all(seconds: int) -> bool:
    targets = _discover_go_fuzz_targets()
    if not targets:
        print("Go fuzz: no targets discovered, skipping")
        return True
    per_target = max(10, seconds // len(targets))
    print(f"Go fuzz: {len(targets)} targets, {per_target}s each")
    all_passed = True
    for name, pkg in targets:
        if not run_go_target(name, pkg, per_target):
            all_passed = False
    return all_passed


def _parse_go_corpus_file(path: Path) -> str | None:
    lines = path.read_text().splitlines()
    if not lines or not lines[0].startswith("go test fuzz"):
        return None
    values = []
    for line in lines[1:]:
        line = line.strip()
        if line:
            values.append(line)
    return ", ".join(values) if values else None


def _count_existing_go_seeds(text: str, fuzz_name: str) -> int:
    pattern = re.compile(rf"{re.escape(fuzz_name)}.*?f\.Fuzz", re.DOTALL)
    m = pattern.search(text)
    if not m:
        return 0
    block = m.group(0)
    return block.count("f.Add(")


def _inject_go_seed(fuzz_file: Path, fuzz_name: str, seed_args: str) -> bool:
    text = fuzz_file.read_text()
    add_call = f"\tf.Add({seed_args})"

    if add_call in text:
        return False

    if _count_existing_go_seeds(text, fuzz_name) >= MAX_EXAMPLES_PER_TEST:
        return False

    pattern = re.compile(
        rf"(func {re.escape(fuzz_name)}\(f \*testing\.F\) \{{[^}}]*?)(f\.Fuzz)",
        re.DOTALL,
    )
    m = pattern.search(text)
    if not m:
        return False

    text = text[:m.end(1)] + add_call + "\n\t" + text[m.start(2):]
    fuzz_file.write_text(text)
    return True


def replay_and_inject_go() -> int:
    print("\n=== Checking Go fuzz corpus for crash reproducers ===", flush=True)
    corpus_root = Path("controller")
    corpus_dirs = list(corpus_root.rglob("testdata/fuzz"))
    if not corpus_dirs:
        print("No Go fuzz corpus found.")
        return 0

    injected = 0
    for corpus_dir in corpus_dirs:
        for fuzz_dir in sorted(corpus_dir.iterdir()):
            if not fuzz_dir.is_dir():
                continue
            fuzz_name = fuzz_dir.name

            fuzz_file = None
            for candidate in fuzz_dir.parent.parent.glob("*_fuzz_test.go"):
                if fuzz_name in candidate.read_text():
                    fuzz_file = candidate
                    break

            if not fuzz_file:
                continue

            for entry in sorted(fuzz_dir.iterdir()):
                if not entry.is_file():
                    continue
                seed_args = _parse_go_corpus_file(entry)
                if seed_args and _inject_go_seed(fuzz_file, fuzz_name, seed_args):
                    injected += 1
                    print(f"  added f.Add({seed_args}) to {fuzz_file.name}::{fuzz_name}")

    if injected:
        print(f"\n{injected} regression f.Add() seed(s) injected into Go fuzz tests.")
        print("Review with 'git diff controller/', then commit.")
    else:
        print("No new Go crash reproducers to inject.")
    return injected


# --- Main dispatch ---


def main() -> int:
    parser = argparse.ArgumentParser(description="Run fuzz tests within a time budget")
    parser.add_argument("--time", default="30m", help="total time budget (e.g. 30m, 2h, 48h)")
    parser.add_argument("--python-only", action="store_true")
    parser.add_argument("--go-only", action="store_true")
    parser.add_argument("--go-target", help="run a single Go fuzz target by name")
    parser.add_argument("--list-go-targets", action="store_true", help="print Go targets as JSON for CI matrix")
    parser.add_argument(
        "--max-examples-per-test", type=int, default=1,
        help="max regression examples to inject per test function (default: 1)",
    )
    args = parser.parse_args()

    global MAX_EXAMPLES_PER_TEST
    MAX_EXAMPLES_PER_TEST = args.max_examples_per_test

    go_targets = _discover_go_fuzz_targets()

    if args.list_go_targets:
        targets = [{"name": name, "pkg": pkg} for name, pkg in go_targets]
        print(json.dumps(targets))
        return 0

    total = parse_duration(args.time)

    if args.go_target:
        match = [(n, p) for n, p in go_targets if n == args.go_target]
        if not match:
            print(f"Unknown Go target: {args.go_target}", file=sys.stderr)
            print(f"Available: {', '.join(n for n, _ in go_targets)}", file=sys.stderr)
            return 1
        name, pkg = match[0]
        ok = run_go_target(name, pkg, total)
        replay_and_inject_go()
        return 0 if ok else 1

    if args.python_only:
        print(f"Python fuzz budget: {args.time} ({total}s)")
        run_python(total)
        return 0

    if args.go_only:
        print(f"Go fuzz budget: {args.time} ({total}s)")
        ok = run_go_all(total)
        replay_and_inject_go()
        return 0 if ok else 1

    slots = 1 + len(go_targets)
    per_slot = max(30, total // slots)
    print(f"Fuzz budget: {args.time} ({total}s) -- {slots} slots, {per_slot}s each")
    run_python(per_slot)
    all_passed = True
    for name, pkg in go_targets:
        if not run_go_target(name, pkg, per_slot):
            all_passed = False
    replay_and_inject_go()
    return 0 if all_passed else 1


if __name__ == "__main__":
    sys.exit(main())
