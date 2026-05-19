# Glossary

## Acronyms

* `ACME`: Automatic Certificate Management Environment, a protocol for automated TLS certificate issuance and renewal, commonly used with Let's Encrypt via cert-manager.
* `ADB`: Android Debug Bridge, a command-line tool for communicating with Android devices, tunneled through a Jumpstarter driver for remote Android access.
* `ADR`: Architecture Decision Record, a structured document for capturing important architectural decisions with context and consequences.
* `API`: Application Programming Interface, the programmatic interface exposed by Jumpstarter drivers, clients, and services for automated device interaction.
* `BLE`: Bluetooth Low Energy, a wireless communication protocol for short-range data transfer, supported by a Jumpstarter driver.
* `CAN`: Controller Area Network, a vehicle bus protocol used in automotive and industrial systems, supported by a Jumpstarter driver.
* `CI/CD`: Continuous Integration/Continuous Deployment.
* `CRD`: Custom Resource Definition, a Kubernetes extension mechanism used by Jumpstarter to store information about clients, exporters, leases, and other resources.
* `DNS`: Domain Name System, the hierarchical naming system used to resolve Jumpstarter service endpoint hostnames to IP addresses.
* `DoIP`: Diagnostics over Internet Protocol (ISO-13400), an automotive protocol for transmitting diagnostic messages over TCP/IP, supported by a Jumpstarter driver.
* `DUT`: Device Under Test.
* `ELF`: Executable and Linkable Format, the standard binary file format for executables on Unix-like systems, auto-detected by the Renode driver for firmware loading.
* `GPIO`: General-Purpose Input/Output, digital signal pins on a device that can be programmatically controlled through Jumpstarter's gpiod driver.
* `gRPC`: Google Remote Procedure Call, the communication framework used by Jumpstarter for all client-exporter and service communication.
* `HDMI`: High-Definition Multimedia Interface, a video/audio interface used for display output capture from devices under test.
* `HiL`: Hardware-in-the-Loop, a testing methodology where real hardware components are integrated into a simulation loop to validate software against physical devices.
* `HTTP/2`: The second major version of the HTTP protocol, required by gRPC for multiplexed communication between Jumpstarter components.
* `IQN`: iSCSI Qualified Name, a unique identifier used to name iSCSI targets and initiators in the Jumpstarter iSCSI driver.
* `iSCSI`: Internet Small Computer Systems Interface, a storage networking protocol used by the Jumpstarter iSCSI driver to export disk images as network-accessible LUNs.
* `ISO-TP`: ISO 15765-2 Transport Protocol, a transport layer protocol for transmitting messages that exceed the CAN frame size limit, used for UDS over CAN.
* `JEP`: Jumpstarter Enhancement Proposal, a design document that proposes a significant new feature, process change, or architectural decision for the Jumpstarter project.
* `JTAG`: Joint Test Action Group, a hardware debugging and programming interface standard used for on-chip debugging of embedded processors.
* `JWT`: JSON Web Token, the authentication mechanism used by Jumpstarter in distributed mode to secure communication between clients, exporters, and the controller.
* `KEP`: Kubernetes Enhancement Proposal, Kubernetes's formal process for proposing significant changes, referenced as prior art for the JEP process.
* `KVM`: Keyboard, Video, Mouse.
* `LUN`: Logical Unit Number, an identifier for a logical storage unit exported through iSCSI by the Jumpstarter iSCSI driver.
* `MCU`: Microcontroller Unit, a small computer on a single integrated circuit containing a processor, memory, and programmable I/O peripherals.
* `MCP`: Model Context Protocol, a standard protocol that enables AI coding agents and assistants to interact with external tools and services through structured tool definitions.
* `mTLS`: Mutual TLS, a form of TLS where both the client and server authenticate each other using certificates, used between Jumpstarter control-plane components.
* `NAT`: Network Address Translation, a method of mapping private IP addresses to public ones; the Jumpstarter router enables exporter access through NAT boundaries.
* `OIDC`: OpenID Connect, an identity authentication protocol built on top of OAuth 2.0 that Jumpstarter uses for external authentication provider integration.
* `OKD`: The community distribution of Kubernetes that powers OpenShift, providing a Kubernetes platform with developer- and operations-centric tools.
* `OLM`: Operator Lifecycle Manager, a Kubernetes tool that manages the lifecycle of operators including installation, updates, and dependency resolution.
* `OTLP`: OpenTelemetry Protocol, the wire protocol defined by OpenTelemetry for transmitting telemetry data such as metrics, logs, and traces.
* `PEP`: Python Enhancement Proposal, Python's process for proposing changes to the language, referenced as prior art for the JEP process.
* `PTY`: Pseudo-Terminal, a software abstraction that emulates a hardware text terminal, used by hook scripts for line-buffered output.
* `QEMU`: Quick Emulator, an open-source machine emulator and virtualizer used by the Jumpstarter QEMU driver to create Linux-class virtual targets for testing.
* `QMP`: QEMU Machine Protocol, QEMU's JSON-based control interface used for programmatic management of virtual machine instances.
* `RBAC`: Role-Based Access Control, a security model that restricts system access based on the roles assigned to individual users within an organization.
* `RPC`: Remote Procedure Call, the communication paradigm used by Jumpstarter drivers to invoke methods across network boundaries via gRPC.
* `RTOS`: Real-Time Operating System, an operating system designed for deterministic processing in embedded systems such as FreeRTOS, Zephyr, and ThreadX.
* `SNMP`: Simple Network Management Protocol, a standard protocol for monitoring and managing network devices, supported by a Jumpstarter driver.
* `SOME/IP`: Scalable service-Oriented MiddlewarE over IP, an automotive middleware protocol for service-oriented communication in vehicles, supported by a Jumpstarter driver.
* `SSH`: Secure Shell, a network protocol for secure remote access that Jumpstarter can tunnel through its driver streaming interface.
* `SWD`: Serial Wire Debug, a two-pin ARM debugging interface used as an alternative to JTAG for programming and debugging ARM processors.
* `TCP`: Transmission Control Protocol, the network transport protocol used for direct client-to-exporter connections in direct mode.
* `TFTP`: Trivial File Transfer Protocol, a lightweight file transfer protocol commonly used for network booting and firmware transfer in embedded systems.
* `TLS`: Transport Layer Security, the cryptographic protocol used to encrypt gRPC communication between Jumpstarter components.
* `TSDB`: Time Series Database, a database optimized for time-stamped data such as Prometheus metrics.
* `UART`: Universal Asynchronous Receiver-Transmitter, a serial communication hardware interface used for console access to embedded devices.
* `UDS`: Unified Diagnostic Services (ISO-14229), a standardized automotive diagnostic protocol for communicating with ECUs, supported by Jumpstarter drivers.
* `UF2`: USB Flashing Format, a file format designed for flashing microcontrollers over USB, used by the Pi Pico driver's BOOTSEL mass storage flashing.
* `VNC`: Virtual Network Computing, a graphical desktop-sharing protocol used by Jumpstarter adapters and drivers to provide remote visual access to devices.
* `XCP`: Universal Measurement and Calibration Protocol, a protocol for reading, writing, and calibrating internal parameters of ECUs, supported by a Jumpstarter driver.
* `YAML`: YAML Ain't Markup Language, the data serialization format used for all Jumpstarter configuration files.

