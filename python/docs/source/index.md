# Welcome to Jumpstarter

Jumpstarter is a free and open-source testing tool that enables you to test your software stack on both real hardware and virtual environments using CI/CD principles. It provides powerful testing tools that leverage Cloud Native principles, modern CI/CD technologies, and open standards for the next generation of edge devices, whether physical or emulated.

## Device Automation Made Simple

Jumpstarter decouples your target devices (physical or virtual) from test runners, development machines, and CI/CD pipelines, allowing you to use the same automation scripts, commands, and processes everywhere. Think of it as a *Makefile* for device automation that can run locally on your machine or distributed across your infrastructure.

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

## Resources

```{eval-rst}
* `Python Packages <./packages/index.html>`_
```
