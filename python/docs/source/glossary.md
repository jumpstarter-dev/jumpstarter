# Glossary

## Acronyms

* `DUT`: Device Under Test
* `CRD`: Custom Resource Definition
* `CI/CD`: Continuous Integration/Continuous Deployment
* `gRPC`: Google Remote Procedure Call
* `JWT`: JSON Web Token
* `KVM`: Keyboard, Video, Mouse

## Entities

* `exporter`: A Linux service that exports the interfaces to the DUTs. An
  exporter connects directly to a Jumpstarter server or directly to a client.

* `client`: A developer or a CI/CD pipeline that connects to the Jumpstarter
  server and leases exporters. The client can run tests on the leased resources.

* `controller`: The central service that authenticates and connects the
  exporters and clients, manages leases, and provides an inventory of available
  exporters and clients.

* `router`: A service used by the controller to route messages between clients
  and exporters through a gRPC tunnel, enabling remote access to exported
  interfaces.

* `host`: A system running the exporter service, typically a low-cost test
  system such as a single board computer with sufficient interfaces to connect
  to hardware.

## Concepts

* `device`: A device that is exposed on an exporter. The exporter enumerates
  these devices and makes them available for use in tests. Examples of resources
  include:
  * Network interface
  * Serial port
  * GPIO pin
  * Storage device (USB Muxer, SD-Wire, etc.)
  * CAN bus interface

* `lease`: A time-limited reservation of an exporter. A lease is created by a
  client and allows the client to use the exporter resources for a limited time.
  Leases ensure exclusive access to specific devices/exporters.

* `adapter`: A component that transforms connections exposed by drivers into
  different forms or interfaces. Adapters take a driver client as input and
  provide alternative ways to interact with the underlying connection, such as
  port forwarding, VNC access, or terminal emulation.

* `interface class`: An abstract base class that defines the contract for driver
  implementations. It specifies the required methods that must be implemented by
  driver classes and provides the client class path through the `client()` class
  method.

* `driver class`: A class that implements an interface and inherits from the
  `Driver` base class. It uses the `@export` decorator to expose methods that
  can be called remotely by clients.

* `driver client class`: The driver client class that is used directly by end
  users. It interacts with the `driver class` remotely via remote procedure call
  to invoke exported methods, which in turn interact with the exporter
  resources.

* `driver`: The term for both the `driver class` and the corresponding `driver
  client class`, not to be confused with `Driver`, the base class of all `driver
  classes`. Drivers in the main `jumpstarter` repository are called `in-tree
  drivers`, otherwise they are called `out-of-tree drivers`. Drivers
  implementing predefined interfaces are called `standard drivers`, otherwise
  they are called `custom drivers`.

* `composite driver`: A driver that combines multiple lower-level drivers to
  create higher-level abstractions or specialized workflows, organized in a tree
  structure to represent complex device configurations.

* `local mode`: An operation mode where clients communicate directly with
  exporters running on the same machine or through direct network connections,
  ideal for individual developers working directly with accessible hardware or
  virtual devices.

* `distributed mode`: An operation mode that enables multiple teams to securely
  share hardware resources across a network using a Kubernetes-based controller
  to coordinate access to exporters and manage leases.

* `stream`: A continuous data exchange channel established by drivers for
  communications like serial connections or video streaming, enabling real-time
  interaction with both physical and virtual interfaces across the network.

* `message`: Commands sent from driver clients to driver implementations,
  allowing the client to trigger actions or retrieve information from the
  device.