## Entities

* `client`: A developer or a CI/CD pipeline that connects to the Jumpstarter
  service and leases exporters. The client can run tests on the leased resources.

* `controller`: The central service that authenticates and connects the
  exporters and clients, manages leases, and provides an inventory of available
  exporters and clients.

* `DUT Link Board`: An open-source hardware board designed for Jumpstarter that
  provides power control, serial, and storage multiplexing for a device under
  test.

* `exporter`: A Linux service that exports the interfaces to the DUTs. An
  exporter connects directly to a Jumpstarter server or directly to a client.

* `host`: A system running the exporter service, typically a low-cost test
  system such as a single board computer with sufficient interfaces to connect
  to hardware.

* `MCP server`: A Jumpstarter component (`jmp mcp serve`) that exposes hardware
  control as structured MCP tools accessible by AI coding agents and assistants.

* `operator`: A Kubernetes operator that installs and manages the Jumpstarter
  controller, router, and related infrastructure resources via a `Jumpstarter`
  custom resource.

* `router`: A service used by the controller to route messages between clients
  and exporters through a gRPC tunnel, enabling remote access to exported
  interfaces.

* `service`: The Kubernetes-based backend that provides the controller, router,
  and authentication components for managing clients, exporters, and leases in
  distributed mode.

## Concepts

* `@export decorator`: A Python decorator that marks a driver method to be
  exposed over gRPC as a remotely callable unary or server-streaming RPC.

