# ![bolt](python/assets/bolt.svg) Jumpstarter

[![Matrix](https://img.shields.io/matrix/jumpstarter%3Amatrix.org?color=blue)](https://matrix.to/#/#jumpstarter:matrix.org)
[![Etherpad](https://img.shields.io/badge/Etherpad-Notes-blue?logo=etherpad)](https://etherpad.jumpstarter.dev/pad-lister)
[![Community Meeting](https://img.shields.io/badge/Weekly%20Meeting-Google%20Meet-blue?logo=google-meet)](https://meet.google.com/gzd-hhbd-hpu)
![GitHub Release](https://img.shields.io/github/v/release/jumpstarter-dev/jumpstarter)
![PyPI - Version](https://img.shields.io/pypi/v/jumpstarter)


Jumpstarter is a free and open source framework for testing and developing
software on real and virtual hardware. Developers, test scripts, CI pipelines,
and AI agents reach devices through the same programmatic interfaces, whether a
board sits on your desk, in a shared lab, or runs as a virtual device. Tests stay
ordinary pytest or shell scripts with no job language to learn, and every
component runs on infrastructure you control.

## Highlights

- **One interface for real and virtual devices** - The same driver
  interfaces, CLI, and Python API work with a board on your desk, a board in a
  remote lab, or a virtual target such as QEMU, Renode, an Android emulator, or
  Cuttlefish.
- **Your tools, unchanged** - Tests are plain pytest, shell scripts, or any
  other program. `jmp shell` connects them to a device, and the same commands
  run unchanged in CI.
- **Ready for AI agents** - The built-in MCP server lets any assistant that
  supports local MCP servers lease, control, and inspect hardware with your
  credentials, bound by the same access policies as you.
- **Remote labs without lab accounts** - Exporters connect out to the service
  over gRPC, so lab hosts can sit behind NAT or a firewall. Users never log in
  to them, and local flash images stream through the lease connection, so no
  shared filesystem is needed.
- **Secure sharing** - Single sign-on through your OIDC provider, leases for
  exclusive access, and access policies that decide which clients can use which
  devices and for how long.
- **Kubernetes native** - Exporters, clients, leases, and access policies are
  Kubernetes resources, and pools of virtual devices scale with lease demand.
  The controller and router serve Prometheus metrics, and an optional telemetry
  service collects exporter metrics and logs.
- **Broad hardware coverage** - More than 60 driver packages for power,
  serial consoles, storage and flashing, video, networking, Bluetooth, mobile
  devices, and automotive protocols such as CAN, UDS, DoIP, and SOME/IP. New
  drivers are written in plain Python.
- **Open source** - Apache 2.0 licensed, self-hosted, and supported on Linux
  and macOS.

## Repository Structure

This monorepo contains all Jumpstarter components:

| Directory | Description |
|-----------|-------------|
| [`python/`](python/) | Python client, CLI, drivers, and testing framework |
| [`controller/`](controller/) | Kubernetes controller and operator (Jumpstarter Service) |
| [`protocol/`](protocol/) | gRPC protocol definitions (protobuf) |
| [`e2e/`](e2e/) | End-to-end testing infrastructure |

## Quick Start

### Install the CLI

```shell
pip install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-cli
```

Or install all Python components:

```shell
pip install --extra-index-url https://pkg.jumpstarter.dev/simple/ jumpstarter-all
```

### Upgrade the CLI

```shell
jmp self update
```

### Deploy the Service

To install the Jumpstarter Service in your Kubernetes cluster, see the
[Service Installation](https://jumpstarter.dev/main/getting-started/installation/index.html)
documentation.

## Components

### Python Client & Drivers (`python/`)

The Python implementation provides:
- `jmp` CLI tool for interacting with hardware
- Client libraries for test automation
- Hardware drivers for various devices
- Testing framework integration
- `jmp mcp`, an MCP server that gives AI agents the same access to hardware

See [`python/README.md`](python/README.md) for details.

### Jumpstarter Service (`controller/`)

The Kubernetes-native service that provides:
- Centralized hardware management
- Client and exporter routing
- Authentication and authorization
- Multi-tenant support

**Prerequisites:**
- Kubernetes (version must match the k8s.io dependencies in [controller/go.mod](controller/go.mod))
- kubectl (version matching the k8s.io dependencies in [controller/go.mod](controller/go.mod))

See [`controller/README.md`](controller/README.md) for deployment instructions.

### Protocol (`protocol/`)

The gRPC-based communication layer that enables:
- Unified interface for virtual and physical hardware
- Secure communication over HTTPS
- Tunneling support for Unix sockets, TCP, and UDP
- Flexible topology with direct or routed connections

See [`protocol/README.md`](protocol/README.md) for details.

### End-to-End Tests (`e2e/`)

Comprehensive testing infrastructure for the entire Jumpstarter stack.

See the [Makefile](Makefile) for available targets.

## Development

### Prerequisites

- Python (for Python components, see [.py-version](.py-version))
- Go (for controller, see [.go-version](.go-version))
- Docker/Podman (for container builds)
- kubectl (version matching the k8s.io dependencies in [controller/go.mod](controller/go.mod))

### Building

```shell
# Build all components
make build

# Build specific components
make build-python      # Python packages
make build-controller  # Controller binary

# Regenerate the gRPC code after changing protocol/
make -C controller protobuf-gen
make -C python protobuf-gen

# Run tests
make test

# Run end-to-end tests
make e2e-setup  # First time only
make e2e        # Run tests
make e2e-clean  # Clean up
```

## Documentation

Jumpstarter's documentation is available at [jumpstarter.dev](https://jumpstarter.dev).

- [Getting Started](https://jumpstarter.dev/main/getting-started/)
- [Introduction](https://jumpstarter.dev/main/introduction/)
- [Reference](https://jumpstarter.dev/main/reference/)
- [Contributing](https://jumpstarter.dev/main/contributing.html)

## Contributing

Jumpstarter welcomes contributors of all levels of experience! See the
[contributing](https://jumpstarter.dev/main/contributing.html) guide to get started.

### Community

- [Matrix Chat](https://matrix.to/#/#jumpstarter:matrix.org)
- [Weekly Meeting](https://meet.google.com/gzd-hhbd-hpu)
- [Meeting Notes](https://etherpad.jumpstarter.dev/pad-lister)

## License

Jumpstarter is licensed under the Apache 2.0 License ([LICENSE](LICENSE) or
[https://www.apache.org/licenses/LICENSE-2.0](https://www.apache.org/licenses/LICENSE-2.0)).
