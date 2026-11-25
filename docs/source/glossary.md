# Glossary

## Acronyms

```{glossary}
:sorted:

CR
  Custom Resource - an object of a kind that a CRD defines, such as an Exporter or a Lease.

CRD
  Custom Resource Definition - Kubernetes extension for Jumpstarter resources.

DUT
  Device Under Test.

gRPC
  gRPC Remote Procedure Calls - Jumpstarter's communication framework.

HiL
  Hardware-in-the-Loop - testing with real hardware in the loop.

MAN
  Manual - reference documentation for command-line tools.

JEP
  Jumpstarter Enhancement Proposal - design document for significant changes.

MCP
  Model Context Protocol - enables AI agents to interact with hardware.

OIDC
  OpenID Connect - the identity layer on OAuth 2.0 that Jumpstarter uses to sign in users and service accounts.
```

## Entities

```{glossary}
:sorted:

access policy
ExporterAccessPolicy
  Kubernetes CR that decides which clients may lease the exporters it selects, with what priority and for how long.

client
  A user, script or CI pipeline that uses exporters, through the controller in distributed mode or directly in local and direct mode.

controller
  Central service for authentication, lease management, and inventory.

exporter
  Service that exposes hardware interfaces to clients over gRPC.

ExporterSet
  Kubernetes CR that manages a pool of exporter replicas with auto-scaling.

host
  Machine running the exporter, typically a single board computer.

operator
  Kubernetes operator that deploys the controller, router, optional telemetry service, and CRDs.

provisioner
  Backend named in a VirtualTargetClass that creates and deletes the virtual targets of an ExporterSet, such as `qemu.jumpstarter.dev`.

router
  Routes traffic between clients and exporters through a gRPC tunnel.

service
  Kubernetes backend providing controller, router, and authentication.

VirtualTargetClass
  CR that defines provisioner backend and shared parameters for virtual targets.
```

## Concepts

```{glossary}
:sorted:

adapter
  Transforms driver connections into other forms (port forwarding, VNC, etc).

client config
  YAML file with the controller endpoint, credentials and driver settings a client uses, managed with `jmp config client`.

device
  Hardware or virtual resource exposed on an exporter.

direct mode
  Client connects to an exporter over TCP without a controller.

distributed mode
  Shared hardware access across teams via the Jumpstarter controller.

driver
  Modular component providing a standardized interface to a device type.

exporter config
  YAML file that defines an exporter: its drivers, its hooks and, in distributed mode, the controller it registers with. Managed with `jmp config exporter`.

exporter shell
  Interactive shell spawned by `jmp shell` for driver CLI access.

hook
  Script that runs automatically on the exporter at lease boundaries.

label
  Key-value pair such as `board=rpi4` on an exporter or client, matched by label selectors and access policies.

label selector
  Expression that matches labels when leasing an exporter, for example `board=rpi4,arch!=x86_64`.

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
