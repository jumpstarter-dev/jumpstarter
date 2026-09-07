# NanoKVM-USB Driver

`jumpstarter-driver-nanokvm-usb` provides KVM (Keyboard, Video, Mouse) control for
[NanoKVM-USB](https://github.com/sipeed/NanoKVM-USB) devices connected directly
to the exporter host over USB.

Unlike the network-based [NanoKVM](nanokvm.md) driver, this package talks to the
hardware through:

- **USB Serial** (default 57600 baud) for keyboard and mouse HID reports
- **UVC** (USB video class) for HDMI capture as a standard camera device

## Features

- **Video capture**: Snapshots and live JPEG frame streams from the UVC device
- **Keyboard control**: Paste text and press keys via serial HID
- **Mouse control**: Absolute and relative movement, clicks, and scrolling
- **Composite driver**: Access video and HID through a unified `NanoKVMUSB` interface

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-nanokvm-usb
```

## Configuration

### Basic configuration

```yaml
export:
  nanokvm-usb:
    type: jumpstarter_driver_nanokvm_usb.driver.NanoKVMUSB
    config:
      serial_port: "/dev/ttyACM0"
      baud_rate: 57600
      video_device: 0
      video_width: 1920
      video_height: 1080
      video_fps: 30
      screen_width: 1920
      screen_height: 1080
```

### Config parameters

| Parameter | Description | Type | Required | Default |
|-----------|-------------|------|----------|---------|
| serial_port | Serial device path for HID | str | yes | |
| baud_rate | Serial baud rate | int | no | 57600 |
| video_device | OpenCV camera index or device path | int/str | no | 0 |
| video_width | Requested capture width | int | no | 1920 |
| video_height | Requested capture height | int | no | 1080 |
| video_fps | Capture rate for `stream()` | int | no | 30 |
| screen_width | Target screen width for relative mouse moves | int | no | 1920 |
| screen_height | Target screen height for relative mouse moves | int | no | 1080 |

## Architecture

The driver is a composite with two child interfaces:

1. **video**: UVC snapshot capture and live frame streaming
2. **hid**: Keyboard and mouse control over USB serial

Both children share a single `NanoKVMUSBDevice` instance on the exporter so the
serial port and camera are opened once.

## Video streaming

The `stream()` driver method is exposed as a Jumpstarter **stream** (not a regular
RPC call). Video does not go to a fixed URL on the exporter; it is tunneled over
the Jumpstarter connection to whichever **client** opens the stream.

### Lifecycle

1. A client calls `video.stream("stream")` (context manager) or `open_stream()`.
2. The exporter starts an async task that captures JPEG frames from UVC and sends
   them through the stream.
3. The client reads frames with `stream.receive()` — each message is one JPEG.
4. When the client closes the context (or calls `close()`), the exporter stops
   capturing and releases the stream.

While the stream is active, the exporter dedicates a background task to video
capture. This does **not** block the whole exporter process (it is async), but it
does keep the UVC device busy until the client disconnects. HID commands remain
available on the `hid` child during streaming.

For recording, OCR, frame deduplication, and preprocessing without blocking
``jmp shell``, use ``edge-clearance-delivery/video-receiver/`` (``stream-bridge.py`` +
``video-receiver.py``).

### Client examples

Single frame via snapshot:

```python
image = lease.drivers["nanokvm-usb"].video.snapshot()
image.save("screen.jpg")
```

Low-level stream access (raw JPEG bytes):

```python
video = lease.drivers["nanokvm-usb"].video

with video.stream("stream") as stream:
    while True:
        frame_jpeg = stream.receive()
```

## API reference

### NanoKVMUSBClient

Composite client with `video` and `hid` children.

### NanoKVMUSBVideoClient

```{eval-rst}
.. autoclass:: jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBVideoClient()
    :members: snapshot
```

### NanoKVMUSBHIDClient

```{eval-rst}
.. autoclass:: jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBHIDClient()
    :members: paste_text, press_key, reset_hid, mouse_move_abs, mouse_move_rel, mouse_click, mouse_scroll
```

## CLI usage

```bash
# Snapshot
j nanokvm-usb video snapshot

# Keyboard
j nanokvm-usb hid paste "Hello, World!"
j nanokvm-usb hid press enter

# Mouse
j nanokvm-usb hid mouse move 0.5 0.5
j nanokvm-usb hid mouse click --button left --x 0.5 --y 0.5
```

## Host requirements

The exporter must run on the machine where the NanoKVM-USB is plugged in.

### Linux permissions

Add your user to the `dialout` group for serial port access:

```bash
sudo usermod -a -G dialout $USER
```

On Arch Linux, use the `uucp` group instead. Log out and back in after changing
group membership.

### Finding devices

**Serial port** (Linux): typically `/dev/ttyACM0` or `/dev/ttyUSB0`

**Video device**: OpenCV camera index or `/dev/video*`

```python
from jumpstarter_driver_nanokvm_usb.video import VideoCapture

for device in VideoCapture.list_devices():
    print(device)
```

## Differences from the network NanoKVM driver

| Feature | NanoKVM (network) | NanoKVM-USB |
|---------|-------------------|-------------|
| Connection | HTTP/WebSocket | USB serial + UVC |
| Video stream | MJPEG from device API | UVC capture on exporter host |
| Virtual disk/CD-ROM | Yes | No |
| Device reboot | Yes | No |
| Auth | Username/password | None (local USB) |
