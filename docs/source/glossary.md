# Glossary

## Acronyms

* `DUT`: Device Under Test

## Entities

* `exporter`: A Linux service that exports the interfaces to the DUTs. An
  exporter connects directly to a Jumpstarter server or directly to a client.

* `client`: A developer or a CI/CD pipeline that connects to the Jumpstarter
  server and leases exporters. The client can run tests on the leased resources.

* `controller`: The central service that authenticates and connects the
  exporters and clients, manages leases, and provides an inventory of available
  exporters and clients.

## Concepts

* `Device`: A device that is exposed on an exporter. The exporter enumerates
  these devices and makes them available for use in tests. Examples of resources
  include:
  * Network interface
  * Serial port
  * GPIO pin
  * Storage device (USB Muxer, SD-Wire, etc.)
  * CAN bus interface

* `Lease`: A time-limited reservation of an exporter. A lease is created by a
  client and allows the client to use the exporter resources for a limited time.

* `adapter`: A component that transforms connections exposed by drivers into different forms
  or interfaces. Adapters take a driver client as input and provide alternative ways to interact
  with the underlying connection, such as port forwarding, VNC access, or terminal emulation.

* `interface class`: An abstract base class that defines the contract for driver
  implementations. It specifies the required methods that must be implemented by
  driver classes and provides the client class path through the `client()` class
  method.

* `driver class`: A class that implements an interface and inherits from the
  `Driver` base class. It uses the `@export` decorator to expose methods that
  can be called remotely by clients.

* `driver client class`: The driver client class that is used
  directly by end users. It interacts with the `driver class` remotely via
  remote procedure call to invoke exported methods, which in turn interact with
  the exporter resources.

* `driver`: The term for both the `driver class` and the corresponding `driver client
  class`, not to be confused with `Driver`, the base class of all `driver
  classes`. Drivers in the main `jumpstarter` repository are called `in-tree drivers`,
  otherwise they are called `out-of-tree drivers`. Drivers implementing predefined
  interfaces are called `standard drivers`, otherwise they are called `custom drivers`.
