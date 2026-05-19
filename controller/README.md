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
make docker-push IMG=<some-registry>/jumpstarter-controller:tag
make install
make deploy IMG=<some-registry>/jumpstarter-controller:tag

make undeploy
make uninstall
```

For production deployment, see the
[Service Installation](https://jumpstarter.dev/main/getting-started/installation/index.html)
documentation.
