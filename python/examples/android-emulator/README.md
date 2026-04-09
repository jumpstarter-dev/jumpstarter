# Android Emulator Testing Example

This example demonstrates testing an Android device through Jumpstarter's ADB
tunneling. It boots an Android emulator, connects via ADB through the
Jumpstarter gRPC pipeline, and runs tests using the `adbutils` Python API.

## Prerequisites

- Android SDK with emulator and platform-tools
- An Android Virtual Device (AVD) created

## Quick Setup

The `setup.sh` script handles SDK detection, system image installation, and AVD
creation automatically:

```bash
cd python/examples/android-emulator
source setup.sh
```

This will:

1. Find your Android SDK (checks `ANDROID_HOME`, then common default locations)
2. Detect your CPU architecture (arm64 for Apple Silicon, x86_64 for Intel)
3. Install the appropriate system image if not present
4. Create a `jumpstarter_test` AVD if it doesn't exist
5. Set `PATH` and `ANDROID_AVD_NAME` environment variables

## Running the Tests

```bash
pytest jumpstarter_example_android_emulator/ -v
```

Or with a custom AVD:

```bash
ANDROID_AVD_NAME=my_avd pytest jumpstarter_example_android_emulator/ -v
```

## What the Tests Demonstrate

All tests run through the full Jumpstarter driver/gRPC/client pipeline:

| Test                     | Description                                         |
| ------------------------ | --------------------------------------------------- |
| `test_device_properties` | Read device model and SDK version via `getprop`     |
| `test_list_packages`     | List installed packages, verify Settings app exists |
| `test_launch_settings`   | Launch built-in Settings activity                   |
| `test_file_push_pull`    | Push/pull files to/from the device                  |
| `test_display_size`      | Read display dimensions                             |
| `test_battery_info`      | Read battery status from `dumpsys`                  |
| `test_adb_list_devices`  | Verify ADB server on exporter sees the emulator     |

## Architecture

```text
[Test Code] -> [AndroidEmulatorClient] -> [gRPC] -> [AndroidEmulator Driver]
                    |                                       |
                    |-- .power.on/off()                     |-- AndroidEmulatorPower (emulator process)
                    |-- .adb.forward_adb()                  |-- AdbServer (port 15037)
                    |-- .adb_device()                       |
                    v                                       v
              [adbutils] <-- TCP tunnel --> [ADB Server] <-- [Emulator]
```

The emulator boots once (session-scoped fixture) and all tests share the
connection, keeping total run time reasonable despite the ~60 second boot time.
