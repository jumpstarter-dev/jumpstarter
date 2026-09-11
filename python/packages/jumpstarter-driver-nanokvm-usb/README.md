# NanoKVM-USB Driver

`jumpstarter-driver-nanokvm-usb` provides KVM (Keyboard, Video, Mouse) control for [NanoKVM-USB](https://github.com/sipeed/NanoKVM-USB) devices connected directly to the exporter host over USB.

Unlike the network-based [NanoKVM](https://github.com/sipeed/NanoKVM) driver, this package talks to the hardware through:

- **USB Serial** (default 57600 baud) for keyboard and mouse HID reports
- **UVC** (USB video class) for HDMI capture as a standard camera device

## Features

- **Video capture**: Snapshots from the UVC device
- **Keyboard control**: Paste text and press keys via serial HID
- **Mouse control**: Absolute and relative movement, clicks, and scrolling
- **Composite driver**: Access video and HID through a unified `NanoKVMUSB` interface

## Requirements

The Jumpstarter exporter must run on the machine where the NanoKVM-USB is plugged in.

### Linux permissions

Add your user to the `dialout` group for serial port access:

```bash
sudo usermod -a -G dialout $USER
```

Log out and back in for the change to take effect.

On Arch Linux, use the `uucp` group instead.

## Installation

```bash
pip install jumpstarter-driver-nanokvm-usb
```

## Configuration

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
      video_format: mjpeg_passthrough
      video_jpeg_quality: 98
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
| video_fps | Requested capture FPS | int | no | 30 |
| video_format | `mjpeg_passthrough` (native UVC JPEG via `v4l2-ctl`) or `jpeg` (OpenCV re-encode) | str | no | mjpeg_passthrough |
| video_jpeg_quality | JPEG quality when `video_format=jpeg` (1–100) | int | no | 95 |

Passthrough requires `v4l-utils` (`v4l2-ctl`) on the exporter host.
| screen_width | Target screen width for relative mouse moves | int | no | 1920 |
| screen_height | Target screen height for relative mouse moves | int | no | 1080 |

## Architecture

The driver is a composite with two child interfaces:

1. **video**: UVC snapshot capture
2. **hid**: Keyboard and mouse control over USB serial

## Video streaming

Live video uses Jumpstarter **streams**: each `stream.receive()` returns one JPEG
frame as raw `bytes`. HID remains available in parallel while the stream is open.

**Low-level client example** (inside a Jumpstarter lease with the exporter name
`nanokvm-usb`):

```python
video = lease.drivers["nanokvm-usb"].video

with video.stream("stream") as stream:
    while True:
        frame_jpeg = stream.receive()  # bytes
        ...
```

> **⚠️ Experimental — [`edge-clearance-delivery`](https://github.com/mparram/edge-clearance-delivery)**
> The external pipeline for MP4 recording, OCR,
> deduplication, and preprocessing (below) is **experimental** and lives outside
> this package.

Reading frames in the same process as `jmp shell` blocks the interactive session
while Tesseract or MP4 encoding run. For continuous capture without blocking HID,
use `stream-bridge.py` in a background process; it opens the same stream and forwards
frames over TCP to `video-recorder.py` and `ocr-receiver.py`. Inside `jmp shell`:

```bash
python edge-clearance-delivery/video-receiver/stream-bridge.py --use-env \
  --connect-mp4 127.0.0.1:8765 --connect-ocr 127.0.0.1:8766 &
```

See `run-clearance-demo.sh` in that repo for the full pipeline.

## API reference

### NanoKVMUSBVideoClient

- `snapshot(skip_frames=3)` → PIL Image
- `stream("stream")` → `BlockingStream` of JPEG bytes (see `DriverClient`)

### NanoKVMUSBHIDClient

- `paste_text(text)`
- `press_key(key)`
- `reset_hid()`
- `mouse_move_abs(x, y)` — normalized 0.0–1.0
- `mouse_move_rel(dx, dy)` — normalized -1.0–1.0
- `mouse_click(button, x?, y?)`
- `mouse_scroll(dx, dy)`

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
j nanokvm-usb hid mouse scroll --dy -5
```

## Finding devices

**Serial port** (Linux): typically `/dev/ttyACM0` or `/dev/ttyUSB0`

**Video device**: list UVC devices with OpenCV or check `/dev/video*`

```python
from jumpstarter_driver_nanokvm_usb.video import VideoCapture

for device in VideoCapture.list_devices():
    print(device)
```

## Differences from the network NanoKVM driver

| Feature | NanoKVM (network) | NanoKVM-USB |
|---------|-------------------|-------------|
| Connection | HTTP/WebSocket | USB serial + UVC |
| Video stream | MJPEG over network | Local UVC capture |
| Virtual disk/CD-ROM | Yes | No |
| Device reboot | Yes | No |
| Auth | Username/password | None (local USB) |
