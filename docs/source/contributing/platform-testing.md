# Platform builds and tests

Python and Rust CI use the same checks across operating systems. Pull requests
run Python 3.14 on Ubuntu 24.04 and Windows Server 2025. Merge-queue and manual
runs cover Python 3.12, 3.13, and 3.14 on Ubuntu, macOS 15, and Windows.
Platform-specific tests and native APIs are gated at their module or test
boundary; a failing supported feature remains a failure.

## Python checks

The shared Python action builds source distributions and wheels for every
buildable workspace package. The action builds the native core wheel on
Windows, synchronizes all packages and extras from `uv.lock`, and checks CLI
startup. The test runner discovers the packages under `python/packages` and runs
each package's suite in an isolated environment. On Windows it runs only the
packages listed in `WINDOWS_PACKAGES` in `python/scripts/run_package_tests.py`;
a driver joins that list together with its Windows support. Package pytest
configuration and doctests are preserved. Native-core tests and CI-helper tests
run using the fully synchronized workspace environment.

Each suite produces a log, JUnit XML, and coverage report. `summary.json` records
the actual OS, architecture, Python version, result, and test/skip counts.
Failures, collection errors, environment errors, and timeouts fail the job;
pytest's explicit no-tests exit status is reported separately. Independent
suites continue after a failure. The default timeout is 900 seconds per suite,
including isolated environment setup. Cleanup contains Windows descendants
through `jumpstarter-proc` and Unix descendants through a process group.

From the repository's `python` directory, the same runner is available without
Make:

```console
uv sync --locked --all-packages --all-extras
uv build --all-packages --out-dir dist
uv run --no-sync python scripts/run_package_tests.py --logs-dir ../.e2e/python-tests --jobs 4
```

Use `--package jumpstarter-driver-network` to select a member (including one
not yet listed for Windows), or `--timeout` to adjust the per-suite deadline.
`make test` and `make pkg-test-<package>` keep their existing per-package
behavior on Linux and macOS. For local debugging with an already synchronized environment,
`--test-python <path-to-python>` reuses that interpreter; the report records that
isolation was disabled. CI does not use this override.

Windows needs Rust and the MSVC build tools for the native core package. See
the {download}`native package's build notes <../../../python/native/jumpstarter-core/docs/platforms/windows.md>`.
Linux and macOS jobs install their existing QEMU/Renode prerequisites; tests
requiring unavailable external tools report explicit skips.

## Rust checks

The shared Rust action runs formatting, `cargo check`, `cargo build`, Clippy,
and tests for the entire workspace and all targets, followed by doctests. Cargo
uses the checked-in lockfile. The Python interpreter for PyO3 is selected from
the job's Python matrix; its runtime library is made available to test binaries.
Extension-module mode is enabled by maturin for wheels, rather than for ordinary
Rust test executables.

Native APIs and Unix execution tests use Rust `cfg` gates. Compiling the
workspace does not imply that every backend exists on the selected OS; see
each crate's module documentation for availability.

## Hosted Windows coverage

The x64 hosted `windows-2025` image runs Windows Server 2025. It is the only
Windows CI target, keeping the matrix small while covering every supported
Python version. It does not qualify Windows 10 or 11. See the
[hosted runner inventory](https://github.com/actions/runner-images#available-images).

CI uses GitHub-hosted VMs exclusively. There are no self-hosted or desktop-runner
jobs. The Windows reports record the actual OS build and Python version so
Server results remain distinguishable from any future desktop qualification.
The native Windows end-to-end runners in `e2e/windows` run manually on a
Windows machine; see their
[instructions](https://github.com/jumpstarter-dev/jumpstarter/blob/main/e2e/windows/README.md).
