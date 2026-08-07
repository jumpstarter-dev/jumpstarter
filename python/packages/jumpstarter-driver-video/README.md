# Video driver

`jumpstarter-driver-video` provides the common interface for video source
drivers (`VideoInterface` / `VideoClient`) and an `HttpVideo` driver for
HTTP/MJPEG camera sources.

`HttpVideo` covers cameras that are reachable from the exporter over the
network rather than attached to it as a local V4L2 device — for example a
camera connected to the DUT itself, such as ESP32 camera firmware serving
an MJPEG stream over WiFi, or a generic IP camera.

## Installation

```shell
pip3 install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-driver-video
```

## Configuration

Example configuration:

```yaml
export:
  video:
    type: jumpstarter_driver_video.driver.HttpVideo
    config:
      # MJPEG stream URL
      url: http://192.168.1.50/stream
      # optional single-JPEG endpoint; if omitted, snapshots are taken by
      # grabbing the first frame off the MJPEG stream
      snapshot_url: http://192.168.1.50/snapshot.jpg
```

## Usage

```python
# take a snapshot (returns a PIL Image)
img = client.video.snapshot()
img.save("frame.jpg")

# raw JPEG bytes
data = client.video.snapshot_bytes()

# source state: online, resolution, and fps when the source reports it
state = client.video.state()
print(state.online, state.width, state.height, state.fps)
```

CLI (inside a `jmp shell`):

```shell
j video state                   # show whether the source is online, and its resolution
j video snapshot -o frame.jpg   # save a single frame
j video stream                  # local MJPEG server proxying the stream, opens browser
```

## Implementing a video driver

Video source drivers should implement `VideoInterface`:

- `snapshot()` (`@export`): return a single JPEG frame, base64 encoded
- `state()` (`@export`): return a `VideoState` (online, resolution, fps)
- `stream_path()` (`@export`): HTTP path serving MJPEG on the `connect` stream
- `connect()` (`@exportstream`): byte stream to an HTTP server serving MJPEG

`VideoClient` then provides snapshots, the streaming CLI, and the local
stream proxy for free. The `UStreamer` driver in
`jumpstarter-driver-ustreamer` is an example implementation for local V4L2
devices.

A source with richer state can return a model that **extends** `VideoState`,
so generic consumers keep reading the common fields while source-specific
detail stays available. `UStreamerState` does exactly that: it fills
`online`/`width`/`height`/`fps` from ustreamer's own status document, which
remains reachable through its `result` field.

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_video.driver.HttpVideo
```

```{eval-rst}
.. autoclass:: jumpstarter_driver_video.client.VideoClient
    :members:
```
