# Operator

For production deployments, install Jumpstarter on Kubernetes or OpenShift
clusters using the Jumpstarter {term}`operator`.

## Prerequisites

- A Kubernetes or OpenShift cluster
- `kubectl` (or `oc`) configured for your cluster
- Cluster-admin permissions (required to install CRDs and {term}`operator` RBAC)
- A DNS domain for Jumpstarter {term}`service` endpoints (for example,
  `jumpstarter.example.com`)
- An ingress controller on Kubernetes, or Routes on OpenShift

```{note}
`spec.baseDomain` creates these {term}`service` hostnames with
`jumpstarter.example.com`:
- `grpc.jumpstarter.example.com`
- `router.jumpstarter.example.com`
- `login.jumpstarter.example.com`
```

## Install

### Install the Operator

Apply the {term}`operator` installer from a release asset:

```{code-block} console
$ kubectl apply -f https://github.com/jumpstarter-dev/jumpstarter/releases/download/v0.8.1/operator-installer.yaml
$ kubectl wait --namespace jumpstarter-operator-system \
    --for=condition=available deployment/jumpstarter-operator-controller-manager \
    --timeout=120s
```

Alternatively, install via OLM or OperatorHub:

```{tab} Kubernetes
Install from [OperatorHub](https://operatorhub.io/operator/jumpstarter-operator).
Requires OLM to be installed in your cluster.
```

```{tab} OpenShift
1. Go to **Operators -> OperatorHub** in the web console.
2. Search for **Jumpstarter Operator** and install it.
3. Verify: `oc get csv -n openshift-operators | grep jumpstarter`

Or via CLI subscription:

    apiVersion: operators.coreos.com/v1alpha1
    kind: Subscription
    metadata:
      name: jumpstarter-operator
      namespace: openshift-operators
    spec:
      channel: alpha
      name: jumpstarter-operator
      source: community-operators
      sourceNamespace: openshift-marketplace
      installPlanApproval: Automatic
```

### Create a Namespace

```{code-block} console
$ kubectl create namespace jumpstarter-lab
```

### Create a Jumpstarter Custom Resource

The {term}`operator` reconciles the `Jumpstarter` CR and creates Deployments,
Services, and networking resources for {term}`controller`/{term}`router`/login
endpoints.

```{tab} Kubernetes
```{code-block} yaml
apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: jumpstarter
  namespace: jumpstarter-lab
spec:
  baseDomain: jumpstarter.example.com
  certManager:
    enabled: true
  controller:
    image: quay.io/jumpstarter-dev/jumpstarter-controller:0.8.1-rc.2
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
        - address: grpc.jumpstarter.example.com:443
          ingress:
            enabled: true
            class: nginx
    login:
      endpoints:
        - address: login.jumpstarter.example.com:443
          ingress:
            enabled: true
            class: nginx
  routers:
    image: quay.io/jumpstarter-dev/jumpstarter-controller:0.8.1-rc.2
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
        - address: router.jumpstarter.example.com:443
          ingress:
            enabled: true
            class: nginx
```
```

```{tab} OpenShift
```{code-block} yaml
apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: jumpstarter
  namespace: jumpstarter-lab
spec:
  baseDomain: jumpstarter.example.com
  certManager:
    enabled: true
  controller:
    image: quay.io/jumpstarter-dev/jumpstarter-controller:0.8.1-rc.2
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
        - address: grpc.jumpstarter.example.com:443
          route:
            enabled: true
    login:
      endpoints:
        - address: login.jumpstarter.example.com:443
          route:
            enabled: true
  routers:
    image: quay.io/jumpstarter-dev/jumpstarter-controller:0.8.1-rc.2
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
        - address: router.jumpstarter.example.com:443
          route:
            enabled: true
```
```

```{code-block} console
$ kubectl apply -f jumpstarter.yaml
```

## Verify

```{tab} Kubernetes
```{code-block} console
$ kubectl get jumpstarter -n jumpstarter-lab
$ kubectl get deploy,svc,ingress -n jumpstarter-lab
```
```

```{tab} OpenShift
```{code-block} console
$ kubectl get jumpstarter -n jumpstarter-lab
$ kubectl get deploy,svc,route -n jumpstarter-lab
```

Ensure DNS is configured so route hostnames resolve correctly.
```

## Configuration

### TLS and gRPC

Jumpstarter uses {term}`gRPC` for communication, which requires HTTP/2 support.
The {term}`operator` configures TLS passthrough at the ingress or route for
{term}`gRPC` endpoints and edge TLS termination for login endpoints.

```{note}
When using ingress-nginx, enable
[`--enable-ssl-passthrough`](https://kubernetes.github.io/ingress-nginx/user-guide/cli-arguments/)
on the ingress controller.
```

### OAuth and OIDC

Configure through `spec.authentication.jwt` in the `Jumpstarter` CR. The
{term}`operator` applies this to {term}`controller` runtime settings but does
not install your identity provider. See
[Authentication](../../configuration/authentication.md) for examples.

### cert-manager

Set `spec.certManager.enabled: true` for {term}`operator`-managed certificates.

```{tab} Self-signed
```{code-block} yaml
spec:
  certManager:
    enabled: true
    server:
      selfSigned:
        enabled: true
```

Creates: `<name>-selfsigned-issuer`, `<name>-ca`, `<name>-ca-issuer`,
`<name>-controller-tls`, `<name>-router-<replica>-tls`.
```

```{tab} External issuer
```{code-block} yaml
spec:
  certManager:
    enabled: true
    server:
      issuerRef:
        name: my-cluster-issuer
        kind: ClusterIssuer
```
```

```{tab} ACME
```{code-block} yaml
spec:
  controller:
    login:
      endpoints:
        - address: login.jumpstarter.example.com:443
          ingress:
            enabled: true
            class: nginx
            annotations:
              cert-manager.io/cluster-issuer: letsencrypt-prod
```
```

### GitOps

Use the {term}`operator` installer and manage your `Jumpstarter` CR
declaratively in GitOps flows.

### Operator Behavior

- If `spec.baseDomain` is empty on OpenShift, the {term}`operator` auto-detects
  the cluster domain.
- If no endpoint service type is enabled, the {term}`operator` auto-selects:
  route, then ingress, then clusterIP.
- {term}`Controller` and {term}`router` auth secrets persist across CR
  deletion/recreation.
- {term}`Router` replicas are one Deployment per replica; `$(replica)`
  placeholders are substituted per replica.

For the full `Jumpstarter` CRD field reference, see the
[CRDs](../../../reference/crds/index.md).
