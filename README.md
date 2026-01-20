# ![bolt](python/assets/bolt.svg) Jumpstarter

[![Matrix](https://img.shields.io/matrix/jumpstarter%3Amatrix.org?color=blue)](https://matrix.to/#/#jumpstarter:matrix.org)
[![Etherpad](https://img.shields.io/badge/Etherpad-Notes-blue?logo=etherpad)](https://etherpad.jumpstarter.dev/pad-lister)
[![Community Meeting](https://img.shields.io/badge/Weekly%20Meeting-Google%20Meet-blue?logo=google-meet)](https://meet.google.com/gzd-hhbd-hpu)
![GitHub Release](https://img.shields.io/github/v/release/jumpstarter-dev/jumpstarter)
![PyPI - Version](https://img.shields.io/pypi/v/jumpstarter)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/jumpstarter-dev/jumpstarter)

A free, open source tool for automated testing on real and virtual hardware with
CI/CD integration. Simplify device automation with consistent rules across local
and distributed environments.

## Highlights

- 🧪 **Unified Testing** - One tool for local, virtual, and remote hardware
- 🐍 **Python-Powered** - Leverage Python's testing ecosystem
- 🔌 **Hardware Abstraction** - Simplify complex hardware interfaces with drivers
- 🌐 **Collaborative** - Share test hardware globally
- ⚙️ **CI/CD Ready** - Works with cloud native developer environments and pipelines
- 💻 **Cross-Platform** - Supports Linux and macOS

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
pip install --extra-index-url https://pkg.jumpstarter.dev/ jumpstarter-cli
```

Or install all Python components:

```shell
pip install --extra-index-url https://pkg.jumpstarter.dev/ jumpstarter-all
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

See [`python/README.md`](python/README.md) for details.

### Jumpstarter Service (`controller/`)

The Kubernetes-native service that provides:
- Centralized hardware management
- Client and exporter routing
- Authentication and authorization
- Multi-tenant support

**Prerequisites:**
- Kubernetes v1.11.3+
- kubectl v1.11.3+

See [`controller/README.md`](controller/README.md) for deployment instructions.

### Protocol (`protocol/`)

The gRPC-based communication layer that enables:
- Unified interface for virtual and physical hardware
- Secure communication over HTTPS
- Tunneling support for Unix sockets, TCP, and UDP
- Flexible topology with direct or routed connections

See [`protocol/README.md`](protocol/README.md) for details.

### End-to-End Tests (`e2e/`)

GitHub Actions composite action for running end-to-end tests across the
entire Jumpstarter stack.

## Development

### Prerequisites

- Python 3.11+ (for Python components)
- Go 1.22+ (for controller)
- Docker/Podman (for container builds)
- kubectl (for Kubernetes deployment)

### Building

```shell
# Build all components
make all

# Build specific components
make python      # Python packages
make controller  # Controller binary
make protocol    # Generate protocol code

# Run tests
make test
```

### Running Locally

```shell
# Start a local development environment
make dev
```

## Documentation

Jumpstarter's documentation is available at [jumpstarter.dev](https://jumpstarter.dev).

- [Getting Started](https://jumpstarter.dev/main/getting-started/)
- [User Guide](https://jumpstarter.dev/main/introduction/)
- [API Reference](https://jumpstarter.dev/main/api/)
- [Contributing Guide](https://jumpstarter.dev/main/contributing.html)

## Contributing

Jumpstarter welcomes contributors of all levels of experience! See the
[contributing guide](https://jumpstarter.dev/main/contributing.html) to get started.

### Community

- [Matrix Chat](https://matrix.to/#/#jumpstarter:matrix.org)
- [Weekly Meeting](https://meet.google.com/gzd-hhbd-hpu)
- [Meeting Notes](https://etherpad.jumpstarter.dev/pad-lister)

## License

Jumpstarter is licensed under the Apache 2.0 License ([LICENSE](LICENSE) or
[https://www.apache.org/licenses/LICENSE-2.0](https://www.apache.org/licenses/LICENSE-2.0)).
