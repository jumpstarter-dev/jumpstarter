# jumpstarter-driver-mitmproxy

A [Jumpstarter](https://jumpstarter.dev) driver for [mitmproxy](https://mitmproxy.org) — bringing HTTP(S) interception, backend mocking, and traffic recording to Hardware-in-the-Loop testing.

## What it does

This driver manages a `mitmdump` or `mitmweb` process on the Jumpstarter exporter host, providing your pytest HiL tests with:

- **Backend mocking** — Return deterministic JSON responses for any API endpoint, with hot-reloadable definitions and wildcard path matching
- **SSL/TLS interception** — Inspect and modify HTTPS traffic from your DUT
- **Traffic recording & replay** — Capture a "golden" session against real servers, then replay it offline in CI
- **Browser-based UI** — Launch `mitmweb` for interactive traffic inspection during development
- **Scenario files** — Load complete mock configurations from JSON, swap between test scenarios instantly

## Installation

```bash
# On both the exporter host and test client
pip install --extra-index-url https://pkg.jumpstarter.dev/simple \
    jumpstarter-driver-mitmproxy
```

Or build from source:

```bash
uv build
pip install dist/jumpstarter_driver_mitmproxy-*.whl
```

## Exporter Configuration

```yaml
# /etc/jumpstarter/exporters/my-bench.yaml
export:
  proxy:
    type: jumpstarter_driver_mitmproxy.driver.MitmproxyDriver
    config:
      listen_port: 8080     # Proxy port (DUT connects here)
      web_port: 8081        # mitmweb browser UI port
      ssl_insecure: true    # Skip upstream cert verification
```

See `examples/exporter.yaml` for a full exporter config with DUT Link, serial, and video drivers.

## Usage

### In pytest

```python
def test_device_status(client):
    proxy = client.proxy

    # Start with web UI for debugging
    proxy.start(mode="mock", web_ui=True)

    # Mock a backend endpoint
    proxy.set_mock(
        "GET", "/api/v1/status",
        body={"id": "device-001", "status": "active"},
    )

    # ... interact with DUT via client.serial, client.video ...

    proxy.stop()
```

### With context managers

```python
def test_firmware_update(client):
    proxy = client.proxy

    with proxy.session(mode="mock", web_ui=True):
        with proxy.mock_endpoint(
            "GET", "/api/v1/updates/check",
            body={"update_available": True, "version": "2.6.0"},
        ):
            # DUT will see the mocked update
            trigger_update_check(client)
            assert_update_dialog_shown(client)
        # Mock auto-removed here
    # Proxy auto-stopped here
```

### From jmp shell

```
$ jmp shell --exporter my-bench

jumpstarter local > j proxy start --mode mock --web-ui
Started in 'mock' mode on 0.0.0.0:8080 | Web UI: http://0.0.0.0:8081

jumpstarter local > j proxy status
{"running": true, "mode": "mock", "web_ui_address": "http://0.0.0.0:8081", ...}

jumpstarter local > j proxy stop
Stopped (was 'mock' mode)
```

## Modes

| Mode          | Binary           | Description                              |
| ------------- | ---------------- | ---------------------------------------- |
| `mock`        | mitmdump/mitmweb | Intercept traffic, return mock responses |
| `passthrough` | mitmdump/mitmweb | Transparent proxy, log only              |
| `record`      | mitmdump/mitmweb | Capture all traffic to a flow file       |
| `replay`      | mitmdump/mitmweb | Serve responses from a recorded flow     |

Add `web_ui=True` to any mode for the browser-based mitmweb interface.

## Mock Scenarios

Create JSON files with endpoint definitions:

```json
{
  "GET /api/v1/status": {
    "status": 200,
    "body": {"id": "device-001", "status": "active"}
  },
  "POST /api/v1/telemetry": {
    "status": 202,
    "body": {"accepted": true}
  },
  "GET /api/v1/search*": {
    "status": 200,
    "body": {"results": []}
  }
}
```

Load in tests with `proxy.load_mock_scenario("my-scenario.json")` or the `mock_scenario` context manager.

## Container Deployment

```bash
podman build -t jumpstarter-mitmproxy:latest .

podman run --rm -it --privileged \
  -v /dev:/dev \
  -v /etc/jumpstarter:/etc/jumpstarter:Z \
  -p 8080:8080 -p 8081:8081 \
  jumpstarter-mitmproxy:latest \
  jmp exporter start my-bench
```

## SSL/TLS Setup

For HTTPS interception, install the mitmproxy CA cert on your DUT:

```python
# Get the cert path from your test
cert_path = proxy.get_ca_cert_path()
# -> /etc/mitmproxy/mitmproxy-ca-cert.pem
```

Then install it on the DUT via serial, adb, or your provisioning system.

## License

Apache-2.0
