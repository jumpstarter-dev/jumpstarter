# uStreamer Driver

`jumpstarter-driver-ustreamer` provides functionality for using the ustreamer
video streaming server driven by the jumpstarter exporter. This driver takes a
video device and exposes both snapshot and streaming interfaces.

## Installation

```{code-block} console
:substitutions:
$ pip3 install --extra-index-url {{index_url}} jumpstarter-driver-ustreamer
```

## Configuration

Example configuration:

```{literalinclude} ustreamer.yaml
:language: yaml
```

```{doctest}
:hide:
>>> from jumpstarter.config.exporter import ExporterConfigV1Alpha1DriverInstance
>>> ExporterConfigV1Alpha1DriverInstance.from_path("source/reference/package-apis/drivers/ustreamer.yaml").instantiate()
Traceback (most recent call last):
...
io.UnsupportedOperation: fileno
```

## Usage

### CLI

The uStreamer client exposes a `video` command inside `jmp shell` for
inspecting the current stream state, saving a snapshot, and starting a local
MJPEG proxy server.

```console
$ j video --help
Usage: j video [OPTIONS] COMMAND [ARGS]...

  Video capture and streaming

Options:
  --help  Show this message and exit.

Commands:
  snapshot  Save a single snapshot to file
  state     Show video source state
  stream    Start local MJPEG streaming server
```

#### `j video state`

```console
$ j video state --help
Usage: j video state [OPTIONS]

  Show video source state

Options:
  --help  Show this message and exit.
```

#### `j video snapshot`

```console
$ j video snapshot --help
Usage: j video snapshot [OPTIONS]

  Save a single snapshot to file

Options:
  -o, --output TEXT  Output file path
  --help             Show this message and exit.
```

#### `j video stream`

```console
$ j video stream --help
Usage: j video stream [OPTIONS]

  Start local MJPEG streaming server

  Proxies ustreamer's native MJPEG stream through the jumpstarter tunnel. Frame
  rate is controlled by ustreamer's configuration.

Options:
  -p, --port INTEGER        Local server port (0 = auto)
  --browser / --no-browser  Open in web browser
  --help                    Show this message and exit.
```

## API Reference

```{eval-rst}
.. autoclass:: jumpstarter_driver_ustreamer.client.UStreamerClient()
    :members:
```
