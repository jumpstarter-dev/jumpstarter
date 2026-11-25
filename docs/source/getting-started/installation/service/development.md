# Development

Install Jumpstarter on local Kubernetes clusters using kind or minikube. Ideal
for learning about the {term}`service` quickly or for validating Jumpstarter
{term}`driver`s in CI/CD pipelines. For production deployments, see
[Production](production.md).

## Prerequisites

- Docker or Podman installed
- kind or minikube installed
- `kubectl` installed and configured
- Administrator access to your cluster (required for {term}`CRD` installation)

## Install

The `jmp admin` CLI can create a local cluster and install Jumpstarter in
a single command:

```{code-block} console
$ jmp admin create cluster
```

```{warning}
If automatic IP detection fails, use `--ip` to set your address manually.
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

`jmp admin create cluster` reuses a kind or minikube cluster that already exists
under the given name and installs Jumpstarter into it:

````{tab} kind
```{code-block} console
$ jmp admin create cluster <cluster-name> --kind
```
````

````{tab} minikube
```{code-block} console
$ jmp admin create cluster <cluster-name> --minikube
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
$ minikube start --extra-config=apiserver.service-node-port-range=30000-32767
```
````

Then follow the [Production](production.md) guide using a `baseDomain`
appropriate for your local environment (for example, `nip.io` based hostnames).

## Uninstall

To remove Jumpstarter but keep the cluster, delete the `Jumpstarter` resource
that `jmp admin create cluster` created. The {term}`operator` and cert-manager stay
installed:

```{code-block} console
$ kubectl delete jumpstarter jumpstarter -n jumpstarter-lab
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

For complete documentation of all `jmp admin` options, see the
[MAN pages](../../../reference/man-pages/jmp.md).
