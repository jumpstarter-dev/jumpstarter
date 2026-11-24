# Hooks

Jumpstarter supports lifecycle hooks that execute shell scripts automatically before or after a {term}`lease`.

A `beforeLease` hook runs after a lease is assigned but
before drivers are available to the client, and an `afterLease` hook runs after
the client finishes its {term}`session`, the lease ends, or the exporter stops.
Hooks are optional and
configured in the [Exporter](exporters.md) YAML configuration file (exporter config).

Hooks execute on the exporter {term}`host` using a configurable interpreter (defaulting
to `/bin/sh`) and can use the {term}`j` CLI to interact with drivers locally on the
{term}`exporter`. The `exec` field lets you choose a different interpreter such as
`/bin/bash` or `python3`. The `script` field accepts either an inline script
or a path to a script file on disk. Common use cases include powering on
devices, validating hardware state, flashing firmware, and cleaning up after
tests.

## How Hooks Work

The following diagram shows the full lifecycle of a {term}`lease` with both {term}`hook`s
configured:

```{mermaid}
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
    Note over Exporter: Keep session and final logs until reservation release
    Client->>Controller: Release lease
    Controller->>Exporter: Lease released
    Exporter->>Exporter: Close connections, session, and drivers
```

The {term}`exporter` transitions through these states during a {term}`lease`:

1. **{term}`Lease` assigned** - The {term}`controller` assigns a {term}`lease` to the {term}`exporter`.
2. **`BEFORE_LEASE_HOOK`** - The `beforeLease` script runs. Driver access is
   blocked until the {term}`hook` completes successfully.
3. **`LEASE_READY`** - The {term}`hook` succeeded and the client can now access
   drivers.
4. **Client {term}`session`** - The client uses drivers normally.
5. **{term}`Session` finishes** - The client calls `EndSession`, or the controller
   releases or reassigns the {term}`lease`. Disconnecting alone does not release
   the reservation.
6. **`AFTER_LEASE_HOOK`** - The `afterLease` script runs. The {term}`session` remains
   open so `j` commands can still interact with drivers.
7. **`AVAILABLE`** - The {term}`hook` completed. The session keeps status and final
   logs available until the controller releases the reservation; `EndSession`
   alone does not release it.
8. **Teardown** - Connections, session sockets, and drivers close before the
   {term}`exporter` admits another {term}`lease`.

```{note}
If no {term}`hook`s are configured, the {term}`exporter` transitions directly from {term}`lease`
assignment to `LEASE_READY` and from {term}`session` end to `AVAILABLE`.
```

### Lease Changes and Shutdown

Lease changes and stop requests are retained while a hook is running. The hook
finishes, subject to its configured timeout, before the exporter handles the latest
request. Eligible cleanup runs once, with the same session and hook socket still
open. A replacement lease starts only after teardown completes.

In controller mode:

- **`SIGHUP` (drain):** wait for the active reservation to end, then stop. An idle
  exporter stops immediately. Finishing a session with `EndSession` alone does not
  complete a drain while the controller still holds the reservation.
- **`SIGTERM`, `SIGINT`, or `SIGQUIT` (stop-now):** finish the current hook and
  eligible cleanup, then stop locally without waiting for controller release.
  Stop-now overrides an earlier drain request.
- **`--exit-on-lease-end`:** stop after the admitted lease's full teardown;
  runtime-sidecar shutdown happens afterward.

[Direct mode](../getting-started/guides/setup/direct-mode.md) uses the same hook
sequencing, but has no reservation to drain. All stop requests, including `SIGHUP`,
finish the current hook and eligible cleanup before closing the local session.
EndSession runs cleanup once and keeps status and logs available until stop.

Configure service or container shutdown grace to allow hook timeouts and resource
cleanup. A teardown error stops the lifecycle instead of admitting another lease.

## Configuration

{term}`Hook`s are configured in the `hooks` section of the exporter config file:

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
    timeout: 60
    onFailure: endLease
  afterLease:
    script: |
      j power off
    timeout: 30
    onFailure: warn
