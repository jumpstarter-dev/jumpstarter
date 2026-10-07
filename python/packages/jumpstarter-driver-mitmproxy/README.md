# mitmproxy Driver

A [Jumpstarter](https://jumpstarter.dev) driver for [mitmproxy](https://mitmproxy.org) - bringing HTTP(S) interception, backend mocking, and traffic recording to Hardware-in-the-Loop testing.

This driver manages a `mitmdump` or `mitmweb` process on the Jumpstarter exporter host, providing your pytest HiL tests with:

- **Backend mocking** - Return deterministic JSON responses for any API endpoint, with hot-reloadable definitions, wildcard path matching, conditional rules, sequences, templates, and custom addons
- **SSL/TLS interception** - Inspect and modify HTTPS traffic from your DUT, with easy CA certificate retrieval for DUT provisioning
- **Traffic recording & replay** - Capture a "golden" session against real servers, then replay it offline in CI
- **Request capture** - Record every request the DUT makes, assert on them in your tests, and read back response bodies
- **Network condition emulation** - Throttle bandwidth, add latency and jitter, or drop requests to test the DUT on a weak link
- **Browser-based UI** - Launch `mitmweb` for interactive traffic inspection, with TCP port forwarding through the Jumpstarter tunnel
- **Scenario files** - Load complete mock configurations from YAML or JSON, swap between test scenarios instantly
- **Full CLI** - Control the proxy interactively from `jmp shell` sessions

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

### Installing mitmproxy

The driver launches `mitmdump`/`mitmweb` as external programs rather than
importing mitmproxy, so mitmproxy is **not** pulled in as a Python dependency.

- **Exporter hosts** need mitmproxy installed separately
  (`uv tool install mitmproxy` or `pipx install mitmproxy`). **Test clients**
  don't — they only talk to the driver's client API.
- **Services (for example under systemd)** often don't have `~/.local/bin` on
  `PATH`. In that case install mitmproxy system-wide, for example with
  `UV_TOOL_BIN_DIR=/usr/local/bin uv tool install mitmproxy`.
- **Custom addons that import extra packages** now need those packages in
  mitmproxy's own environment, for example
  `uv tool install mitmproxy --with pillow`. Previously they could use anything
  installed in jumpstarter's environment.

## Configuration

### Exporter Configuration

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
        data: /opt/jumpstarter/mitmproxy  # must be writable; omit for a per-user temp dir
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
| `directories.data` | Base data directory | str | `$TMPDIR/jumpstarter-mitmproxy-<user>` |
| `directories.conf` | mitmproxy config/certs dir | str | `{data}/conf` |
| `directories.flows` | Recorded flow files dir | str | `{data}/flows` |
| `directories.addons` | Custom addon scripts dir | str | `{data}/addons` |
| `directories.mocks` | Mock definitions dir | str | `{data}/mock-responses` |
| `directories.files` | Files to serve from mocks | str | `{data}/mock-files` |
| `ssl_insecure` | Skip upstream SSL verification | bool | `true` |
| `mock_scenario` | Scenario file to auto-load on startup | str | `""` |
| `mocks` | Inline mock endpoint definitions | dict | `{}` |

Leaving `directories.data` unset works anywhere, including macOS and CI
runners where `/opt` is not writable, but temporary directories can be
cleared. On a long-lived exporter host, set it to a persistent path the
exporter's user can write to.

See `examples/exporter.yaml` in the package source for a full exporter config with DUT Link, serial, and video drivers.

### SSL/TLS Setup

For HTTPS interception, the mitmproxy CA certificate must be installed on the DUT. The certificate is generated the first time the proxy starts.

#### From the CLI

```console
j proxy cert                             # writes ./mitmproxy-ca-cert.pem
j proxy cert /tmp/ca.pem                 # custom output path
```

#### From Python

```python
# Get the PEM certificate contents
pem = proxy.get_ca_cert()

# Write to a local file
from pathlib import Path
Path("/tmp/mitmproxy-ca.pem").write_text(pem)

# Or push directly to the DUT via serial/ssh/adb
dut.write_file("/etc/ssl/certs/mitmproxy-ca.pem", pem)
```

#### Exporter-side path

If you need the path on the exporter host itself (for provisioning scripts that run locally):

```python
cert_path = proxy.get_ca_cert_path()
# -> /opt/jumpstarter/mitmproxy/conf/mitmproxy-ca-cert.pem
```

## Usage

### Modes

| Mode          | Description                                      |
|---------------|--------------------------------------------------|
| `mock`        | Intercept traffic, return mock responses         |
| `passthrough` | Transparent proxy, log only                      |
| `record`      | Capture all traffic to a binary flow file        |
| `replay`      | Serve responses from a previously recorded flow  |

Add `web_ui=True` (Python) or `--web-ui` (CLI) to any mode for the mitmweb browser interface.

### CLI Commands

During a `jmp shell` session, control the proxy with `j proxy <command>`:

#### Lifecycle

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

#### Mock Management

```console
j proxy mock list                        # list configured mocks
j proxy mock clear                       # remove all mocks
j proxy mock load happy-path.yaml        # load a scenario file
j proxy mock load my-capture/            # load a saved capture directory
```

#### Traffic Capture

```console
j proxy capture list                     # show captured requests
j proxy capture clear                    # clear captured requests
j proxy capture watch                    # stream requests live (Ctrl+C to stop)
j proxy capture watch -f '/api/v1/*'     # stream only matching paths
j proxy capture save ./my-capture        # export as scenario to directory
j proxy capture save -f '/api/v1/*' ./my-capture  # with path filter
j proxy capture save --exclude-mocked ./my-capture
```

#### Flow Files

```console
j proxy flow list                        # list recorded flow files
j proxy flow save capture_20260101.bin   # download to current directory
j proxy flow save capture_20260101.bin /tmp/my.bin  # download to specific path
```

#### Web UI & Certificates

```console
j proxy web                              # forward mitmweb UI to localhost:8081
j proxy web --port 9090                  # forward to a custom port
j proxy cert                             # download CA cert to ./mitmproxy-ca-cert.pem
j proxy cert /tmp/ca.pem                 # download to a specific path
```

### Mock Scenarios

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

See `examples/scenarios/` in the package source for complete scenario examples including conditional rules, templates, and sequences.

### Web UI Port Forwarding

The mitmweb UI runs on the exporter host and is not directly reachable from the test client. The `web` command tunnels it through the Jumpstarter gRPC transport:

```console
j proxy start -m mock -w                 # start with web UI on the exporter
j proxy web                              # tunnel to localhost:8081
j proxy web --port 9090                  # use a custom local port
```

Then open `http://localhost:8081` in your browser to inspect traffic in real time.

### Python API

#### Basic Usage

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

#### Context Managers

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
| `proxy.mock_patch_endpoint(method, path, patches)` | Temporary patch of a real response |
| `proxy.mock_scenario(file)` | Load/clear a scenario file |
| `proxy.mock_conditional(method, path, rules)` | Temporary conditional mock |
| `proxy.recording()` | Record traffic to a flow file |
| `proxy.capture()` | Capture and assert on requests |
| `proxy.shaping(rate_kbit, latency_ms, ...)` | Temporary traffic shaping |

#### Request Capture

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

`wait_for_request` matches the path exactly, or as a prefix when it ends in `*`.
Pass `use_regex=True` to match it as a regular expression, `expected_status` to
only accept a response with that status, and `"*"` as the method to accept any:

```python
proxy.wait_for_request("*", r"^/api/v1/devices/\d+$", use_regex=True)
proxy.wait_for_request("POST", "/api/v1/telemetry", expected_status=202)
```

Matching does not consume the request, so the same capture can be waited for and
then inspected.

Captured response bodies stay on the exporter. Read one back without needing
access to the exporter's filesystem:

```python
result = proxy.get_response_body(r"/api/v1/status")  # most recent match
assert result["status"] == 200
assert result["body"]["status"] == "active"           # parsed if JSON

first = proxy.get_response_body(r"/api/v1/status", index=0)  # oldest match
```

`get_response_body` returns `body`, `path`, `status` and `truncated` (bodies are
read up to 1 MiB) and raises `LookupError` when nothing matches.

To keep every capture as a test artifact, export them with text response bodies
inlined:

```python
import json
from pathlib import Path

captures = proxy.export_captured_requests(max_body_size=256 * 1024)
Path("artifacts/captures.json").write_text(json.dumps(captures, indent=2))
```

Each body is capped at `max_body_size` bytes and marked
`response_body_truncated` when cut off. Binary bodies are skipped. The whole
export is kept under the gRPC message limit, so bodies are dropped first and
the export stops early on a very large capture.

The capture buffer itself is bounded the same way. Once it reaches about
3.5 MB, the oldest entries are discarded to make room.

#### Traffic Shaping

Emulate a slow or unreliable network between the DUT and its backend. Shaping
applies to all traffic through the proxy, takes effect immediately, and lasts
for the current proxy session only. The proxy must be running, and stopping or
restarting it clears the shaping.

```python
def test_survives_weak_link(client):
    proxy = client.proxy

    with proxy.session(mode="passthrough"):
        with proxy.shaping(rate_kbit=400, latency_ms=250, jitter_ms=50):
            # DUT now sees a 400 kbit/s link with 200-300 ms of added latency
            assert_download_completes(client)
        # shaping cleared here
```

Or without a context manager:

```python
proxy.shape(latency_ms=500, drop_pct=5)   # replaces any previous shaping
proxy.get_shaping()  # {"rate_kbit": 0, "latency_ms": 500.0, "jitter_ms": 0.0, "drop_pct": 5.0}
proxy.clear_shaping()
```

| Parameter | Description |
| --------- | ----------- |
| `rate_kbit` | Bandwidth cap in kilobits per second, applied to each direction separately. `0` for unlimited. |
| `latency_ms` | Delay added once per request, up to 60000 ms. |
| `jitter_ms` | Random spread of ± this many ms around `latency_ms`. Must not exceed `latency_ms`. |
| `drop_pct` | Percentage of requests (0-100) to fail. The client sees a connection error, not an HTTP error status. |

`shape` raises `ValueError` for out-of-range values and `RuntimeError` when the
proxy is not running.

Shaping works at the HTTP level: latency is per request, and loss drops whole
requests rather than packets. To shape the link itself at the packet level, use
a network-level tool on the exporter host instead.

#### Advanced Mocking

##### Conditional responses

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

##### Response sequences

Return different responses on successive calls:

```python
proxy.set_mock_sequence("GET", "/api/v1/auth/token", [
    {"status": 200, "body": {"token": "aaa"}, "repeat": 3},
    {"status": 401, "body": {"error": "expired"}, "repeat": 1},
    {"status": 200, "body": {"token": "bbb"}},
])
```

##### Dynamic templates

Responses with per-request dynamic values:

```python
proxy.set_mock_template("GET", "/api/v1/weather", {
    "temp_f": "{{random_int(60, 95)}}",
    "condition": "{{random_choice('sunny', 'rain')}}",
    "timestamp": "{{now_iso}}",
    "request_id": "{{uuid}}",
})
```

##### Simulated latency

```python
proxy.set_mock_with_latency(
    "GET", "/api/v1/status",
    body={"status": "online"},
    latency_ms=3000,
)
```

##### File serving

```python
proxy.set_mock_file(
    "GET", "/api/v1/downloads/firmware.bin",
    "firmware/test.bin",
    content_type="application/octet-stream",
)
```

##### Custom addon scripts

```python
proxy.set_mock_addon(
    "GET", "/streaming/audio/channel/*",
    "hls_audio_stream",
    addon_config={"segment_duration_s": 6},
)
```

#### State Store

Share state between tests and conditional mock rules:

```python
proxy.set_state("auth_token", "mock-token-001")
proxy.set_state("retries", 3)

token = proxy.get_state("auth_token")   # "mock-token-001"
all_state = proxy.get_all_state()       # {"auth_token": "...", "retries": 3}

proxy.clear_state()
```

## Container Deployment

The `quay.io/jumpstarter-dev/jumpstarter` image includes this driver and
mitmproxy. Run an exporter from it with the exporter config mounted:

```bash
podman run --rm -it --privileged \
  -v /dev:/dev \
  -v /etc/jumpstarter:/etc/jumpstarter:Z \
  -p 8080:8080 -p 8081:8081 \
  quay.io/jumpstarter-dev/jumpstarter:latest \
  jmp run --exporter my-bench
```

To build the image yourself, run this from the repository root:

```bash
podman build -f python/Containerfile -t jumpstarter:latest .
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_mitmproxy.client.MitmproxyClient()
    :members:
```

```{eval-rst}
.. autoclass:: jumpstarter_driver_mitmproxy.driver.MitmproxyDriver()
```
