# Local cluster with minikube

If you want to play with the Jumpstarter Controller on your local machine,
we recommend running a local Kubernetes cluster.

```{warning}
We do not recommend a local cluster for a production environment.
Please use a full Kubernetes installation either on-prem or in the cloud.
```

minikube is a tool for running local Kubernetes clusters using local VMs or Podman/Docker container “nodes”,
it works across several platforms and can be used with different hypervisors.

You can find more information on the [minikube website](https://minikube.sigs.k8s.io/docs/start/).

## Installation

### Start a minikube cluster

First, we must start a local minikube cluster with the correct features enabled to support Jumpstarter.

```bash
# We must expand the default NodePort range to include the Jumpstarter ports
minikube start --extra-config=apiserver.service-node-port-range=8000-9000
```

### Install Jumpstarter with the CLI

To simplify the installation in your Kubernetes cluster, the Jumpstarter CLI
provides the `jmp admin install` command to automatically run Helm with the
correct arguments.

```{tip}
If you do not have Helm installed, please [install the latest release](https://helm.sh/docs/intro/install/).
```

```
# Install Jumpstarter with default options
$ jmp admin install --ip $(minikube ip)

# Options provided by the Jumpstarter CLI
$ jmp admin install --help
Usage: jmp admin install [OPTIONS]

  Install the Jumpstarter service in a Kubernetes cluster

Options:
  --helm TEXT                 Path or name of a helm executable
  --name TEXT                 The name of the chart installation
  -c, --chart TEXT            The URL of a Jumpstarter helm chart to install
  -n, --namespace TEXT        Namespace to install Jumpstarter components in
  -i, --ip TEXT               IP address of your host machine
  -b, --basedomain TEXT       Base domain of the Jumpstarter service
  -g, --grpc-endpoint TEXT    The gRPC endpoint to use for the Jumpstarter API
  -r, --router-endpoint TEXT  The gRPC endpoint to use for the router
  --nodeport                  Use Nodeport routing (recommended)
  --ingress                   Use a Kubernetes ingress
  --route                     Use an OpenShift route
  -v, --version TEXT          The version of the service to install
  --kubeconfig FILENAME       path to the kubeconfig file
  --context TEXT              Kubernetes context to use
  --help                      Show this message and exit.
```

### Install Jumpstarter with Helm

```{tip}
If you do not have Helm installed, please [install the latest release](https://helm.sh/docs/intro/install/).
```

```{code-block} bash
:substitutions:
# Get the minikube cluster IP address
export IP=$(minikube ip)
# Setup the base domain and endpoints with nip.io
export BASEDOMAIN="jumpstarter.${IP}.nip.io"
export GRPC_ENDPOINT="grpc.${BASEDOMAIN}:8082"
export GRPC_ROUTER_ENDPOINT="router.${BASEDOMAIN}:8083"

# Install the Jumpstarter service in the namespace jumpstarter-lab with Helm
helm upgrade jumpstarter --install oci://quay.io/jumpstarter-dev/helm/jumpstarter \
            --create-namespace --namespace jumpstarter-lab \
            --set global.baseDomain=${BASEDOMAIN} \
            --set jumpstarter-controller.grpc.endpoint=${GRPC_ENDPOINT} \
            --set jumpstarter-controller.grpc.routerEndpoint=${GRPC_ROUTER_ENDPOINT} \
            --set global.metrics.enabled=false \
            --set jumpstarter-controller.grpc.nodeport.enabled=true \
            --set jumpstarter-controller.grpc.nodeport.port=8082 \
            --set jumpstarter-controller.grpc.nodeport.routerPort=8083 \
            --set jumpstarter-controller.grpc.mode=nodeport \
            --version={{controller_version}}
```
