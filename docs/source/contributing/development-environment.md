# Development Environment

You can use
[Eclipse Che](https://github.com/jumpstarter-dev/jumpstarter/blob/main/.devfile.yaml),
[devcontainers](https://github.com/jumpstarter-dev/jumpstarter/tree/main/.devcontainer),
or your favorite OS/distro to develop Jumpstarter. The following examples
are for Fedora.

Jumpstarter is programmed in Python, Go and Rust, the Jumpstarter controller is
written in Go, while the core and drivers are written in Python. The
`jumpstarter-exec` remote command execution bridge is written in Rust.

## Python Environment

The Jumpstarter core and drivers live in the
[jumpstarter](https://github.com/jumpstarter-dev/jumpstarter) repository.

We use [uv](https://docs.astral.sh/uv/) as our Python package and project
manager, and `make` as our build interface.

To install the basic set of dependencies, run the following commands:
```console
$ sudo dnf install -y python-devel g++ make git uv qemu qemu-user-static
```

Then you can clone the project and build the virtual environment with:

```console
$ git clone https://github.com/jumpstarter-dev/jumpstarter.git
$ cd jumpstarter
$ make -C python sync
```

At this point you can run any of the jumpstarter commands prefixing them with
`uv run` from the `python/` directory:

```console
$ cd python
$ uv run jmp
```

### Running the Tests

To run the tests, you can use `make` from the `python/` directory:
```console
$ make test
```

You can also run specific tests with:

```console
$ make pkg-test-${package_name}
```

## Go Environment

The Jumpstarter controller lives in the `controller/` directory within the
[jumpstarter](https://github.com/jumpstarter-dev/jumpstarter) monorepo.

To install the basic set of dependencies, run the following commands:

```console
$ sudo dnf install -y git make golang kubectl
```

Then you can build the controller from the repository root:

```console
$ cd controller
$ make build
```

At this point you can deploy the controller in a Kubernetes cluster in Docker
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

## Rust Environment

The `jumpstarter-exec` remote command execution bridge lives in the `rust/`
directory. CI pins the Rust toolchain version with `RUST_TOOLCHAIN` in
`.github/workflows/rust-tests.yaml`. With `cargo` installed,
build and test it from the repository root:

```console
$ make build-rust
$ make test-rust
```
