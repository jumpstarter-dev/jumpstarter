# CLI

For local development and testing, install Jumpstarter on local Kubernetes
clusters using kind or minikube. Ideal for learning about the {term}`service`
quickly or for validating Jumpstarter drivers in CI/CD pipelines.

## Prerequisites

- Docker or Podman installed
- `kubectl` installed and configured
- Administrator access to your cluster (required for CRD installation)

## Install

The {term}`jmp admin` CLI can create a local cluster and install Jumpstarter in
a single command:

```{code-block} console
$ jmp admin create cluster
```

```{warning}
If automatic IP detection fails, check with `jmp admin ip` and use `--ip` to
set your address manually.
```

```{tip}
By default, Jumpstarter uses kind if available. Use `--minikube` to force
minikube instead.
```

````{tab} kind
```{code-block} console
$ jmp admin create cluster --kind
```

Options: `--force-recreate`, `--skip-install`, `--extra-certs <PATH>`,
`--kind-extra-args`, custom cluster name as first argument.
````

````{tab} minikube
```{code-block} console
$ jmp admin create cluster --minikube
```

Options: `--force-recreate`, `--skip-install`, `--extra-certs <PATH>`,
`--minikube-extra-args`, custom cluster name as first argument.
````

### Install on an Existing Cluster

```{warning}
Jumpstarter requires specific NodePort configurations. It is recommended to
create a new cluster or use the automatic creation above.
```

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

## Verify

```{code-block} console
$ kubectl get pods -n jumpstarter-lab --watch
```

## Configuration

### Manual Cluster Setup

For more control, create the cluster yourself before installing:

````{tab} kind
Create a kind cluster config that enables NodePorts. Save as `kind_config.yaml`:

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

```{code-block} console
$ kind create cluster --config kind_config.yaml
```
````

````{tab} minikube
```{code-block} console
$ minikube start --extra-config=apiserver.service-node-port-range=8000-9000
```
````

Then follow the [Operator](service-production.md) guide using a `baseDomain`
appropriate for your local environment (for example, `nip.io` based hostnames).

## Uninstall

```{code-block} console
$ jmp admin uninstall
```

To delete the local cluster completely:

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

For complete documentation of all {term}`jmp admin` options, see the
[MAN pages](../../../reference/man-pages/jmp.md).
