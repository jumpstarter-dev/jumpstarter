# Welcome to Jumpstarter

```{eval-rst}
.. image:: https://img.shields.io/badge/GitHub-Repository-blue?logo=github
   :target: https://github.com/jumpstarter-dev/jumpstarter
   :alt: GitHub Repository

.. image:: https://img.shields.io/badge/PyPI-Packages-blue?logo=pypi
   :target: https://pypi.org/project/jumpstarter/
   :alt: Python Packages

.. image:: https://img.shields.io/matrix/jumpstarter%3Amatrix.org?color=blue
   :target: https://matrix.to/#/#jumpstarter:matrix.org
   :alt: Matrix Chat

.. image:: https://img.shields.io/badge/Etherpad-Notes-blue?logo=etherpad
   :target: https://etherpad.jumpstarter.dev/pad-lister
   :alt: Etherpad Notes

.. image:: https://img.shields.io/badge/Weekly%20Meeting-Google%20Meet-blue?logo=google-meet
   :target: https://meet.google.com/gzd-hhbd-hpu
   :alt: Weekly Meeting
```

Jumpstarter is a free and open source testing tool that bridges the gap between development workflows and deployment environments. It enables you to test your software stack consistently across both real hardware and virtual environments using cloud native principles. By decoupling your target devices (physical or virtual) from test runners, development machines, and CI/CD pipelines, Jumpstarter allows you to use the same automation scripts everywhere - like a *Makefile* for device automation.

```{include} ../../README.md
:start-after: "## Highlights"
:end-before: "##"
```

## Learning Paths

### 🔰 For Newcomers
**New to Jumpstarter?** Start here to understand the core concepts and basic workflows:
- [What is Jumpstarter?](introduction/index.md) - Understand the key concepts and components
- [Installation Guide](installation/index.md) - Get Jumpstarter installed on your system
- [Setting Up Your First Local Exporter](getting-started/setup-local-exporter.md) - Connect to your first device

### 💻 For Testers & Developers
**Want to use Jumpstarter for testing?** These resources will help you automate tests for your devices:
- [Setting Up Client & Exporter](getting-started/setup-exporter-client.md) - Configure your testing environment
- [Command Line Interface](cli/index.md) - Master the CLI for automation scripts
- [Example Projects](https://github.com/jumpstarter-dev/jumpstarter/tree/main/examples) - Real-world testing examples

### 🛠️ For Contributors
**Looking to extend Jumpstarter?** Learn how to develop your own drivers and components:
- [Architecture Overview](architecture.md) - Understand how Jumpstarter works internally
- [Driver Development](introduction/drivers.md) - Create custom drivers for new hardware
- [API Reference](api-reference/index.md) - Comprehensive API documentation
- [Contributing Guide](contributing.md) - Guidelines for contributing to the project

### 🏢 For Teams & Enterprise
**Building a distributed CI environment?** Scale Jumpstarter across your organization:
- [Distributed Mode Setup](installation/service/index.md) - Deploy the Kubernetes-based controller
- [Solution Architecture](solution-architecture.md) - Reference architectures for complex environments
- [Managing Lab Resources](introduction/service.md) - Coordinate access to shared hardware

```{toctree}
:maxdepth: 3
:hidden:

introduction/index.md
installation/index.md
getting-started/index.md
cli/index.md
config/index.md
architecture.md
solution-architecture.md
contributing.md
glossary.md
api-reference/index.md
```