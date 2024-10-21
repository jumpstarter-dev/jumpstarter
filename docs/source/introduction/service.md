# Jumpstarter Service

When building a lab with many devices under test, it quickly becomes difficult
to keep track of devices, schedule access for automated tests, and perform routine
tasks such as batch updates.

Jumpstarter provides a [Cloud Native](https://www.cncf.io/) service that can
be installed in any [Kubernetes](https://kubernetes.io/) cluster to manage
connected clients and exporters.

If you're already using a Kubernetes-native CI tool such as
[Tekton](https://tekton.dev/), [Jenkins X](https://jenkins-x.io/),
or [GitLab CI](https://docs.gitlab.com/ee/user/clusters/agent/ci_cd_workflow.html),
Jumpstarter can integrate directly into your existing cloud or on-prem cluster.

```{mermaid}
block-beta

    client("Client")
    space
    block:service
    columns 1
        controller
        router
    end
    space
    block:exporters
    columns 1
        exporter["Exporter"]
        exporter2["Exporter"]
        exporter3["Exporter"]
    end
    space
    block:duts
    columns 1
        dut1["Target"]
        dut2["Target"]
        dut3["Target"]
    end
    exporter-->service
    exporter2-->service
    exporter3-->service
    exporter-->dut1
    exporter2-->dut2
    exporter3-->dut3
    client-->service
```

## Controller

The core of the Jumpstarter service is the `controller`, which manages access to
devices, authenticates clients/exporters, and maintains a set of labels to easily
identify specific devices.

The Controller is implemented as a Kubernetes [controller](https://github.com/jumpstarter-dev/jumpstarter-controller) 
using [Custom Resource Definitions (CRDs)](https://kubernetes.io/docs/concepts/extend-kubernetes/api-extension/custom-resources/)
to store information about clients, exporters, leases, etc.

### Leases

When a client requests access to an exporter and a matching instance is found, a 
`lease` is created. The lease ensures that each lesee (client) has exclusive 
access to a specific device/exporter.

Clients can be scheduled to access a specific exporter or any exporter that matches
a set of requested labels, similar to [node selection](https://kubernetes.io/docs/concepts/scheduling-eviction/assign-pod-node/#nodeselector)
in Kubernetes. This enable flexible CI-driven testing even when physical resources
are limited.

### Sessions

Within a lease, a `session` is be created to interact with the hardware.
The lifecycle of a session typically follows that of a test suite, with some
setup logic, a set of tests, and teardown logic to reset the hardware to a known
state.

Multiple sessions may be created within one lease, however only one session can
exist at one time.

## Router

The `router` service is used by the controller to route messages between
clients and exporters through a gRPC tunnel. This enables remote access to 
exported interfaces via a the client.

Once a lease is established, all traffic between the client and the exporter
flows through a router instance. While there may only be one controller,
the router can be scaled to handle traffic from many clients/exporters 
simultaneously.
