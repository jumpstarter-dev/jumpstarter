# Glossary

## Acronyms

```{glossary}
:sorted:

DUT
  Device Under Test.

gRPC
  Google Remote Procedure Call, the communication framework used by
  Jumpstarter for all client-exporter and service communication.

HiL
  Hardware-in-the-Loop, a testing methodology where real hardware
  components are integrated into a simulation loop to validate software against
  physical devices.

JEP
  Jumpstarter Enhancement Proposal, a design document that proposes a
  significant new feature, process change, or architectural decision for the
  Jumpstarter project.

MCP
  Model Context Protocol, a standard protocol that enables AI coding
  agents and assistants to interact with external tools and services. Jumpstarter
  exposes hardware control via MCP through the `jmp mcp serve` command.
```

## Entities

```{glossary}
:sorted:

client
  A developer or a CI/CD pipeline that connects to the Jumpstarter
  service and leases exporters. The client can run tests on the leased resources.

controller
  The central service that authenticates and connects the
  exporters and clients, manages leases, and provides an inventory of available
  exporters and clients.

exporter
  A Linux service that exports the interfaces to the DUTs. An
  exporter connects directly to a Jumpstarter server or directly to a client.

host
  A system running the exporter service, typically a low-cost test
  system such as a single board computer with sufficient interfaces to connect
  to hardware.

operator
  A Kubernetes operator that installs and manages the Jumpstarter
  controller, router, and related infrastructure resources via a `Jumpstarter`
  custom resource.

router
  A service used by the controller to route messages between clients
  and exporters through a gRPC tunnel, enabling remote access to exported
  interfaces.

service
  The Kubernetes-based backend that provides the controller, router,
  and authentication components for managing clients, exporters, and leases in
  distributed mode.
```

## Concepts

```{glossary}
:sorted:

adapter
  A component that transforms connections exposed by drivers into
  different forms or interfaces, such as port forwarding, VNC access, or
  terminal emulation.

device
  A hardware or virtual resource exposed on an exporter. Examples include
  network interfaces, serial ports, GPIO pins, storage devices, and CAN bus
  interfaces.

direct mode
  An operation mode where a client connects directly to an
  exporter over TCP without a controller or Kubernetes cluster, useful for
  single-user remote access to hardware on another machine.

distributed mode
  An operation mode that enables multiple teams to securely
  share hardware resources across a network using a Kubernetes-based controller
  to coordinate access to exporters and manage leases.

driver
  A modular component that provides a standardized interface to a
  specific hardware or virtual device type. Drivers run on the exporter and
  expose methods over gRPC that clients can call remotely.

exporter shell
  An interactive shell environment spawned by `jmp shell` that
  provides access to an exporter's driver CLI interfaces via the `j` command.

hook
  A shell script configured on an exporter that runs automatically at
  lease boundaries -- before drivers are available to the client, or after the
  session ends but before the lease is released.

label selector
  Key-value metadata attached to exporters that clients use to
  select specific devices for leasing, similar to Kubernetes label selectors.

lease
  A time-limited reservation of an exporter that ensures exclusive access
  to specific devices for the duration of testing.

local mode
  An operation mode where clients communicate directly with
  exporters running on the same machine, ideal for individual developers
  working with accessible hardware or virtual devices.

session
  A connection context created when a client connects to an exporter,
  during which driver instances are maintained and tests are executed.
```

## Tools

```{glossary}
:sorted:

j
  A shorthand CLI command available within the exporter shell
  that provides access to driver CLI interfaces for the current session.

jmp
  The primary Jumpstarter CLI tool used for managing clients, exporters,
  leases, configuration, and shell sessions. Subcommands include `jmp admin`,
  `jmp shell`, `jmp login`, and `jmp mcp serve`.
```
