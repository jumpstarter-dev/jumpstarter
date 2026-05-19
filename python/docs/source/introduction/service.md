# Service

When building a lab with many {term}`DUT`s, it quickly becomes difficult
to keep track of {term}`device`s, schedule access for automated tests, and perform
routine maintenance such as batch updates.

Jumpstarter provides a {term}`service` that can be installed in any
[Kubernetes](https://kubernetes.io/) cluster to manage connected clients and
{term}`exporter`s.

If you're already using a Kubernetes-native CI tool such as
[Tekton](https://tekton.dev/), [Jenkins X](https://jenkins-x.io/),
[Konflux](https://konflux-ci.dev), or [GitLab
CI](https://docs.gitlab.com/user/clusters/agent/ci_cd_workflow/), Jumpstarter
can integrate directly into your existing cloud or on-premises cluster.

## Controller

The core of the {term}`service` is the {term}`controller`, which manages access to {term}`device`s,
authenticates clients/{term}`exporter`s, and maintains a set of {term}`label selector`s to easily
identify specific {term}`device`s.

The {term}`Controller` is implemented as a Kubernetes
[controller](https://github.com/jumpstarter-dev/jumpstarter/tree/main/controller) using
[Custom Resource Definitions
(CRDs)](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/)
to store information about clients, {term}`exporter`s, {term}`lease`s, and other resources.

### Leases

When a client requests access to an {term}`exporter` and a matching instance is found, a
{term}`lease` is created. The {term}`lease` ensures that each lessee (client) has exclusive
access to a specific {term}`device`/{term}`exporter`.

Clients can be scheduled to access a specific {term}`exporter` or any {term}`exporter` that
matches a set of requested labels, similar to [node
selection](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector)
in Kubernetes. This enables flexible CI-driven testing even when physical
resources are limited.

## Router

The {term}`router` routes traffic between clients and {term}`exporter`s through a {term}`gRPC` tunnel.
This allows clients to reach {term}`exporter`s without public IP addresses or behind
NATs/firewalls. Clients on the same network can also connect directly to an
{term}`exporter`, bypassing the {term}`router`.

Once a {term}`lease` is established, all traffic flows through a {term}`router` instance. While
there may only be one {term}`controller`, the {term}`router` can be scaled with multiple
instances to handle many clients and {term}`exporter`s simultaneously.

All communication between clients and drivers uses {term}`gRPC` with three RPC styles
(unary, server streaming, and bidirectional streaming). See
[Driver Communication](drivers.md#communication) for details.