* `@exportstream decorator`: A Python decorator that marks a driver method as
  opening a bidirectional byte stream over gRPC for real-time data exchange such
  as serial communication or video capture.

* `adapter`: A component that transforms connections exposed by drivers into
  different forms or interfaces. Adapters take a driver client as input and
  provide alternative ways to interact with the underlying connection, such as
  port forwarding, VNC access, or terminal emulation.

* `afterLease hook`: A lifecycle hook script that runs after the client session
  ends but before the lease is released, typically used for device cleanup.

* `beforeLease hook`: A lifecycle hook script that runs after a lease is assigned
  but before drivers are available to the client, typically used for device
  initialization.

* `client config`: A YAML configuration file that stores client connection
  settings including the service endpoint, authentication token, and allowed
  driver packages.

* `composite driver`: A driver that combines multiple lower-level drivers to
  create higher-level abstractions or specialized workflows, organized in a tree
  structure to represent complex device configurations.

* `custom driver`: A driver that defines its own interface rather than
  implementing a predefined Jumpstarter interface, built for specialized hardware
  or domain-specific abstractions.

* `device`: A device that is exposed on an exporter. The exporter enumerates
  these devices and makes them available for use in tests. Examples include
  network interfaces, serial ports, GPIO pins, storage devices, and CAN bus
  interfaces.

* `direct mode`: An operation mode where a client connects directly to an
  exporter over TCP without a controller or Kubernetes cluster, useful for
  single-user remote access to hardware on another machine.

* `distributed mode`: An operation mode that enables multiple teams to securely
  share hardware resources across a network using a Kubernetes-based controller
  to coordinate access to exporters and manage leases.

* `driver`: The term for both the `driver class` and the corresponding `driver
  client class`, not to be confused with `Driver`, the base class of all `driver
  classes`. Drivers in the main `jumpstarter` repository are called `in-tree
  drivers`, otherwise they are called `out-of-tree drivers`. Drivers
  implementing predefined interfaces are called `standard drivers`, otherwise
  they are called `custom drivers`.

* `driver allowlist`: A client configuration setting that restricts which driver
  Python packages can be dynamically loaded, preventing execution of untrusted
  code in distributed mode.

* `driver class`: A class that implements an interface and inherits from the
  `Driver` base class. It uses the `@export` decorator to expose methods that
  can be called remotely by clients.

* `driver client class`: The driver client class that is used directly by end
  users. It interacts with the `driver class` remotely via remote procedure call
  to invoke exported methods, which in turn interact with the exporter
  resources.

* `driver tree`: The hierarchical tree structure in which Jumpstarter organizes
  composite and child drivers to represent complex device configurations and
  their relationships.

* `edge termination`: An ingress or route TLS configuration where TLS is
  terminated at the edge (ingress/route) rather than being passed through to
  backend pods, used for Jumpstarter's HTTP login endpoints.

* `exemplar`: A Prometheus feature that attaches arbitrary key-value metadata to
  individual metric samples, used by Jumpstarter to carry high-cardinality
  context like lease_id without inflating series cardinality.

* `exporter config`: A YAML configuration file that defines which drivers an
  exporter loads, their parameters, and optional lifecycle hooks and connection
  settings.

* `exporter shell`: An interactive shell environment spawned by `jmp shell` that
  provides access to an exporter's driver CLI interfaces via the `j` command.

* `exporter status`: The current state of an exporter in its lifecycle. States
  include `AVAILABLE`, `BEFORE_LEASE_HOOK`, `LEASE_READY`,
  `AFTER_LEASE_HOOK`, `BEFORE_LEASE_HOOK_FAILED`, `AFTER_LEASE_HOOK_FAILED`,
  and `OFFLINE`.

* `hook`: A shell script configured on an exporter that runs automatically at
  lease boundaries. A `beforeLease` hook runs after a lease is assigned but
  before drivers are available to the client, and an `afterLease` hook runs
  after the session ends but before the lease is released.

* `in-tree driver`: A driver that is maintained within the main Jumpstarter
  repository and distributed as an official package.

* `interface class`: An abstract base class that defines the contract for driver
  implementations. It specifies the required methods that must be implemented by
  driver classes and provides the client class path through the `client()` class
  method.

* `label selector`: Key-value metadata attached to exporters that clients use to
  select specific devices for leasing, similar to Kubernetes label selectors.

* `lease`: A time-limited reservation of an exporter. A lease is created by a
  client and allows the client to use the exporter resources for a limited time.
  Leases ensure exclusive access to specific devices/exporters.

