# Architecture

The Jumpstarter architecture is based on a client/server model. This enables a
single client to communicate with one or many devices under test.

Devices can either be connected to the same machine as the client or distributed
across remote test runners, for example, in a hybrid cloud CI environment.

The core of this architecture is the gRPC protocol that connects clients to
devices, either directly in local mode, or through a central controller in
distributed mode.

## Core Components

Jumpstarter consists of several key components that work together to provide
testing capabilities for both physical hardware and virtual devices:

### Device Under Test (DUT)

The DUT is the hardware or virtual device being tested with Jumpstarter. One or
more devices can be connected to a single exporter instance so they are treated
as a single unit that can be tested together.

### Clients

The Jumpstarter client is a Python library and CLI tool that connects to
exporters either locally through a socket or remotely through a central server.
Clients can run test scripts, direct commands, or interactive shells to control
hardware.

For more information, see [Clients](./introduction/clients.md).

### Drivers

Jumpstarter drivers are modular components that provide the ability to interact
with specific hardware interfaces or virtual devices. Drivers follow a
consistent pattern with interface definitions, implementation classes, and
client interfaces.

For more information, see [Drivers](./introduction/drivers.md).

### Adapters

Adapters transform connections established by drivers into different forms or interfaces. 
While drivers establish and manage the basic connections to hardware or virtual devices, 
adapters take these connections and provide alternative ways to interact with them, making 
them more convenient for specific use cases.

For example, a network driver might establish a basic TCP connection, while an adapter
could transform that connection into a web-based VNC client interface, a Unix socket, 
or a serial console-like interface.

For more information, see [Adapters](./introduction/adapters.md).

### Exporters

The exporter is a service that runs locally or on a remote Linux device and
"exports" the interfaces connected to the Device Under Test. The exporter
implements a gRPC service that clients can connect to either directly or through
a controller to interact with devices.

For more information, see [Exporters](./introduction/exporters.md).

### Controller and Router

In distributed environments, Jumpstarter provides Kubernetes-based components:

- **Controller**: Manages client leases on exporter instances and tracks
  connected clients and exporters
- **Router**: Facilitates message routing between clients and exporters through
  gRPC streams

For more information, see [Service](./introduction/service.md).

## Operation Modes

Jumpstarter supports two primary operation modes: local and distributed.

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

When no client configuration or environment variables are present, Jumpstarter
runs in local mode and communicates with a built-in exporter service via a local
socket connection.

This mode enables easy development of tests and drivers without requiring
Kubernetes or other infrastructure, whether working with physical hardware or
virtual devices.

#### Example: Running Local Tests

```bash
jmp shell --exporter my-hardware-exporter
jmp shell --exporter my-virtual-exporter

pytest test_device.py
```

### Distributed Mode

In distributed mode, a Kubernetes-based controller manages access to exporters
distributed across a network, with JWT token-based authentication securing all
connections.

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

This mode supports multiple clients and exporters, with the controller managing
leases to ensure exclusive access to both hardware and virtual device resources.

#### Authentication Flow

The authentication flow in distributed mode works as follows:

1. **Client Registration**: Clients are registered in the Kubernetes cluster
   with a unique identity
2. **Token Issuance**: The controller issues JWT tokens to authenticated clients
   and exporters
3. **Secure Communication**: All gRPC communication between components is
   secured with these tokens
4. **Access Control**: The controller enforces permissions based on token
   identity:

   - Which exporters a client can request leases for
   - What actions a client can perform
   - Which driver packages are allowed to be loaded

This authentication mechanism enables fine-grained access control in multi-user
environments and prevents unauthorized access to hardware resources.

#### Example: Running Distributed Tests

```bash
# Configure client with server information
jmp config client use my-client

# Request a lease on an exporter with specific labels
jmp create lease --selector vendor=acme,model=widget-v2

# Run tests using the leased exporter
pytest test_device.py
```

## Authentication

Authentication for both clients and exporters is handled through JWT tokens
managed by the controller.

This authentication mechanism enables dynamic registration of clients and
exporters, allowing controlled access to hardware resources. For example, a CI
pipeline can be granted access to only specific exporters based on its
credentials.

## Integration with Existing Tools

Jumpstarter is designed to integrate with a wide range of existing tools and
workflows. For detailed information about integration patterns and solution
architectures, see the [Solution Architecture](./solution-architecture.md)
document.