# Development Environment

You can use
[devspaces](https://github.com/jumpstarter-dev/jumpstarter/blob/main/.devfile.yaml),
[devcontainers](https://github.com/jumpstarter-dev/jumpstarter/tree/main/.devcontainer),
or your favorite OS/distro to develop Jumpstarter, however the following
examples are for Fedora 42.

Jumpstarter is programmed in Python and Go, the Jumpstarter controller is
written in Go, while the core and drivers are written in Python.

## Python Environment

The Jumpstarter core and drivers live in the
[jumpstarter](https://github.com/jumpstarter-dev/jumpstarter) repository.

We use [uv](https://docs.astral.sh/uv/) as our python package and project
manager, and `make` as our build interface.

To install the basic set of dependencies, run the following commands:
```console
$ sudo dnf install -y python-devel g++ make git uv qemu qemu-user-static
```

Then you can clone the project and build the virtual environment with:

```console
$ git clone https://github.com/jumpstarter-dev/jumpstarter.git
$ cd jumpstarter
$ make sync
```

At this point you can run any of the jumpstarter commands prefixing them with
`uv run`:

```console
$ uv run jmp
```

### Running the Tests

To run the tests, you can use the `make` command:
```console
$ make test
```

You can also run specific tests with:

```console
$ make test-pkg-${package_name}
```

## Go Environment

The Jumpstarter controller lives in the
[jumpstarter-controller](https://github.com/jumpstarter-dev/jumpstarter-controller)
repository.

To install the basic set of dependencies, run the following commands:

```console
$ sudo dnf install -y git make golang kubectl
```

Then you can clone the project and build the project with:

```console
$ git clone https://github.com/jumpstarter-dev/jumpstarter-controller.git
$ cd jumpstarter-controller
$ make build
```

At this point you can deploy the controller in a kubernetes cluster in docker
(`kind`) with:

```console
$ CONTAINER_TOOL=podman make deploy
```

And you can cleanup and stop the controller/cluster with:

```console
$ CONTAINER_TOOL=podman make clean
```

### Running the Tests
To run the tests, you can use the `make` command:
```console
$ make test
```
