# Hooks

Jumpstarter supports lifecycle hooks that execute shell scripts automatically before or after a lease.

A `beforeLease` hook runs after a lease is assigned but
before drivers are available to the client, and an `afterLease` hook runs after
the session ends but before the lease is released. Hooks are optional and
configured in the [Exporter](exporters.md) YAML configuration file.

Hooks execute on the exporter host using a configurable interpreter (defaulting
to `/bin/sh`) and can use the `j` CLI to interact with drivers locally on the
exporter. The `exec` field lets you choose a different interpreter such as
`/bin/bash` or `python3`. The `script` field accepts either an inline script
or a path to a script file on disk. Common use cases include powering on
devices, validating hardware state, flashing firmware, and cleaning up after
tests.

## How Hooks Work

The following diagram shows the full lifecycle of a lease with both hooks
configured:

```{mermaid}
:config: {"theme":"base","themeVariables":{"primaryColor":"#f8f8f8","primaryTextColor":"#000","primaryBorderColor":"#e5e5e5","lineColor":"#3d94ff","secondaryColor":"#f8f8f8","tertiaryColor":"#fff"}}
sequenceDiagram
    participant Controller
    participant Exporter
    participant Hook as Hook Script
    participant Client

    Client->>Controller: Request lease
    Controller->>Exporter: Assign lease
    Exporter->>Exporter: Status: BEFORE_LEASE_HOOK
    Exporter->>Hook: Execute beforeLease script
    Note over Hook: j power on
    Hook-->>Exporter: Exit code 0
    Exporter->>Exporter: Status: LEASE_READY
    Exporter->>Client: Drivers available
    Client->>Exporter: Use drivers...
    Client->>Exporter: End session
    Exporter->>Exporter: Status: AFTER_LEASE_HOOK
    Exporter->>Hook: Execute afterLease script
    Note over Hook: j power off
    Hook-->>Exporter: Exit code 0
    Exporter->>Exporter: Status: AVAILABLE
    Exporter->>Controller: Release lease
```

The exporter transitions through these states during a lease:

1. **Lease assigned** -- The controller assigns a lease to the exporter.
2. **`BEFORE_LEASE_HOOK`** -- The `beforeLease` script runs. Driver access is
   blocked until the hook completes successfully.
3. **`LEASE_READY`** -- The hook succeeded and the client can now access
   drivers.
4. **Client session** -- The client uses drivers normally.
5. **Session ends** -- The client disconnects or the lease is released.
6. **`AFTER_LEASE_HOOK`** -- The `afterLease` script runs. The session remains
   open so `j` commands can still interact with drivers.
7. **`AVAILABLE`** -- The hook completed and the lease is released. The
   exporter is ready for the next lease.

```{note}
If no hooks are configured, the exporter transitions directly from lease
assignment to `LEASE_READY` and from session end to `AVAILABLE`.
```

## Configuration

Hooks are configured in the `hooks` section of the exporter config file:

```yaml
apiVersion: jumpstarter.dev/v1alpha1
kind: ExporterConfig
metadata:
  namespace: default
  name: demo
endpoint: grpc.jumpstarter.example.com:443
token: xxxxx
export:
  power:
    type: jumpstarter_driver_yepkit.driver.Ykush
    config:
      serial: "YK25838"
      port: "1"
hooks:
  beforeLease:
    script: |
      j power on
      sleep 5
      j devices validate
    timeout: 60
    onFailure: endLease
  afterLease:
    script: |
      j power off
    timeout: 30
    onFailure: warn
```

### Field Reference

