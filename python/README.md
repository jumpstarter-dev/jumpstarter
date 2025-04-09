# ![bolt](./assets/bolt.svg) Jumpstarter

[![PyPI](https://img.shields.io/badge/PyPI-Packages-blue?logo=pypi)](https://pypi.org/project/jumpstarter/)
[![Matrix](https://img.shields.io/matrix/jumpstarter%3Amatrix.org?color=blue)](https://matrix.to/#/#jumpstarter:matrix.org)
[![Etherpad](https://img.shields.io/badge/Etherpad-Notes-blue?logo=etherpad)](https://etherpad.jumpstarter.dev/pad-lister)
[![Community Meeting](https://img.shields.io/badge/Weekly%20Meeting-Google%20Meet-blue?logo=google-meet)](https://meet.google.com/gzd-hhbd-hpu)

A free, open-source tool for automated testing on real and virtual hardware with CI/CD integration. Simplify device automation with consistent rules across local and distributed environments.

## Highlights

- **Unified Testing** - One tool for local, virtual, and remote hardware
- **Python-Powered** - Leverage Python's testing ecosystem
- **Hardware Abstraction** - Simplify interfaces with drivers
- **Collaborative** - Share test hardware globally
- **CI/CD Ready** - Works with cloud native developer environments and pipelines
- **Cross-Platform** - Supports Linux and macOS

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
see you involved in the project. See the [contributing guide](CONTRIBUTING.md) to get started.

## License

Jumpstarter is licensed under the Apache 2.0 License ([LICENSE](LICENSE) or [https://www.apache.org/licenses/LICENSE-2.0](https://www.apache.org/licenses/LICENSE-2.0)).
