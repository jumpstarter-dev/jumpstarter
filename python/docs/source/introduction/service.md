# Service

When building a lab with many {term}`DUT`s (devices under test), it quickly becomes difficult
to keep track of devices, schedule access for automated tests, and perform
routine maintenance such as batch updates.

Jumpstarter provides a service that can be installed in any
[Kubernetes](https://kubernetes.io/) cluster to manage connected clients and
exporters.

If you're already using a Kubernetes-native CI tool such as
[Tekton](https://tekton.dev/), [Jenkins X](https://jenkins-x.io/),
[Konflux](https://konflux-ci.dev), or [GitLab
CI](https://docs.gitlab.com/user/clusters/agent/ci_cd_workflow/), Jumpstarter
can integrate directly into your existing cloud or on-premises cluster.

## Controller

The core of the Service is the {term}`controller`, which manages access to devices,
authenticates clients/exporters, and maintains a set of {term}`label selector`s to easily
identify specific devices.

The Controller is implemented as a Kubernetes
[controller](https://github.com/jumpstarter-dev/jumpstarter/tree/main/controller) using
[Custom Resource Definitions
(CRDs)](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/)
to store information about clients, exporters, leases, and other resources.

### Leases

When a client requests access to an exporter and a matching instance is found, a
{term}`lease` is created. The lease ensures that each lessee (client) has exclusive
access to a specific device/exporter.

Clients can be scheduled to access a specific exporter or any exporter that
matches a set of requested labels, similar to [node
selection](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector)
in Kubernetes. This enables flexible CI-driven testing even when physical
resources are limited.

## Router

The {term}`router` routes traffic between clients and exporters through a {term}`gRPC` tunnel.
This allows clients to reach exporters without public IP addresses or behind
NATs/firewalls. Clients on the same network can also connect directly to an
exporter, bypassing the Router.

Once a lease is established, all traffic flows through a router instance. While
there may only be one controller, the router can be scaled with multiple
instances to handle many clients and exporters simultaneously.

All communication between clients and drivers uses gRPC with three {term}`RPC styles`
(unary, server streaming, and bidirectional streaming). See
[Driver Communication](drivers.md#communication) for details.