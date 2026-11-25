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
manager, and `make` as our build interface. Run the commands on this page from
the repository root.

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

At this point you can run any of the Jumpstarter commands by prefixing them
with `uv run --project python`:

```console
$ uv run --project python jmp
```

### Running the Tests

To run the tests:

```console
$ make -C python test
```

You can also run specific tests with:

```console
$ make -C python pkg-test-${package_name}
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
$ make -C controller build
```

At this point you can deploy the controller in a Kubernetes cluster in Docker
(`kind`) with:

```console
$ make -C controller deploy CONTAINER_TOOL=podman
```

And you can cleanup and stop the controller/cluster with:

```console
$ make -C controller clean CONTAINER_TOOL=podman
```

### Running the Tests

To run the tests:

```console
$ make -C controller test
```

## Rust Environment

The `jumpstarter-exec` remote command execution bridge lives in the `rust/`
directory. CI pins the Rust toolchain version with `RUST_TOOLCHAIN` in
`.github/workflows/rust-tests.yaml`. With `cargo` installed,
build and test it from the repository root:

```console
$ make -C rust build
$ make -C rust test
```
