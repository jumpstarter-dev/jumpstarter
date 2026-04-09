# Production Deployment

For production deployments, you can install Jumpstarter on Kubernetes or OpenShift clusters with proper ingress, monitoring, and security configurations.

## Prerequisites

Before installing in production, ensure you have:

- A production Kubernetes cluster available
- `kubectl` installed and configured to access your cluster
- Administrator access to your cluster (required for CRD installation)
- Domain name for service endpoints
- Ingress controller installed (for Kubernetes) or Routes configured (for OpenShift)
```{note}
`global.baseDomain` creates these service hostnames with `jumpstarter.example.com`:
- `grpc.jumpstarter.example.com`
- `router.jumpstarter.example.com` (for router endpoints)
```

## TLS and gRPC Configuration

Jumpstarter uses gRPC for communication, which has specific requirements for production deployments:

### gRPC Requirements

- **HTTP/2 Support**: gRPC requires HTTP/2; ensure the path from clients to the service supports it
- **Keep-Alive Settings**: The Jumpstarter service and client configure gRPC keep-alive by default; you usually do not need to tune these separately.

### TLS for gRPC

The [Jumpstarter operator](service-operator.md) installs gRPC with **TLS passthrough** at the ingress or route: encrypted traffic is forwarded to the controller and router pods, which terminate TLS. HTTP login endpoints use edge TLS termination instead.

```{note}
When using ingress-nginx, you must enable the [`--enable-ssl-passthrough`](https://kubernetes.github.io/ingress-nginx/user-guide/cli-arguments/) flag on the ingress controller, as SSL passthrough is disabled by default. See the [ingress-nginx TLS documentation](https://kubernetes.github.io/ingress-nginx/user-guide/tls/#ssl-passthrough) for more details.
```

## Installation

To install Jumpstarter, see [Install with Operator](service-operator.md). That guide includes:

- Installing the operator from the release asset (`operator-installer.yaml`), OperatorHub and OLM.
- Creating a `Jumpstarter` custom resource for vanilla Kubernetes with Ingress
- Creating a `Jumpstarter` custom resource for OpenShift with Routes
- Notes on integrating external OAuth/OIDC and cert-manager setups

## GitOps and ArgoCD

Use the operator installer and manage your `Jumpstarter` custom resource declaratively in GitOps flows. See [Install with Operator](service-operator.md) for the manifests and endpoint patterns to use on Kubernetes (Ingress) and OpenShift (Route).