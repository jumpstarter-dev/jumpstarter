# Testing

This guide explains how to write and run hardware tests using
[pytest](https://docs.pytest.org/) with Jumpstarter. The `jumpstarter-testing`
package provides a base class that handles connection management, letting you
focus on test logic.

## Prerequisites

Install the following packages in your Python environment:

- `jumpstarter-testing` - `pytest` integration for Jumpstarter
- `pytest` - the test framework

Install any driver packages your tests require (for example,
`jumpstarter-driver-power` or `jumpstarter-driver-opendal`). The examples in this
guide that use console interaction with `PexpectAdapter` require
`jumpstarter-driver-network`.

## The JumpstarterTest base class

`JumpstarterTest` is a `pytest` class that provides a `client` fixture scoped to
the test class. It connects to a Jumpstarter {term}`exporter` in one of two ways:

1. **Shell mode**: when the `JUMPSTARTER_HOST` environment variable is set (for
   example, inside a `jmp shell` session), it connects to the {term}`exporter` from that
   environment.
2. **{term}`Lease` mode**: when `JUMPSTARTER_HOST` is not set, it loads the default
   client config and acquires a {term}`lease` using the `selector` class variable.

```{literalinclude} ../../../examples/getting-started/testing_basic.py
:language: python
```

The `selector` class variable is a comma-separated list of {term}`label selector`s that
identify which {term}`exporter` to {term}`lease`. It is only used when running outside a shell
{term}`session`.

The `client` object exposes driver interfaces as nested attributes. In the
example above, `dutlink` is a composite driver that provides child drivers like
`power` and `storage`. The exact attribute names depend on your exporter config.

## Running tests

### Inside a shell session

Start an {term}`exporter shell` first, then run `pytest` inside it:

```console
$ jmp shell --exporter my-exporter
$ pytest test_my_device.py
$ exit
```

In this mode, `JumpstarterTest` detects `JUMPSTARTER_HOST` and connects to the
active {term}`exporter`. The `selector` class variable is ignored.

### With automatic lease acquisition

Run `pytest` directly without a shell {term}`session`. `JumpstarterTest` loads the default
client configuration and acquires a {term}`lease` matching your `selector`:

```console
$ pytest test_my_device.py
```

This requires a configured client (see
[Setup Distributed Mode](../setup/distributed-mode.md)).

## Writing custom fixtures

Create additional `pytest` fixtures that build on the `client` fixture provided by
`JumpstarterTest`. This is useful for setting up {term}`device` state or wrapping driver
interfaces.

```{literalinclude} ../../../examples/getting-started/testing_fixtures.py
:language: python
```

The `client` fixture has class scope, so it is shared across all test methods in
a class. Custom fixtures can have any scope up to `class`.

Serial console interaction uses `PexpectAdapter` from `jumpstarter-driver-network`,
which wraps a driver client class into a [pexpect](https://pexpect.readthedocs.io/)
`fdspawn` object. Use `expect()` and `sendline()` instead of `read_until()`.

## Combining with pytest features

### Logging

Use Python's `logging` module to add diagnostic output to tests. `pytest` captures
log output by default and displays it for failing tests.

```{literalinclude} ../../../examples/getting-started/testing_logging.py
:language: python
```

### Skipping and marking tests

Use standard `pytest` markers to control test execution:

```{literalinclude} ../../../examples/getting-started/testing_markers.py
:language: python
```

Run only tests without the `slow` marker:

```console
$ pytest -m "not slow"
```

### Fixtures for setup and teardown

A fixture that manages storage flashing before tests:

```{literalinclude} ../../../examples/getting-started/testing_setup_teardown.py
:language: python
```

## CI integration

`JumpstarterTest` works in CI pipelines. Use either shell mode or {term}`lease` mode
depending on your setup.

### Shell mode in CI

````{tab} GitHub
```{code-block} yaml
# .github/workflows/hardware-test.yml
jobs:
  hardware-test:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v3
      - name: Run tests in shell
        run: |
          jmp shell --exporter my-exporter -- pytest tests/
```
````

````{tab} GitLab
```{code-block} yaml
# .gitlab-ci.yml
hardware-test:
  tags:
    - self-hosted
  script:
    - jmp shell --exporter my-exporter -- pytest tests/
```
````

### Lease mode in CI

When tests use `selector` and run outside a shell, configure the client before
running `pytest`:

````{tab} GitHub
```{code-block} yaml
# .github/workflows/hardware-test.yml
jobs:
  hardware-test:
    runs-on: self-hosted
    steps:
      - uses: actions/checkout@v3
      - name: Configure client
        run: jmp config client use ci-client
      - name: Run tests
        run: pytest tests/
```
````

````{tab} GitLab
```{code-block} yaml
# .gitlab-ci.yml
hardware-test:
  tags:
    - self-hosted
  script:
    - jmp config client use ci-client
    - pytest tests/
```
````

## Troubleshooting

**Tests fail with `RuntimeError` about missing environment**
: Ensure you are either running inside a `jmp shell` session or have a default
  client configured with `jmp config client use <name>`.

**Lease acquisition times out**
: Verify that an {term}`exporter` matching your `selector` labels is running and
  registered with the {term}`controller`. Check available {term}`exporter`s with
  `jmp get exporters`.

**`client` fixture setup fails**
: Confirm that `jumpstarter-testing` is installed, and either: `JUMPSTARTER_HOST`
  is set correctly in shell mode, or a valid default client is configured for
  {term}`lease` mode.
