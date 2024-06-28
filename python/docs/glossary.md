# Jumpstarter glossary

## Personas
* `System Administrator`: the persona responsible for the infrastructure and the Jumpstarter
  deployment.

* `Lab Administrator`: the persona responsible for the Jumpstarter based lab, has the ability
  to create and manage exporters and clients.

* `Developer`: can lease and test exported resources, run tests. Can also create CI scripts
  to run on automation pipelines. Developers should also be able to test locally without
  the need for a Jumpstarter service as soon as the interface hardware is available to
  the local machine.

## Entities

* `Exporter`: a linux enabled device or a service that exports the interfaces to the devices
  to be tested. An exporter connects to the Jumpstarter server and waits for commands from
  a client.

* `Client`: a Developer or a CI/CD pipeline that connects to the Jumpstarter server and
  leases resources from the exporters. The client can run tests on the leased resources.

* `Jumpstarter service`: the central service that authenticates and connects the exporters
  and clients, manages leases and provides an inventory of available exporters and clients.

## Concepts

* `Resource`: a device that is exposed on a exporter, the exporter enumerates those devices
  and makes them available for use in tests. Examples of resources can be:
    * network interface
    * serial port
    * GPIO pin
    * Storage device (USB Muxer, SD-Wire, etc.)
    * a CAN bus interface

* `Lease`: a time limited reservation of a exporter, a lease is created by a client and
  allows the client to use the exporter resources for a limited time.

* `DriverClass`: is the name of the driver class that is used to interact with the exporter
  resources.