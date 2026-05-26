# Hooks

Jumpstarter supports lifecycle hooks that execute shell scripts automatically before or after a {term}`lease`.

A `beforeLease` hook runs after a lease is assigned but
before drivers are available to the client, and an `afterLease` hook runs after
the {term}`session` ends but before the lease is released. Hooks are optional and
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
    Exporter->>Controller: Release lease
```

The {term}`exporter` transitions through these states during a {term}`lease`:

1. **{term}`Lease` assigned** - The {term}`controller` assigns a {term}`lease` to the {term}`exporter`.
2. **`BEFORE_LEASE_HOOK`** - The `beforeLease` script runs. Driver access is
   blocked until the {term}`hook` completes successfully.
3. **`LEASE_READY`** - The {term}`hook` succeeded and the client can now access
   drivers.
4. **Client {term}`session`** - The client uses drivers normally.
5. **{term}`Session` ends** - The client disconnects or the {term}`lease` is released.
6. **`AFTER_LEASE_HOOK`** - The `afterLease` script runs. The {term}`session` remains
   open so `j` commands can still interact with drivers.
7. **`AVAILABLE`** - The {term}`hook` completed and the {term}`lease` is released. The
   {term}`exporter` is ready for the next {term}`lease`.

```{note}
If no {term}`hook`s are configured, the {term}`exporter` transitions directly from {term}`lease`
assignment to `LEASE_READY` and from {term}`session` end to `AVAILABLE`.
```

## Configuration

{term}`Hook`s are configured in the `hooks` section of the exporter config file:

```{literalinclude} ../examples/introduction/hooks_exporter_config.yaml
:language: yaml
```

### Field Reference

| Field                    | Type    | Default      | Description                                                                                                                                                                                |
| ------------------------ | ------- | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `hooks.beforeLease`      | object  | *(none)*     | {term}`Hook` that runs after {term}`lease` assignment, before drivers are available                                                                                                                        |
| `hooks.afterLease`       | object  | *(none)*     | {term}`Hook` that runs after the {term}`session` ends, before the {term}`lease` is released                                                                                                                        |
| `hooks.<hook>.exec`      | string  | *(auto)*     | Interpreter used to execute the script. Auto-detected from file extension when not set (`.py` uses the exporter's Python, `.sh` uses `/bin/sh`). Defaults to `/bin/sh` for inline scripts. |
| `hooks.<hook>.script`    | string  | *(required)* | Inline script or path to a script file (auto-detected)                                                                                                                                     |
| `hooks.<hook>.timeout`   | integer | `120`        | Maximum execution time in seconds                                                                                                                                                          |
| `hooks.<hook>.onFailure` | string  | `"warn"`     | Action on failure: `"warn"`, `"endLease"`, or `"exit"`                                                                                                                                     |

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
| `CLIENT_NAME`       | Name of the client holding the {term}`lease`                                                |
| `JMP_DRIVERS_ALLOW` | Set to `UNSAFE` to enable access to all drivers ({term}`hook`s run locally on the {term}`exporter`) |

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

{term}`Hook` output is streamed to the client in real time. Every line written to
stdout or stderr by the {term}`hook` script is captured and forwarded to the client
through the {term}`exporter`'s log stream. The `beforeLease` {term}`hook` output is tagged
with the `BEFORE_LEASE_HOOK` log source, and `afterLease` output is tagged
with `AFTER_LEASE_HOOK`.

{term}`Hook`s run inside a pseudo-terminal (PTY) to force line buffering, so output
appears on the client as each line is written rather than being held in a
block buffer. This means `echo` statements, `j` CLI output, and any other
text written to the terminal will be visible immediately.

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
- **`afterLease`**: The {term}`exporter` returns to `AVAILABLE` and the {term}`lease` is
  released normally.

This is useful for {term}`hook`s that perform best-effort actions where failure should
not disrupt the workflow.

### `endLease`

The {term}`lease` is ended and the client is notified of the failure:

- **`beforeLease`**: The exporter status transitions to
  `BEFORE_LEASE_HOOK_FAILED`. The client discovers the failure through status
  polling and the {term}`lease` is released. The interactive shell is skipped.
- **`afterLease`**: The exporter status transitions to
  `AFTER_LEASE_HOOK_FAILED`. Since the {term}`session` has already ended, this
  primarily serves as a signal to the client that cleanup did not complete
  successfully. The {term}`exporter` remains available for new {term}`lease`s.

This is the recommended mode for `beforeLease` validation {term}`hook`s where you want
the client to know immediately that the {term}`device` is not ready.

### `exit`

The {term}`exporter` shuts down entirely with exit code `1` (Failure):

- **`beforeLease`**: The exporter status transitions to
  `BEFORE_LEASE_HOOK_FAILED`. The {term}`exporter` then shuts down, going offline. The
  shutdown is deferred until the client has had a chance to observe the failure
  status.
- **`afterLease`**: The exporter status transitions to
  `AFTER_LEASE_HOOK_FAILED` and the {term}`exporter` shuts down immediately.

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

## Use Cases

### Device Initialization

Power on the {term}`device` and wait until it is reachable over SSH before the client
connects:

```{literalinclude} ../examples/introduction/hook_device_init.yaml
:language: yaml
```

Note that the `j ssh` command does not have a built-in connection timeout, so
each attempt uses the system SSH default (typically ~30 seconds). Passing
`-o ConnectTimeout=2` keeps each retry attempt short so the loop can complete
within the {term}`hook`'s `timeout`.

### Device Cleanup

Power off the {term}`device` after each {term}`lease` to ensure a clean environment for the
next user:

```{literalinclude} ../examples/introduction/hook_device_cleanup.yaml
:language: yaml
```

### Firmware Flashing

Flash known-good firmware before each test {term}`session` to guarantee a consistent
starting state:

```{literalinclude} ../examples/introduction/hook_firmware_flash.yaml
:language: yaml
```

### Using Bash

Set `exec: /bin/bash` to use bash-specific features such as `[[ ]]` tests,
arrays, and process substitution:

```{literalinclude} ../examples/introduction/hook_bash.yaml
:language: yaml
```

### Using Python

Point `script` to a `.py` file. The {term}`exporter` auto-detects the `.py`
extension and runs it with its own Python interpreter, so the {term}`hook` has
access to all installed packages including the Jumpstarter client library.
Python {term}`hook`s can use the driver client APIs directly by importing
`jumpstarter.utils.env.env`, which connects to the local {term}`exporter` {term}`session`
via the `JUMPSTARTER_HOST` socket automatically.

Exporter config:

```{literalinclude} ../examples/introduction/hook_python_config.yaml
:language: yaml
```

`/opt/jumpstarter/hooks/prepare_device.py`:

```{literalinclude} ../examples/introduction/hook_prepare_device.py
:language: python
```

The `env()` context manager returns a `DriverClient` whose attributes
correspond to the exported drivers (e.g. `client.power`, `client.storage`).
This is the same API used by the `j` CLI and by test scripts connecting to
an {term}`exporter`.

### Using a Script File

Point `script` to an existing file on disk instead of writing the script
inline. The interpreter runs the file directly:

```{literalinclude} ../examples/introduction/hook_script_file.yaml
:language: yaml
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
