# Service

When building a lab with many devices under test, it quickly becomes difficult
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

The core of the Service is the Controller, which manages access to devices,
authenticates clients/exporters, and maintains a set of labels to easily
identify specific devices.

The Controller is implemented as a Kubernetes
[controller](https://github.com/jumpstarter-dev/jumpstarter/tree/main/controller) using
[Custom Resource Definitions
(CRDs)](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/)
to store information about clients, exporters, leases, and other resources.

### Leases

When a client requests access to an exporter and a matching instance is found, a
Lease is created. The lease ensures that each lessee (client) has exclusive
access to a specific device/exporter.

Clients can be scheduled to access a specific exporter or any exporter that
matches a set of requested labels, similar to [node
selection](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector)
in Kubernetes. This enables flexible CI-driven testing even when physical
resources are limited.

## Router

The Router service is used by the controller to route messages between clients
and exporters through a gRPC tunnel. This enables remote access to exported
interfaces via the client.

Once a lease is established, all traffic between the client and the exporter
flows through a router instance. While there may only be one controller, the
router can be scaled with multiple instances to handle traffic between many
clients and exporters simultaneously.

The Router works like ngrok or Cloudflare Tunnel -- it allows clients to connect
to exporters without public IP addresses or behind NATs/firewalls by tunneling
byte streams over bidirectional gRPC. Clients can also connect directly to an
exporter when both are on the same network.

```{mermaid}
:config: {"theme":"base","themeVariables":{"primaryColor":"#f8f8f8","primaryTextColor":"#000","primaryBorderColor":"#e5e5e5","lineColor":"#3d94ff","secondaryColor":"#f8f8f8","tertiaryColor":"#fff"}}
flowchart LR
    Client["Client\n(CLI / Python API)"]
    Router["Router"]
    subgraph "Exporter Host"
        Exporter["Exporter"]
        Drivers["Drivers"]
        Exporter --- Drivers
    end

    Client <-- "gRPC Tunnel\n(bidirectional)" --> Router
    Router <-- "gRPC Tunnel\n(bidirectional)" --> Exporter
    Client -. "Direct path\n(same network)" .-> Exporter
```