# Local cluster with kind

If you want to play with the Jumpstarter Controller on your local machine,
we recommend running a local Kubernetes cluster on your development machine.

```{warning}
We do not recommend a local cluster for a production environment such as a lab.
Please use a full Kubernetes installation either on-prem or in the cloud.
```

Kind is a tool for running local Kubernetes clusters using Podman or Docker container “nodes”.
You can find more information on the [kind website](https://kind.sigs.k8s.io/docs/user/quick-start/).

## Installation

Begin by figuring out the LAN ip address that it's accessible for your docker/podman host, and do:

```bash
export IP="LAN accessible address to your docker/podman instance"
```

### Create a kind cluster

```bash
cat <<EOF > kind_config.yaml
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
EOF

kind create cluster  --config kind_config.yaml
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
            --version={{version}}
```
