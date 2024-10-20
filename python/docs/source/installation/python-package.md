# Python Package

The `jumpstarter` Python package contains everything you'll need to get started
with Jumpstarter.

This package includes:
- A client library.
- A CLI tool.
- An exporter runtime and built-in drivers.

The Python package can be installed using ``pip`` or similar, Python 3.10 or newer is required.:

<!-- TODO: create meta-package-to-install-all -->

```bash
$ pip3 install --extra-index-url https://docs.jumpstarter.dev/packages/simple \
        jumpstarter jumpstarter-driver-ustreamer jumpstarter-driver-can jumpstarter-driver-sdwire \
        jumpstarter-driver-dutlink jumpstarter-driver-raspberrypi \
        jumpstarter-imagehash
```

```{tip}
This will install the `jumpstarter` package globally, when using Jumpstarter
as a library, it is recommended to install the package in a virtual environment
instead.
```