```

### Field Reference

| Field                    | Type    | Default      | Description                                                                                                                                                                                |
| ------------------------ | ------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `hooks.beforeLease`      | object  | *(none)*     | {term}`Hook` that runs after {term}`lease` assignment, before drivers are available                                                                                                                        |
| `hooks.afterLease`       | object  | *(none)*     | Cleanup {term}`hook` that runs when the session finishes, the lease ends, or the exporter stops, before session resources close |
| `hooks.<hook>.exec`      | string  | *(auto)*     | Interpreter used to execute the script. Auto-detected from file extension when not set (`.py` uses the exporter's Python, `.sh` uses `/bin/sh`). Defaults to `/bin/sh` for inline scripts. |
| `hooks.<hook>.script`    | string  | *(required)* | Inline script or path to a script file (auto-detected)                                                                                                                                     |
| `hooks.<hook>.timeout`   | integer | `120`        | Maximum execution time in seconds                                                                                                                                                          |
| `hooks.<hook>.onFailure` | string  | `"warn"`     | Action on failure: `"warn"`, `"endLease"`, or `"exit"`                                                                                                                                     |
| `hooks.<hook>.onInterrupt` | string | `"rerun"`   | What the restarted exporter does with the hook if an exporter restart cut it off: `"rerun"` or `"fail"`. See [Exporter Restarts](#exporter-restarts)                                     |

### Script Modes

The `script` field supports two modes, detected automatically:

- **Inline script** (default): When the value contains multiple lines or does
  not point to an existing file, it is passed to the interpreter with the `-c`
  flag (e.g. `/bin/sh -c "echo hello"`).
- **Script file**: When the value is a single line that matches an existing file
  on disk, the interpreter runs the file directly (e.g. `/bin/bash /opt/hooks/setup.sh`).

When `exec` is not set and the script is a file, the interpreter is
auto-detected from the file extension:

| Extension | Interpreter                          | Notes                                                                         |
| --------- | ------------------------------------ | ----------------------------------------------------------------------------- |
| `.py`     | {term}`Exporter`'s Python (`sys.executable`) | Has access to all installed packages including the Jumpstarter client library |
| `.sh`     | `/bin/sh`                            | POSIX shell                                                                   |
| *(other)* | `/bin/sh`                            | Fallback for unrecognized extensions                                          |

Set `exec` explicitly to override auto-detection (e.g. `exec: /bin/bash` for
a `.sh` file that needs bash features).

## Environment Variables

{term}`Hook` scripts receive a pre-configured environment that enables the `j` CLI to
communicate with the {term}`exporter` {term}`session`:

| Variable            | Description                                                                         |
| ------------------- | ----------------------------------------------------------------------------------- |
| `JUMPSTARTER_HOST`  | Unix socket path for `j` CLI access to the {term}`exporter` {term}`session`                         |
| `LEASE_NAME`        | Name of the current {term}`lease` assigned by the {term}`controller`                                |
| `CLIENT_NAME`       | Name of the client holding the {term}`lease` (the client that held it, for an `afterLease` hook run after it ended) |
| `JMP_DRIVERS_ALLOW` | Set to `UNSAFE` to enable access to all drivers ({term}`hook`s run locally on the {term}`exporter`) |
| `JMP_MOTD_FILE`     | `beforeLease` only: path to a file; content written here is appended to the motd shown to the client |

These variables are set automatically. The {term}`hook` uses a dedicated Unix socket
separate from the client connection to avoid protocol interference.

The {term}`hook` environment is also configured to signal noninteractive mode. Even
though {term}`hook`s run in a PTY (for line-buffered output), they are not interactive
{term}`session`s. The following variables are set to prevent programs from displaying
prompts or interactive UI:

| Variable              | Value            | Purpose                                             |
| --------------------- | ---------------- | --------------------------------------------------- |
| `TERM`                | `dumb`           | Disables colors, cursor movement, and terminal UI   |
| `DEBIAN_FRONTEND`     | `noninteractive` | Prevents `apt`/`dpkg` prompts on Debian-based hosts |
| `GIT_TERMINAL_PROMPT` | `0`              | Prevents git from prompting for credentials         |

Additionally, `PS1` is removed from the environment so the shell does not
emit a prompt.

## Logging

{term}`Hook` output is captured in the {term}`exporter`'s session log stream.
In controller mode, client connections wait for the before-hook outcome; its buffered output is
available once they connect. Connected clients receive after-hook output as the
hook runs. The `beforeLease` {term}`hook` output is tagged
with the `BEFORE_LEASE_HOOK` log source, and `afterLease` output is tagged
with `AFTER_LEASE_HOOK`.

{term}`Hook`s run inside a pseudo-terminal (PTY) to force line buffering, so output
is captured line by line rather than being held in a block buffer. This includes
`echo` statements, `j` CLI output, and other text written to the terminal.

```{note}
Because {term}`hook`s use a PTY, programs that detect terminal mode (such as
`grep --color=auto`) will behave as though running interactively.
```

## Failure Handling

The `onFailure` field controls what happens when a hook script exits with a
non-zero exit code or exceeds its timeout. A {term}`hook` is considered failed when the
shell process returns a non-zero exit code or when execution exceeds the
configured `timeout`.

### `warn`

The default mode. The failure is logged as a warning and the {term}`lease` lifecycle
continues as if the {term}`hook` succeeded:

- **`beforeLease`**: Drivers are unblocked and the client can connect normally.
  The exporter status transitions to `LEASE_READY`.
- **`afterLease`**: The {term}`exporter` returns to `AVAILABLE` while retaining the
  session until the client or controller releases the reservation.

This is useful for {term}`hook`s that perform best-effort actions where failure should
not disrupt the workflow.

### `endLease`

In controller mode, the {term}`lease` is ended and the client is notified of the failure:

The exporter requests release only when a hook fails with `onFailure: endLease`.
Successful cleanup, `warn`, skipped hooks, cancellation, and unexpected
orchestration errors do not request release. Normal completion is handled by
the client or controller expiry.

- **`beforeLease`**: The exporter status transitions to
  `BEFORE_LEASE_HOOK_FAILED`. The client discovers the failure through status
  polling and the {term}`lease` is released. The interactive shell and after-hook
  are skipped.
- **`afterLease`**: The exporter status transitions to
  `AFTER_LEASE_HOOK_FAILED`. Since the {term}`session` has already ended, this
  primarily serves as a signal to the client that cleanup did not complete
  successfully. The {term}`exporter` remains available for new {term}`lease`s.

This is the recommended mode for `beforeLease` validation {term}`hook`s where you want
the client to know immediately that the {term}`device` is not ready.

Direct mode has no reservation to release. It keeps the failed single session
until the exporter stops; a before-hook failure still skips the after-hook.

### `exit`

The {term}`exporter` shuts down entirely with exit code `1` (Failure):

- **`beforeLease`**: Report `BEFORE_LEASE_HOOK_FAILED` and request a drain. The
  after-hook is skipped.
- **`afterLease`**: Report `AFTER_LEASE_HOOK_FAILED` and request a drain.

In controller mode, failure status and logs remain available until the controller
releases the reservation, or a stronger stop-now request arrives. Teardown finishes
before the exporter goes offline. A terminal controller-stream failure retains its
own exit classification, and received signal codes take precedence over exporter
exit codes.

In direct mode, `exit` stops locally after the failure without waiting for a
reservation release.

The exit code `1` signals to service managers such as `systemd` that the shutdown
was intentional. If your `systemd` unit uses `Restart=always`, you should
configure `RestartPreventExitStatus=1` to prevent automatic restarts after an
`exit` failure.

```{warning}
The `exit` failure mode is a drastic action intended for critical failures
where the {term}`device` may be in an unusable state. It takes the {term}`exporter` offline
until manually restarted. Use `endLease` for most validation scenarios and
reserve `exit` for critical failures.
```

### Timeout Behavior

When a {term}`hook` exceeds its `timeout`, the process is terminated with `SIGTERM`
followed by `SIGKILL` if the process does not exit within a few seconds. The
resulting failure is then handled according to the `onFailure` setting, exactly
as if the script had exited with a non-zero exit code.

## Exporter Restarts

Restarting the {term}`exporter` process, for example with `systemctl restart`,
or a crash, does not end the {term}`lease` it is serving. {term}`Hook`s follow the
lease, not the process: `beforeLease` runs once when a lease starts, and
`afterLease` once when it ends.

- When the exporter stops during a lease (`SIGTERM`, `SIGINT`, `SIGQUIT`, or
  exiting after losing its connection to the controller), it does not run the
  `afterLease` hook and does not release the lease. A `beforeLease` hook that is
  still running is stopped. Open client connections drop.
- When it starts again, it continues from how far the lease's hooks got:

  | Hooks got to | Lease still active | Lease ended while the exporter was down |
  |---|---|---|
  | `beforeLease` finished | resumes the lease; no hook runs | runs `afterLease` |
  | `beforeLease` cut off | runs it again or fails it (`onInterrupt`) | runs `afterLease` |
  | `afterLease` cut off | runs it again or fails it (`onInterrupt`) | runs it again or fails it (`onInterrupt`) |
  | `beforeLease` failed with `endLease` or `exit` | skips `afterLease`; releases the lease for `endLease` | skips `afterLease` |

  A resumed lease is not set up again, and the restarted exporter does not reset
  its device to the default state a new lease starts from (such as the
  `default` port state of a Ykush driver), so the device stays as the client
  left it, unless its driver switches it off when the exporter stops (see the
  note below). A lease that is done with its hooks is never set up again;
  as without a restart, the exporter releases it only when a hook fails with
  `onFailure: endLease`, and otherwise leaves it to the client or its expiry.

The exporter reports itself available only after it has run an `afterLease` hook
it still owed, and the {term}`controller` does not assign a new lease to it
before that, so cleanup never runs on a device another lease is using.

To end the current lease before stopping, send `SIGHUP` instead: the exporter
waits for the lease to end, runs the `afterLease` hook, and then exits.

### Hooks Cut Off by a Restart

A hook that was running when the exporter stopped may have done any part of its
work. `onInterrupt` sets what the restarted exporter does with it:

- `rerun` (default): run the hook again. The `$LEASE_NAME` environment variable
  stays the same across runs, so a hook can use it to skip work it already did.
  After 3 attempts the hook fails instead.
- `fail`: do not run it again; it fails, and its `onFailure` applies. Use this
  for hooks that are not safe to run twice.

If the exporter is stopped while an `afterLease` hook is running, including one
it runs on starting for a lease that ended while it was down, it lets the hook
finish before exiting. Give the service manager a stop timeout longer than the
hook's `timeout`: systemd stops a service after `TimeoutStopSec=` (90 seconds by
default); podman stops a container after 10 seconds by default (`StopTimeout=`
in a Quadlet unit, which must stay below the unit's `TimeoutStopSec=`); and
Kubernetes after `terminationGracePeriodSeconds`.

### Requirements

The controller keeps a record of each lease's hooks in the Exporter status
(`leaseHooks`, see `kubectl get exporter <name> -o yaml`), and the exporter starts
a hook only after the controller has stored it as running. It also records how
the hook ended before going on, so a finished hook is never mistaken for one a
restart cut off. While the controller is unreachable, a lease therefore waits at
these points; clients cannot connect without the controller anyway, and no
hook runs before its start is recorded. An exporter that exits after losing
the controller leaves the rest to its next start: it runs an `afterLease` hook
the lease still owes, and handles a hook whose outcome was not recorded like
one a restart cut off. With a controller that does not keep this
record, the exporter behaves as in earlier releases:
stopping it runs the `afterLease` hook, and the restarted exporter runs the
`beforeLease` hook again. Exporters configured with `exitOnLeaseEnd` behave the
same way, because their runtime does not outlive the exporter process.

Rolling an exporter back to a release without these records is safe: it
registers without saying it keeps them, so the controller drops its record and
does not hold it for owed cleanup, and its leases behave as in earlier releases.
Upgrading it again starts without a record, like the first upgrade.

```{note}
Some drivers switch the device off whenever the exporter closes a session,
which includes stopping the exporter and starting it again: the Tasmota power
driver switches its outlet off, and the QEMU and Android emulator drivers power
their VM off. A resumed lease then finds the device off, and its `beforeLease`
hook does not run again to switch it on.
```

## Use Cases

### Device Initialization

Power on the {term}`device` and wait until it is reachable over SSH before the client
connects:

```yaml
hooks:
  beforeLease:
    script: |
      echo "Powering on device..."
      j power on
      echo "Waiting for SSH to become available..."
      for i in $(seq 1 30); do
        if j ssh -o ConnectTimeout=2 - echo "Device ready"; then
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
within the {term}`hook`'s `timeout`.

