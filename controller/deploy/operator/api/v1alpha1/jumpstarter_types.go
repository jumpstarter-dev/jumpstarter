/*
Copyright 2025.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package v1alpha1

import (
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	apiserverv1beta1 "k8s.io/apiserver/pkg/apis/apiserver/v1beta1"
)

// yaml mockup of the JumpstarterSpec
// spec:
//   baseDomain: example.com
//   useCertManager: true
//   controller:
//     image: quay.io/jumpstarter/jumpstarter:0.7.2
//     imagePullPolicy: IfNotPresent
//     resources:
//       requests:
//         cpu: 100m
//         memory: 100Mi
//     replicas: 2
//     exporterOptions:
//       offlineTimeout: 180s
//     restApi:
//       tls:
//         certSecret: jumpstarter-rest-api-tls
//       endpoints:
//         - hostname: rest-api.example.com
//           route:
//             class: default
//     grpc:
//       tls:
//         certSecret: jumpstarter-tls
//       endpoints:
//         - hostname: grpc.example.com
//  	     route:
//    	     	enabled: true
//         - hostname: grpc2.example.com
//   	     ingress:
//    	     	enabled: true
//         		annotations:
//         		labels:
//         - hostname: this.one.is.optional.com
// 			 nodeport:
//         		enabled: true
//         		port: 9090
//         		annotations:
//         		labels:
//         - hostname: this.one.is.optional.too.com
// 			 loadBalancer:
//         		enabled: true
//         		port: 9090
//         		annotations:
//         		labels:
//       keepalive:
//         minTime: 1s
//         permitWithoutStream: true
//         timeout: 180s
//         intervalTime: 10s
//   routers:
//     image: quay.io/jumpstarter/jumpstarter:0.7.2
//     imagePullPolicy: IfNotPresent
//     resources:
//       requests:
//         cpu: 100m
//         memory: 100Mi
//     replicas: 3
//     topologySpreadConstraints:
//       - topologyKey: "kubernetes.io/hostname"
//         whenUnsatisfiable: ScheduleAnyway
//       - topologyKey: "kubernetes.io/zone"
//         whenUnsatisfiable: ScheduleAnyway
//     grpc:
//       tls:
//         certSecret: jumpstarter-router-tls
//       endpoints:
//         - hostname: router-$(replica).router.example.com
//           route:
//             enabled: true
//           ingress:
//             enabled: true
//             class: default
//           nodeport:
//             enabled: true
//             port: 9090
//           loadBalancer:
//             annotations:
//             labels:
//             enabled: true
//       keepalive:
//         minTime: 1s
//         permitWithoutStream: true
//         timeout: 180s
//         intervalTime: 10s
//   authentication:
//     internal:
//       prefix: "internal:"
//       enabled: true
//     k8s:
//       enabled: true
//     jwt:
//        - issuer:
//            url: https://auth.example.com/auth/realms/EmployeeIDP
//          audiences:
//            - account
//          claimMappings:
//            username:
//              claim: "preferred_username"
//              prefix: "corp:"
//

// JumpstarterSpec defines the desired state of a Jumpstarter deployment. A deployment
// can be created in a namespace of the cluster, and that's where all the Jumpstarter
// resources and services will reside.
type JumpstarterSpec struct {
	// Base domain used to construct FQDNs for all service endpoints.
	// This domain will be used to generate the default hostnames for Routes, Ingresses, and certificates.
	// Example: "example.com" will generate endpoints like "grpc.example.com", "router.example.com"
	// +kubebuilder:validation:Pattern=^[a-z0-9]([a-z0-9\-\.]*[a-z0-9])?$
	BaseDomain string `json:"baseDomain,omitempty"`

	// Enable automatic TLS certificate management using cert-manager.
	// When enabled, jumpstarter will interact with cert-manager to automatically provision
	// and renew TLS certificates for all endpoints. Requires cert-manager to be installed in the cluster.
	// +kubebuilder:default=true
	UseCertManager bool `json:"useCertManager,omitempty"`

	// Controller configuration for the main Jumpstarter API and gRPC services.
	// The controller handles gRPC and REST API requests from clients and exporters.
	Controller ControllerConfig `json:"controller,omitempty"`

	// Router configuration for the Jumpstarter router service.
	// Routers handle gRPC traffic routing and load balancing.
	Routers RoutersConfig `json:"routers,omitempty"`

	// Authentication configuration for client and exporter authentication.
	// Supports multiple authentication methods including internal tokens, Kubernetes tokens, and JWT.
	Authentication AuthenticationConfig `json:"authentication,omitempty"`
}

// RoutersConfig defines the configuration for Jumpstarter router pods.
// Routers handle gRPC traffic routing and load balancing between clients and exporters.
type RoutersConfig struct {
	// Container image for the router pods in 'registry/repository/image:tag' format.
	// If not specified, defaults to the latest stable version of the Jumpstarter router.
	Image string `json:"image,omitempty"`

	// Image pull policy for the router container.
	// Controls when the container image should be pulled from the registry.
	// +kubebuilder:default="IfNotPresent"
	// +kubebuilder:validation:Enum=Always;IfNotPresent;Never
	ImagePullPolicy corev1.PullPolicy `json:"imagePullPolicy,omitempty"`

	// Resource requirements for router pods.
	// Defines CPU and memory requests and limits for each router pod.
	Resources corev1.ResourceRequirements `json:"resources,omitempty"`

	// Number of router replicas to run.
	// Must be a positive integer. Minimum recommended value is 3 for high availability.
	// +kubebuilder:default=3
	// +kubebuilder:validation:Minimum=1
	Replicas int32 `json:"replicas,omitempty"`

	// Topology spread constraints for router pod distribution.
	// Ensures router pods are distributed evenly across nodes and zones.
	// Useful for high availability and fault tolerance.
	TopologySpreadConstraints []corev1.TopologySpreadConstraint `json:"topologySpreadConstraints,omitempty"`

	// gRPC configuration for router endpoints.
	// Defines how router gRPC services are exposed and configured.
	GRPC GRPCConfig `json:"grpc,omitempty"`
}

// ControllerConfig defines the configuration for Jumpstarter controller pods.
// The controller is responsible for the gRPC and REST API services used by clients
// and exporters to interact with Jumpstarter.
type ControllerConfig struct {
	// Container image for the controller pods in 'registry/repository/image:tag' format.
	// If not specified, defaults to the latest stable version of the Jumpstarter controller.
	Image string `json:"image,omitempty"`

	// Image pull policy for the controller container.
	// Controls when the container image should be pulled from the registry.
	// +kubebuilder:default="IfNotPresent"
	// +kubebuilder:validation:Enum=Always;IfNotPresent;Never
	ImagePullPolicy corev1.PullPolicy `json:"imagePullPolicy,omitempty"`

	// Resource requirements for controller pods.
	// Defines CPU and memory requests and limits for each controller pod.
	Resources corev1.ResourceRequirements `json:"resources,omitempty"`

	// Number of controller replicas to run.
	// Must be a positive integer. Minimum recommended value is 2 for high availability.
	// +kubebuilder:default=2
	// +kubebuilder:validation:Minimum=1
	Replicas int32 `json:"replicas,omitempty"`

	// Exporter options configuration.
	// Controls how exporters connect and behave when communicating with the controller.
	ExporterOptions ExporterOptions `json:"exporterOptions,omitempty"`

	// REST API configuration for HTTP-based clients.
	// Enables non-gRPC clients to interact with Jumpstarter for listing leases,
	// managing exporters, and creating new leases. Use this when you need HTTP/JSON access.
	RestAPI RestAPIConfig `json:"restApi,omitempty"`

	// gRPC configuration for controller endpoints.
	// Defines how controller gRPC services are exposed and configured.
	GRPC GRPCConfig `json:"grpc,omitempty"`

	// Authentication configuration for client and exporter authentication.
	// Configures how clients and exporters can authenticate with Jumpstarter.
	// Supports multiple authentication methods including internal tokens, Kubernetes tokens, and JWT.
	Authentication AuthenticationConfig `json:"authentication,omitempty"`
}

// ExporterOptions defines configuration options for exporter behavior.
type ExporterOptions struct {
	// Offline timeout duration for exporters.
	// After this duration without communication, an exporter is considered offline.
	// This drives the online/offline status field of exporters, and offline exporters
	// won't be considered for leases.
	// +kubebuilder:default="180s"
	OfflineTimeout *metav1.Duration `json:"offlineTimeout,omitempty"`
}

// GRPCConfig defines gRPC service configuration.
// Configures how gRPC services are exposed and their connection behavior.
type GRPCConfig struct {
	// TLS configuration for secure gRPC communication.
	// Requires a Kubernetes secret containing the TLS certificate and private key.
	// If useCertManager is enabled, this secret will be automatically created.
	// See also: spec.useCertManager for automatic certificate management.
	TLS TLSConfig `json:"tls,omitempty"`

	// List of gRPC endpoints to expose.
	// Each endpoint can use different networking methods (Route, Ingress, NodePort, or LoadBalancer)
	// based on your cluster setup. Example: Use Route for OpenShift, Ingress for standard Kubernetes.
	Endpoints []Endpoint `json:"endpoints,omitempty"`

	// Keepalive configuration for gRPC connections.
	// Controls connection health checks and idle connection management.
	// Helps maintain stable connections in load-balanced environments.
	Keepalive *GRPCKeepaliveConfig `json:"keepalive,omitempty"`
}

// GRPCKeepaliveConfig defines keepalive settings for gRPC connections.
// These settings help maintain stable connections in load-balanced environments
// and detect connection issues early.
type GRPCKeepaliveConfig struct {
	// Minimum time between keepalives that the connection will accept, under this threshold
	// the other side will get a GOAWAY signal.
	// Prevents excessive keepalive traffic on the network.
	// +kubebuilder:default="1s"
	MinTime *metav1.Duration `json:"minTime,omitempty"`

	// Allow keepalive pings even when there are no active RPC streams.
	// Useful for detecting connection issues in idle connections.
	// This is important to keep TCP gRPC connections alive when traversing
	// load balancers and proxies.
	// +kubebuilder:default=true
	PermitWithoutStream bool `json:"permitWithoutStream,omitempty"`

	// Timeout for keepalive ping acknowledgment.
	// If a ping is not acknowledged within this time, the connection is considered broken.
	// The default is high to avoid issues when the network on a exporter is overloaded, i.e.
	// during flashing.
	// +kubebuilder:default="180s"
	Timeout *metav1.Duration `json:"timeout,omitempty"`

	// Maximum time a connection can remain idle before being closed.
	// It defaults to infinity.
	MaxConnectionIdle *metav1.Duration `json:"maxConnectionIdle,omitempty"`

	// Maximum age of a connection before it is closed and recreated.
	// Helps prevent issues with long-lived connections. It defaults to infinity.
	MaxConnectionAge *metav1.Duration `json:"maxConnectionAge,omitempty"`

	// Grace period for closing connections that exceed MaxConnectionAge.
	// Allows ongoing RPCs to complete before closing the connection.
	MaxConnectionAgeGrace *metav1.Duration `json:"maxConnectionAgeGrace,omitempty"`

	// Interval between keepalive pings.
	// How often to send keepalive pings to check connection health. This is important
	// to keep TCP gRPC connections alive when traversing load balancers and proxies.
	// +kubebuilder:default="10s"
	IntervalTime *metav1.Duration `json:"intervalTime,omitempty"`
}

// AuthenticationConfig defines authentication methods for Jumpstarter.
// Supports multiple authentication methods that can be enabled simultaneously.
type AuthenticationConfig struct {
	// Internal authentication configuration.
	// Built-in authenticator that issues tokens for clients and exporters.
	// This is the simplest authentication method and is enabled by default.
	Internal InternalAuthConfig `json:"internal,omitempty"`

	// Kubernetes authentication configuration.
	// Enables authentication using Kubernetes service account tokens.
	// Useful for integrating with existing Kubernetes RBAC policies.
	K8s K8sAuthConfig `json:"k8s,omitempty"`

	// JWT authentication configuration.
	// Enables authentication using external JWT tokens from OIDC providers.
	// Supports multiple JWT authenticators for different identity providers.
	JWT []apiserverv1beta1.JWTAuthenticator `json:"jwt,omitempty"`
}

// InternalAuthConfig defines the built-in authentication configuration.
// The internal authenticator issues tokens for clients and exporters to authenticate
// with Jumpstarter. This is the simplest authentication method.
type InternalAuthConfig struct {
	// Prefix to add to the subject claim of issued tokens.
	// Helps distinguish internal tokens from other authentication methods.
	// Example: "internal:" will result in subjects like "internal:user123"
	// +kubebuilder:default="internal:"
	// +kubebuilder:validation:MaxLength=50
	Prefix string `json:"prefix,omitempty"`

	// Enable the internal authentication method.
	// When disabled, clients cannot use internal tokens for authentication.
	// +kubebuilder:default=true
	Enabled bool `json:"enabled,omitempty"`

	// Token validity duration for issued tokens.
	// After this duration, tokens expire and must be renewed.
	// +kubebuilder:default="43800h"
	TokenLifetime *metav1.Duration `json:"tokenLifetime,omitempty"`
}

// K8sAuthConfig defines Kubernetes service account authentication.
// Enables authentication using Kubernetes service account tokens.
type K8sAuthConfig struct {
	// Enable Kubernetes authentication.
	// When enabled, clients can authenticate using Kubernetes service account tokens.
	// +kubebuilder:default=false
	Enabled bool `json:"enabled,omitempty"`
}

// TLSConfig defines TLS configuration for secure communication.
type TLSConfig struct {
	// Name of the Kubernetes secret containing the TLS certificate and private key.
	// The secret must contain 'tls.crt' and 'tls.key' keys.
	// If useCertManager is enabled, this secret will be automatically created.
	// +kubebuilder:validation:Pattern=^[a-z0-9]([a-z0-9\-\.]*[a-z0-9])?$
	CertSecret string `json:"certSecret,omitempty"`
}

// RestAPIConfig defines REST API configuration for HTTP-based clients.
// Provides HTTP/JSON access to Jumpstarter functionality.
type RestAPIConfig struct {
	// TLS configuration for secure HTTP communication.
	// Requires a Kubernetes secret containing the TLS certificate and private key.
	TLS TLSConfig `json:"tls,omitempty"`

	// List of REST API endpoints to expose.
	// Each endpoint can use different networking methods (Route, Ingress, NodePort, or LoadBalancer)
	// based on your cluster setup.
	Endpoints []Endpoint `json:"endpoints,omitempty"`
}

// Endpoint defines a single endpoint configuration.
// An endpoint can use one or more networking methods: Route, Ingress, NodePort, or LoadBalancer.
// Multiple methods can be configured simultaneously for the same hostname.
type Endpoint struct {
	// Hostname for this endpoint.
	// Required for Route and Ingress endpoints. Optional for NodePort and LoadBalancer endpoints.
	// When optional, the hostname is used for certificate generation and DNS resolution.
	// +kubebuilder:validation:Pattern=^[a-z0-9]([a-z0-9\-\.]*[a-z0-9])?$
	Hostname string `json:"hostname,omitempty"`

	// Route configuration for OpenShift clusters.
	// Creates an OpenShift Route resource for this endpoint.
	// Only applicable in OpenShift environments.
	Route *RouteConfig `json:"route,omitempty"`

	// Ingress configuration for standard Kubernetes clusters.
	// Creates an Ingress resource for this endpoint.
	// Requires an ingress controller to be installed.
	Ingress *IngressConfig `json:"ingress,omitempty"`

	// NodePort configuration for direct node access.
	// Exposes the service on a specific port on each node.
	// Useful for bare-metal or simple cluster setups.
	NodePort *NodePortConfig `json:"nodeport,omitempty"`

	// LoadBalancer configuration for cloud environments.
	// Creates a LoadBalancer service for this endpoint.
	// Requires cloud provider support for LoadBalancer services.
	LoadBalancer *LoadBalancerConfig `json:"loadBalancer,omitempty"`
}

// RouteConfig defines OpenShift Route configuration.
type RouteConfig struct {
	// Enable the OpenShift Route for this endpoint.
	// When disabled, no Route resource will be created for this endpoint.
	// When not specified, the operator will determine the best networking option for your cluster.
	Enabled bool `json:"enabled,omitempty"`

	// Annotations to add to the OpenShift Route resource.
	// Useful for configuring route-specific behavior and TLS settings.
	Annotations map[string]string `json:"annotations,omitempty"`

	// Labels to add to the OpenShift Route resource.
	// Useful for monitoring, cost allocation, and resource organization.
	Labels map[string]string `json:"labels,omitempty"`
}

// IngressConfig defines Kubernetes Ingress configuration.
type IngressConfig struct {
	// Enable the Kubernetes Ingress for this endpoint.
	// When disabled, no Ingress resource will be created for this endpoint.
	// When not specified, the operator will determine the best networking option for your cluster.
	Enabled bool `json:"enabled,omitempty"`

	// Ingress class name for the Kubernetes Ingress.
	// Specifies which ingress controller should handle this ingress.
	// +kubebuilder:default="default"
	Class string `json:"class,omitempty"`

	// Annotations to add to the Kubernetes Ingress resource.
	// Useful for configuring ingress-specific behavior, TLS settings, and load balancer options.
	Annotations map[string]string `json:"annotations,omitempty"`

	// Labels to add to the Kubernetes Ingress resource.
	// Useful for monitoring, cost allocation, and resource organization.
	Labels map[string]string `json:"labels,omitempty"`
}

// NodePortConfig defines Kubernetes NodePort service configuration.
type NodePortConfig struct {
	// Enable the NodePort service for this endpoint.
	// When disabled, no NodePort service will be created for this endpoint.
	// When not specified, the operator will determine the best networking option for your cluster.
	Enabled bool `json:"enabled,omitempty"`

	// NodePort port number to expose on each node.
	// Must be in the range 30000-32767 for most Kubernetes clusters.
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=65535
	Port int32 `json:"port,omitempty"`

	// Annotations to add to the NodePort service.
	// Useful for configuring service-specific behavior and load balancer options.
	Annotations map[string]string `json:"annotations,omitempty"`

	// Labels to add to the NodePort service.
	// Useful for monitoring, cost allocation, and resource organization.
	Labels map[string]string `json:"labels,omitempty"`
}

// LoadBalancerConfig defines Kubernetes LoadBalancer service configuration.
type LoadBalancerConfig struct {
	// Enable the LoadBalancer service for this endpoint.
	// When disabled, no LoadBalancer service will be created for this endpoint.
	// When not specified, the operator will determine the best networking option for your cluster.
	Enabled bool `json:"enabled,omitempty"`

	// Port number for the LoadBalancer service.
	// Must be a valid port number (1-65535).
	// +kubebuilder:validation:Minimum=1
	// +kubebuilder:validation:Maximum=65535
	Port int32 `json:"port,omitempty"`

	// Annotations to add to the LoadBalancer service.
	// Useful for configuring cloud provider-specific load balancer options.
	// Example: "service.beta.kubernetes.io/aws-load-balancer-type: nlb"
	Annotations map[string]string `json:"annotations,omitempty"`

	// Labels to add to the LoadBalancer service.
	// Useful for monitoring, cost allocation, and resource organization.
	Labels map[string]string `json:"labels,omitempty"`
}

// JumpstarterStatus defines the observed state of Jumpstarter.
// This field is currently empty but can be extended to include status information
// such as deployment status, endpoint URLs, and health information.
type JumpstarterStatus struct {
	// INSERT ADDITIONAL STATUS FIELD - define observed state of cluster
	// Important: Run "make" to regenerate code after modifying this file
}

// +kubebuilder:object:root=true
// +kubebuilder:subresource:status

// Jumpstarter is the Schema for the jumpstarters API.
type Jumpstarter struct {
	metav1.TypeMeta   `json:",inline"`
	metav1.ObjectMeta `json:"metadata,omitempty"`

	Spec   JumpstarterSpec   `json:"spec,omitempty"`
	Status JumpstarterStatus `json:"status,omitempty"`
}

// +kubebuilder:object:root=true

// JumpstarterList contains a list of Jumpstarter deployments.
// This is used by kubectl to list multiple Jumpstarter resources.
type JumpstarterList struct {
	metav1.TypeMeta `json:",inline"`
	metav1.ListMeta `json:"metadata,omitempty"`
	Items           []Jumpstarter `json:"items"`
}

func init() {
	SchemeBuilder.Register(&Jumpstarter{}, &JumpstarterList{})
}
