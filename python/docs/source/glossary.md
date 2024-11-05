# Glossary

```{warning}
This project is still evolving, so these docs may be incomplete or out-of-date.
```

## Acronyms

* `DUT`: Device Under Test

## Entities

* `exporter`: A linux service that exports the interfaces to the DUTs.
  An exporter connects directly to a Jumpstarter server or directly to a client.

* `client`: A Developer or a CI/CD pipeline that connects to the Jumpstarter server
  and leases exporters. The client can run tests on the leased
  resources.

* `controller`: The central service that authenticates and connects the exporters
  and clients, manages leases and provides an inventory of available exporters and
  clients.

## Concepts

* `Device`: a device that is exposed on a exporter, the exporter enumerates those
  devices and makes them available for use in tests. Examples of resources can be:
  * network interface
  * serial port
  * GPIO pin
  * Storage device (USB Muxer, SD-Wire, etc.)
  * a CAN bus interface

* `Lease`: a time limited reservation of a exporter, a lease is created by a client
  and allows the client to use the exporter resources for a limited time.

* `driver class`: is the name of the driver class that is used to interact with
  the exporter resources.

* `driver client class`: is the name of the driver client class that is used directly
  by end users, it interacts with `DriverClass` remotely via remote procedure call to
  invoke `DriverClass` provided methods, which in turn interacts with the exporter resources.

* `driver`: is the name for `driver class` and the corresponding `driver client class`, not to
  be confused with `Driver`, the base class of all `driver class`. Drivers in the main `jumpstarter`
  repo are called `in-tree drivers`, otherwise called `out-of-tree drivers`. Drivers implementing
  predefined interfaces are called `standard drivers`, otherwise called `custom drivers`.
