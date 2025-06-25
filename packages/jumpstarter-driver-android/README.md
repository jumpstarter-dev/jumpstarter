# Android Driver

`jumpstarter-driver-android` provides ADB and Android emulator functionality for Jumpstarter.

This functionality enables you to write test cases and custom drivers for physical
and virtual Android devices running in CI, on the edge, or on your desk.

## Installation

```bash
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-android
```

## Drivers

This package provides the following drivers:

### `AdbServer`

This driver can start, stop, and forward an ADB daemon server running on the exporter.

This driver implements the `TcpNetwork` driver from `jumpstarter-driver-network` to support forwarding the ADB connection through Jumpstarter.

#### Configuration

ADB server configuration example:

```yaml
export:
  adb:
    type: jumpstarter_driver_android.driver.AdbServer
    config:
      port: 1234 # Specify a custom port to run ADB on and forward
```

ADB configuration parameters:

| Parameter  | Description                      | Default Value | Optional | Supported Values        |
| ---------- | -------------------------------- | ------------- | -------- | ----------------------- |
| `adb_path` | Path to the ADB executable.      | `"adb"`       | Yes      | Any valid path          |
| `host`     | Host address for the ADB server. | `"127.0.0.1"` | Yes      | Any valid IP address.   |
| `port`     | Port for the ADB server.         | `5037`        | Yes      | `1` ≤ Integer ≤ `65535` |

### `Scrcpy`

