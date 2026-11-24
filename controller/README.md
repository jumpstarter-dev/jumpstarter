# Jumpstarter Controller

The Jumpstarter controller is the Kubernetes-native service component of
[Jumpstarter](https://jumpstarter.dev). It implements the server-side gRPC
services defined by the [Jumpstarter Protocol](../protocol/), running as a
Kubernetes operator that manages Custom Resources for clients, exporters, and
leases. The controller enables distributed hardware sharing by routing traffic
between clients and exporters, handling lease negotiation, and enforcing access
policies.

## Development

```sh
# build the images, create a kind cluster and deploy through the operator
make deploy
# or on k3s
make deploy CLUSTER_TYPE=k3s

# delete the cluster
make clean
```

For production deployment, see the
[Service Installation](https://jumpstarter.dev/main/getting-started/installation/index.html)
documentation.
