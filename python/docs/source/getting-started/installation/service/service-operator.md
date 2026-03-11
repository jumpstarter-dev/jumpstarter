# Install with Operator

This guide covers installing Jumpstarter with the Kubernetes operator, using:

- **Ingress** on vanilla Kubernetes
- **Route** on OpenShift

It mirrors how `make deploy METHOD=operator` deploys the operator and creates a `Jumpstarter` custom resource (CR), but uses production-friendly manifests and release artifacts.

## Prerequisites

- A Kubernetes, OpenShift, or OKD cluster
- `kubectl` (or `oc`) configured for your cluster
- Cluster-admin permissions (required to install CRDs and operator RBAC)
- A DNS domain for Jumpstarter service endpoints (for example, `jumpstarter.example.com`)
- An ingress controller on Kubernetes, or Routes on OpenShift/OKD

```{note}
This page focuses on operator installation and core CR configuration. It does not cover full setup of external components such as Dex/other OIDC providers or cert-manager installation.
```

## Install the operator

````{tab} Kubernetes (OLM installed)
If your Kubernetes cluster already has OLM, install the operator from OperatorHub and then continue with the `Jumpstarter` custom resource in this guide.

OperatorHub package page:

- [Jumpstarter Operator on OperatorHub](https://operatorhub.io/operator/jumpstarter-operator)

```{note}
On vanilla Kubernetes, this OperatorHub path assumes OLM is already installed and configured in your cluster.
```
````

````{tab} OpenShift / OKD (OperatorHub recommended)
1. Log in to the OpenShift/OKD web console with cluster-admin permissions.
2. Go to **Operators -> OperatorHub**.
3. Search for **Jumpstarter Operator** and install it.
4. Wait until the installed operator status is `Succeeded`.

Verify from CLI:

```{code-block} console
$ oc get csv -n openshift-operators | grep jumpstarter
```
````

````{tab} OpenShift / OKD (CLI OLM subscription)
Create a `Subscription` (example: `subscription.yaml`):

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

Apply and verify:

```{code-block} console
$ oc apply -f subscription.yaml
$ oc get csv -n openshift-operators | grep jumpstarter
```
````

````{tab} Manual installer YAML (any cluster)
Apply the operator installer from a release asset:

```{code-block} console
$ kubectl apply -f https://github.com/jumpstarter-dev/jumpstarter/releases/download/<release-tag>/operator-installer.yaml
```

For example:

```{code-block} console
$ kubectl apply -f https://github.com/jumpstarter-dev/jumpstarter/releases/download/v0.8.1/operator-installer.yaml
```

Wait for the operator deployment:

```{code-block} console
$ kubectl wait --namespace jumpstarter-operator-system \
    --for=condition=available deployment/jumpstarter-operator-controller-manager \
    --timeout=120s
```
````

## Create a namespace for Jumpstarter

```{code-block} console
$ kubectl create namespace jumpstarter-lab
```

## Create a `Jumpstarter` custom resource

The operator reconciles the `Jumpstarter` CR and creates Deployments, Services, and networking resources (Ingresses or Routes) for controller/router/login endpoints.

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

Save as `jumpstarter.yaml`, then apply:

```{code-block} console
$ kubectl apply -f jumpstarter.yaml
```

## Verify deployment

Check CR status and workloads:

```{code-block} console
$ kubectl get jumpstarter -n jumpstarter-lab
$ kubectl get deploy,svc,ingress,route -n jumpstarter-lab
```

```{note}
`route` is only available on OpenShift/OKD. On vanilla Kubernetes, use `ingress`.
```

```{note}
For OpenShift/OKD, set `spec.baseDomain` to a domain that resolves to your route hosts (for example, `jumpstarter.example.com`). Ensure DNS is configured so these route hostnames resolve correctly.
```

## OAuth and cert-manager integration notes

- **OAuth / OIDC integration**: Configure this through `spec.authentication.jwt` in the `Jumpstarter` CR (issuer URL, audiences, and claim mappings). The operator applies this configuration to controller runtime settings, but does not install or configure your identity provider.
- **cert-manager integration**: Set `spec.certManager.enabled: true` to let the operator manage server certificates. You can use operator-managed self-signed certificates or reference an existing `Issuer`/`ClusterIssuer` with `spec.certManager.server.issuerRef`. Installing and configuring cert-manager itself remains an external prerequisite.

For detailed authentication examples and field-level reference, see [Authentication](../../configuration/authentication.md).

## cert-manager configuration examples

These examples are aligned with scenarios covered by the operator e2e tests in `controller/deploy/operator/test/e2e/e2e_test.go`.

### Self-signed cert-manager mode

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
    server:
      selfSigned:
        enabled: true
  controller:
    grpc:
      endpoints:
        - address: grpc.jumpstarter.example.com:443
          ingress:
            enabled: true
            class: nginx
  routers:
    grpc:
      endpoints:
        - address: router.jumpstarter.example.com:443
          ingress:
            enabled: true
            class: nginx
```

The operator creates and uses:

- `<name>-selfsigned-issuer`
- `<name>-ca`
- `<name>-ca-issuer`
- `<name>-controller-tls`
- `<name>-router-<replica>-tls`

### External issuer reference (ClusterIssuer)

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
    server:
      issuerRef:
        name: my-cluster-issuer
        kind: ClusterIssuer
  controller:
    grpc:
      endpoints:
        - address: grpc.jumpstarter.example.com:443
          route:
            enabled: true
  routers:
    grpc:
      endpoints:
        - address: router.jumpstarter.example.com:443
          route:
            enabled: true
```

In this mode, the operator issues certificates with your referenced issuer and does not create the self-signed issuer chain.

### Login endpoint with cert-manager default TLS secret naming

When cert-manager is enabled and `controller.login.tls.secretName` is not set, the generated login Ingress uses the default TLS secret name `login-tls`.

For Ingress-based login endpoints, you can use `controller.login.endpoints[].ingress.annotations` to integrate with ACME issuers (for example Let's Encrypt) managed by cert-manager.

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
    server:
      selfSigned:
        enabled: true
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

### Login endpoint with explicit TLS secret

If you want a specific login certificate secret, set `controller.login.tls.secretName`:

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
    server:
      selfSigned:
        enabled: true
  controller:
    login:
      tls:
        secretName: login-custom-tls
      endpoints:
        - address: login.jumpstarter.example.com:443
          ingress:
            enabled: true
            class: nginx
```

## Operator behavior insights

From the current operator implementation in `controller/deploy/operator`, these behaviors are useful to know when authoring manifests:

- If `spec.baseDomain` is empty and the cluster exposes OpenShift Route APIs, the operator auto-detects the cluster domain and sets `spec.baseDomain` to `jumpstarter.<namespace>.<cluster-domain>`.
- If an endpoint has no enabled service type, the operator auto-selects one in this order: `route` (if available), then `ingress`, then `clusterIP`.
- gRPC endpoints (`controller.grpc`, `routers.grpc`) use TLS passthrough semantics in generated Ingress/Route resources; login endpoints use edge TLS termination.
- Controller and router auth secrets are created once with fixed names (`jumpstarter-controller-secret`, `jumpstarter-router-secret`) and are intentionally not owner-referenced, so they persist across CR deletion/recreation.
- Router replicas are implemented as one Deployment per replica, and `$(replica)` placeholders in endpoint addresses are substituted per replica.
- When router NodePort is enabled for multiple replicas, the operator offsets NodePort by replica index for router services.
- Even when cert-manager is disabled, the operator still creates `jumpstarter-service-ca-cert` (with empty `ca.crt`) for CLI discoverability.
- Status conditions are populated on the `Jumpstarter` resource and include deployment readiness plus cert-manager/certificate readiness when cert-manager is enabled.

## Jumpstarter API field reference

The `Jumpstarter` CRD is `operator.jumpstarter.dev/v1alpha1`.

### Top-level spec fields

| Field | Type | Description |
| --- | --- | --- |
| `spec.baseDomain` | `string` | Base DNS domain for generated endpoint hostnames (for example `grpc.<baseDomain>`). |
| `spec.certManager` | `object` | Certificate management settings for controller/router/login TLS integration. |
| `spec.controller` | `object` | Controller deployment, endpoint, and runtime settings. |
| `spec.routers` | `object` | Router deployment scale, resources, topology, and endpoint settings. |
| `spec.authentication` | `object` | Internal, Kubernetes token, JWT, and auto-provisioning authentication settings. |

### Controller and router fields

| Field | Type | Description |
| --- | --- | --- |
| `spec.controller.image` | `string` | Controller container image. |
| `spec.controller.imagePullPolicy` | `string` | Pull policy (`Always`, `IfNotPresent`, `Never`). |
| `spec.controller.resources` | `object` | Controller resource requests/limits. |
| `spec.controller.replicas` | `integer` | Number of controller pods. |
| `spec.controller.exporterOptions.offlineTimeout` | `duration` | Timeout before exporter is considered offline. |
| `spec.controller.grpc.tls.certSecret` | `string` | Manual TLS secret name for controller gRPC when cert-manager is disabled. |
| `spec.controller.grpc.endpoints[]` | `array` | Controller gRPC endpoint definitions (address + exposure method). |
| `spec.controller.grpc.keepalive.*` | `object` | gRPC keepalive tuning options. |
| `spec.controller.login.tls.secretName` | `string` | Optional TLS secret used by login edge-termination ingress/route. |
| `spec.controller.login.endpoints[]` | `array` | Login endpoint definitions (address + exposure method). |
| `spec.routers.image` | `string` | Router container image. |
| `spec.routers.imagePullPolicy` | `string` | Pull policy (`Always`, `IfNotPresent`, `Never`). |
| `spec.routers.resources` | `object` | Router resource requests/limits. |
| `spec.routers.replicas` | `integer` | Router replica count (operator creates one deployment per replica). |
| `spec.routers.topologySpreadConstraints[]` | `array` | Pod spread constraints for router deployments. |
| `spec.routers.grpc.tls.certSecret` | `string` | Manual TLS secret name for router gRPC when cert-manager is disabled. |
| `spec.routers.grpc.endpoints[]` | `array` | Router endpoint definitions; supports `$(replica)` placeholder in address. |
| `spec.routers.grpc.keepalive.*` | `object` | Router gRPC keepalive tuning options. |

### Authentication fields

| Field | Type | Description |
| --- | --- | --- |
| `spec.authentication.internal.enabled` | `boolean` | Enables internal token-based auth. |
| `spec.authentication.internal.prefix` | `string` | Username/subject prefix for internal auth. |
| `spec.authentication.internal.tokenLifetime` | `duration` | Internal token validity period. |
| `spec.authentication.k8s.enabled` | `boolean` | Enables Kubernetes service account token auth. |
| `spec.authentication.jwt[]` | `array` | JWT authenticators (issuer, audiences, claim mappings). |
| `spec.authentication.autoProvisioning.enabled` | `boolean` | Auto-create users authenticated by external providers. |

### cert-manager fields

| Field | Type | Description |
| --- | --- | --- |
| `spec.certManager.enabled` | `boolean` | Enables operator cert-manager integration. |
| `spec.certManager.server.selfSigned.enabled` | `boolean` | Enables operator-managed self-signed CA mode. |
| `spec.certManager.server.selfSigned.caDuration` | `duration` | Self-signed CA certificate duration. |
| `spec.certManager.server.selfSigned.certDuration` | `duration` | Issued server certificate duration. |
| `spec.certManager.server.selfSigned.renewBefore` | `duration` | Renewal lead time before expiration. |
| `spec.certManager.server.issuerRef.name` | `string` | Existing Issuer/ClusterIssuer name. |
| `spec.certManager.server.issuerRef.kind` | `string` | `Issuer` or `ClusterIssuer`. |
| `spec.certManager.server.issuerRef.group` | `string` | Issuer API group (default `cert-manager.io`). |
| `spec.certManager.server.issuerRef.caBundle` | `bytes` | Optional PEM CA bundle published for clients. |

### Endpoint schema (used in gRPC/login endpoint arrays)

| Field | Type | Description |
| --- | --- | --- |
| `address` | `string` | Host/address, optional port, supports `$(replica)` for router endpoints. |
| `route.enabled` | `boolean` | Create OpenShift Route for endpoint. |
| `route.annotations` / `route.labels` | `map` | Route metadata overrides. |
| `ingress.enabled` | `boolean` | Create Kubernetes Ingress for endpoint. |
| `ingress.class` | `string` | Ingress class name. |
| `ingress.annotations` / `ingress.labels` | `map` | Ingress metadata overrides. |
| `nodeport.enabled` | `boolean` | Create NodePort service for endpoint. |
| `nodeport.port` | `integer` | Requested NodePort value. |
| `nodeport.annotations` / `nodeport.labels` | `map` | NodePort service metadata overrides. |
| `loadBalancer.enabled` | `boolean` | Create LoadBalancer service for endpoint. |
| `loadBalancer.port` | `integer` | Service port for LoadBalancer exposure. |
| `loadBalancer.annotations` / `loadBalancer.labels` | `map` | LoadBalancer service metadata overrides. |
| `clusterIP.enabled` | `boolean` | Create ClusterIP service for endpoint. |
| `clusterIP.annotations` / `clusterIP.labels` | `map` | ClusterIP service metadata overrides. |

### Status conditions

| Condition type | Meaning |
| --- | --- |
| `Ready` | Overall operator-managed deployment readiness. |
| `ControllerDeploymentReady` | Controller deployment is available. |
| `RouterDeploymentsReady` | All router deployments are available. |
| `CertManagerAvailable` | cert-manager CRDs are present (when enabled). |
| `IssuerReady` | Configured issuer is ready (when enabled). |
| `ControllerCertificateReady` | Controller TLS secret is ready (when enabled). |
| `RouterCertificatesReady` | Router TLS secrets are ready for all replicas (when enabled). |

