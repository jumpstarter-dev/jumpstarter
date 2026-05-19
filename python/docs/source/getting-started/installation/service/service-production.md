# Operator

For production deployments, install Jumpstarter on Kubernetes or OpenShift
clusters using the Jumpstarter {term}`operator`.

## Prerequisites

- A Kubernetes, OpenShift, or OKD cluster
- `kubectl` (or `oc`) configured for your cluster
- Cluster-admin permissions (required to install CRDs and {term}`operator` RBAC)
- A DNS domain for Jumpstarter {term}`service` endpoints (for example,
  `jumpstarter.example.com`)
- An ingress controller on Kubernetes, or Routes on OpenShift/OKD

```{note}
`spec.baseDomain` creates these {term}`service` hostnames with
`jumpstarter.example.com`:
- `grpc.jumpstarter.example.com`
- `router.jumpstarter.example.com`
- `login.jumpstarter.example.com`
```

## Install

### Install the Operator

````{tab} Kubernetes (OLM installed)
Install the {term}`operator` from OperatorHub:

- [Jumpstarter Operator on OperatorHub](https://operatorhub.io/operator/jumpstarter-operator)

```{note}
This assumes OLM is already installed and configured in your cluster.
```
````

````{tab} OpenShift / OKD (OperatorHub)
1. Log in to the OpenShift/OKD web console with cluster-admin permissions.
2. Go to **Operators -> OperatorHub**.
3. Search for **Jumpstarter Operator** and install it.
4. Wait until the installed {term}`operator` status is `Succeeded`.

```{code-block} console
$ oc get csv -n openshift-operators | grep jumpstarter
```
````

````{tab} OpenShift / OKD (CLI subscription)
```yaml
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

```{code-block} console
$ oc apply -f subscription.yaml
$ oc get csv -n openshift-operators | grep jumpstarter
```
````

````{tab} Manual installer YAML (any cluster)
```{code-block} console
$ kubectl apply -f https://github.com/jumpstarter-dev/jumpstarter/releases/download/v0.8.1/operator-installer.yaml
$ kubectl wait --namespace jumpstarter-operator-system \
    --for=condition=available deployment/jumpstarter-operator-controller-manager \
    --timeout=120s
```
````

### Create a Namespace

```{code-block} console
$ kubectl create namespace jumpstarter-lab
```

### Create a Jumpstarter Custom Resource

The {term}`operator` reconciles the `Jumpstarter` CR and creates Deployments,
Services, and networking resources for {term}`controller`/{term}`router`/login
endpoints.

````{tab} Kubernetes (Ingress)
```yaml
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
````

````{tab} OpenShift / OKD (Route)
```yaml
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
````

```{code-block} console
$ kubectl apply -f jumpstarter.yaml
```

## Verify

```{code-block} console
$ kubectl get jumpstarter -n jumpstarter-lab
$ kubectl get deploy,svc,ingress -n jumpstarter-lab   # Kubernetes
$ kubectl get deploy,svc,route -n jumpstarter-lab     # OpenShift/OKD
```

```{note}
For OpenShift/OKD, ensure DNS is configured so route hostnames resolve correctly.
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

````{tab} Self-signed
```yaml
spec:
  certManager:
    enabled: true
    server:
      selfSigned:
        enabled: true
```

Creates: `<name>-selfsigned-issuer`, `<name>-ca`, `<name>-ca-issuer`,
`<name>-controller-tls`, `<name>-router-<replica>-tls`.
````

````{tab} External issuer
```yaml
spec:
  certManager:
    enabled: true
    server:
      issuerRef:
        name: my-cluster-issuer
        kind: ClusterIssuer
```
````

````{tab} Login with ACME
```yaml
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
````

### GitOps

Use the {term}`operator` installer and manage your `Jumpstarter` CR
declaratively in GitOps flows.

### Operator Behavior Notes

- If `spec.baseDomain` is empty on OpenShift, the {term}`operator` auto-detects
  the cluster domain.
- If no endpoint service type is enabled, the {term}`operator` auto-selects:
  route, then ingress, then clusterIP.
- {term}`Controller` and {term}`router` auth secrets persist across CR
  deletion/recreation.
- {term}`Router` replicas are one Deployment per replica; `$(replica)`
  placeholders are substituted per replica.

## API Reference

The `Jumpstarter` CRD is `operator.jumpstarter.dev/v1alpha1`.

### Top-level Spec

| Field | Type | Description |
| --- | --- | --- |
| `spec.baseDomain` | `string` | Base DNS domain for generated endpoint hostnames. |
| `spec.certManager` | `object` | Certificate management settings. |
| `spec.controller` | `object` | {term}`Controller` deployment, endpoint, and runtime settings. |
| `spec.routers` | `object` | {term}`Router` deployment scale, resources, and endpoint settings. |
| `spec.authentication` | `object` | Authentication settings (internal, Kubernetes, JWT, auto-provisioning). |

### Controller and Router

| Field | Type | Description |
| --- | --- | --- |
| `spec.controller.image` | `string` | Controller container image. |
| `spec.controller.imagePullPolicy` | `string` | Pull policy (`Always`, `IfNotPresent`, `Never`). |
| `spec.controller.resources` | `object` | Controller resource requests/limits. |
| `spec.controller.replicas` | `integer` | Number of controller pods. |
| `spec.controller.exporterOptions.offlineTimeout` | `duration` | Timeout before {term}`exporter` is considered offline. |
| `spec.controller.grpc.tls.certSecret` | `string` | Manual TLS secret name when cert-manager is disabled. |
| `spec.controller.grpc.endpoints[]` | `array` | Controller {term}`gRPC` endpoint definitions. |
| `spec.controller.grpc.keepalive.*` | `object` | {term}`gRPC` keepalive tuning options. |
| `spec.controller.login.tls.secretName` | `string` | Optional TLS secret for login edge-termination. |
| `spec.controller.login.endpoints[]` | `array` | Login endpoint definitions. |
| `spec.routers.image` | `string` | Router container image. |
| `spec.routers.imagePullPolicy` | `string` | Pull policy. |
| `spec.routers.resources` | `object` | Router resource requests/limits. |
| `spec.routers.replicas` | `integer` | Router replica count (one deployment per replica). |
| `spec.routers.topologySpreadConstraints[]` | `array` | Pod spread constraints for {term}`router` deployments. |
| `spec.routers.grpc.tls.certSecret` | `string` | Manual TLS secret name when cert-manager is disabled. |
| `spec.routers.grpc.endpoints[]` | `array` | Router endpoint definitions; supports `$(replica)` placeholder. |
| `spec.routers.grpc.keepalive.*` | `object` | Router {term}`gRPC` keepalive tuning options. |

### Authentication

| Field | Type | Description |
| --- | --- | --- |
| `spec.authentication.internal.enabled` | `boolean` | Enables internal token-based auth. |
| `spec.authentication.internal.prefix` | `string` | Username/subject prefix for internal auth. |
| `spec.authentication.internal.tokenLifetime` | `duration` | Internal token validity period. |
| `spec.authentication.k8s.enabled` | `boolean` | Enables Kubernetes service account token auth. |
| `spec.authentication.jwt[]` | `array` | JWT authenticators (issuer, audiences, claim mappings). |
| `spec.authentication.autoProvisioning.enabled` | `boolean` | Auto-create users authenticated by external providers. |

### cert-manager

| Field | Type | Description |
| --- | --- | --- |
| `spec.certManager.enabled` | `boolean` | Enables {term}`operator` cert-manager integration. |
| `spec.certManager.server.selfSigned.enabled` | `boolean` | Enables self-signed CA mode. |
| `spec.certManager.server.selfSigned.caDuration` | `duration` | Self-signed CA certificate duration. |
| `spec.certManager.server.selfSigned.certDuration` | `duration` | Issued server certificate duration. |
| `spec.certManager.server.selfSigned.renewBefore` | `duration` | Renewal lead time before expiration. |
| `spec.certManager.server.issuerRef.name` | `string` | Existing Issuer/ClusterIssuer name. |
| `spec.certManager.server.issuerRef.kind` | `string` | `Issuer` or `ClusterIssuer`. |
| `spec.certManager.server.issuerRef.group` | `string` | Issuer API group (default `cert-manager.io`). |
| `spec.certManager.server.issuerRef.caBundle` | `bytes` | Optional PEM CA bundle published for clients. |

### Endpoints

| Field | Type | Description |
| --- | --- | --- |
| `address` | `string` | Host/address, optional port, supports `$(replica)` for {term}`router` endpoints. |
| `route.enabled` | `boolean` | Create OpenShift Route. |
| `route.annotations` / `route.labels` | `map` | Route metadata overrides. |
| `ingress.enabled` | `boolean` | Create Kubernetes Ingress. |
| `ingress.class` | `string` | Ingress class name. |
| `ingress.annotations` / `ingress.labels` | `map` | Ingress metadata overrides. |
| `nodeport.enabled` | `boolean` | Create NodePort service. |
| `nodeport.port` | `integer` | Requested NodePort value. |
| `loadBalancer.enabled` | `boolean` | Create LoadBalancer service. |
| `loadBalancer.port` | `integer` | Service port. |
| `clusterIP.enabled` | `boolean` | Create ClusterIP service. |

### Status Conditions

| Condition | Meaning |
| --- | --- |
| `Ready` | Overall deployment readiness. |
| `ControllerDeploymentReady` | Controller deployment is available. |
| `RouterDeploymentsReady` | All {term}`router` deployments are available. |
| `CertManagerAvailable` | cert-manager CRDs are present (when enabled). |
| `IssuerReady` | Configured issuer is ready (when enabled). |
| `ControllerCertificateReady` | Controller TLS secret is ready (when enabled). |
| `RouterCertificatesReady` | Router TLS secrets are ready for all replicas (when enabled). |
