# Jumpstarter Operator

The Jumpstarter operator manages the lifecycle of Jumpstarter controller
components on Kubernetes using the Operator Lifecycle Manager (OLM). It
packages the controller, router, and associated resources into a single
installable unit.

## Development

Run these from the repository root:

```sh
make -C controller/deploy/operator docker-build docker-push IMG=<some-registry>/jumpstarter-operator:tag
make -C controller/deploy/operator install
make -C controller/deploy/operator deploy IMG=<some-registry>/jumpstarter-operator:tag

make -C controller/deploy/operator undeploy
make -C controller/deploy/operator uninstall
```

For production deployment, see the
[Service Installation](https://jumpstarter.dev/main/getting-started/installation/index.html)
documentation.
