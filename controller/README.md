# Jumpstarter Controller

The Jumpstarter controller is the Kubernetes-native service component of
[Jumpstarter](https://jumpstarter.dev). It implements the server-side gRPC
services defined by the [Jumpstarter Protocol](../protocol/), running as a
Kubernetes operator that manages Custom Resources for clients, exporters, and
leases. The controller enables distributed hardware sharing by routing traffic
between clients and exporters, handling lease negotiation, and enforcing access
policies.

## Development

Run these from the repository root:

```sh
# build the images, create a kind cluster and deploy through the operator
make -C controller deploy
# or on k3s
make -C controller deploy CLUSTER_TYPE=k3s

# delete the cluster
make -C controller clean
# or on k3s (this uninstalls k3s)
make -C controller clean CLUSTER_TYPE=k3s
```

For production deployment, see the
[Service Installation](https://jumpstarter.dev/main/getting-started/installation/index.html)
documentation.
