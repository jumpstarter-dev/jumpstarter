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

- **HTTP/2 Support**: gRPC requires HTTP/2, ensure your ingress controller or load balancer supports it
- **gRPC Protocol**: Some ingress controllers require specific annotations for gRPC traffic
- **Keep-Alive Settings**: Long-lived gRPC connections may need keep-alive configuration
- **Load Balancing**: Use consistent hashing or session affinity for gRPC connections

### TLS Termination Options

Choose one of these TLS termination approaches:

**Option 1: TLS Termination at Ingress/Route (Recommended)**
- Terminate TLS at the ingress controller or OpenShift route
- Simpler certificate management
- Better performance with fewer encryption hops

**Option 2: End-to-End TLS**
- TLS from client to Jumpstarter service
- Higher security but more complex certificate management
- Required for strict compliance environments

```{warning}
gRPC over HTTP/1.1 is not supported. Ensure your ingress controller supports HTTP/2 and is properly configured for gRPC traffic.
```

## Installation

To install Jumpstarter, see [Install with Operator](service-operator.md). That guide includes:

- Installing the operator from the release asset (`operator-installer.yaml`), OperatorHub and OLM.
- Creating a `Jumpstarter` custom resource for vanilla Kubernetes with Ingress
- Creating a `Jumpstarter` custom resource for OpenShift with Routes
- Notes on integrating external OAuth/OIDC and cert-manager setups

## GitOps and ArgoCD

Use the operator installer and manage your `Jumpstarter` custom resource declaratively in GitOps flows. See [Install with Operator](service-operator.md) for the manifests and endpoint patterns to use on Kubernetes (Ingress) and OpenShift (Route).