# What is Jumpstarter?

Jumpstarter is a free and open-source testing tool that enables you to test your
software stack on both real hardware and virtual environments using CI/CD
principles.

Automated testing with physical hardware (Hardware-in-the-Loop or HiL) and
virtual devices has been established for years in industries such as automotive and
manufacturing. However, these tools are often expensive and inaccessible to
hobbyists and open-source projects.

Jumpstarter provides powerful testing tools that leverage [Cloud
Native](https://www.cncf.io/) principles, modern CI/CD technologies, and open
standards for the next generation of edge devices, whether physical or emulated.

For a detailed technical overview of the architecture, see the
[Architecture](../architecture.md) documentation.

## Core Components

Jumpstarter consists of the following core components:

- [Clients](./clients.md) - Python library and CLI tools that allow you to
  interact with your devices
- [Drivers](./drivers.md) - Modular interfaces that define how to interact with
  specific hardware or virtual interfaces
- [Exporters](./exporters.md) - Services that expose device interfaces using
  drivers
- [Service](./service.md) - Kubernetes-based controller that manages device
  access

## Development Environment

Since Jumpstarter's core components are written in Python, they can run almost
anywhere. This means that you can set up a test lab with physical hardware or
emulated devices (using tools like QEMU), while still using the same Linux-based
CI systems you currently host in the cloud.

Jumpstarter integrates seamlessly with the existing ecosystem of Python testing
tools such as [pytest](https://docs.pytest.org/en/stable/). You can also use the
Jumpstarter CLI directly from shell scripts and Makefiles allowing you to write
simple automation scripts easily.

In addition to testing, Jumpstarter can also act as a KVM (Keyboard, Video,
Mouse) switch - a hardware device that allows multiple computers to share a
single set of input/output devices. Similarly, Jumpstarter enables developers to
remotely access and control both physical and virtual devices for ad-hoc
development whether they are sitting at the next desk or working remotely.

## Operation Modes

Jumpstarter can be used in either a *local-only* or *distributed* environment
depending on your development needs.

### Local-Only Mode

When using Jumpstarter locally, you can develop drivers, write automated tests,
and control your devices directly from your development machine without
additional infrastructure.

The *local-only mode* is useful when:

- Working with hardware on your desk or virtual devices that you have unlimited
  access to
- Developing and testing drivers for new hardware or emulated environments
- Creating initial test automation scripts
- Using QEMU or other virtualization tools to emulate target devices

For details on how this mode works, see the [Running Tests
Locally](../architecture.md#local-mode) section in the architecture
documentation.

### Distributed Mode

As your project grows, Jumpstarter helps you collaborate across teams, implement
CI/CD pipelines, and automate common tasks such as firmware updates.

The *distributed mode* leverages [Kubernetes](https://kubernetes.io/) to support
the management of multiple devices (physical or virtual) directly from your
existing cluster. This allows for seamless integration with many existing Cloud
Native technologies such as [Tekton](https://tekton.dev),
[ArgoCD](https://argoproj.github.io/cd/), and
[Prometheus](https://prometheus.io/docs/introduction/overview/).

The distributed mode is ideal when:

- Multiple teams need access to shared hardware or virtual device resources
- Continuous integration requires scheduled tests on physical or emulated
  devices
- Test environments are distributed across multiple locations
- Devices (physical or virtual) need to be managed remotely

For technical details on this mode, see the [Running Tests Through a Central
Controller](../architecture.md#distributed-mode) section in the architecture
documentation.

## Getting Started

To start using Jumpstarter, check out the following guides:

- [Setup a Local Client](../getting-started/setup-local-exporter.md) - For
  local-only development
- [Setup a Remote Client](../getting-started/setup-exporter-client.md) - For
  distributed environments