This driver is a stub `TcpNetwork` driver to provide [`scrcpy`](https://github.com/Genymobile/scrcpy) support by managing its own ADB forwarding internally. This
allows developers to access a device via `scrcpy` without full ADB access if needed.

#### Configuration

Scrcpy configuration example:

```yaml
export:
  adb:
    type: jumpstarter_driver_android.driver.Scrcpy
    config:
      port: 1234 # Specify a custom port to look for ADB on
```

### `AndroidDevice`

This top-level composite driver provides an `adb` and `scrcpy` interfaces
to remotely control an Android device connected to the exporter.

#### Configuration

Android device configuration example:

```yaml
export:
  android:
    type: jumpstarter_driver_android.driver.AndroidDevice
    config:
      adb:
        port: 1234 # Specify a custom port to run ADB on
```

#### Children

- `adb` - `AdbServer` instance configured to tunnel the Android devices ADB connection.
- `scrcpy` - `Scrcpy` instance to remotely access an Android device's screen.

### `AndroidEmulator`

This composite driver extends the base `AndroidDevice` driver to provide a `power`
interface to remotely start/top an android emulator instance running on the exporter.

#### Children

- `adb` - `AdbServer` instance configured to tunnel the Android devices ADB connection.
- `scrcpy` - `Scrcpy` instance to remotely access an Android device's screen.
- `power` - `AndroidEmulatorPower` instance to turn on/off an emualtor instance.

#### Configuration

Android emulator configuration example:

```yaml
export:
  android:
    type: jumpstarter_driver_android.driver.AndroidEmulator
    config:
      adb: # Takes same parameters as the `AdbServer` driver
        port: 1234 # Specify a custom port to run ADB on
      emulator:
        avd: "Pixel_9_Pro"
        cores: 4
        memory: 2048
        # Add additional parameters as needed
```

Emulator configuration parameters:

| Parameter                 | Description                                        | Default Value | Optional | Supported Values                                           |
| ------------------------- | -------------------------------------------------- | ------------- | -------- | ---------------------------------------------------------- |
| `emulator_path`           | Path to the emulator executable.                   | `"emulator"`  | Yes      | Any valid path                                             |
| `avd`                     | Specifies the Android Virtual Device (AVD) to use. | `"default"`   | Yes      | Any valid AVD name                                         |
| `cores`                   | Number of CPU cores to allocate.                   | `4`           | Yes      | Integer ≥ `1`                                              |
| `memory`                  | Amount of RAM (in MB) to allocate.                 | `2048`        | Yes      | `1024` ≤ Integer ≤ 16384                                   |
| `sysdir`                  | Path to the system directory.                      | `null`        | Yes      | Any valid path                                             |
| `system`                  | Path to the system image.                          | `null`        | Yes      | Any valid path                                             |
| `vendor`                  | Path to the vendor image.                          | `null`        | Yes      | Any valid path                                             |
| `kernel`                  | Path to the kernel image.                          | `null`        | Yes      | Any valid path                                             |
| `ramdisk`                 | Path to the ramdisk image.                         | `null`        | Yes      | Any valid path                                             |
| `data`                    | Path to the data partition.                        | `null`        | Yes      | Any valid path                                             |
| `sdcard`                  | Path to the SD card image.                         | `null`        | Yes      | Any valid path                                             |
| `partition_size`          | Size of the system partition (in MB).              | `2048`        | Yes      | `512` ≤ Integer ≤ `16384`                                  |
| `writable_system`         | Enables writable system partition.                 | `false`       | Yes      | `true`, `false`                                            |
| `cache`                   | Path to the cache partition.                       | `null`        | Yes      | Any valid path                                             |
| `cache_size`              | Size of the cache partition (in MB).               | `null`        | Yes      | Integer ≥ `16`                                             |
| `no_cache`                | Disables the cache partition.                      | `false`       | Yes      | `true`, `false`                                            |
| `no_snapshot`             | Disables snapshots.                                | `false`       | Yes      | `true`, `false`                                            |
| `no_snapshot_load`        | Prevents loading snapshots.                        | `false`       | Yes      | `true`, `false`                                            |
| `no_snapshot_save`        | Prevents saving snapshots.                         | `false`       | Yes      | `true`, `false`                                            |
| `snapshot`                | Specifies a snapshot to load.                      | `null`        | Yes      | Any valid path                                             |
| `force_snapshot_load`     | Forces loading of the specified snapshot.          | `false`       | Yes      | `true`, `false`                                            |
| `no_snapshot_update_time` | Prevents updating snapshot timestamps.             | `false`       | Yes      | `true`, `false`                                            |
| `qcow2_for_userdata`      | Enables QCOW2 format for userdata.                 | `false`       | Yes      | `true`, `false`                                            |
| `no_window`               | Runs the emulator without a graphical window.      | `true`        | Yes      | `true`, `false`                                            |
| `gpu`                     | Specifies the GPU mode.                            | `"auto"`      | Yes      | `"auto"`, `"host"`, `"swiftshader"`, `"angle"`, `"guest"`  |
| `gpu_mode`                | Specifies the GPU rendering mode.                  | `"auto"`      | Yes      | `"auto"`, `"host"`, `"swiftshader"`, `"angle"`, `"guest"`  |
| `no_boot_anim`            | Disables the boot animation.                       | `false`       | Yes      | `true`, `false`                                            |
| `skin`                    | Specifies the emulator skin.                       | `null`        | Yes      | Any valid path                                             |
| `dpi_device`              | Sets the screen DPI.                               | `null`        | Yes      | Integer ≥ 0                                                |
| `fixed_scale`             | Enables fixed scaling.                             | `false`       | Yes      | `true`, `false`                                            |
| `scale`                   | Sets the emulator scale.                           | `"1"`         | Yes      | Any valid scale                                            |
| `vsync_rate`              | Sets the vertical sync rate.                       | `null`        | Yes      | Integer ≥ 1                                                |
| `qt_hide_window`          | Hides the emulator window in Qt.                   | `false`       | Yes      | `true`, `false`                                            |
| `multidisplay`            | Configures multiple displays.                      | `[]`          | Yes      | List of tuples                                             |
| `no_location_ui`          | Disables the location UI.                          | `false`       | Yes      | `true`, `false`                                            |
| `no_hidpi_scaling`        | Disables HiDPI scaling.                            | `false`       | Yes      | `true`, `false`                                            |
| `no_mouse_reposition`     | Disables mouse repositioning.                      | `false`       | Yes      | `true`, `false`                                            |
| `virtualscene_poster`     | Configures virtual scene posters.                  | `{}`          | Yes      | Dictionary                                                 |
| `guest_angle`             | Enables guest ANGLE.                               | `false`       | Yes      | `true`, `false`                                            |
| `wifi_client_port`        | Port for Wi-Fi client.                             | `null`        | Yes      | `1` ≤ Integer ≤ `65535`                                    |
| `wifi_server_port`        | Port for Wi-Fi server.                             | `null`        | Yes      | `1` ≤ Integer ≤ `65535`                                    |
| `net_tap`                 | Configures network TAP.                            | `null`        | Yes      | Any valid path                                             |
| `net_tap_script_up`       | Script to run when TAP is up.                      | `null`        | Yes      | Any valid path                                             |
| `net_tap_script_down`     | Script to run when TAP is down.                    | `null`        | Yes      | Any valid path                                             |
| `dns_server`              | Specifies the DNS server.                          | `null`        | Yes      | Any valid IP                                               |
| `http_proxy`              | Configures the HTTP proxy.                         | `null`        | Yes      | Any valid proxy                                            |
| `netdelay`                | Configures network delay.                          | `"none"`      | Yes      | `"none"`, `"umts"`, `"gprs"`, `"edge"`, `"hscsd"`          |
| `netspeed`                | Configures network speed.                          | `"full"`      | Yes      | `"full"`, `"gsm"`, `"hscsd"`, `"gprs"`, `"edge"`, `"umts"` |
| `port`                    | Specifies the emulator port.                       | `5554`        | Yes      | `5554` ≤ Integer ≤ `5682`                                  |
| `no_audio`                | Disables audio in the emulator.                    | `false`       | Yes      | `true`, `false`                                            |
| `audio`                   | Configures audio settings.                         | `null`        | Yes      | Any valid path                                             |
| `allow_host_audio`        | Enables host audio.                                | `false`       | Yes      | `true`, `false`                                            |
| `camera_back`             | Configures the back camera.                        | `"emulated"`  | Yes      | `"emulated"`, `"webcam0"`, `"none"`                        |
| `camera_front`            | Configures the front camera.                       | `"emulated"`  | Yes      | `"emulated"`, `"webcam0"`, `"none"`                        |
| `timezone`                | Sets the emulator's timezone.                      | `null`        | Yes      | Any valid timezone                                         |
| `change_language`         | Changes the language.                              | `null`        | Yes      | Any valid language                                         |
| `change_country`          | Changes the country.                               | `null`        | Yes      | Any valid country                                          |
| `change_locale`           | Changes the locale.                                | `null`        | Yes      | Any valid locale                                           |
| `encryption_key`          | Configures the encryption key.                     | `null`        | Yes      | Any valid path                                             |
| `selinux`                 | Configures SELinux mode.                           | `null`        | Yes      | `"enforcing"`, `"permissive"`, `"disabled"`                |
| `accel`                   | Configures hardware acceleration.                  | `"auto"`      | Yes      | `"auto"`, `"off"`, `"on"`                                  |
| `no_accel`                | Disables hardware acceleration.                    | `false`       | Yes      | `true`, `false`                                            |
| `engine`                  | Configures the emulator engine.                    | `"auto"`      | Yes      | `"auto"`, `"qemu"`, `"swiftshader"`                        |
| `verbose`                 | Enables verbose logging.                           | `false`       | Yes      | `true`, `false`                                            |
| `show_kernel`             | Displays kernel messages.                          | `false`       | Yes      | `true`, `false`                                            |
| `logcat`                  | Configures logcat filters.                         | `null`        | Yes      | Any valid filter                                           |
| `debug_tags`              | Configures debug tags.                             | `null`        | Yes      | Any valid tags                                             |
| `tcpdump`                 | Configures TCP dump.                               | `null`        | Yes      | Any valid path                                             |
| `detect_image_hang`       | Detects image hangs.                               | `false`       | Yes      | `true`, `false`                                            |
| `save_path`               | Configures save path.                              | `null`        | Yes      | Any valid path                                             |
| `grpc_port`               | Configures gRPC port.                              | `null`        | Yes      | `1` ≤ Integer ≤ `65535`                                    |
| `grpc_tls_key`            | Configures gRPC TLS key.                           | `null`        | Yes      | Any valid path                                             |
| `grpc_tls_cert`           | Configures gRPC TLS certificate.                   | `null`        | Yes      | Any valid path                                             |
| `grpc_tls_ca`             | Configures gRPC TLS CA.                            | `null`        | Yes      | Any valid path                                             |
| `grpc_use_token`          | Enables gRPC token usage.                          | `false`       | Yes      | `true`, `false`                                            |
| `grpc_use_jwt`            | Enables gRPC JWT usage.                            | `true`        | Yes      | `true`, `false`                                            |
| `acpi_config`             | Configures ACPI settings.                          | `null`        | Yes      | Any valid path                                             |
| `append_userspace_opt`    | Appends userspace options.                         | `{}`          | Yes      | Dictionary                                                 |
| `feature`                 | Configures emulator features.                      | `{}`          | Yes      | Dictionary                                                 |
| `icc_profile`             | Configures ICC profile.                            | `null`        | Yes      | Any valid path                                             |
| `sim_access_rules_file`   | Configures SIM access rules.                       | `null`        | Yes      | Any valid path                                             |
| `phone_number`            | Configures phone number.                           | `null`        | Yes      | Any valid number                                           |
| `usb_passthrough`         | Configures USB passthrough.                        | `null`        | Yes      | Tuple of integers                                          |
| `waterfall`               | Configures waterfall display.                      | `null`        | Yes      | Any valid path                                             |
| `restart_when_stalled`    | Restarts emulator when stalled.                    | `false`       | Yes      | `true`, `false`                                            |
| `wipe_data`               | Wipes user data on startup.                        | `false`       | Yes      | `true`, `false`                                            |
| `delay_adb`               | Delays ADB startup.                                | `false`       | Yes      | `true`, `false`                                            |
| `quit_after_boot`         | Quits emulator after boot.                         | `null`        | Yes      | Integer ≥ 0                                                |
| `qemu_args`               | Configures QEMU arguments.                         | `[]`          | Yes      | List of strings                                            |
| `props`                   | Configures emulator properties.                    | `{}`          | Yes      | Dictionary                                                 |
| `env`                     | Configures environment variables.                  | `{}`          | Yes      | Dictionary                                                 |

### `AndroidEmulatorPower`

This driver implements the `PowerInterface` from the `jumpstarter-driver-power`
package to turn on/off the android emulator running on the exporter.

> ⚠️ **Warning:** This driver should not be used standalone as it does not provide ADB forwarding.

## Clients

The Android driver provides the following clients for interacting with Android devices/emulators.

### `AndroidClient`

The `AndroidClient` provides a generic composite client for interacting with Android devices.

#### CLI

```plain
$ jmp shell --exporter-config ~/.config/jumpstarter/exporters/android-local.yaml

~/jumpstarter ⚡ local ➤ j android
Usage: j android [OPTIONS] COMMAND [ARGS]...

  Generic composite device

Options:
  --help  Show this message and exit.

Commands:
  adb     Run adb using a local executable against the remote adb server.
  power   Generic power
  scrcpy  Run scrcpy using a local executable against the remote adb server.

~/repos/jumpstarter ⚡ local ➤ exit
```

### `AdbClient`

The `AdbClient` provides methods to forward the ADB server from an exporter to the client and interact with ADB either through the [`adbutils`](https://github.com/openatx/adbutils) Python package or via the `adb` CLI tool.

### CLI

This client provides a wrapper CLI around your local `adb` tool to provide additional
Jumpstarter functionality such as automatic port forwarding and remote control
of the ADB server on the exporter.

```plain
~/jumpstarter ⚡local ➤ j android adb --help
Usage: j android adb [OPTIONS] [ARGS]...

  Run adb using a local adb binary against the remote adb server.

  This command is a wrapper around the adb command-line tool. It allows you to
  run regular adb commands with an automatically forwarded adb server running
  on your Jumpstarter exporter.

  When executing this command, the exporter adb daemon is forwarded to a local
  port. The adb server address and port are automatically set in the
  environment variables ANDROID_ADB_SERVER_ADDRESS and
  ANDROID_ADB_SERVER_PORT, respectively. This configures your local adb client
  to communicate with the remote adb server.

  Most command line arguments and commands are passed directly to the adb CLI.
  However, some arguments and commands are not supported by the Jumpstarter
  adb client. These options include: -a, -d, -e, -L, --one-device.

  The following adb commands are also not supported in remote adb
  environments: connect, disconnect, reconnect, nodaemon, pair

  When running start-server or kill-server, Jumpstarter will start or kill the
  adb server on the exporter.

  Use the forward-adb command to forward the adb server address and port to a
  local port manually.

Options:
  -H TEXT     Local adb host to forward to.  [default: 127.0.0.1]
  -P INTEGER  Local adb port to forward to.  [default: 5038]
  --adb TEXT  Path to the ADB executable  [default: adb]
  --help      Show this message and exit.
```

### API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_android.client.AdbClient()
    :members: forward_adb, adb_client
```

### `ScrcpyClient`

The `ScrcpyClient` provides CLI integration with the [`scrcpy`](https://github.com/Genymobile/scrcpy) tool for remotely interacting with physical and virtual Android devices.

> **Note:** The `scrcpy` CLI tool is required on your client device to use this driver client.

#### CLI

Similar to the ADB client, the `ScrcpyClient` also provides a wrapper around
the local `scrcpy` tool to automatically port-forward the ADB connection.

```plain
~/jumpstarter ⚡local ➤ j android scrcpy --help
Usage: j android scrcpy [OPTIONS] [ARGS]...

  Run scrcpy using a local executable against the remote adb server.

  This command is a wrapper around the scrcpy command-line tool. It allows you
  to run scrcpy against a remote Android device through an ADB server tunneled
  via Jumpstarter.

  When executing this command, the adb server address and port are forwarded
  to the local scrcpy executable. The adb server socket path is set in the
  environment variable ADB_SERVER_SOCKET, allowing scrcpy to communicate with
  the remote adb server.

  Most command line arguments are passed directly to the scrcpy executable.

Options:
  -H TEXT        Local adb host to forward to.  [default: 127.0.0.1]
  -P INTEGER     Local adb port to forward to.  [default: 5038]
  --scrcpy TEXT  Path to the scrcpy executable  [default: scrcpy]
  --help         Show this message and exit.
```