| Field | Type | Default | Description |
| --- | --- | --- | --- |
| `hooks.beforeLease` | object | *(none)* | Hook that runs after lease assignment, before drivers are available |
| `hooks.afterLease` | object | *(none)* | Hook that runs after the session ends, before the lease is released |
| `hooks.<hook>.exec` | string | *(auto)* | Interpreter used to execute the script. Auto-detected from file extension when not set (`.py` uses the exporter's Python, `.sh` uses `/bin/sh`). Defaults to `/bin/sh` for inline scripts. |
| `hooks.<hook>.script` | string | *(required)* | Inline script or path to a script file (auto-detected) |
| `hooks.<hook>.timeout` | integer | `120` | Maximum execution time in seconds |
| `hooks.<hook>.onFailure` | string | `"warn"` | Action on failure: `"warn"`, `"endLease"`, or `"exit"` |

### Script Modes

The `script` field supports two modes, detected automatically:

- **Inline script** (default): When the value contains multiple lines or does
  not point to an existing file, it is passed to the interpreter with the `-c`
  flag (e.g. `/bin/sh -c "echo hello"`).
- **Script file**: When the value is a single line that matches an existing file
  on disk, the interpreter runs the file directly (e.g. `/bin/bash /opt/hooks/setup.sh`).

When `exec` is not set and the script is a file, the interpreter is
auto-detected from the file extension:

| Extension | Interpreter | Notes |
| --- | --- | --- |
| `.py` | Exporter's Python (`sys.executable`) | Has access to all installed packages including the Jumpstarter client library |
| `.sh` | `/bin/sh` | POSIX shell |
| *(other)* | `/bin/sh` | Fallback for unrecognized extensions |

Set `exec` explicitly to override auto-detection (e.g. `exec: /bin/bash` for
a `.sh` file that needs bash features).

## Environment Variables

Hook scripts receive a pre-configured environment that enables the `j` CLI to
communicate with the exporter session:

| Variable            | Description                                                                         |
| ------------------- | ----------------------------------------------------------------------------------- |
| `JUMPSTARTER_HOST`  | Unix socket path for `j` CLI access to the exporter session                         |
| `LEASE_NAME`        | Name of the current lease assigned by the controller                                |
| `CLIENT_NAME`       | Name of the client holding the lease                                                |
| `JMP_DRIVERS_ALLOW` | Set to `UNSAFE` to enable access to all drivers (hooks run locally on the exporter) |

These variables are set automatically. The hook uses a dedicated Unix socket
separate from the client connection to avoid protocol interference.

## Logging

Hook output is streamed to the client in real time. Every line written to
stdout or stderr by the hook script is captured and forwarded to the client
through the exporter's log stream. The `beforeLease` hook output is tagged
with the `BEFORE_LEASE_HOOK` log source, and `afterLease` output is tagged
with `AFTER_LEASE_HOOK`.

Hooks run inside a pseudo-terminal (PTY) to force line buffering, so output
appears on the client as each line is written rather than being held in a
block buffer. This means `echo` statements, `j` CLI output, and any other
text written to the terminal will be visible immediately.

```{note}
Because hooks use a PTY, programs that detect terminal mode (such as
`grep --color=auto`) will behave as though running interactively.
```

## Failure Handling

The `onFailure` field controls what happens when a hook script exits with a
non-zero exit code or exceeds its timeout. A hook is considered failed when the
shell process returns a non-zero exit code or when execution exceeds the
configured `timeout`.

### `warn`

The default mode. The failure is logged as a warning and the lease lifecycle
continues as if the hook succeeded:

- **`beforeLease`**: Drivers are unblocked and the client can connect normally.
  The exporter status transitions to `LEASE_READY`.
- **`afterLease`**: The exporter returns to `AVAILABLE` and the lease is
  released normally.

This is useful for hooks that perform best-effort actions where failure should
not disrupt the workflow.

### `endLease`

The lease is ended and the client is notified of the failure:

- **`beforeLease`**: The exporter status transitions to
  `BEFORE_LEASE_HOOK_FAILED`. The client discovers the failure through status
  polling and the lease is released. The interactive shell is skipped.
- **`afterLease`**: The exporter status transitions to
  `AFTER_LEASE_HOOK_FAILED`. Since the session has already ended, this
  primarily serves as a signal to the client that cleanup did not complete
  successfully. The exporter remains available for new leases.

This is the recommended mode for `beforeLease` validation hooks where you want
the client to know immediately that the device is not ready.

### `exit`

The exporter shuts down entirely with exit code `1` (Failure):

- **`beforeLease`**: The exporter status transitions to
  `BEFORE_LEASE_HOOK_FAILED`. The exporter then shuts down, going offline. The
  shutdown is deferred until the client has had a chance to observe the failure
  status.
- **`afterLease`**: The exporter status transitions to
  `AFTER_LEASE_HOOK_FAILED` and the exporter shuts down immediately.

The exit code `1` signals to service managers such as `systemd` that the shutdown
was intentional. If your systemd unit uses `Restart=always`, you should
configure `RestartPreventExitStatus=1` to prevent automatic restarts after an
`exit` failure.

```{warning}
The `exit` failure mode is a drastic action intended for critical failures
where the device may be in an unusable state. It takes the exporter offline
until manually restarted. Use `endLease` for most validation scenarios and
reserve `exit` for critical failures.
```

### Timeout Behavior

When a hook exceeds its `timeout`, the process is terminated with `SIGTERM`
followed by `SIGKILL` if the process does not exit within a few seconds. The
resulting failure is then handled according to the `onFailure` setting, exactly
as if the script had exited with a non-zero exit code.

## Use Cases

### Device Initialization

Power on the device and wait until it is reachable over SSH before the client
connects:

```yaml
hooks:
  beforeLease:
    script: |
      echo "Powering on device..."
      j power on
      echo "Waiting for SSH to become available..."
      for i in $(seq 1 30); do
        if j ssh -o ConnectTimeout=2 -- echo "Device ready"; then
          exit 0
        fi
        sleep 1
      done
      echo "Device did not become reachable"
      exit 1
    timeout: 120
    onFailure: endLease
```

Note that the `j ssh` command does not have a built-in connection timeout, so
each attempt uses the system SSH default (typically ~30 seconds). Passing
`-o ConnectTimeout=2` keeps each retry attempt short so the loop can complete
within the hook's `timeout`.

### Device Cleanup

Power off the device after each lease to ensure a clean environment for the
next user:

```yaml
hooks:
  afterLease:
    script: |
      echo "Cleaning up..."
      j power off
    timeout: 30
    onFailure: warn
```

### Firmware Flashing

Flash known-good firmware before each test session to guarantee a consistent
starting state:

```yaml
hooks:
  beforeLease:
    script: |
      echo "Flashing firmware..."
      j storage write --image /var/lib/jumpstarter/images/firmware.bin
      j power cycle
      sleep 10
    timeout: 180
    onFailure: endLease
```

### Using Bash

Set `exec: /bin/bash` to use bash-specific features such as `[[ ]]` tests,
arrays, and process substitution:

```yaml
hooks:
  beforeLease:
    exec: /bin/bash
    script: |
      echo "Checking device readiness..."
      [[ -f /dev/ttyUSB0 ]] || { echo "Serial device missing"; exit 1; }
      j power on
    timeout: 60
    onFailure: endLease
```

### Using Python

Point `script` to a `.py` file. The exporter auto-detects the `.py`
extension and runs it with its own Python interpreter, so the hook has
access to all installed packages including the Jumpstarter client library.
Python hooks can use the driver client APIs directly by importing
`jumpstarter.utils.env.env`, which connects to the local exporter session
via the `JUMPSTARTER_HOST` socket automatically.

Exporter config:

```yaml
hooks:
  beforeLease:
    script: /opt/jumpstarter/hooks/prepare_device.py
    timeout: 60
    onFailure: endLease
```

`/opt/jumpstarter/hooks/prepare_device.py`:

```python
import os
from jumpstarter.utils.env import env

lease = os.environ["LEASE_NAME"]
print(f"Preparing device for lease {lease}")

with env() as client:
    client.power.on()
    print("Power on complete")
```

The `env()` context manager returns a `DriverClient` whose attributes
correspond to the exported drivers (e.g. `client.power`, `client.storage`).
This is the same API used by the `j` CLI and by test scripts connecting to
an exporter.

### Using a Script File

Point `script` to an existing file on disk instead of writing the script
inline. The interpreter runs the file directly:

```yaml
hooks:
  beforeLease:
    exec: /bin/bash
    script: /opt/jumpstarter/hooks/prepare_device.sh
    timeout: 120
    onFailure: endLease
```

## Best Practices

- Keep hook scripts short and focused on a single concern (initialization or
  cleanup).
- Set an appropriate `timeout` for each hook. The default of 120 seconds may be
  too generous for simple scripts and too short for firmware flashing.
- Use `onFailure: endLease` for `beforeLease` validation so clients get
  immediate feedback when a device is not ready.
- Use `onFailure: warn` for `afterLease` cleanup unless leaving the device in a
  bad state poses a safety risk.
- Reserve `onFailure: exit` for critical failures that require manual
  intervention.
- Hook output is streamed to the client in real time. Include informative
  `echo` statements for observability.
- The interpreter is auto-detected from the file extension (`.py` uses the
  exporter's Python, `.sh` uses `/bin/sh`). Set `exec` explicitly to
  override (e.g. `exec: /bin/bash` for bash-specific syntax).
- The `j` CLI is available in hook scripts because `JUMPSTARTER_HOST` is set
  automatically. No additional configuration is needed.
