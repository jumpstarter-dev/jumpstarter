# Jumpstarter

`operator.jumpstarter.dev/v1alpha1`

Jumpstarter is the Schema for the jumpstarters API.

## Spec

| Field | Type | Description |
| --- | --- | --- |
| `spec.authentication` | object | Authentication configuration for client and exporter authentication. |
| `spec.authentication.autoProvisioning` | object | Automatic user provisioning configuration, this is useful for creating |
| `spec.authentication.autoProvisioning.enabled` | boolean (default: `False`) | Enable auto provisioning. |
| `spec.authentication.internal` | object | Internal authentication configuration. |
| `spec.authentication.internal.enabled` | boolean (default: `True`) | Enable the internal authentication method. |
| `spec.authentication.internal.prefix` | string (default: `internal:`) | Prefix to add to the subject claim of issued tokens. |
| `spec.authentication.internal.tokenLifetime` | string (default: `43800h`) | Token validity duration for issued tokens. |
| `spec.authentication.jwt` | array | JWT authentication configuration. |
| `spec.authentication.jwt[].claimMappings` | object | claimMappings points claims of a token to be treated as user attributes. |
| `spec.authentication.jwt[].claimValidationRules` | array | claimValidationRules are rules that are applied to validate token claims to authenticate users. |
| `spec.authentication.jwt[].issuer` | object | issuer contains the basic OIDC provider connection options. |
| `spec.authentication.jwt[].userValidationRules` | array | userValidationRules are rules that are applied to final user before completing authentication. |
| `spec.authentication.k8s` | object | Kubernetes authentication configuration. |
| `spec.authentication.k8s.enabled` | boolean (default: `False`) | Enable Kubernetes authentication. |
| `spec.baseDomain` | string | Base domain used to construct FQDNs for all service endpoints. |
| `spec.certManager` | object | CertManager configuration for automatic TLS certificate management. |
| `spec.certManager.enabled` | boolean (default: `False`) | Enable cert-manager integration for automatic TLS certificate management. |
| `spec.certManager.server` | object | Server certificate configuration for controller and router endpoints. |
| `spec.certManager.server.issuerRef` | object | Reference an existing cert-manager Issuer or ClusterIssuer. |
| `spec.certManager.server.selfSigned` | object | Create a self-signed CA managed by the operator. |
| `spec.controller` | object (default: `{}`) | Controller configuration for the main Jumpstarter API and gRPC services. |
| `spec.controller.exporterOptions` | object | Exporter options configuration. |
| `spec.controller.exporterOptions.offlineTimeout` | string (default: `180s`) | Offline timeout duration for exporters. |
| `spec.controller.grpc` | object | gRPC configuration for controller endpoints. |
| `spec.controller.grpc.endpoints` | array | List of gRPC endpoints to expose. |
| `spec.controller.grpc.keepalive` | object | Keepalive configuration for gRPC connections. |
| `spec.controller.grpc.tls` | object | TLS configuration for secure gRPC communication. |
| `spec.controller.image` | string (default: `quay.io/jumpstarter-dev/jumpstarter-controller:latest`) | Container image for the controller pods in 'registry/repository/image:tag' format. |
| `spec.controller.imagePullPolicy` | `Always` | `IfNotPresent` | `Never` (default: `IfNotPresent`) | Image pull policy for the controller container. |
| `spec.controller.login` | object | Login endpoint configuration for simplified CLI login. |
| `spec.controller.login.endpoints` | array | List of login endpoints to expose. |
| `spec.controller.login.tls` | object | TLS configuration for the login endpoint. |
| `spec.controller.replicas` | integer (default: `2`) | Number of controller replicas to run. |
| `spec.controller.resources` | object | Resource requirements for controller pods. |
| `spec.controller.restApi` | object | REST API configuration for HTTP-based clients. |
| `spec.controller.restApi.endpoints` | array | List of REST API endpoints to expose. |
| `spec.controller.restApi.tls` | object | TLS configuration for secure HTTP communication. |
| `spec.leasePolicy` | object (default: `{}`) | Lease policy configuration for controlling lease behavior. |
| `spec.leasePolicy.maxTags` | integer (default: `10`) | Maximum number of user-defined tags allowed per lease. |
| `spec.routers` | object (default: `{}`) | Router configuration for the Jumpstarter router service. |
| `spec.routers.grpc` | object | gRPC configuration for router endpoints. |
| `spec.routers.grpc.endpoints` | array | List of gRPC endpoints to expose. |
| `spec.routers.grpc.keepalive` | object | Keepalive configuration for gRPC connections. |
| `spec.routers.grpc.tls` | object | TLS configuration for secure gRPC communication. |
| `spec.routers.image` | string (default: `quay.io/jumpstarter-dev/jumpstarter-controller:latest`) | Container image for the router pods in 'registry/repository/image:tag' format. |
| `spec.routers.imagePullPolicy` | `Always` | `IfNotPresent` | `Never` (default: `IfNotPresent`) | Image pull policy for the router container. |
| `spec.routers.replicas` | integer (default: `3`) | Number of router replicas to run. |
| `spec.routers.resources` | object | Resource requirements for router pods. |
| `spec.routers.topologySpreadConstraints` | array | Topology spread constraints for router pod distribution. |

## Status

| Field | Type | Description |
| --- | --- | --- |
| `status.conditions` | array | Conditions represent the latest available observations of the Jumpstarter state. |
| `status.conditions[].lastTransitionTime` | string | lastTransitionTime is the last time the condition transitioned from one status to another. |
| `status.conditions[].message` | string | message is a human readable message indicating details about the transition. |
| `status.conditions[].observedGeneration` | integer | observedGeneration represents the .metadata.generation that the condition was set based upon. |
| `status.conditions[].reason` | string | reason contains a programmatic identifier indicating the reason for the condition's last transition. |
| `status.conditions[].status` | `True` | `False` | `Unknown` | status of the condition, one of True, False, Unknown. |
| `status.conditions[].type` | string | type of condition in CamelCase or in foo.example.com/CamelCase. |
