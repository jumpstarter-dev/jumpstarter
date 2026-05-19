# Jumpstarter Operator

The Jumpstarter operator manages the lifecycle of Jumpstarter controller
components on Kubernetes using the Operator Lifecycle Manager (OLM). It
packages the controller, router, and associated resources into a single
installable unit.

## Development

```sh
make docker-build docker-push IMG=<some-registry>/jumpstarter-operator:tag
make install
make deploy IMG=<some-registry>/jumpstarter-operator:tag

make undeploy
make uninstall
```

For production deployment, see the
[Service Installation](https://jumpstarter.dev/main/getting-started/installation/index.html)
documentation.
