# Local cluster with kind

If you want to play with the Jumpstarter Controller on your local machine,
we recommend running a local Kubernetes cluster.

```{warning}
We do not recommend a local cluster for a production environment.
Please use a full Kubernetes installation either on-prem or in the cloud.
```

Kind is a tool for running local Kubernetes clusters using Podman or Docker container “nodes”.

```{tip}
We recommend using [minikube](./minikube-helm.md) if you cannot easily use Kind in your local environment
(e.g. need to use [untrusted root certificates](https://minikube.sigs.k8s.io/docs/handbook/untrusted_certs/)).
```


You can find more information on the [kind website](https://kind.sigs.k8s.io/docs/user/quick-start/).

## Installation

### Create a kind cluster

First, create a kind cluster config that enables the use of nodeports to host the Jumpstarter services.

```yaml
# kind_config.yaml
kind: Cluster
apiVersion: kind.x-k8s.io/v1alpha4
kubeadmConfigPatches:
- |
  kind: ClusterConfiguration
  apiServer:
    extraArgs:
      "service-node-port-range": "3000-32767"
- |
  kind: InitConfiguration
  nodeRegistration:
    kubeletExtraArgs:
      node-labels: "ingress-ready=true"
nodes:
- role: control-plane
  extraPortMappings:
  - containerPort: 80 # ingress controller
    hostPort: 5080
    protocol: TCP
  - containerPort: 30010 # grpc nodeport
    hostPort: 8082
    protocol: TCP
  - containerPort: 30011 # grpc router nodeport
    hostPort: 8083
    protocol: TCP
  - containerPort: 443 # minimalistic UI
    hostPort: 5443
    protocol: TCP
```

Next, create a kind cluster using the config you created.

```bash
kind create cluster  --config kind_config.yaml
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
$ jmp admin install

# Options provided by the Jumpstarter CLI
$ jmp admin install --help
```

```{program-output} jmp admin install --help
```

### Install Jumpstarter with Helm

If you prefer to manually install with Helm, the following command should work.

```{code-block} bash
:substitutions:
# Get the IP address of your computer
# On Linux you can run: ip route get 1.1.1.1 | grep -oP 'src \K\S+'
export IP="X.X.X.X"
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
            --set jumpstarter-controller.grpc.mode=nodeport \
            --version={{controller_version}}
```
