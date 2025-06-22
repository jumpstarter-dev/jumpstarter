# Service

This section explains how to install and configure the Jumpstarter service in
your Kubernetes cluster. The service enables centralized management of your
Jumpstarter environment. Before installing, ensure you have:

- A Kubernetes cluster available
- `kubectl` installed and configured to access your cluster
- [Helm](https://helm.sh/docs/intro/install/) (version 3.x or newer)
- Administrator access to your cluster (required for CRD installation)
- Domain name for service endpoints (or use `nip.io` for local testing)

```{note}
`global.baseDomain` creates these service hostnames with `jumpstarter.example.com`:
- `grpc.jumpstarter.example.com`
- `router.jumpstarter.example.com` (for router endpoints)
```

## Kubernetes with Helm

Install Jumpstarter on a standard Kubernetes cluster or OpenShift using Helm:

````{tab} Kubernetes
```{code-block} console
:substitutions:
$ helm upgrade jumpstarter --install oci://quay.io/jumpstarter-dev/helm/jumpstarter \
        --create-namespace --namespace jumpstarter-lab \
        --set global.baseDomain=jumpstarter.example.com \
        --set global.metrics.enabled=true \
        --set jumpstarter-controller.grpc.mode=ingress \
        --version={{controller_version}}
```
````

````{tab} OpenShift
```{code-block} console
:substitutions:
$ helm upgrade jumpstarter --install oci://quay.io/jumpstarter-dev/helm/jumpstarter \
        --create-namespace --namespace jumpstarter-lab \
        --set global.baseDomain=jumpstarter.example.com \
        --set global.metrics.enabled=true \
        --set jumpstarter-controller.grpc.mode=route \
        --version={{controller_version}}
```
````

## Install with OpenShift and ArgoCD

You can also use ArgoCD to install Jumpstarter in your OpenShift cluster:

First, create and label a namespace for Jumpstarter:

```console
$ kubectl create namespace jumpstarter-lab
$ kubectl label namespace jumpstarter-lab argocd.argoproj.io/managed-by=openshift-gitops
```

For ArgoCD to manage Jumpstarter CRDs, create this `ClusterRole` and `ClusterRoleBinding`:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  annotations:
    argocds.argoproj.io/name: openshift-gitops
    argocds.argoproj.io/namespace: openshift-gitops
  name: openshift-gitops-argocd-appcontroller-crd
rules:
- apiGroups:
  - 'apiextensions.k8s.io'
  resources:
  - 'customresourcedefinitions'
  verbs:
  - '*'
---
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  annotations:
    argocds.argoproj.io/name: openshift-gitops
    argocds.argoproj.io/namespace: openshift-gitops
  name: openshift-gitops-argocd-appcontroller-crd
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: openshift-gitops-argocd-appcontroller-crd
subjects:
- kind: ServiceAccount
  name: openshift-gitops-argocd-application-controller
  namespace: openshift-gitops
```

Create an ArgoCD Application to deploy Jumpstarter:

```{warning}
The secrets `jumpstarter-controller.controllerSecret` and `jumpstarter-controller.routerSecret`
must be unique for each installation. While Helm can auto-generate these, ArgoCD cannot -
you must manually create these in your Jumpstarter namespace.
```

```{code-block} yaml
:substitutions:
apiVersion: argoproj.io/v1alpha1
kind: Application
metadata:
  name: jumpstarter
  namespace: openshift-gitops
spec:
  destination:
    name: in-cluster
    namespace: jumpstarter-lab
  project: default
  source:
    chart: jumpstarter
    helm:
      parameters:
      - name: global.baseDomain
        value: devel.jumpstarter.dev
      - name: global.metrics.enabled
        value: "true"
      - name: jumpstarter-controller.controllerSecret
        value: "pick-a-secret-DONT-USE-THIS-DEFAULT"
      - name: jumpstarter-controller.routerSecret
        value: "again-pick-a-secret-DONT-USE-THIS-DEFAULT"
      - name: jumpstarter-controller.grpc.mode
        value: "route"
    repoURL: quay.io/jumpstarter-dev/helm
    targetRevision: "{{controller_version}}"
```

## Local Cluster

If you want to test Jumpstarter locally, you can create a local cluster using tools such as [minikube](https://minikube.sigs.k8s.io/docs/start/) and [kind](https://kind.sigs.k8s.io/docs/user/quick-start/).

```{tip}
The quickest way to get started is using the [Jumpstarter admin CLI](#install-jumpstarter-with-the-cli) with the `--create-cluster` flag, which automatically creates and configures your local cluster. For manual cluster setup, continue reading below.
```

````{tab} kind
Kind is a tool for running local Kubernetes clusters using Podman or Docker
containerized "nodes".

```{tip}
Consider minikube for environments requiring [untrusted certificates](https://minikube.sigs.k8s.io/docs/handbook/untrusted_certs/).
```

Find more information on the [kind
website](https://kind.sigs.k8s.io/docs/user/quick-start/).

### Create a kind cluster

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
Minikube runs local Kubernetes clusters using VMs or container "nodes". It works
across several platforms and supports different hypervisors, making it ideal for
local development and testing.

Find more information on the [minikube
website](https://minikube.sigs.k8s.io/docs/start/).

### Create a minikube cluster

Expand the default NodePort range to include the Jumpstarter ports:

```{code-block} console
$ minikube start --extra-config=apiserver.service-node-port-range=8000-9000
```
````

### Install Jumpstarter with the CLI

The Jumpstarter CLI provides the `jmp admin install` command to automatically
run Helm with the correct arguments, simplifying installation in your Kubernetes
cluster.

```{warning}
Sometimes the automatic IP address detection for will not work correctly, to check if Jumpstarter can determine your IP address, run `jmp admin ip`. If the IP address cannot be determined, use the `--ip` argument to manually set your IP address.
```

#### Create cluster and install Jumpstarter in one command

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

Example with custom cluster name:

```{code-block} console
$ jmp admin install --kind --create-cluster --cluster-name my-jumpstarter-cluster
```

#### Install Jumpstarter on existing cluster

If you already have a cluster running, install Jumpstarter with default options:

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

#### Uninstall Jumpstarter

Uninstall Jumpstarter with the CLI:

```{code-block} console
$ jmp admin uninstall
```

To also delete the local cluster when uninstalling, use the `--delete-cluster` flag:

````{tab} kind
```{code-block} console
$ jmp admin uninstall --delete-cluster --kind
```
````

````{tab} minikube
```{code-block} console
$ jmp admin uninstall --delete-cluster --minikube
```
````

To check the status of the installation, run:

```{code-block} console
$ kubectl get pods -n jumpstarter-lab --watch
NAME                                    READY   STATUS      RESTARTS   AGE
jumpstarter-controller-cc74d879-6b22b   1/1     Running     0          48s
jumpstarter-secrets-w42z4               0/1     Completed   0          48s
```

For complete documentation of the `jmp admin install` command and all available
options, see the [MAN pages](../../reference/man-pages/jmp.md).

### Install Jumpstarter with Helm

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
