# Introduction

Jumpstarter is an open source framework that brings enterprise-grade testing
capabilities to everyone. While established industries like automotive and
manufacturing have long used HiL testing, these tools have typically been
expensive proprietary systems. Jumpstarter democratizes this technology through
a free, cloud native approach that works with both physical hardware and virtual
devices.

At its core, Jumpstarter uses a client/server architecture where a single client
can control multiple devices under test. Its modular design supports both local
development (devices connected directly to your machine) and distributed testing
environments (devices accessed remotely through a central controller). All
communication happens over gRPC, providing a consistent interface regardless of
deployment model.

Built on Python, Jumpstarter integrates easily with existing development
workflows and runs almost anywhere. It works with common testing tools like
[pytest](https://docs.pytest.org/en/stable/), shell scripts, Makefiles, and
typical CI/CD systems. Beyond testing, it can function as a virtual KVM
(Keyboard, Video, Mouse) switch, enabling remote access to physical devices for
development.

## Core Components

Jumpstarter architecture is based on the following key components:

- Device Under Test (DUT) - Hardware or virtual device being tested
- [Drivers](drivers.md) - Interfaces for DUT communication
- [Adapters](adapters.md) - Convert driver connections into various formats
- [Exporters](exporters.md) - Expose device interfaces over network via gRPC
- [Clients](clients.md) - Libraries and CLI tools for device interaction
- [Service](service.md) - Kubernetes controller for resource management

Component interactions include:

- **DUT and Drivers** - Drivers provide standardized interfaces to DUT's
  hardware connections
- **Drivers and Adapters** - Adapters transform driver connections for
  specialized use cases
- **Drivers/Adapters and Exporters** - Exporters manage drivers/adapters and
  expose them via gRPC
- **Exporters and Clients** - Clients connect to exporters to control devices
- **Clients/Exporters and Service** - Service manages access control and
  resource allocation in distributed mode

Together, these components form a comprehensive testing framework that bridges
the gap between development and deployment environments.

## Operation Modes

Building on these components, Jumpstarter implements two operation modes that
provide flexibility for different scenarios: *local* and *distributed* modes.

### Local Mode

In local mode, clients communicate directly with exporters running on the same
machine or through direct network connections.

```{mermaid}
:config: {"theme":"base","themeVariables":{"primaryColor":"#f8f8f8","primaryTextColor":"#000","primaryBorderColor":"#e5e5e5","lineColor":"#3d94ff","secondaryColor":"#f8f8f8","tertiaryColor":"#fff"}}
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
variables are present, Jumpstarter runs in local mode and communicates with a
built-in exporter service via a local socket connection, requiring no Kubernetes
or other infrastructure. Developers can work with devices on their desk, develop
drivers, create automation scripts, and test with QEMU or other virtualization
tools.

```console
$ jmp shell --exporter my-exporter
$ pytest test_device.py
```

The example above shows typical local mode usage: first connecting to an
exporter (which manages the device interfaces) using the `jmp shell` command,
and then running tests against the device with pytest. The `--exporter` flag
specifies which exporter configuration to use, allowing you to easily switch
between different hardware or virtual device setups.

### Distributed Mode

Distributed mode enables multiple teams to securely share hardware resources
across a network. It uses a Kubernetes-based controller to coordinate access to
exporters, managing leases that grant exclusive access to DUT resources, while
JWT token-based authentication secures all connections between clients and
exporters.

```{mermaid}
:config: {"theme":"base","themeVariables":{"primaryColor":"#f8f8f8","primaryTextColor":"#000","primaryBorderColor":"#e5e5e5","lineColor":"#3d94ff","secondaryColor":"#f8f8f8","tertiaryColor":"#fff"}}
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

Distributed mode is ideal for environments where teams need to share hardware
resources, especially in CI/CD pipelines requiring scheduled device testing. It
excels in geographically distributed test environments where devices are spread
across multiple locations, and in any scenario requiring centralized management
of testing resources. All these scenarios require a robust security model to
manage access rights and prevent resource conflicts.

To address these security needs, the distributed mode implements a comprehensive
authentication system that secures access through:

- **Client Registration** - Clients register in the Kubernetes cluster with
   unique identities
- **Token Issuance** - Controller issues JWT tokens to authenticated clients and
   exporters
- **Secure Communication** - All gRPC communication between components uses
   token authentication
- **Access Control** - Controller enforces permissions based on token identity:
   - Which exporters a client can lease
   - What actions a client can perform
   - Which driver packages can be loaded

This security model enables dynamic registration of clients and exporters,
allowing fine-grained access control in multi-user environments. For example, CI
pipelines can be granted access only to specific exporters based on their
credentials, ensuring proper resource isolation in shared testing environments.

The following example shows how to run tests in distributed mode:

```console
$ jmp config client use my-client
$ jmp create lease --selector vendor=acme,model=widget-v2
$ pytest test_device.py
```

The example above demonstrates the distributed mode workflow: first configuring
the client with connection information for the central controller, then
requesting a lease on an exporter that matches specific criteria (using selector
labels), and finally running tests against the acquired DUT. The lease system
ensures exclusive access to the requested resources for the duration of testing,
preventing conflicts with other users or pipelines in the shared environment.

```{toctree}
:maxdepth: 1
:hidden:
drivers.md
adapters.md
exporters.md
clients.md
service.md
```
