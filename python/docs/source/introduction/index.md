# Introduction

Jumpstarter is an open source framework that brings enterprise-grade testing
capabilities to everyone. While established industries like automotive and
manufacturing have long used {term}`HiL` testing, these tools have typically been
expensive proprietary systems. Jumpstarter democratizes this technology through
a free, cloud native approach that works with both physical hardware and virtual
devices.

At its core, Jumpstarter uses a client/server architecture where a single client
can control multiple devices under test. Its modular design supports both local
development (devices connected directly to your machine) and {term}`distributed mode`
testing environments (devices accessed remotely through a central {term}`controller`). All
communication happens over {term}`gRPC`, providing a consistent interface regardless of
deployment model. Every interface is programmatic -- there is no GUI-only
workflow that a script or agent cannot replicate. A human developer running
jmp shell, a [pytest](https://docs.pytest.org/en/stable/) script, a CI
pipeline, and an
[AI agent](../getting-started/guides/integration-patterns/agentic.md)
all use the exact same APIs, authentication, and access controls.

Built on Python, Jumpstarter integrates easily with existing development
workflows and runs almost anywhere. It works with common testing tools like
pytest, shell scripts, Makefiles, and typical CI/CD systems. Beyond testing, it
can function as a virtual KVM (Keyboard, Video, Mouse) switch, enabling remote
access to physical devices for development.

## Core Components

Jumpstarter architecture is based on the following key components:

- {term}`DUT` - Hardware or virtual device being tested
- [Drivers](drivers.md) - Interfaces for {term}`DUT` communication
- [{term}`Adapter`s](adapters.md) - Convert driver connections into various formats
- [Exporters](exporters.md) - Expose device interfaces over network via {term}`gRPC`
- [Hooks](hooks.md) - Lifecycle scripts that run at {term}`lease` boundaries
- [Clients](clients.md) - Libraries and CLI tools for device interaction
- [Service](service.md) - Kubernetes {term}`controller` for resource management

Component interactions include:

- **{term}`DUT` and Drivers** - Drivers provide standardized interfaces to {term}`DUT`'s
  hardware connections
- **Drivers and {term}`Adapter`s** - {term}`Adapter`s transform driver connections for
  specialized use cases
- **Drivers/{term}`Adapter`s and {term}`Exporter`s** - {term}`Exporter`s manage drivers/{term}`adapter`s and
  expose them via {term}`gRPC`
- **{term}`hook`s and {term}`Exporter`s** - {term}`hook`s execute shell scripts at {term}`lease` boundaries,
  running before drivers are available and after the {term}`session` ends
- **{term}`Exporter`s and Clients** - Clients connect to {term}`exporter`s to control {term}`device`s
- **Clients/{term}`Exporter`s and {term}`service`** - {term}`service` manages access control and
  resource allocation in {term}`distributed mode`

Together, these components form a comprehensive testing framework that bridges
the gap between development and deployment environments.

```{mermaid}
flowchart TB
    subgraph "Kubernetes Cluster"
        Controller["Controller\nInventory / Lease / Access Control"]
        Router["Router\nNAT Traversal Rendezvous"]
        CRDs["CRDs\nExporter, Client, Lease"]
        Controller --- CRDs
    end

    subgraph "Exporter Host"
        Exporter["Exporter"]
        subgraph "Drivers"
            GPIO["GPIO"]
            HDMI["HDMI"]
            Serial["Serial"]
            Storage["Storage"]
        end
        Exporter --- GPIO
        Exporter --- HDMI
        Exporter --- Serial
        Exporter --- Storage
    end

    DUT["Device Under Test"]
    GPIO --> DUT
    HDMI --> DUT
    Serial --> DUT
    Storage --> DUT

    Client["Client\n(CLI / Python API)"]

    Client -- "Remote Access\n(gRPC)" --> Router
    Router <--> Exporter
    Client -. "Local Dev\n(direct)" .-> Exporter
    Controller <--> Router
```

## Operation Modes

Building on these components, Jumpstarter implements three operation modes that
provide flexibility for different scenarios: {term}`local mode`,
{term}`direct mode`, and {term}`distributed mode`.

### Local Mode

In {term}`local mode`, clients communicate directly with {term}`exporter`s running on the same
machine or through direct network connections.

```{mermaid}
flowchart TB
    subgraph "Developer Machine"
        Client["Client\n(Python Library/CLI)"]
        Exporter["Exporter\n(Local Service)"]
    end

    subgraph "Target Devices"
        DUT["Physical/Virtual\nDevice Under Test"]
        Power["Power Interface"]
        Serial["Serial Interface"]
        Storage["Storage Interface"]
    end

    Client <--> |"gRPC via Socket"| Exporter
    Exporter --> Power
    Exporter --> Serial
    Exporter --> Storage
    Power --> DUT
    Serial --> DUT
    Storage --> DUT
```

This mode is ideal for individual developers working directly with accessible
hardware or virtual devices. When no client configuration or environment
variables are present, Jumpstarter runs in {term}`local mode` and communicates with a
built-in {term}`exporter` service via a local socket connection, requiring no Kubernetes
or other infrastructure. Developers can work with devices on their desk, develop
drivers, create automation scripts, and test with QEMU or other virtualization
tools.

```console
$ jmp shell --exporter my-exporter
$ pytest test_device.py
```

The example above shows typical {term}`local mode` usage: first connecting to an
{term}`exporter` (which manages the {term}`device` interfaces) using the jmp shell command,
and then running tests against the device with pytest. The `--exporter` flag
specifies which exporter configuration to use, allowing you to easily switch
between different hardware or virtual {term}`device` setups.

### Direct Mode

{term}`Direct mode` connects a client to an {term}`exporter` over TCP without a
{term}`controller` or Kubernetes cluster. This is useful when hardware is on one
machine and the client is on another, but you don't need multi-user
{term}`lease` management.

```{mermaid}
flowchart LR
    subgraph "Client Machine"
        Client["Client\n(Python Library/CLI)"]
    end

    subgraph "Exporter Machine"
        Exporter["Exporter\n(Remote Service)"]
        Power["Power"]
        Serial["Serial"]
        Storage["Storage"]
    end

    DUT["Device Under Test"]

    Client <--> |"gRPC via TCP"| Exporter
    Exporter --> Power
    Exporter --> Serial
    Exporter --> Storage
    Power --> DUT
    Serial --> DUT
    Storage --> DUT
```

```console
$ jmp shell --exporter example-direct
```

Only one client should connect at a time. For shared, multi-user environments
use {term}`distributed mode` instead.

### Distributed Mode

{term}`Distributed mode` enables multiple teams to securely share hardware resources
across a network. It uses a Kubernetes-based {term}`controller` to coordinate access to
{term}`exporter`s, managing {term}`lease`s that grant exclusive access to {term}`DUT` resources, while
JWT token-based authentication secures all connections between clients and
{term}`exporter`s.

```{mermaid}
flowchart TB
    subgraph "Kubernetes Cluster"
        Controller["Controller\nResource Management"]
        Router["Router\nMessage Routing"]
        Auth["Authentication\nJWT Tokens"]
    end

    subgraph "Test Runners"
        Client1["Client 1\n(CI Pipeline)"]
        Client2["Client 2\n(Developer)"]
    end

    subgraph "Lab Resources"
        Exporter1["Exporter 1\n(Physical Hardware)"]
        Exporter2["Exporter 2\n(Virtual Devices)"]
        subgraph "Devices"
            DUT1["Physical Device 1"]
            DUT2["Physical Device 2"]
            DUT3["Virtual Device"]
        end
    end

    Client1 <--> |"JWT Authentication"| Auth
    Client2 <--> |"JWT Authentication"| Auth
    Exporter1 <--> |"JWT Authentication"| Auth
    Exporter2 <--> |"JWT Authentication"| Auth
    Auth <--> Controller

    Client1 <--> |"gRPC (Authorized)"| Controller
    Client2 <--> |"gRPC (Authorized)"| Controller
    Controller <--> Router
    Router <--> |"gRPC"| Exporter1
    Router <--> |"gRPC"| Exporter2
    Exporter1 --> DUT1
    Exporter1 --> DUT2
    Exporter2 --> DUT3
```

{term}`Distributed mode` is ideal for environments where teams need to share hardware
resources, especially in CI/CD pipelines requiring scheduled device testing. It
excels in geographically distributed test environments where devices are spread
across multiple locations, and in any scenario requiring centralized management
of testing resources. All these scenarios require a robust security model to
manage access rights and prevent resource conflicts.

To address these security needs, the {term}`distributed mode` implements a comprehensive
authentication system that secures access through:

- **Client Registration** - Clients register in the Kubernetes cluster with
   unique identities
- **Token Issuance** - {term}`Controller` issues JWT tokens to authenticated clients and
   {term}`exporter`s
- **Secure Communication** - All {term}`gRPC` communication between components uses
   token authentication
- **Access Control** - {term}`Controller` enforces permissions based on token identity:
   - Which {term}`exporter`s a client can {term}`lease`
   - What actions a client can perform
   - Which driver packages can be loaded

This security model enables dynamic registration of clients and {term}`exporter`s,
allowing fine-grained access control in multi-user environments. For example, CI
pipelines can be granted access only to specific {term}`exporter`s based on their
credentials, ensuring proper resource isolation in shared testing environments.

The following example shows how to run tests in {term}`distributed mode`:

```console
$ jmp config client use my-client
$ jmp create lease --selector vendor=acme,model=widget-v2
$ pytest test_device.py
```

The example above demonstrates the {term}`distributed mode` workflow: first configuring
the client with connection information for the central {term}`controller`, then
requesting a {term}`lease` on an {term}`exporter` that matches specific criteria (using
{term}`label selector`s), and finally running tests against the acquired {term}`DUT`. The {term}`lease` system
ensures exclusive access to the requested resources for the duration of testing,
preventing conflicts with other users or pipelines in the shared environment.

```{toctree}
:maxdepth: 1
:hidden:
drivers.md
adapters.md
exporters.md
hooks.md
clients.md
service.md
```
