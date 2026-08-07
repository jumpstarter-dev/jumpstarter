# ADB Driver

`jumpstarter-driver-adb` tunnels Android Debug Bridge (ADB) connections over Jumpstarter, enabling remote Android device access via standard ADB tools such as Android Studio.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-adb
```

For the optional Python ADB API:

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ "jumpstarter-driver-adb[python-api]"
```

## Configuration

Example exporter configuration:

```yaml
export:
  adb:
    type: jumpstarter_driver_adb.driver.AdbServer
    config:
      host: "127.0.0.1"
      port: 15037
```

### Configuration Parameters

| Parameter | Description                                    | Type | Required | Default                    |
| --------- | ---------------------------------------------- | ---- | -------- | -------------------------- |
| adb_path        | Path to the ADB executable on the exporter          | str   | no       | "adb" (resolved from PATH) |
| host            | Host address of the ADB server on the exporter      | str   | no       | "127.0.0.1"                |
| port            | Port of the ADB server on the exporter              | int   | no       | 15037                      |
| connect_timeout | Timeout (seconds) for `connect`/`disconnect` commands | float | no       | 30.0                       |

### Port Assignment

The exporter runs its own ADB server on a non-standard port (default: 15037)
to avoid conflicting with the standard ADB server on port 5037
(if Jumpstarter is running in local mode). This is important because tools like
Android Studio automatically start and maintain an ADB server on port 5037 and
will restart it if killed.

On the client side, the `tunnel` command binds to an auto-assigned port by
default. Use `-P` to specify a port (such as 5037) if needed.

## Usage

### Run ADB commands

All standard adb commands are passed through to the remote ADB server:

```bash
# List devices
j adb devices

# Interactive shell
j adb shell

# Run a command on the device
j adb shell getprop ro.product.model

# Install an app
j adb install app.apk

# View device logs
j adb logcat

# Push/pull files
j adb push local_file.txt /sdcard/
j adb pull /sdcard/remote_file.txt .
```

### Persistent tunnel

The `tunnel` command is the only Jumpstarter-specific command. All other
commands (including `start-server`, `kill-server`, `connect`, `disconnect`,
`reconnect`, `pair`) are passed through to the remote ADB server.

```bash
# Create a persistent ADB tunnel (auto-assigned port)
j adb tunnel

# Create a tunnel on a specific port
j adb tunnel -P 5038

# Background the tunnel for continued shell use
j adb tunnel &
```

When a persistent tunnel is running, subsequent `j adb` commands will
automatically reuse it instead of creating a new ephemeral tunnel. This
makes commands faster and ensures a consistent connection.

For native `adb` or external tools, export the env vars printed by the
`tunnel` command in another terminal.

### Unsupported commands

The `nodaemon` command is not supported as it would start a local ADB server
process, ignoring the tunnel entirely.

### Connecting to a remote device

