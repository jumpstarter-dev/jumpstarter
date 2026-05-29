# mitmproxy Driver

A [Jumpstarter](https://jumpstarter.dev) driver for [mitmproxy](https://mitmproxy.org) - bringing HTTP(S) interception, backend mocking, and traffic recording to Hardware-in-the-Loop testing.

This driver manages a `mitmdump` or `mitmweb` process on the Jumpstarter exporter host, providing your pytest HiL tests with:

- **Backend mocking** - Return deterministic JSON responses for any API endpoint, with hot-reloadable definitions, wildcard path matching, conditional rules, sequences, templates, and custom addons
- **SSL/TLS interception** - Inspect and modify HTTPS traffic from your DUT, with easy CA certificate retrieval for DUT provisioning
- **Traffic recording & replay** - Capture a "golden" session against real servers, then replay it offline in CI
- **Request capture** - Record every request the DUT makes and assert on them in your tests
- **Browser-based UI** - Launch `mitmweb` for interactive traffic inspection, with TCP port forwarding through the Jumpstarter tunnel
- **Scenario files** - Load complete mock configurations from YAML or JSON, swap between test scenarios instantly
- **Full CLI** - Control the proxy interactively from `jmp shell` sessions

## Installation

```{code-block} bash
# On both the exporter host and test client
pip install --extra-index-url https://pkg.jumpstarter.dev/simple \
    jumpstarter-driver-mitmproxy
```

Or build from source:

```{code-block} bash
uv build
pip install dist/jumpstarter_driver_mitmproxy-*.whl
```

## Configuration

### Exporter Configuration

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/config.yaml
:language: yaml
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

See `examples/exporter.yaml` in the package source for a full exporter config with DUT Link, serial, and video drivers.

### SSL/TLS Setup

For HTTPS interception, the mitmproxy CA certificate must be installed on the DUT. The certificate is generated the first time the proxy starts.

#### From the CLI

```console
j proxy cert                             # writes ./mitmproxy-ca-cert.pem
j proxy cert /tmp/ca.pem               # custom output path
```

#### From Python

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/usage.py
:language: python
```

#### Exporter-side path

If you need the path on the exporter host itself (for provisioning scripts that run locally):

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/usage_exporter.py
:language: python
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
j proxy cert /tmp/ca.pem                # download to a specific path
```

### Mock Scenarios

Create YAML or JSON files with endpoint definitions:

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/config_scenarios.yaml
:language: yaml
```

Load from CLI or Python:

```console
j proxy mock load happy-path.yaml
j proxy mock load my-capture/            # directory from 'capture save'
```

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/usage_scenarios.py
:language: python
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

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/usage_basic.py
:language: python
```

#### Context Managers

Context managers ensure clean teardown even if the test fails:

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/usage_context.py
:language: python
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

#### Request Capture

Verify that the DUT is making the right API calls:

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/usage_capture.py
:language: python
```

#### Advanced Mocking

##### Conditional responses

Return different responses based on request headers, body, or query params:

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/usage_conditionals.py
:language: python
```

##### Response sequences

Return different responses on successive calls:

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/usage_sequences.py
:language: python
```

##### Dynamic templates

Responses with per-request dynamic values:

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/usage_templates.py
:language: python
```

##### Simulated latency

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/usage_latency.py
:language: python
```

##### File serving

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/usage_files.py
:language: python
```

##### Custom addon scripts

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/usage_addons.py
:language: python
```

See `examples/addons/` in the package source for ready-to-use addon scripts including WebSocket data streaming, HLS audio streaming, and MJPEG video streaming.

#### State Store

Share state between tests and conditional mock rules:

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/usage_state.py
:language: python
```


### Example Test Fixtures

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/conftest.py
:language: python
```

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/test_device.py
:language: python
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_mitmproxy.driver.MitmproxyDriver()
```

### Container Deployment

```{literalinclude} ../../../../../packages/jumpstarter-driver-mitmproxy/examples/container_deploy.bash
:language: bash
```
