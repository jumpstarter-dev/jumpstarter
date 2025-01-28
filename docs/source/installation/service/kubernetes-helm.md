# Kubernetes

The Jumpstarter service can be installed on a Kubernetes cluster using Helm.

## Install with Helm

```{note}
Please note that `global.baseDomain` is used to create the host names for the services,
with the provided example the services will be available at grpc.jumpstarter.example.com
```

```{code-block} bash
:substitutions:
helm upgrade jumpstarter --install oci://quay.io/jumpstarter-dev/helm/jumpstarter \
        --create-namespace --namespace jumpstarter-lab \
        --set global.baseDomain=jumpstarter.example.com \
        --set global.metrics.enabled=true `# disable if metrics not available` \
        --set jumpstarter-controller.grpc.mode=ingress \
        --version={{controller_version}}
```