When the Android device is **not** attached to the exporter over USB but is
reachable over the network (for example a virtual device such as
[Cuttlefish](https://source.android.com/docs/devices/cuttlefish), or a device
exposing `adb` over TCP/IP), the exporter's ADB server must `connect` to it
before any `adb` command will see it.

The `connect_device` / `disconnect_device` driver methods run
`adb connect <host:port>` / `adb disconnect <host:port>` on the exporter. The
address is supplied by the caller — this driver does **not** discover or scan
for devices. Timeouts and command failures raise, so callers can react instead
of receiving a silent error string.

#### From the CLI

`connect` and `disconnect` are also plain adb commands, so they pass through the
tunnel like any other:

```bash
# Connect the exporter's ADB server to a networked device, then use it
j adb connect 10.0.0.5:6520
j adb devices
j adb shell getprop ro.product.model
j adb disconnect 10.0.0.5:6520
```

#### From a parent (composite) driver

The intended use case is a higher-level driver that owns the device lifecycle
and knows the address deterministically — no IP discovery needed. For example,
the Cuttlefish driver embeds an `AdbServer` child and connects to a pinned
address derived from its own config (`host` + an ADB port computed from the
instance number) after the virtual device is created:

```python
class CuttlefishServer(CompositeInterface, Driver):
    def __post_init__(self):
        super().__post_init__()
        # AdbServer runs on the exporter; the parent drives connect/disconnect
        self.children["adb"] = AdbServer(host="127.0.0.1", port=self.adb_server_port)

    def _adb_device(self) -> str:
        # Address is known from config, never scanned
        return f"{self.host}:{6520 + self.instance_num - 1}"

    def _connect(self):
        adb = self.children["adb"]
        device = self._adb_device()
        try:
            adb.connect_device(device)
        except (subprocess.CalledProcessError, TimeoutError) as e:
            # Device may not be up yet; the boot-wait loop below reconnects.
            self.logger.warning("ADB connect to %s failed (%s); retrying while waiting for boot", device, e)
        # unexpected exceptions (config/programming errors) propagate

    def _wait_for_boot(self):
        adb = self.children["adb"]
        device = self._adb_device()
        deadline = time.monotonic() + self.boot_timeout
        while time.monotonic() < deadline:
            try:
                adb.connect_device(device)
                if self._is_booted(device):
                    return
            except (subprocess.CalledProcessError, TimeoutError):
                pass
            time.sleep(3)
        raise TimeoutError(f"{device} did not come online within {self.boot_timeout}s")
```

Because `connect_device` raises on failure or timeout, the parent catches only
the *expected* connection failures (letting configuration or programming errors
propagate) and drives its own retry loop rather than parsing return strings.

### Integration with Android Ecosystem Tools

#### Forward ADB for external tools

The `tunnel` command creates a persistent tunnel that other `j adb` commands
reuse automatically. For external tools, export the env vars printed by the
command:

```bash
# In the jmp shell:
j adb tunnel
```

```bash
# In another terminal, using the port printed by the tunnel command:
export ANDROID_ADB_SERVER_ADDRESS=127.0.0.1
export ANDROID_ADB_SERVER_PORT=<port>
adb devices
```

#### Android Studio

Android Studio automatically starts and maintains its own ADB server on
port 5037. Because of this, the `tunnel` command uses an auto-assigned port
by default to avoid conflicts.

To use the tunnel with Android Studio:

1. Note the port printed by `j adb tunnel`
2. Configure Android Studio to use a custom ADB server port, or:
3. Kill Android Studio's ADB server, bind the tunnel to port 5037, and
   restart Android Studio:

```bash
adb kill-server
j adb tunnel -P 5037
# Note: Android Studio may restart the ADB server on 5037 when opened,
# causing a conflict. If this happens, use the auto-assigned port instead.
```

#### Trade Federation (tradefed)

tradefed discovers devices through the ADB server via the
`ANDROID_ADB_SERVER_PORT` environment variable:

```bash
# Terminal 1: Start the tunnel
j adb tunnel
# Note the port, e.g. 54321

# Terminal 2: Run tradefed with the tunnel port
export ANDROID_ADB_SERVER_PORT=54321
tradefed.sh
# > list devices   <-- shows remote devices
```

#### Python API

You can also perform interactions via ADB using the
[`adbutils`](https://github.com/openatx/adbutils) Python package.

```python
# Requires: pip install jumpstarter-driver-adb[python-api]
import adbutils

with client.adb.forward_adb(port=0) as (host, port):
    adb = adbutils.AdbClient(host=host, port=port)
    for device in adb.device_list():
        print(device.serial, device.prop.model)
```

### CLI

#### Standard ADB commands (passed through)

| Usage                         | Description                                       |
| ----------------------------- | ------------------------------------------------- |
| `j adb <command> [args...]`   | Run any adb command against the remote ADB server |
| `j adb devices`               | List connected devices                            |
| `j adb shell [command]`       | Open a shell or run a command on the device       |
| `j adb install <apk>`         | Install an APK                                    |
| `j adb push <local> <remote>` | Push a file to the device                         |
| `j adb pull <remote> <local>` | Pull a file from the device                       |
| `j adb logcat`                | View device logs                                  |

#### Jumpstarter-specific commands

| Usage                    | Description                                                             |
| ------------------------ | ----------------------------------------------------------------------- |
| `j adb tunnel [-P PORT]` | Create a persistent ADB tunnel (auto-assigned port, or specify with -P) |

#### Options

| Option       | Description                          | Default   |
| ------------ | ------------------------------------ | --------- |
| `-H HOST`    | Local address to tunnel ADB to       | 127.0.0.1 |
| `-P PORT`    | Local port to tunnel ADB to (0=auto) | 0         |
| `--adb PATH` | Path to local adb executable         | adb       |

## API Reference

### Driver

```{eval-rst}
.. autoclass:: jumpstarter_driver_adb.driver.AdbServer()
    :members: start_server, kill_server, connect_device, disconnect_device, list_devices
```

### Client

```{eval-rst}
.. autoclass:: jumpstarter_driver_adb.client.AdbClient()
    :members: forward_adb, start_server, kill_server, connect_device, disconnect_device, list_devices
```
