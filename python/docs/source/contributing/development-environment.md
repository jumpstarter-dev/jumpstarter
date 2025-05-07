# Development environment

You can use [devspaces](https://github.com/jumpstarter-dev/jumpstarter/blob/main/.devfile.yaml),
[devcontainers](https://github.com/jumpstarter-dev/jumpstarter/tree/main/.devcontainer), or your favorite OS/distro to develop Jumpstarter,
however the following examples are for Fedora 42.

Jumpstarter is programmed in Python and Go, the Jumpstarter controller is written in Go, while the core and drivers are written in Python.

## Python environment

The Jumpstarter core and drivers live in the [jumpstarter](https://github.com/jumpstarter-dev/jumpstarter) repository.

We use [uv](https://docs.astral.sh/uv/) as our python package and project manager,
and `make` as our build interface.

To install the basic set of dependencies, run the following commands:
```bash
sudo dnf install -y python-devel g++ make git uv qemu qemu-user-static
```

Then you can clone the project and build the virtual environment with:
```bash
git clone https://github.com/jumpstarter-dev/jumpstarter.git
cd jumpstarter
make sync
```

At this point you can run any of the jumpstarter commands prefixing them with `uv run`:

i.e.:
```bash
uv run jmp
```

### Running the tests
To run the tests, you can use the `make` command:
```bash
make test
```

You can also run specific tests with:
```bash
make test-pkg-${package_name}
```

## Go environment

The Jumpstarter controller lives in the
[jumpstarter-controller](https://github.com/jumpstarter-dev/jumpstarter-controller)
repository.

To install the basic set of dependencies, run the following commands:
```bash
sudo dnf install -y git make golang kubectl
```

Then you can clone the project and build the project with:
```bash
git clone https://github.com/jumpstarter-dev/jumpstarter-controller.git
cd jumpstarter-controller
make build
```

At this point you can deploy the controller in a kubernetes cluster in docker (`kind`) with:
```bash
CONTAINER_TOOL=podman make deploy
```

And you can cleanup and stop the controller/cluster with:
```bash
CONTAINER_TOOL=podman make clean
```

### Running the tests
To run the tests, you can use the `make` command:
```bash
make test
```