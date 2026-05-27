# ![bolt](python/assets/bolt.svg) Jumpstarter

[![Matrix](https://img.shields.io/matrix/jumpstarter%3Amatrix.org?color=blue)](https://matrix.to/#/#jumpstarter:matrix.org)
[![Etherpad](https://img.shields.io/badge/Etherpad-Notes-blue?logo=etherpad)](https://etherpad.jumpstarter.dev/pad-lister)
[![Community Meeting](https://img.shields.io/badge/Weekly%20Meeting-Google%20Meet-blue?logo=google-meet)](https://meet.google.com/gzd-hhbd-hpu)
![GitHub Release](https://img.shields.io/github/v/release/jumpstarter-dev/jumpstarter)
![PyPI - Version](https://img.shields.io/pypi/v/jumpstarter)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/jumpstarter-dev/jumpstarter)

Jumpstarter is a free and open source test automation framework. It bridges the gap
between embedded development workflows and deployment environments, enabling
consistent automated testing across real hardware and virtual environments with
CI/CD integration. Every interface is programmatic, so human developers, test
scripts, CI pipelines, and AI agents interact with devices through the same APIs.

## Highlights

- 🧪 **Unified Testing** - One tool for physical and virtual devices under test
- 🔌 **Hardware Abstraction** - Control UART, CAN, SPI, GPIO, power, and USB through drivers
- 🐍 **Python-Powered** - Integrate with PyTest and Python's testing ecosystem
- 🌐 **Collaborative** - Share and securely lease test hardware across teams
- ⚙️ **Automation Ready** - Same APIs for humans, test scripts, CI pipelines, and AI agents
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

Comprehensive testing infrastructure for the entire Jumpstarter stack:
- `setup-e2e.sh` - One-time environment setup (auto-installs bats libraries on macOS)
- `run-e2e.sh` - Quick test runner for iterations
- `action.yml` - GitHub Actions composite action for CI/CD
- Full integration tests covering authentication, exporters, and clients

Run e2e tests locally:
```shell
# First time setup
make e2e-setup

# Run tests (repeat as needed)
make e2e        # or: make e2e-run

# Or full setup + run in one command
make e2e-full

# Clean up e2e environment (delete cluster, certs, etc.)
make e2e-clean
```

## Development

### Prerequisites

- Python 3.11+ (for Python components)
- Go 1.24+ (for controller)
- Docker/Podman (for container builds)
- kubectl (for Kubernetes deployment)

### Building

```shell
# Build all components
make build

# Build specific components
make build-python      # Python packages
make build-controller  # Controller binary
make build-protocol    # Generate protocol code

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
- [User Guide](https://jumpstarter.dev/main/introduction/)
- [API Reference](https://jumpstarter.dev/main/reference/)
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
