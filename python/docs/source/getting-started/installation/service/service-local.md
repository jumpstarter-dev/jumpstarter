# Local Installation

For local development and testing, you can install Jumpstarter on local Kubernetes clusters using tools like kind or minikube. This is ideal for getting started quickly or for CI/CD pipelines.

## Prerequisites

Before installing locally, ensure you have:

- Docker or Podman installed (for kind)
- `kubectl` installed and configured to access your cluster
- [Helm](https://helm.sh/docs/intro/install/) (version 3.x or newer)
- Administrator access to your cluster (required for CRD installation)

## Install with Jumpstarter CLI

The Jumpstarter CLI provides the `jmp admin install` command to automatically
run Helm with the correct arguments, simplifying installation in your Kubernetes
cluster. This is the recommended approach for getting started quickly.

```{warning}
Sometimes the automatic IP address detection for will not work correctly, to check if Jumpstarter can determine your IP address, run `jmp admin ip`. If the IP address cannot be determined, use the `--ip` argument to manually set your IP address.
```

### Create a Local Cluster and Install Jumpstarter

If you want to test Jumpstarter locally with more control over the setup, you can create a local cluster using tools such as [minikube](https://minikube.sigs.k8s.io/docs/start/) and [kind](https://kind.sigs.k8s.io/docs/user/quick-start/).

[**kind**](https://kind.sigs.k8s.io/docs/user/quick-start/) (Kubernetes in Docker) is a tool for running local Kubernetes clusters using Docker or Podman containerized "nodes". It's lightweight and fast to start, making it excellent for CI/CD pipelines and quick local testing.

[**minikube**](https://minikube.sigs.k8s.io/docs/start/) runs local Kubernetes clusters using VMs or container "nodes". It works across several platforms and supports different hypervisors, making it ideal for local development and testing. It's particularly useful in environments requiring untrusted certificates.

```{tip}
Consider minikube for environments requiring [untrusted certificates](https://minikube.sigs.k8s.io/docs/handbook/untrusted_certs/).
```

The admin CLI can automatically create a local cluster and install Jumpstarter with a single command:

````{tab} kind
```{code-block} console
$ jmp admin install --kind --create-cluster
```
````

````{tab} minikube
```{code-block} console
$ jmp admin install --minikube --create-cluster
```
````

Additional options for cluster creation:

- `--cluster-name`: Specify a custom cluster name (default: `jumpstarter-lab`)
- `--force-recreate-cluster`: Force recreate the cluster if it already exists (destroys all data)
- `--kind-extra-args`: Pass additional arguments to kind cluster creation
- `--minikube-extra-args`: Pass additional arguments to minikube cluster creation
- `--kind-extra-args`: Pass additional arguments to kind cluster creation

To set a custom cluster name:

````{tab} kind
```{code-block} console
$ jmp admin install --kind --create-cluster --cluster-name my-jumpstarter-cluster
```
````

````{tab} minikube
```{code-block} console
$ jmp admin install --minikube --create-cluster --cluster-name my-jumpstarter-cluster
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

Uninstall Jumpstarter with the CLI:

```{code-block} console
$ jmp admin uninstall
```

To delete the local cluster when uninstalling, use the `--delete-cluster` flag:

````{tab} kind
```{code-block} console
$ jmp admin uninstall --kind --delete-cluster
```
````

````{tab} minikube
```{code-block} console
$ jmp admin uninstall --minikube --delete-cluster
```
````

For complete documentation of the `jmp admin install` command and all available
options, see the [MAN pages](../../reference/man-pages/jmp.md).

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