* `local mode`: An operation mode where clients communicate directly with
  exporters running on the same machine or through direct network connections,
  ideal for individual developers working directly with accessible hardware or
  virtual devices.

* `message`: Commands sent from driver clients to driver implementations,
  allowing the client to trigger actions or retrieve information from the
  device.

* `NAT traversal`: The technique of establishing connections across NAT
  boundaries, enabled by the Jumpstarter router so that exporters behind
  firewalls or private networks can be reached by clients.

* `onFailure`: A hook configuration field that controls the behavior when a hook
  script fails: `warn` (continue normally), `endLease` (terminate the lease), or
  `exit` (shut down the exporter).

* `out-of-tree driver`: A driver that is maintained outside the main Jumpstarter
  repository, typically developed by third parties for specialized hardware.

* `RPC styles`: The three gRPC communication patterns used by Jumpstarter
  drivers: unary (single request/response), server streaming (one request,
  multiple responses), and bidirectional streaming (full-duplex byte stream).

* `session`: A connection context created when a client connects to an exporter,
  during which driver instances are maintained and tests are executed.

* `standard driver`: A driver that implements one of Jumpstarter's predefined
  interface contracts, ensuring interoperability with standard client tooling.

* `stream`: A continuous data exchange channel established by drivers for
  communications like serial connections or video streaming, enabling real-time
  interaction with both physical and virtual interfaces across the network.

* `TLS passthrough`: An ingress or route configuration where encrypted TLS
  traffic is forwarded directly to backend pods for termination, rather than
  being decrypted at the ingress layer.

* `user config`: A YAML configuration file at `~/.config/jumpstarter/config.yaml`
  that defines global user settings including the currently selected client
  configuration.

## Tools and Commands

* `cert-manager`: A Kubernetes add-on that automates the management and issuance
  of TLS certificates, optionally used by the Jumpstarter operator for server
  certificate management.

* `Dex`: A federated OpenID Connect provider that can be deployed in Kubernetes
  to provide OIDC authentication for Jumpstarter.

* `env()`: A context manager from `jumpstarter.common.utils` that creates a
  client connected to the exporter configured in the shell environment.

* `j`: A shorthand CLI command available within the Jumpstarter exporter shell
  that provides access to driver CLI interfaces for the current session.

* `jmp`: The primary Jumpstarter CLI tool used for managing clients, exporters,
  leases, configuration, and shell sessions.

* `jmp admin`: A `jmp` subcommand group for administrative operations such as
  creating/managing clusters, clients, exporters, and installing the Jumpstarter
  service.

* `jmp login`: A `jmp` subcommand that authenticates a client or exporter
  against the controller using OIDC or token-based authentication.

* `jmp mcp serve`: A `jmp` subcommand that starts an MCP server exposing
  Jumpstarter hardware control as structured tools for AI agents.

* `jmp shell`: A `jmp` subcommand that spawns an interactive shell session
  connected to a local or remote exporter, providing access to driver interfaces
  via the `j` command.

* `JumpstarterTest`: A pytest base class provided by the `jumpstarter-testing`
  package that handles connection management, lease acquisition, and client
  fixture setup for hardware tests.

* `Keycloak`: An open-source identity and access management solution that can
  serve as an OIDC provider for Jumpstarter authentication.

* `kind`: Kubernetes in Docker, a tool for running local Kubernetes clusters
  using container "nodes", used for local Jumpstarter development and testing.

* `kubectl`: The Kubernetes command-line tool used to interact with Kubernetes
  clusters, required for installing and managing the Jumpstarter service.

* `minikube`: A tool that runs local Kubernetes clusters using VMs or containers,
  supported as an alternative to kind for local Jumpstarter installations.

* `oc`: The OpenShift CLI tool, an alternative to `kubectl` for managing
  OpenShift/OKD clusters where Jumpstarter can be deployed.

* `PexpectAdapter`: An adapter from `jumpstarter-driver-network` that wraps a
  serial console driver client into a pexpect `fdspawn` object for
  pattern-based console interaction in tests.

* `pytest`: A Python testing framework that Jumpstarter integrates with through
  the `jumpstarter-testing` package for writing hardware tests.

* `Renode`: An open-source embedded systems emulation framework by Antmicro for
  simulating microcontroller-class targets, integrated as a Jumpstarter driver.

* `uv`: A modern Python package and project manager used by the Jumpstarter
  project for dependency management and virtual environment creation.
