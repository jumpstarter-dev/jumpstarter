# Service

When building a lab with many {term}`DUT`s, it quickly becomes difficult
to keep track of {term}`device`s, schedule access for automated tests, and perform
routine maintenance such as batch updates.

Jumpstarter provides a {term}`service` that can be installed in any
[Kubernetes](https://kubernetes.io/) cluster to manage connected {term}`client`s and
{term}`exporter`s.

If you're already using a Kubernetes-native CI tool such as
[Tekton](https://tekton.dev/), [Jenkins X](https://jenkins-x.io/),
[Konflux](https://konflux-ci.dev), or [GitLab
CI](https://docs.gitlab.com/user/clusters/agent/ci_cd_workflow/), Jumpstarter
can integrate directly into your existing cloud or on-premises cluster.

## Controller

The core of the service is the {term}`controller`, which manages access to devices,
authenticates clients/exporters, and maintains a set of {term}`label selector`s to easily
identify specific devices.

The Controller is implemented as a Kubernetes controller using
{term}`CRD`s to store information about clients, exporters,
{term}`lease`s, and other resources. See the
[CRDs reference](../reference/crds/index.md) for the full field definitions.

### Leases

When a client requests access to an exporter and a matching instance is found, a
lease is created. The lease ensures that each lessee (client) has exclusive
access to a specific device/exporter. The client holding a lease
can share it with other clients, which can then connect to the exporter but
cannot change, share, transfer or release the lease; see
[Lease Sharing](../getting-started/guides/examples/lease-sharing.md).

Clients can be scheduled to access a specific exporter or any exporter that
matches a set of requested {term}`label`s, similar to [node
selection](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector)
in Kubernetes. This enables flexible CI-driven testing even when physical
resources are limited.

An administrator can take an exporter out of rotation without deleting it by
setting `spec.enabled: false` on its Exporter resource. The controller then
assigns it no new leases, unless the lease request allows disabled
exporters, for example with `jmp shell --allow-disabled` or
`jmp create lease --allow-disabled`.

## Router

The {term}`router` routes traffic between clients and exporters through a {term}`gRPC` tunnel.
This allows clients to reach exporters without public IP addresses or behind
NATs/firewalls. Clients on the same network can also connect directly to an
exporter in {term}`direct mode`, bypassing the controller and the
router.

Once a lease is established, all traffic flows through a router instance. While
there may only be one controller, the router can be scaled with multiple
instances to handle many clients and exporters simultaneously.

All communication between clients and {term}`driver`s uses gRPC with three RPC styles
(unary, server streaming, and bidirectional streaming). See
[Driver Communication](drivers.md#communication) for details.

## Telemetry

The optional telemetry service collects logs and metrics. Exporters and
`jmp shell` {term}`session`s push their log entries to it. It requests metrics from each
connected exporter over a stream the exporter keeps open, and publishes
them on a Prometheus `/metrics` endpoint. Log entries go to Loki when a Loki
endpoint is configured, and are otherwise written to the service's standard
output as JSON.

The controller tells clients and exporters where to reach the telemetry
service, and they connect to it directly rather than through the router. See
[Telemetry endpoints](../getting-started/installation/service/production.md#telemetry-endpoints)
for how to enable and expose it.
