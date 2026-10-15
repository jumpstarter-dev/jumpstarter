# Development Environment

You can use
[Eclipse Che](https://github.com/jumpstarter-dev/jumpstarter/blob/main/.devfile.yaml),
[devcontainers](https://github.com/jumpstarter-dev/jumpstarter/tree/main/.devcontainer),
or your favorite OS/distro to develop Jumpstarter. The following examples
are for Fedora.

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
$ make build-python
```

At this point you can run any of the jumpstarter commands prefixing them with
`uv run` from the `python/` directory:

```console
$ cd python
$ uv run jmp
```

### Running the Tests

To run the tests, you can use `make` from the repository root:
```console
$ make test
```

You can also run specific tests with:

```console
$ make pkg-test-${package_name}
```

### Developing client-side drivers

Use `jmp shell --overlay` when the hardware and its drivers are already running
in a lab, and the code you are writing lives on the client. A typical case is a
leased exporter whose power, serial, and storage drivers are up on the lab
host, while you iterate on client-side commands, helpers, or orchestration on
your workstation. The lab exporter keeps running; the overlay only adds those
clients to your shell.

`--overlay` takes a client overlay YAML with only an `export` map (no endpoint,
token, or metadata). `jmp shell` still connects with `--client`, `--exporter`,
or `--tls-grpc`, and sets `JMP_CLIENT_OVERLAY` to the absolute path of that
file. Inside the shell, `j`, Python `env()`, and pytest (`JumpstarterTest`)
build client objects from `export` and place them next to the session's drivers:

- Nested `children` are included on the parent client.
- A `ref` is the same client object as the driver it names. That driver can be
  another entry in the overlay or one already on the session, so a ref to a lab
  driver keeps calling the exporter.
- A top-level name that already exists on the session is replaced, and a
  warning naming that client is printed when `j` or `env()` builds the client.
- `config` values are passed through when the client class declares those
  fields.
- Methods that perform a server-side driver call (`call`, `streamingcall`, or
  `stream`) raise an error on an overlaid client. Calls to the lab drivers
  continue to reach the exporter.

```console
$ jmp shell --client lab --selector board=rpi4 --overlay ./overlay.yaml
$ j my-local-tool --help
$ python ./try_client.py
$ pytest test_my_device.py
$ exit
```

```yaml
export:
  lab_power:
    ref: power
  my-local-tool:
    type: my_driver.driver.MyDriver
    config:
      host: bench
    children:
      helper:
        type: my_driver.driver.Helper
      pwr:
        ref: power
  alias:
    ref: my-local-tool.helper
```

`jmp shell --exporter-config ./exporter.yaml` is the other local workflow: it
starts the drivers from that file on your machine. Use `--overlay` when those
drivers should stay on the leased lab exporter and only the client tree is
under development.

`JMP_CLIENT_OVERLAY` is listed with the other shell variables in
[Configuration files](../getting-started/configuration/files.md).

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
