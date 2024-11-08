# Local cluster with minikube
```{warning}
This guide hasn't been tested yet, please report back any issues
```

If you want to play with the Jumpstarter Controller on your local machine,
we recommend running a local Kubernetes cluster on your development machine.

```{warning}
We do not recommend a local cluster for a production environment such as a lab.
Please use a full Kubernetes installation either on-prem or in the cloud.
```

miniKube is a tool for running local Kubernetes clusters using VMs or container nodes,
it works across several platforms and can be used with different hypervisors.

You can find more information on the [minikube website](https://minikube.sigs.k8s.io/docs/start/).

## Installation

### Start a minikube cluster
```bash
minikube start
```

### Get the minikube cluster IP
```bash
export IP=$(minikube ip)
```

### Install Jumpstarter
```{code-block} bash
:substitutions:
export BASEDOMAIN="jumpstarter.${IP}.nip.io"
export GRPC_ENDPOINT="grpc.${BASEDOMAIN}:8082"
export GRPC_ROUTER_ENDPOINT="router.${BASEDOMAIN}:8083"

helm upgrade jumpstarter --install oci://quay.io/jumpstarter-dev/helm/jumpstarter \
            --create-namespace --namespace jumpstarter-lab \
            --set global.baseDomain=${BASEDOMAIN} \
            --set jumpstarter-controller.grpc.endpoint=${GRPC_ENDPOINT} \
            --set jumpstarter-controller.grpc.routerEndpoint=${GRPC_ROUTER_ENDPOINT} \
            --set global.metrics.enabled=false \
            --set jumpstarter-controller.grpc.nodeport.enabled=true \
            --set jumpstarter-controller.grpc.mode=nodeport \
            --version={{controller_version}}
```
