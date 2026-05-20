# Glossary

## Acronyms

```{glossary}
:sorted:

CRD
  Custom Resource Definition - Kubernetes extension for Jumpstarter resources.

DUT
  Device Under Test.

gRPC
  Google Remote Procedure Call - Jumpstarter's communication framework.

HiL
  Hardware-in-the-Loop - testing with real hardware in the loop.

MAN
  Manual - reference documentation for command-line tools.

JEP
  Jumpstarter Enhancement Proposal - design document for significant changes.

MCP
  Model Context Protocol - enables AI agents to interact with hardware.
```

## Entities

```{glossary}
:sorted:

client
  A user or CI pipeline that connects to the service and leases exporters.

controller
  Central service for authentication, lease management, and inventory.

exporter
  Service that exposes hardware interfaces to clients over gRPC.

host
  Machine running the exporter, typically a single board computer.

operator
  Kubernetes operator that deploys the controller, router, and CRDs.

router
  Routes traffic between clients and exporters through a gRPC tunnel.

service
  Kubernetes backend providing controller, router, and authentication.
```

## Concepts

```{glossary}
:sorted:

adapter
  Transforms driver connections into other forms (port forwarding, VNC, etc).

device
  Hardware or virtual resource exposed on an exporter.

direct mode
  Client connects to an exporter over TCP without a controller.

distributed mode
  Shared hardware access across teams via a Kubernetes controller.

driver
  Modular component providing a standardized interface to a device type.

exporter shell
  Interactive shell spawned by `jmp shell` for driver CLI access.

hook
  Shell script that runs automatically at lease boundaries.

label selector
  Key-value metadata for selecting exporters when leasing.

lease
  Time-limited reservation of an exporter with exclusive access.

local mode
  Client and exporter on the same machine, no Kubernetes required.

session
  Connection context between client and exporter during testing.
```

## Tools

```{glossary}
:sorted:

j
  Shorthand CLI for driver access within the exporter shell.

jmp
  Primary Jumpstarter CLI for managing clients, exporters, and leases.
```
