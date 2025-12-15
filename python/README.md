# ![bolt](assets/bolt.svg) Jumpstarter

[![Matrix](https://img.shields.io/matrix/jumpstarter%3Amatrix.org?color=blue)](https://matrix.to/#/#jumpstarter:matrix.org)
[![Etherpad](https://img.shields.io/badge/Etherpad-Notes-blue?logo=etherpad)](https://etherpad.jumpstarter.dev/pad-lister)
[![Community Meeting](https://img.shields.io/badge/Weekly%20Meeting-Google%20Meet-blue?logo=google-meet)](https://meet.google.com/gzd-hhbd-hpu)
![GitHub Release](https://img.shields.io/github/v/release/jumpstarter-dev/jumpstarter)
![PyPI - Version](https://img.shields.io/pypi/v/jumpstarter)
![GitHub Downloads (all assets, all releases)](https://img.shields.io/github/downloads/jumpstarter-dev/jumpstarter/total)
[![Ask DeepWiki](https://deepwiki.com/badge.svg)](https://deepwiki.com/jumpstarter-dev/jumpstarter)

[![E2E Tests](https://github.com/jumpstarter-dev/jumpstarter/actions/workflows/e2e.yaml/badge.svg)](https://github.com/jumpstarter-dev/jumpstarter/actions/workflows/e2e.yaml)
[![Tests](https://github.com/jumpstarter-dev/jumpstarter/actions/workflows/pytest.yaml/badge.svg)](https://github.com/jumpstarter-dev/jumpstarter/actions/workflows/pytest.yaml)
[![documentation](https://github.com/jumpstarter-dev/jumpstarter/actions/workflows/documentation.yaml/badge.svg)](https://github.com/jumpstarter-dev/jumpstarter/actions/workflows/documentation.yaml)<br>
[![Wheels](https://github.com/jumpstarter-dev/jumpstarter/actions/workflows/publish.yaml/badge.svg)](https://github.com/jumpstarter-dev/jumpstarter/actions/workflows/publish.yaml)
[![Flashing bundles](https://github.com/jumpstarter-dev/jumpstarter/actions/workflows/build_oci_bundle.yaml/badge.svg)](https://github.com/jumpstarter-dev/jumpstarter/actions/workflows/build_oci_bundle.yaml)
[![Containers](https://github.com/jumpstarter-dev/jumpstarter/actions/workflows/build.yaml/badge.svg)](https://github.com/jumpstarter-dev/jumpstarter/actions/workflows/build.yaml)

A free, open source tool for automated testing on real and virtual hardware with
CI/CD integration. Simplify device automation with consistent rules across local
and distributed environments.

## Highlights

- 🧪 **Unified Testing** - One tool for local, virtual, and remote hardware
- 🐍 **Python-Powered** - Leverage Python's testing ecosystem
- 🔌 **Hardware Abstraction** - Simplify complex hardware interfaces with
  drivers
- 🌐 **Collaborative** - Share test hardware globally
- ⚙️ **CI/CD Ready** - Works with cloud native developer environments and
  pipelines
- 💻 **Cross-Platform** - Supports Linux and macOS

## Installation

Install all the Jumpstarter Python components:

```shell
pip install --extra-index-url https://pkg.jumpstarter.dev/ jumpstarter-all
```

Or, just install the `jmp` CLI tool:

```shell
pip install --extra-index-url https://pkg.jumpstarter.dev/ jumpstarter-cli
```

To install the [Jumpstarter
Service](https://jumpstarter.dev/main/introduction/service.html) in your Kubernetes
cluster, see the [Service
Installation](https://jumpstarter.dev/main/getting-started/installation/index.html)
documentation.

## Documentation

Jumpstarter's documentation is available at
[jumpstarter.dev](https://jumpstarter.dev).

Additionally, the command line reference documentation can be viewed with `jmp
--help`.

## Contributing

Jumpstarter welcomes contributors of all levels of experience and would love to
see you involved in the project. See the [contributing
guide](https://jumpstarter.dev/main/contributing.html) to get started.

## License

Jumpstarter is licensed under the Apache 2.0 License ([LICENSE](LICENSE) or
[https://www.apache.org/licenses/LICENSE-2.0](https://www.apache.org/licenses/LICENSE-2.0)).
