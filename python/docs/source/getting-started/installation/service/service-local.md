# Local Installation

For local development and testing, you can install Jumpstarter on local Kubernetes clusters using tools like kind or minikube. This is ideal for learning about the distributed service quickly or for creating CI/CD pipelines to validate your own Jumpstarter drivers.

## Prerequisites

Before installing locally, ensure you have:

- Docker or Podman installed (for kind)
- `kubectl` installed and configured to access your cluster
- [Helm](https://helm.sh/docs/intro/install/) (version 3.x or newer)
- Administrator access to your cluster (required for CRD installation)

## Install with Jumpstarter CLI

The Jumpstarter CLI provides convenient commands for local demo/test cluster management and Jumpstarter installation:

- `jmp admin create cluster` - Creates a local cluster and installs Jumpstarter (recommended for getting started quickly)
- `jmp admin delete cluster` - Deletes a local cluster completely
- `jmp admin get clusters` - Get local clusters from a Kubeconfig
- `jmp admin install` - Installs Jumpstarter on an existing cluster
- `jmp admin uninstall` - Removes Jumpstarter from a cluster (but keeps the cluster)

```{warning}
Sometimes the automatic IP address detection for will not work correctly, to check if Jumpstarter can determine your IP address, run `jmp admin ip`. If the IP address cannot be determined, use the `--ip` argument to manually set your IP address.
```

### Create a Local Cluster and Install Jumpstarter

If you want to test Jumpstarter locally with more control over the setup, you can create a local cluster using tools such as [minikube](https://minikube.sigs.k8s.io/docs/start/) and [kind](https://kind.sigs.k8s.io/docs/user/quick-start/).

[**kind**](https://kind.sigs.k8s.io/docs/user/quick-start/) (Kubernetes in Docker) is a tool for running local Kubernetes clusters using Docker or Podman containerized "nodes". It's lightweight and fast to start, making it excellent for CI/CD pipelines and quick local testing.

[**minikube**](https://minikube.sigs.k8s.io/docs/start/) runs local Kubernetes clusters using VMs or container "nodes". It works across several platforms and supports different hypervisors, making it ideal for local development and testing. Minikube works better if you don't have a local Docker/Podman installation.

The admin CLI can automatically create a local cluster and install Jumpstarter with a single command:

By default, Jumpstarter will try to detect which local cluster tools are installed:

```{tip}
By default, Jumpstarter will use `kind` if available, use the `--minikube` argument to force Jumpstarter to use minikube instead.
```

```{code-block} console
$ jmp admin create cluster
```

However, you can also explicitly specify a local cluster tool:

````{tab} kind
```{code-block} console
$ jmp admin create cluster --kind
```

Additional options for cluster creation:

- Custom cluster name: Specify as the first argument (default: `jumpstarter-lab`)
- `--kind <PATH>`: Path to the kind binary to use for cluster management
- `--helm <PATH>`: Path to the Helm binary to install the Jumpstarter service with
- `--force-recreate`: Force recreate the cluster if it already exists (destroys all data)
- `--kind-extra-args`: Pass additional arguments to kind cluster creation
- `--skip-install`: Create the cluster without installing Jumpstarter
- `--extra-certs <PATH>`: Path to custom CA certificate bundle file to inject into the cluster
````

````{tab} minikube
```{code-block} console
$ jmp admin create cluster --minikube
```

Additional options for cluster creation:

- Custom cluster name: Specify as the first argument (default: `jumpstarter-lab`)
- `--minikube <PATH>`: Path to the minikube binary to use for cluster management
- `--helm <PATH>`: Path to the Helm binary to install the Jumpstarter service with
- `--force-recreate`: Force recreate the cluster if it already exists (destroys all data)
- `--minikube-extra-args`: Pass additional arguments to minikube cluster creation
- `--skip-install`: Create the cluster without installing Jumpstarter
- `--extra-certs <PATH>`: Path to custom CA certificate bundle file to inject into the cluster
````

To set a custom cluster name:

````{tab} kind
```{code-block} console
$ jmp admin create cluster my-jumpstarter-cluster --kind
```
````

````{tab} minikube
```{code-block} console
$ jmp admin create cluster my-jumpstarter-cluster --minikube
```
````

### Install Jumpstarter in an Existing Local Cluster

```{warning}
Jumpstarter requires specific `NodePort` configurations, it is recommended to create a new cluster for Jumpstarter or use the automatic creation above.
```

If you already have a local cluster, install Jumpstarter with default options for your local cluster tool:

````{tab} kind
```{code-block} console
$ jmp admin install --kind
```
````

````{tab} minikube
```{code-block} console
$ jmp admin install --minikube
```
````

### Uninstall Jumpstarter

Uninstall Jumpstarter from the cluster with the CLI:

```{code-block} console
$ jmp admin uninstall
```

To delete the local cluster completely, use the cluster delete command:

````{tab} kind
```{code-block} console
$ jmp admin delete cluster --kind
```
````

````{tab} minikube
```{code-block} console
$ jmp admin delete cluster --minikube
```
````

To delete a cluster with a custom name:

````{tab} kind
```{code-block} console
$ jmp admin delete cluster my-jumpstarter-cluster --kind
```
````

````{tab} minikube
```{code-block} console
$ jmp admin delete cluster my-jumpstarter-cluster --minikube
```
````

For complete documentation of the `jmp admin create cluster`, `jmp admin delete cluster`, `jmp admin get clusters`, and `jmp admin install` commands and all available options, see the [MAN pages](../../../reference/man-pages/jmp.md).

## Manual Local Cluster Install

If you want to customize the local cluster further, you can create the cluster yourself.

### Create a Local Cluster

````{tab} kind
#### Create a kind cluster

First, create a kind cluster config that enables nodeports to host the Services.
Save this as `kind_config.yaml`:

```{code-block} yaml
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
  - containerPort: 80
    hostPort: 5080
    protocol: TCP
  - containerPort: 30010
    hostPort: 8082
    protocol: TCP
  - containerPort: 30011
    hostPort: 8083
    protocol: TCP
  - containerPort: 443
    hostPort: 5443
    protocol: TCP
```

Next, create a kind cluster using the config you created:

```{code-block} console
$ kind create cluster --config kind_config.yaml
```
````

````{tab} minikube
#### Create a minikube cluster

Expand the default NodePort range to include the Jumpstarter ports:

```{code-block} console
$ minikube start --extra-config=apiserver.service-node-port-range=8000-9000
```
````

### Install Local Jumpstarter with Helm

For manual installation with Helm, use these commands:

````{tab} kind
```{code-block} console
:substitutions:
$ export IP="X.X.X.X" # Enter the IP address of your computer on the local network
$ export BASEDOMAIN="jumpstarter.${IP}.nip.io"
$ export GRPC_ENDPOINT="grpc.${BASEDOMAIN}:8082"
$ export GRPC_ROUTER_ENDPOINT="router.${BASEDOMAIN}:8083"
$ helm upgrade jumpstarter --install oci://quay.io/jumpstarter-dev/helm/jumpstarter \
    --create-namespace --namespace jumpstarter-lab \
    --set global.baseDomain=${BASEDOMAIN} \
    --set jumpstarter-controller.grpc.endpoint=${GRPC_ENDPOINT} \
    --set jumpstarter-controller.grpc.routerEndpoint=${GRPC_ROUTER_ENDPOINT} \
    --set global.metrics.enabled=false \
    --set jumpstarter-controller.grpc.nodeport.enabled=true \
    --set jumpstarter-controller.grpc.mode=nodeport \
    --version={{controller_version}}
```
````

````{tab} minikube
```{code-block} console
:substitutions:
$ export IP=$(minikube ip)
$ export BASEDOMAIN="jumpstarter.${IP}.nip.io"
$ export GRPC_ENDPOINT="grpc.${BASEDOMAIN}:8082"
$ export GRPC_ROUTER_ENDPOINT="router.${BASEDOMAIN}:8083"
$ helm upgrade jumpstarter --install oci://quay.io/jumpstarter-dev/helm/jumpstarter \
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
````

To check the status of the installation, run:

```{code-block} console
$ kubectl get pods -n jumpstarter-lab --watch
NAME                                    READY   STATUS      RESTARTS   AGE
jumpstarter-controller-cc74d879-6b22b   1/1     Running     0          48s
jumpstarter-secrets-w42z4               0/1     Completed   0          48s
```

To uninstall the Helm release, run:

```{code-block} console
$ helm uninstall jumpstarter --namespace jumpstarter-lab
```