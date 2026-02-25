# jumpstarter-driver-mitmproxy

A [Jumpstarter](https://jumpstarter.dev) driver for [mitmproxy](https://mitmproxy.org) — bringing HTTP(S) interception, backend mocking, and traffic recording to Hardware-in-the-Loop testing.

## What it does

This driver manages a `mitmdump` or `mitmweb` process on the Jumpstarter exporter host, providing your pytest HiL tests with:

- **Backend mocking** — Return deterministic JSON responses for any API endpoint, with hot-reloadable definitions, wildcard path matching, conditional rules, sequences, templates, and custom addons
- **SSL/TLS interception** — Inspect and modify HTTPS traffic from your DUT, with easy CA certificate retrieval for DUT provisioning
- **Traffic recording & replay** — Capture a "golden" session against real servers, then replay it offline in CI
- **Request capture** — Record every request the DUT makes and assert on them in your tests
- **Browser-based UI** — Launch `mitmweb` for interactive traffic inspection, with TCP port forwarding through the Jumpstarter tunnel
- **Scenario files** — Load complete mock configurations from YAML or JSON, swap between test scenarios instantly
- **Full CLI** — Control the proxy interactively from `jmp shell` sessions

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
      listen:
        host: "0.0.0.0"
        port: 8080          # Proxy port (DUT connects here)
      web:
        host: "0.0.0.0"
        port: 8081           # mitmweb browser UI port
      directories:
        data: /opt/jumpstarter/mitmproxy
      ssl_insecure: true     # Skip upstream cert verification

      # Auto-load a scenario on startup (relative to mocks dir)
      # mock_scenario: happy-path.yaml

      # Inline mock definitions (overlaid on scenario)
      # mocks:
      #   GET /api/v1/health:
      #     status: 200
      #     body: {ok: true}
```

### Configuration Reference

| Parameter | Description | Type | Default |
| --------- | ----------- | ---- | ------- |
| `listen.host` | Proxy listener bind address | str | `0.0.0.0` |
| `listen.port` | Proxy listener port | int | `8080` |
| `web.host` | mitmweb UI bind address | str | `0.0.0.0` |
| `web.port` | mitmweb UI port | int | `8081` |
| `directories.data` | Base data directory | str | `/opt/jumpstarter/mitmproxy` |
| `directories.conf` | mitmproxy config/certs dir | str | `{data}/conf` |
| `directories.flows` | Recorded flow files dir | str | `{data}/flows` |
| `directories.addons` | Custom addon scripts dir | str | `{data}/addons` |
| `directories.mocks` | Mock definitions dir | str | `{data}/mock-responses` |
| `directories.files` | Files to serve from mocks | str | `{data}/mock-files` |
| `ssl_insecure` | Skip upstream SSL verification | bool | `true` |
| `mock_scenario` | Scenario file to auto-load on startup | str | `""` |
| `mocks` | Inline mock endpoint definitions | dict | `{}` |

See [`examples/exporter.yaml`](examples/exporter.yaml) for a full exporter config with DUT Link, serial, and video drivers.

## Modes

| Mode          | Description                                      |
|---------------|--------------------------------------------------|
| `mock`        | Intercept traffic, return mock responses         |
| `passthrough` | Transparent proxy, log only                      |
| `record`      | Capture all traffic to a binary flow file        |
| `replay`      | Serve responses from a previously recorded flow  |

Add `web_ui=True` (Python) or `--web-ui` (CLI) to any mode for the mitmweb browser interface.

## CLI Commands

During a `jmp shell` session, control the proxy with `j proxy <command>`:

### Lifecycle

```console
j proxy start                            # start in mock mode (default)
j proxy start -m passthrough             # start in passthrough mode
j proxy start -m mock -w                 # start with mitmweb UI
j proxy start -m record                  # start recording traffic
j proxy start -m replay --replay-file capture_20260213.bin
j proxy stop                             # stop the proxy
j proxy restart                          # restart with same config
j proxy restart -m passthrough           # restart with new mode
j proxy status                           # show proxy status
```

### Mock Management

```console
j proxy mock list                        # list configured mocks
j proxy mock clear                       # remove all mocks
j proxy mock load happy-path.yaml        # load a scenario file
j proxy mock load my-capture/            # load a saved capture directory
```

### Traffic Capture

```console
j proxy capture list                     # show captured requests
j proxy capture clear                    # clear captured requests
j proxy capture save ./my-capture        # export as scenario to directory
j proxy capture save -f '/api/v1/*' ./my-capture  # with path filter
j proxy capture save --exclude-mocked ./my-capture
```

### Flow Files

```console
j proxy flow list                        # list recorded flow files
j proxy flow save capture_20260101.bin   # download to current directory
j proxy flow save capture_20260101.bin /tmp/my.bin  # download to specific path
```

### Web UI & Certificates

```console
j proxy web                              # forward mitmweb UI to localhost:8081
j proxy web --port 9090                  # forward to a custom port
j proxy cert                             # download CA cert to ./mitmproxy-ca-cert.pem
j proxy cert /tmp/ca.pem                # download to a specific path
```

## Python API

### Basic Usage

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

    # ... interact with DUT ...

    proxy.stop()
```

### Context Managers

Context managers ensure clean teardown even if the test fails:

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

Available context managers:

| Context Manager | Description |
| --------------- | ----------- |
| `proxy.session(mode, web_ui)` | Start/stop the proxy |
| `proxy.mock_endpoint(method, path, ...)` | Temporary mock endpoint |
| `proxy.mock_scenario(file)` | Load/clear a scenario file |
| `proxy.mock_conditional(method, path, rules)` | Temporary conditional mock |
| `proxy.recording()` | Record traffic to a flow file |
| `proxy.capture()` | Capture and assert on requests |

### Request Capture

Verify that the DUT is making the right API calls:

```python
def test_telemetry_sent(client):
    proxy = client.proxy

    with proxy.capture() as cap:
        # ... DUT sends telemetry through the proxy ...
        cap.wait_for_request("POST", "/api/v1/telemetry", timeout=10)

    # After the block, cap.requests is a frozen snapshot
    assert len(cap.requests) >= 1
    cap.assert_request_made("POST", "/api/v1/telemetry")
```

### Advanced Mocking

#### Conditional responses

Return different responses based on request headers, body, or query params:

```python
proxy.set_mock_conditional("POST", "/api/auth", [
    {
        "match": {"body_json": {"username": "admin", "password": "secret"}},
        "status": 200,
        "body": {"token": "mock-token-001"},
    },
    {"status": 401, "body": {"error": "unauthorized"}},
])
```

#### Response sequences

Return different responses on successive calls:

```python
proxy.set_mock_sequence("GET", "/api/v1/auth/token", [
    {"status": 200, "body": {"token": "aaa"}, "repeat": 3},
    {"status": 401, "body": {"error": "expired"}, "repeat": 1},
    {"status": 200, "body": {"token": "bbb"}},
])
```

#### Dynamic templates

Responses with per-request dynamic values:

```python
proxy.set_mock_template("GET", "/api/v1/weather", {
    "temp_f": "{{random_int(60, 95)}}",
    "condition": "{{random_choice('sunny', 'rain')}}",
    "timestamp": "{{now_iso}}",
    "request_id": "{{uuid}}",
})
```

#### Simulated latency

```python
proxy.set_mock_with_latency(
    "GET", "/api/v1/status",
    body={"status": "online"},
    latency_ms=3000,
)
```

#### File serving

```python
proxy.set_mock_file(
    "GET", "/api/v1/downloads/firmware.bin",
    "firmware/test.bin",
    content_type="application/octet-stream",
)
```

#### Custom addon scripts

```python
proxy.set_mock_addon(
    "GET", "/streaming/audio/channel/*",
    "hls_audio_stream",
    addon_config={"segment_duration_s": 6},
)
```

### State Store

Share state between tests and conditional mock rules:

```python
proxy.set_state("auth_token", "mock-token-001")
proxy.set_state("retries", 3)

token = proxy.get_state("auth_token")   # "mock-token-001"
all_state = proxy.get_all_state()       # {"auth_token": "...", "retries": 3}

proxy.clear_state()
```

## SSL/TLS Setup

For HTTPS interception, the mitmproxy CA certificate must be installed on the DUT. The certificate is generated the first time the proxy starts.

### From the CLI

```console
j proxy cert                             # writes ./mitmproxy-ca-cert.pem
j proxy cert /tmp/ca.pem               # custom output path
```

### From Python

```python
# Get the PEM certificate contents
pem = proxy.get_ca_cert()

# Write to a local file
from pathlib import Path
Path("/tmp/mitmproxy-ca.pem").write_text(pem)

# Or push directly to the DUT via serial/ssh/adb
dut.write_file("/etc/ssl/certs/mitmproxy-ca.pem", pem)
```

### Exporter-side path

If you need the path on the exporter host itself (for provisioning scripts that run locally):

```python
cert_path = proxy.get_ca_cert_path()
# -> /opt/jumpstarter/mitmproxy/conf/mitmproxy-ca-cert.pem
```

## Mock Scenarios

Create YAML or JSON files with endpoint definitions:

```yaml
# scenarios/happy-path.yaml
endpoints:
  GET /api/v1/status:
    status: 200
    body:
      id: device-001
      status: active
      firmware_version: "2.5.1"

  POST /api/v1/telemetry/upload:
    status: 202
    body:
      accepted: true

  GET /api/v1/search*:      # wildcard prefix match
    status: 200
    body:
      results: []
```

Load from CLI or Python:

```console
j proxy mock load happy-path.yaml
j proxy mock load my-capture/            # directory from 'capture save'
```

```python
proxy.load_mock_scenario("happy-path.yaml")

# Or with automatic cleanup:
with proxy.mock_scenario("happy-path.yaml"):
    run_tests()
```

See [`examples/scenarios/`](examples/scenarios/) for complete scenario examples including conditional rules, templates, and sequences.

## Web UI Port Forwarding

The mitmweb UI runs on the exporter host and is not directly reachable from the test client. The `web` command tunnels it through the Jumpstarter gRPC transport:

```console
j proxy start -m mock -w                 # start with web UI on the exporter
j proxy web                              # tunnel to localhost:8081
j proxy web --port 9090                  # use a custom local port
```

Then open `http://localhost:8081` in your browser to inspect traffic in real time.

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

## License

Apache-2.0
