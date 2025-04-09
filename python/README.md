# ![bolt](./assets/bolt.svg) Jumpstarter

[![badge](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/jumpstarter-dev/jumpstarter/main/assets/badge/v0.json)](https://github.com/jumpstarter-dev/jumpstarter)
[![ci](https://img.shields.io/github/actions/workflow/status/jumpstarter-dev/jumpstarter/build.yaml?branch=main&logo=github&label=CI)](https://github.com/jumpstarter-dev/jumpstarter/actions)
[![release](https://img.shields.io/github/v/release/jumpstarter-dev/jumpstarter)](https://github.com/jumpstarter-dev/jumpstarter/releases)
[![versions](https://img.shields.io/python/required-version-toml?tomlFilePath=https%3A%2F%2Fraw.githubusercontent.com%2Fjumpstarter-dev%2Fjumpstarter%2Fmain%2Fpackages%2Fjumpstarter%2Fpyproject.toml)](https://github.com/jumpstarter-dev/jumpstarter/blob/main/packages/jumpstarter/pyproject.toml)
[![license](https://img.shields.io/github/license/jumpstarter-dev/jumpstarter)](https://github.com/jumpstarter-dev/jumpstarter/blob/main/LICENSE)
[![uv](https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/uv/main/assets/badge/v0.json)](https://github.com/astral-sh/uv)

An open source and cloud native Hardware-in-the-Loop testing tool that enables you to test your software stack on both real hardware and virtual environments using CI/CD principles.

## Highlights

- 🚀 A single, unified testing tool for local, virtual, and remote hardware testing.
- 🐍 Write test scripts in Python with familiar frameworks such as [pytest](https://docs.pytest.org/en/stable/).
- 🛠️ Use [drivers](https://docs.jumpstarter.dev/introduction/drivers.html) to abstract complex hardware interfaces for testing.
- 🔌 Built-in support for common interfaces such as [CAN](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter-driver-can), [IP](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter-driver-network), [GPIO](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter-driver-raspberrypi), [U-Boot](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter-driver-uboot), [SD Wire](https://github.com/jumpstarter-dev/jumpstarter/tree/main/packages/jumpstarter-driver-sdwire), etc.
- 💻 Collaborate with developers around the world on shared test hardware.
- ☸ Integrates with your existing [Cloud Native](https://www.cncf.io/) development environment.
- 🔄 Run hardware tests with your existing CI/CD pipelines in the cloud.
- 📦 Support for containerized test runners with Podman/Docker.
- 🖥️ Supports Linux and macOS.

## Installation

Install all the Jumpstarter Python components:

```console
pip install --extra-index-url https://docs.jumpstarter.dev/packages/simple jumpstarter-all
```

Or, just install the `jmp` CLI tool:

```console
pip install --extra-index-url https://docs.jumpstarter.dev/packages/simple jumpstarter-cli
```

To install the [Jumpstarter Service](https://docs.jumpstarter.dev/introduction/service.html)
in your Kubernetes cluster, see the [Service Installation](https://docs.jumpstarter.dev/installation/service/index.html)
documentation.

## Documentation

Jumpstarter's documentation is available at [docs.jumpstarter.dev](https://docs.jumpstarter.dev).

Additionally, the command line reference documentation can be viewed with `jmp --help`.

## Contributing

Jumpstarter welcomes contributors of all levels of experience and would love to
see you involved in the project. See the [contributing guide](https://github.com/jumpstarter-dev/jumpstarter/blob/main/CONTRIBUTING.md) to get started.

## License

Jumpstarter is licensed under the Apache 2.0 License ([LICENSE](https://github.com/jumpstarter-dev/jumpstarter/blob/main/LICENSE) or [https://www.apache.org/licenses/LICENSE-2.0](https://www.apache.org/licenses/LICENSE-2.0)).
