# NanoKVM-USB Driver


`jumpstarter-driver-nanokvm-usb` provides KVM (Keyboard, Video, Mouse) control for
[NanoKVM-USB](https://github.com/sipeed/NanoKVM-USB) devices connected directly
to the exporter host over USB.

Unlike a network-based NanoKVM driver, this package talks to the
hardware through:

- **USB Serial** (default 57600 baud) for keyboard and mouse HID reports
- **UVC** (USB video class) for HDMI capture as a standard camera device

## Features

- **Video capture**: Snapshots and live JPEG frame streams from the UVC device
- **Keyboard control**: Paste text and press keys via serial HID
- **Mouse control**: Absolute and relative movement, clicks, and scrolling
- **VNC**: Embedded RFB server (view the captured HDMI and control HID from noVNC/TigerVNC)
- **Composite driver**: Access video, HID, and VNC through a unified `NanoKVMUSB` interface

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
      video_device: "/dev/v4l/by-path/pci-0000:00:14.0-usbv2-0:3.4.3:1.0-video-index0"
      video_width: 1920
      video_height: 1080
      video_fps: 30
      screen_width: 1920
      screen_height: 1080
      vnc_enabled: true
      # vnc_password: "secret"
      # vnc_tcp_port: 5900
      # vnc_tcp_bind: "0.0.0.0"  # LAN; use vnc_password
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
| vnc_enabled | Start an embedded RFB server (Unix socket child `vnc`) | bool | no | true |
| vnc_password | VNC password (VncAuth). Empty/None = no authentication | str | no | |
| vnc_tcp_port | Also bind RFB TCP on the exporter (`None` = Unix socket only) | int | no | |
| vnc_tcp_bind | Address for `vnc_tcp_port` (`127.0.0.1` or `0.0.0.0` for LAN) | str | no | 127.0.0.1 |
| vnc_encrypt | Default noVNC `encrypt` URL flag | bool | no | false |

## Architecture

The driver is a composite with three child interfaces:

1. **video**: UVC snapshot capture and live frame streaming
2. **hid**: Keyboard and mouse control over USB serial
3. **vnc**: Unix-socket RFB endpoint (noVNC / any VNC client)

Video stream and VNC share a single capture pump so `/dev/video*` is opened once.
Keyboard and mouse events from the VNC client are translated to the same HID path as `hid`.
The children share a single `NanoKVMUSBDevice` instance on the exporter so the
serial port and camera are opened once.

## Video streaming

The `stream()` driver method is exposed as a Jumpstarter **stream** (not a regular
RPC call). Video does not go to a fixed URL on the exporter; it is tunneled over
the Jumpstarter connection to whichever **client** opens the stream.

### Lifecycle

1. A client calls `video.stream("stream")` (context manager) or `open_stream()`.
2. The exporter's shared FramePump already captures JPEG frames from UVC; the
   stream task forwards those frames to the client.
3. The client reads frames with `stream.receive()` — each message is one JPEG.
4. When the client closes the context (or calls `close()`), the exporter stops
   the stream task. Capture continues if VNC or another stream is still using
   the pump.

HID commands remain available on the `hid` child during streaming and VNC.

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

## VNC

The exporter runs an RFB 3.8 server on a Unix socket. Jumpstarter tunnels that
socket to the **client** (same pattern as QEMU): you do not need to be on the
exporter host. Keyboard and mouse in the VNC client go to the NanoKVM-USB HID.

**From the Jumpstarter client** (any machine with a lease):

```bash
j nanokvm-usb vnc session
```

That opens noVNC against a TCP/WebSocket port on *your* machine. For a native
viewer (TigerVNC, Remmina) on the client:

```bash
j nanokvm-usb vnc forward-tcp 5900
# then connect to localhost:5900
```

Or from Python:

```python
with lease.drivers["nanokvm-usb"].session() as url:
    print(url)  # noVNC URL
```

**TCP on the exporter** (optional): set `vnc_tcp_port` to bind RFB on the exporter
host. Default `vnc_tcp_bind` is `127.0.0.1` (local viewers on that machine only).
Use `vnc_tcp_bind: "0.0.0.0"` to accept LAN clients without a Jumpstarter tunnel.
Binding a non-loopback address without `vnc_password` exposes an unauthenticated
session on the network; set a password.

## API reference

### NanoKVMUSBClient

Composite client with `video`, `hid`, and `vnc` children.

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

### NanoKVMUSBVNCClient

```{eval-rst}
.. autoclass:: jumpstarter_driver_nanokvm_usb.client.NanoKVMUSBVNCClient()
    :members: session
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

# VNC (view + HID) via Jumpstarter tunnel
j nanokvm-usb vnc session
j nanokvm-usb vnc forward-tcp 5900
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
