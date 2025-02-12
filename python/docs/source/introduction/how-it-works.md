# How Jumpstarter Works

Jumpstarter provides a set of tools that enable you to build a consistent
development environment for your hardware. Think of it as a *Makefile*
for hardware automation.

These tools enable you to decouple your target hardware from the test runners,
development machines, and CI/CD pipelines allowing you to use the same automation
scripts, commands, and processes everywhere.

The core components of Jumpstarter are:
- [Clients](./clients.md) that allow you to interact with your hardware through
  a CLI tool or Python library.
- [Drivers](./drivers.md) that describe how to interact with your hardware interfaces.
- [Exporters](./exporters.md) that expose your device's hardware using the drivers.
- A [Service](./service.md) that helps you manage hardware access from anywhere.

Since Jumpstarter's core components are written in Python, it is possible to run
them almost everywhere. This means that you can setup a test lab with low-cost
exporters such as Raspberry Pis or mini PCs, while still using the same
Linux-based CI systems you currently host in the cloud.

Jumpstarter is also able to seamlessly integrate into the existing ecosystem of
Python testing tools such as [pytest](https://docs.pytest.org/en/stable/).
You can also use the Jumpstarter CLI directly from shell scripts and Makefiles
allowing you to write simple automation scripts easily.

In addition to testing, Jumpstarter can also act as a
[KVM](https://en.wikipedia.org/wiki/KVM_switch) allowing developers to remotely
access hardware for ad-hoc development whether they are sitting at the next desk
or on the other side of the globe.

## Development Modes

Jumpstarter can be used in either a *local-only* or *distributed* environment
depending on your hardware development needs.

### Local-Only

When using Jumpstarter locally, you can easily develop drivers, write automated
tests, and control your hardware directly from your development machine.

The *local-only mode* is useful when working with hardware on your desk
that you have unlimited access to.

![local mode](how-it-works-local.svg)

### Distributed

When your project grows, Jumpstarter also can help you collaborate across teams,
implement CI/CD pipelines, and automate common tasks such as firmware updates.

The *distributed mode* takes advantage of [Kubernetes](https://kubernetes.io/)
to support the management of multiple target devices directly from your existing
cluster. This allows for seamless integration with many existing Cloud Native
technologies such as [Tekton](https://tekton.dev), [ArgoCD](https://argoproj.github.io/cd/),
and [Prometheus](https://prometheus.io/docs/introduction/overview/).

![distributed mode](how-it-works-distributed.svg)

The following sections provide more information on the basics of Jumpstarter,
its core components, and how they work together to make hardware testing easier.