### Device Cleanup

Power off the {term}`device` after each {term}`lease` to ensure a clean environment for the
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

Flash known-good firmware before each test {term}`session` to guarantee a consistent
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

Point `script` to a `.py` file. The {term}`exporter` auto-detects the `.py`
extension and runs it with its own Python interpreter, so the {term}`hook` has
access to all installed packages including the Jumpstarter client library.
Python {term}`hook`s can use the driver client APIs directly by importing
`jumpstarter.utils.env.env`, which connects to the local {term}`exporter` {term}`session`
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
an {term}`exporter`.

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

- Keep {term}`hook` scripts short and focused on a single concern (initialization or
  cleanup).
- Set an appropriate `timeout` for each {term}`hook`. The default of 120 seconds may be
  too generous for simple scripts and too short for firmware flashing.
- Use `onFailure: endLease` for `beforeLease` validation so clients get
  immediate feedback when a {term}`device` is not ready.
- Use `onFailure: warn` for `afterLease` cleanup unless leaving the {term}`device` in a
  bad state poses a safety risk.
- Reserve `onFailure: exit` for critical failures that require manual
  intervention.
- {term}`Hook` output is streamed to the client in real time. Include informative
  `echo` statements for observability.
- The interpreter is auto-detected from the file extension (`.py` uses the
  {term}`exporter`'s Python, `.sh` uses `/bin/sh`). Set `exec` explicitly to
  override (e.g. `exec: /bin/bash` for bash-specific syntax).
- The `j` CLI is available in {term}`hook` scripts because `JUMPSTARTER_HOST` is set
  automatically. No additional configuration is needed.
