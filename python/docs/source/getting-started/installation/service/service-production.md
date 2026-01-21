# Production Deployment

For production deployments, you can install Jumpstarter on Kubernetes or OpenShift clusters with proper ingress, monitoring, and security configurations.

## Prerequisites

Before installing in production, ensure you have:

- A production Kubernetes cluster available
- `kubectl` installed and configured to access your cluster
- [Helm](https://helm.sh/docs/intro/install/) (version 3.x or newer)
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

## Install with Helm

Install Jumpstarter on a Kubernetes/OpenShift cluster using Helm:

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

**OpenShift Route TLS Configuration:**

OpenShift automatically creates secure routes with TLS termination. For custom certificates:

```yaml
apiVersion: route.openshift.io/v1
kind: Route
metadata:
  name: jumpstarter-grpc
  annotations:
    haproxy.router.openshift.io/balance: source
    haproxy.router.openshift.io/timeout: 300s
spec:
  host: grpc.jumpstarter.example.com
  tls:
    termination: edge
    certificate: |
      -----BEGIN CERTIFICATE-----
      # Your certificate here
      -----END CERTIFICATE-----
    key: |
      -----BEGIN PRIVATE KEY-----
      # Your private key here
      -----END PRIVATE KEY-----
  to:
    kind: Service
    name: jumpstarter-controller-grpc
    weight: 100
```
````

## Install with ArgoCD

You can use ArgoCD to install Jumpstarter in your production cluster. Below are examples for different platforms:

````{tab} Kubernetes
### Install with ArgoCD on Kubernetes (Amazon EKS)

First, create a namespace for Jumpstarter:

```console
$ kubectl create namespace jumpstarter-lab
```

If your ArgoCD installation requires namespace labeling for management, add the appropriate label:

```console
$ kubectl label namespace jumpstarter-lab argocd.argoproj.io/managed-by=argocd
```

For ArgoCD to manage Jumpstarter CRDs, create this `ClusterRole` and `ClusterRoleBinding`:

```yaml
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRole
metadata:
  name: argocd-application-controller-crd
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
  name: argocd-application-controller-crd
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: argocd-application-controller-crd
subjects:
- kind: ServiceAccount
  name: argocd-application-controller
  namespace: argocd  # Replace with your ArgoCD namespace
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
  namespace: argocd  # Replace with your ArgoCD namespace
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
        value: jumpstarter.example.com
      - name: global.metrics.enabled
        value: "true"
      - name: jumpstarter-controller.controllerSecret
        value: "pick-a-secret-DONT-USE-THIS-DEFAULT"
      - name: jumpstarter-controller.routerSecret
        value: "again-pick-a-secret-DONT-USE-THIS-DEFAULT"
      - name: jumpstarter-controller.grpc.mode
        value: "ingress"
    repoURL: quay.io/jumpstarter-dev/helm
    targetRevision: "{{controller_version}}"
  syncPolicy:
    automated:
      prune: true
      selfHeal: true
    syncOptions:
    - CreateNamespace=true
```
````

````{tab} OpenShift
### Install with ArgoCD on OpenShift

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
        value: jumpstarter.example.com
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
````