# Testing with pytest

This guide explains how to write and run hardware tests using
[pytest](https://docs.pytest.org/) with Jumpstarter. The `jumpstarter-testing`
package provides a base class that handles connection management, letting you
focus on test logic.

## Prerequisites

Install the following packages in your Python environment:

- `jumpstarter-testing` - pytest integration for Jumpstarter
- `pytest` - the test framework

Install any driver packages your tests require (for example,
`jumpstarter-driver-power` or `jumpstarter-driver-opendal`).

## The JumpstarterTest base class

`JumpstarterTest` is a pytest class that provides a `client` fixture scoped to
the test class. It connects to a Jumpstarter exporter in one of two ways:

1. **Shell mode**: when the `JUMPSTARTER_HOST` environment variable is set (for
   example, inside a `jmp shell` session), it connects to the exporter from that
   environment.
2. **Lease mode**: when `JUMPSTARTER_HOST` is not set, it loads the default
   client configuration and acquires a lease using the `selector` class variable.

```python
from jumpstarter_testing.pytest import JumpstarterTest


class TestPowerCycle(JumpstarterTest):
    selector = "board=rpi4"

    def test_power_on(self, client):
        client.power.on()

    def test_power_off(self, client):
        client.power.off()
```

The `selector` class variable is a comma-separated list of label selectors that
identify which exporter to lease. It is only used when running outside a shell
session.

## Running tests

### Inside a shell session

Start an exporter shell first, then run pytest inside it:

```console
$ jmp shell --exporter my-exporter
$ pytest test_my_device.py
$ exit
```

In this mode, `JumpstarterTest` detects `JUMPSTARTER_HOST` and connects to the
active exporter. The `selector` class variable is ignored.

### With automatic lease acquisition

Run pytest directly without a shell session. `JumpstarterTest` loads the default
client configuration and acquires a lease matching your `selector`:

```console
$ pytest test_my_device.py
```

This requires a configured client (see
[Setup Distributed Mode](setup-distributed-mode.md)).

## Writing custom fixtures

Create additional pytest fixtures that build on the `client` fixture provided by
`JumpstarterTest`. This is useful for setting up device state or wrapping driver
interfaces.

```python
import pytest
from jumpstarter_testing.pytest import JumpstarterTest


class TestBoot(JumpstarterTest):
    selector = "board=rpi4"

    @pytest.fixture()
    def powered_device(self, client):
        client.power.off()
        client.storage.write_local_file("firmware.img")
        client.storage.dut()
        client.power.on()
        yield client
        client.power.off()

    def test_device_boots(self, powered_device):
        powered_device.serial.read_until("login:")
```

The `client` fixture has class scope, so it is shared across all test methods in
a class. Custom fixtures can have any scope up to `class`.

## Combining with pytest features

### Logging

Use Python's `logging` module to add diagnostic output to tests. Pytest captures
log output by default and displays it for failing tests.

```python
import logging

from jumpstarter_testing.pytest import JumpstarterTest

log = logging.getLogger(__name__)


class TestDiagnostics(JumpstarterTest):
    selector = "board=rpi4"

    def test_firmware_version(self, client):
        client.power.on()
        version = client.serial.read_until("version:")
        log.info("Firmware reported: %s", version)
        client.power.off()
```

### Skipping and marking tests

Use standard pytest markers to control test execution:

```python
import pytest
from jumpstarter_testing.pytest import JumpstarterTest


class TestOptionalFeatures(JumpstarterTest):
    selector = "board=rpi4"

    @pytest.mark.slow
    def test_long_running_operation(self, client):
        client.power.on()
        client.serial.read_until("ready", timeout=300)
        client.power.off()

    @pytest.mark.skip(reason="hardware not available")
    def test_camera_capture(self, client):
        client.camera.snapshot()
```

Run only tests without the `slow` marker:

```console
$ pytest -m "not slow"
```

### Fixtures for setup and teardown

A fixture that manages TFTP server lifecycle:

```python
import pytest
from jumpstarter_testing.pytest import JumpstarterTest


class TestTftp(JumpstarterTest):
    selector = "board=rpi4"

    @pytest.fixture()
    def tftp_server(self, client):
        client.tftp.start()
        yield client
        client.tftp.stop()

    def test_upload_file(self, tftp_server):
        tftp_server.tftp.put_local_file("test.bin")
        assert "test.bin" in tftp_server.tftp.list_files()
```

## CI integration

`JumpstarterTest` works in CI pipelines. Use either shell mode or lease mode
depending on your setup.

### Shell mode in CI

````{tab} GitHub
```yaml
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
```yaml
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
running pytest:

````{tab} GitHub
```yaml
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
```yaml
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
: Verify that an exporter matching your `selector` labels is running and
  registered with the controller. Check available exporters with
  `jmp get exporters`.

**`client` fixture yields `None`**
: Confirm that the `jumpstarter-testing` package is installed and that
  `JUMPSTARTER_HOST` is set correctly if running in shell mode.
