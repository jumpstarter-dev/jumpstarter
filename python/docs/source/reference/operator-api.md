# Operator API

The Jumpstarter {term}`operator` is configured through the `Jumpstarter` custom
resource (`operator.jumpstarter.dev/v1alpha1`).

## Top-level Spec

| Field | Type | Description |
| --- | --- | --- |
| `spec.baseDomain` | `string` | Base DNS domain for generated endpoint hostnames. |
| `spec.certManager` | `object` | Certificate management settings. |
| `spec.controller` | `object` | {term}`Controller` deployment, endpoint, and runtime settings. |
| `spec.routers` | `object` | {term}`Router` deployment scale, resources, and endpoint settings. |
| `spec.authentication` | `object` | Authentication settings (internal, Kubernetes, JWT, auto-provisioning). |
| `spec.leasePolicy` | `object` | {term}`Lease` policy settings. |
| `spec.leasePolicy.maxTags` | `integer` | Maximum number of tags allowed per {term}`lease` (default: 10). |

## Controller

| Field | Type | Description |
| --- | --- | --- |
| `spec.controller.image` | `string` | Controller container image. |
| `spec.controller.imagePullPolicy` | `string` | Pull policy (`Always`, `IfNotPresent`, `Never`). |
| `spec.controller.resources` | `object` | Controller resource requests/limits. |
| `spec.controller.replicas` | `integer` | Number of controller pods. |
| `spec.controller.exporterOptions.offlineTimeout` | `duration` | Timeout before {term}`exporter` is considered offline. |
| `spec.controller.grpc.tls.certSecret` | `string` | Manual TLS secret name when cert-manager is disabled. |
| `spec.controller.grpc.endpoints[]` | `array` | Controller {term}`gRPC` endpoint definitions. |
| `spec.controller.login.tls.secretName` | `string` | Optional TLS secret for login edge-termination. |
| `spec.controller.login.endpoints[]` | `array` | Login endpoint definitions. |
| `spec.controller.restApi.tls.certSecret` | `string` | TLS secret name for REST API endpoints. |
| `spec.controller.restApi.endpoints[]` | `array` | REST API endpoint definitions. |

## Router

| Field | Type | Description |
| --- | --- | --- |
| `spec.routers.image` | `string` | Router container image. |
| `spec.routers.imagePullPolicy` | `string` | Pull policy (`Always`, `IfNotPresent`, `Never`). |
| `spec.routers.resources` | `object` | Router resource requests/limits. |
| `spec.routers.replicas` | `integer` | Router replica count (one deployment per replica). |
| `spec.routers.topologySpreadConstraints[]` | `array` | Pod spread constraints for {term}`router` deployments. |
| `spec.routers.grpc.tls.certSecret` | `string` | Manual TLS secret name when cert-manager is disabled. |
| `spec.routers.grpc.endpoints[]` | `array` | Router endpoint definitions; supports `$(replica)` placeholder. |

## gRPC Keepalive

Both `spec.controller.grpc.keepalive` and `spec.routers.grpc.keepalive` accept
the same fields:

| Field | Type | Description |
| --- | --- | --- |
| `keepalive.minTime` | `duration` | Minimum time between client pings. |
| `keepalive.permitWithoutStream` | `boolean` | Allow pings when there are no active streams. |
| `keepalive.timeout` | `duration` | Time to wait for a ping ack before closing the connection. |
| `keepalive.intervalTime` | `duration` | Interval between server-to-client pings. |
| `keepalive.maxConnectionIdle` | `duration` | Time after which an idle connection is closed. |
| `keepalive.maxConnectionAge` | `duration` | Maximum age of a connection before it is closed. |
| `keepalive.maxConnectionAgeGrace` | `duration` | Grace period after max connection age before forceful close. |

## Authentication

| Field | Type | Description |
| --- | --- | --- |
| `spec.authentication.internal.enabled` | `boolean` | Enables internal token-based auth. |
| `spec.authentication.internal.prefix` | `string` | Username/subject prefix for internal auth. |
| `spec.authentication.internal.tokenLifetime` | `duration` | Internal token validity period. |
| `spec.authentication.k8s.enabled` | `boolean` | Enables Kubernetes service account token auth. |
| `spec.authentication.jwt[]` | `array` | JWT authenticators (issuer, audiences, claim mappings). |
| `spec.authentication.autoProvisioning.enabled` | `boolean` | Auto-create users authenticated by external providers. |

## cert-manager

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
| `spec.certManager.server.issuerRef.caBundle` | `string (base64)` | Optional PEM CA bundle published for clients. |

## Endpoints

Used in `grpc.endpoints[]`, `login.endpoints[]`, and `restApi.endpoints[]`:

| Field | Type | Description |
| --- | --- | --- |
| `address` | `string` | Host/address, optional port, supports `$(replica)` for {term}`router` endpoints. |
| `route.enabled` | `boolean` | Create OpenShift Route. |
| `route.annotations` | `map` | Route annotation overrides. |
| `route.labels` | `map` | Route label overrides. |
| `ingress.enabled` | `boolean` | Create Kubernetes Ingress. |
| `ingress.class` | `string` | Ingress class name. |
| `ingress.annotations` | `map` | Ingress annotation overrides. |
| `ingress.labels` | `map` | Ingress label overrides. |
| `nodeport.enabled` | `boolean` | Create NodePort service. |
| `nodeport.port` | `integer` | Requested NodePort value. |
| `nodeport.annotations` | `map` | NodePort service annotation overrides. |
| `nodeport.labels` | `map` | NodePort service label overrides. |
| `loadBalancer.enabled` | `boolean` | Create LoadBalancer service. |
| `loadBalancer.port` | `integer` | Service port. |
| `loadBalancer.annotations` | `map` | LoadBalancer service annotation overrides. |
| `loadBalancer.labels` | `map` | LoadBalancer service label overrides. |
| `clusterIP.enabled` | `boolean` | Create ClusterIP service. |
| `clusterIP.annotations` | `map` | ClusterIP service annotation overrides. |
| `clusterIP.labels` | `map` | ClusterIP service label overrides. |

## Status Conditions

| Condition | Meaning |
| --- | --- |
| `Ready` | Overall deployment readiness. |
| `ControllerDeploymentReady` | Controller deployment is available. |
| `RouterDeploymentsReady` | All {term}`router` deployments are available. |
| `CertManagerAvailable` | cert-manager CRDs are present (when enabled). |
| `IssuerReady` | Configured issuer is ready (when enabled). |
| `ControllerCertificateReady` | Controller TLS secret is ready (when enabled). |
| `RouterCertificatesReady` | Router TLS secrets are ready for all replicas (when enabled). |
