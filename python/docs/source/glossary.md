# Glossary

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
  agents and assistants to interact with external tools and services through
  structured tool definitions.

client
  A developer or a CI/CD pipeline that connects to the Jumpstarter
  service and leases exporters. The client can run tests on the leased resources.

controller
  The central service that authenticates and connects the
  exporters and clients, manages leases, and provides an inventory of available
  exporters and clients.

DUT Link Board
  An open-source hardware board designed for Jumpstarter that
  provides power control, serial, and storage multiplexing for a device under
  test.

exporter
  A Linux service that exports the interfaces to the DUTs. An
  exporter connects directly to a Jumpstarter server or directly to a client.

host
  A system running the exporter service, typically a low-cost test
  system such as a single board computer with sufficient interfaces to connect
  to hardware.

MCP server
  A Jumpstarter component (`jmp mcp serve`) that exposes hardware
  control as structured MCP tools accessible by AI coding agents and assistants.

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

@export decorator
  A Python decorator that marks a driver method to be
  exposed over gRPC as a remotely callable unary or server-streaming RPC.

@exportstream decorator
  A Python decorator that marks a driver method as
  opening a bidirectional byte stream over gRPC for real-time data exchange such
  as serial communication or video capture.

adapter
  A component that transforms connections exposed by drivers into
  different forms or interfaces. Adapters take a driver client as input and
  provide alternative ways to interact with the underlying connection, such as
  port forwarding, VNC access, or terminal emulation.

afterLease hook
  A lifecycle hook script that runs after the client session
  ends but before the lease is released, typically used for device cleanup.

beforeLease hook
  A lifecycle hook script that runs after a lease is assigned
  but before drivers are available to the client, typically used for device
  initialization.

client config
  A YAML configuration file that stores client connection
  settings including the service endpoint, authentication token, and allowed
  driver packages.

composite driver
  A driver that combines multiple lower-level drivers to
  create higher-level abstractions or specialized workflows, organized in a tree
  structure to represent complex device configurations.

custom driver
  A driver that defines its own interface rather than
  implementing a predefined Jumpstarter interface, built for specialized hardware
  or domain-specific abstractions.

device
  A device that is exposed on an exporter. The exporter enumerates
  these devices and makes them available for use in tests. Examples include
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
  The term for both the driver class and the corresponding driver
  client class, not to be confused with `Driver`, the base class of all driver
  classes. Drivers in the main Jumpstarter repository are called in-tree
  drivers, otherwise they are called out-of-tree drivers. Drivers
  implementing predefined interfaces are called standard drivers, otherwise
  they are called custom drivers.

driver allowlist
  A client configuration setting that restricts which driver
  Python packages can be dynamically loaded, preventing execution of untrusted
  code in distributed mode.

driver class
  A class that implements an interface and inherits from the
  `Driver` base class. It uses the `@export` decorator to expose methods that
  can be called remotely by clients.

driver client class
  The driver client class that is used directly by end
  users. It interacts with the driver class remotely via remote procedure call
  to invoke exported methods, which in turn interact with the exporter
  resources.

driver tree
  The hierarchical tree structure in which Jumpstarter organizes
  composite and child drivers to represent complex device configurations and
  their relationships.

exporter config
  A YAML configuration file that defines which drivers an
  exporter loads, their parameters, and optional lifecycle hooks and connection
  settings.

exporter shell
  An interactive shell environment spawned by `jmp shell` that
  provides access to an exporter's driver CLI interfaces via the `j` command.

exporter status
  The current state of an exporter in its lifecycle. States
  include `AVAILABLE`, `BEFORE_LEASE_HOOK`, `LEASE_READY`,
  `AFTER_LEASE_HOOK`, `BEFORE_LEASE_HOOK_FAILED`, `AFTER_LEASE_HOOK_FAILED`,
  and `OFFLINE`.

hook
  A shell script configured on an exporter that runs automatically at
  lease boundaries. A beforeLease hook runs after a lease is assigned but
  before drivers are available to the client, and an afterLease hook runs
  after the session ends but before the lease is released.

in-tree driver
  A driver that is maintained within the main Jumpstarter
  repository and distributed as an official package.

interface class
  An abstract base class that defines the contract for driver
  implementations. It specifies the required methods that must be implemented by
  driver classes and provides the client class path through the `client()` class
  method.

label selector
  Key-value metadata attached to exporters that clients use to
  select specific devices for leasing, similar to Kubernetes label selectors.

lease
  A time-limited reservation of an exporter. A lease is created by a
  client and allows the client to use the exporter resources for a limited time.
  Leases ensure exclusive access to specific devices/exporters.

local mode
  An operation mode where clients communicate directly with
  exporters running on the same machine or through direct network connections,
  ideal for individual developers working directly with accessible hardware or
  virtual devices.

message
  Commands sent from driver clients to driver implementations,
  allowing the client to trigger actions or retrieve information from the
  device.

onFailure
  A hook configuration field that controls the behavior when a hook
  script fails: `warn` (continue normally), `endLease` (terminate the lease), or
  `exit` (shut down the exporter).

out-of-tree driver
  A driver that is maintained outside the main Jumpstarter
  repository, typically developed by third parties for specialized hardware.

RPC styles
  The three gRPC communication patterns used by Jumpstarter
  drivers: unary (single request/response), server streaming (one request,
  multiple responses), and bidirectional streaming (full-duplex byte stream).

session
  A connection context created when a client connects to an exporter,
  during which driver instances are maintained and tests are executed.

standard driver
  A driver that implements one of Jumpstarter's predefined
  interface contracts, ensuring interoperability with standard client tooling.

stream
  A continuous data exchange channel established by drivers for
  communications like serial connections or video streaming, enabling real-time
  interaction with both physical and virtual interfaces across the network.

user config
  A YAML configuration file at `~/.config/jumpstarter/config.yaml`
  that defines global user settings including the currently selected client
  configuration.

env()
  A context manager from `jumpstarter.common.utils` that creates a
  client connected to the exporter configured in the shell environment.

j
  A shorthand CLI command available within the Jumpstarter exporter shell
  that provides access to driver CLI interfaces for the current session.

jmp
  The primary Jumpstarter CLI tool used for managing clients, exporters,
  leases, configuration, and shell sessions.

jmp admin
  A `jmp` subcommand group for administrative operations such as
  creating/managing clusters, clients, exporters, and installing the Jumpstarter
  service.

jmp login
  A `jmp` subcommand that authenticates a client or exporter
  against the controller using OIDC or token-based authentication.

jmp mcp serve
  A `jmp` subcommand that starts an MCP server exposing
  Jumpstarter hardware control as structured tools for AI agents.

jmp shell
  A `jmp` subcommand that spawns an interactive shell session
  connected to a local or remote exporter, providing access to driver interfaces
  via the `j` command.

JumpstarterTest
  A pytest base class provided by the `jumpstarter-testing`
  package that handles connection management, lease acquisition, and client
  fixture setup for hardware tests.

PexpectAdapter
  An adapter from `jumpstarter-driver-network` that wraps a
  serial console driver client into a pexpect `fdspawn` object for
  pattern-based console interaction in tests.
```
