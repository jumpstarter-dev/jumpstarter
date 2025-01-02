# Python Packages

The [Jumpstarter Python packages](https://docs.jumpstarter.dev/packages/)
contain all the necessary tools to run an exporter or interact with your
hardware as a client.

This package includes:
- A client library.
- The client CLI tool.
- An exporter runtime and built-in drivers.

The Python package can be installed using ``pip`` or similar, Python {{requires_python}} is required.:

<!-- TODO: create meta-package-to-install-all -->

```bash
$ pip3 install --extra-index-url https://docs.jumpstarter.dev/packages/simple \
        jumpstarter jumpstarter-driver-ustreamer jumpstarter-driver-can jumpstarter-driver-sdwire \
        jumpstarter-driver-dutlink jumpstarter-driver-raspberrypi \
        jumpstarter-imagehash
```

```{tip}
This will install the `jumpstarter` packages globally, when using Jumpstarter
as a library, it is recommended to install the package in a virtual environment
instead. i.e.

$ python3 -m venv ~/.venv/jumpstarter

$ source ~/.venv/jumpstarter/bin/activate

$ pip3 install ....
```

An alternative to installing the packages is to [use the container package](./container-jmp.md).
