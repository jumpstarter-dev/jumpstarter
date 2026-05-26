# Android Emulator Driver

`jumpstarter-driver-androidemulator` manages Android emulator lifecycle with
ADB tunneling through Jumpstarter. It provides power control (start/stop) for
the Android emulator and combines it with the ADB driver for device access.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-androidemulator
```

For the optional Python ADB API:

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ "jumpstarter-driver-androidemulator[python-api]"
```

### Prerequisites

- Android SDK with emulator and platform-tools installed
- `emulator` and `adb` available on PATH (or specify `emulator_path`)
- An AVD created via Android Studio or `avdmanager`

#### Quick AVD Setup

```bash
# Apple Silicon (arm64)
sdkmanager "system-images;android-35;google_apis;arm64-v8a"
avdmanager create avd -n Pixel_6 -k "system-images;android-35;google_apis;arm64-v8a" -d pixel_6

# Intel/AMD (x86_64)
sdkmanager "system-images;android-35;google_apis;x86_64"
avdmanager create avd -n Pixel_6 -k "system-images;android-35;google_apis;x86_64" -d pixel_6
```

## Configuration

Example exporter configuration:

```{literalinclude} ../../../../../packages/jumpstarter-driver-androidemulator/examples/config.yaml
:language: yaml
```

### Configuration Parameters

| Parameter       | Description                        | Type | Required | Default    |
| --------------- | ---------------------------------- | ---- | -------- | ---------- |
| avd_name        | Name of the Android Virtual Device | str  | yes      |            |
| emulator_path   | Path to the emulator executable    | str  | no       | "emulator" |
| headless        | Run without a window               | bool | no       | true       |
| console_port    | Emulator console port              | int  | no       | 5554       |
| adb_server_port | Port for the custom ADB server     | int  | no       | 15037      |

## Usage

### CLI

```bash
# Power on the emulator
j android power on

# Check ADB devices through the tunnel
j android adb devices

# Run ADB commands
j android adb shell getprop ro.product.model

# Create a persistent ADB tunnel
j android adb tunnel

# Power off the emulator
j android power off
```

### Python API

```{literalinclude} ../../../../../packages/jumpstarter-driver-androidemulator/examples/usage.py
:language: python
```

## Architecture

This is a composite driver with two children:

- **`adb`** (`AdbServer` from `jumpstarter-driver-adb`): Manages the ADB server
  and provides TCP tunneling for remote ADB access
- **`power`** (`AndroidEmulatorPower`): Controls the emulator process lifecycle
  via the standard `PowerInterface` (on/off/read)

The emulator registers with the custom ADB server on port 15037 (via the
`ANDROID_ADB_SERVER_PORT` environment variable) to avoid conflicts with any
local ADB server on the standard port 5037.

## API Reference

### Driver

```{eval-rst}
.. autoclass:: jumpstarter_driver_androidemulator.driver.AndroidEmulator()
    :members:
```

```{eval-rst}
.. autoclass:: jumpstarter_driver_androidemulator.driver.AndroidEmulatorPower()
    :members: on, off, read
```

### Client

```{eval-rst}
.. autoclass:: jumpstarter_driver_androidemulator.client.AndroidEmulatorClient()
    :members: adb_device
```
