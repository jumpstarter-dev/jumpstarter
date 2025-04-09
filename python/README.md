# ![bolt](./assets/bolt.svg) Jumpstarter

[![PyPI](https://img.shields.io/badge/PyPI-Packages-blue?logo=pypi)](https://pypi.org/project/jumpstarter/)
[![Matrix](https://img.shields.io/matrix/jumpstarter%3Amatrix.org?color=blue)](https://matrix.to/#/#jumpstarter:matrix.org)
[![Etherpad](https://img.shields.io/badge/Etherpad-Notes-blue?logo=etherpad)](https://etherpad.jumpstarter.dev/pad-lister)
[![Community Meeting](https://img.shields.io/badge/Weekly%20Meeting-Google%20Meet-blue?logo=google-meet)](https://meet.google.com/gzd-hhbd-hpu)

A free, open-source tool for automated testing on real and virtual hardware with CI/CD integration. Simplify device automation with consistent rules across local and distributed environments.

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
pip install --extra-index-url https://jumpstarter.dev/packages/simple jumpstarter-all
```

Or, just install the `jmp` CLI tool:

```console
pip install --extra-index-url https://jumpstarter.dev/packages/simple jumpstarter-cli
```

To install the [Jumpstarter Service](https://jumpstarter.dev/introduction/service.html)
in your Kubernetes cluster, see the [Service Installation](https://jumpstarter.dev/installation/service/index.html)
documentation.

## Documentation

Jumpstarter's documentation is available at [jumpstarter.dev](https://jumpstarter.dev).

Additionally, the command line reference documentation can be viewed with `jmp --help`.

## Contributing

Jumpstarter welcomes contributors of all levels of experience and would love to
see you involved in the project. See the [contributing guide](https://github.com/jumpstarter-dev/jumpstarter/blob/main/CONTRIBUTING.md) to get started.

## License

Jumpstarter is licensed under the Apache 2.0 License ([LICENSE](https://github.com/jumpstarter-dev/jumpstarter/blob/main/LICENSE) or [https://www.apache.org/licenses/LICENSE-2.0](https://www.apache.org/licenses/LICENSE-2.0)).
