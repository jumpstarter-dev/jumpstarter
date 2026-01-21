package config

import (
	"time"

	apiserverv1beta1 "k8s.io/apiserver/pkg/apis/apiserver/v1beta1"
)

// Config represents the main controller configuration structure.
// This matches the YAML structure in the ConfigMap's "config" key.
type Config struct {
	Authentication Authentication `json:"authentication" yaml:"authentication"`
	Provisioning   Provisioning   `json:"provisioning" yaml:"provisioning"`
	Grpc           Grpc           `json:"grpc" yaml:"grpc"`
}

// Authentication defines the authentication configuration for the controller.
// Supports multiple authentication methods: internal tokens, Kubernetes tokens, and JWT.
type Authentication struct {
	Internal Internal                            `json:"internal" yaml:"internal"`
	K8s      K8s                                 `json:"k8s,omitempty" yaml:"k8s,omitempty"`
	JWT      []apiserverv1beta1.JWTAuthenticator `json:"jwt" yaml:"jwt"`
}

// Internal defines the internal token authentication configuration.
type Internal struct {
	// Prefix to add to the subject claim of issued tokens (e.g., "internal:")
	Prefix string `json:"prefix" yaml:"prefix"`

	// TokenLifetime defines how long issued tokens are valid.
	// Parsed as a Go duration string (e.g., "43800h", "30d").
	TokenLifetime string `json:"tokenLifetime,omitempty" yaml:"tokenLifetime,omitempty"`
}

// K8s defines the Kubernetes service account token authentication configuration.
type K8s struct {
	// Enabled indicates whether Kubernetes authentication is enabled.
	Enabled bool `json:"enabled,omitempty" yaml:"enabled,omitempty"`
}

// Provisioning defines the provisioning configuration.
type Provisioning struct {
	Enabled bool `json:"enabled" yaml:"enabled"`
}

// Grpc defines the gRPC server configuration.
type Grpc struct {
	Keepalive Keepalive `json:"keepalive" yaml:"keepalive"`
}

// Keepalive defines the gRPC keepalive configuration.
// All duration fields are parsed as Go duration strings (e.g., "1s", "10s", "180s").
type Keepalive struct {
	// MinTime is the minimum time between keepalives that the server will accept.
	// Default: "1s"
	MinTime string `json:"minTime,omitempty" yaml:"minTime,omitempty"`

	// PermitWithoutStream allows keepalive pings even when there are no active streams.
	// Default: true
	PermitWithoutStream bool `json:"permitWithoutStream,omitempty" yaml:"permitWithoutStream,omitempty"`

	// Timeout is the duration to wait for a keepalive ping acknowledgment.
	// Default: "180s"
	Timeout string `json:"timeout,omitempty" yaml:"timeout,omitempty"`

	// IntervalTime is the duration between keepalive pings.
	// Default: "10s"
	IntervalTime string `json:"intervalTime,omitempty" yaml:"intervalTime,omitempty"`

	// MaxConnectionIdle is the maximum duration a connection can be idle before being closed.
	// Default: infinity (not set)
	MaxConnectionIdle string `json:"maxConnectionIdle,omitempty" yaml:"maxConnectionIdle,omitempty"`

	// MaxConnectionAge is the maximum age of a connection before it is closed.
	// Default: infinity (not set)
	MaxConnectionAge string `json:"maxConnectionAge,omitempty" yaml:"maxConnectionAge,omitempty"`

	// MaxConnectionAgeGrace is the grace period for closing connections that exceed MaxConnectionAge.
	// Default: infinity (not set)
	MaxConnectionAgeGrace string `json:"maxConnectionAgeGrace,omitempty" yaml:"maxConnectionAgeGrace,omitempty"`
}

// Router represents the router configuration mapping.
// This is a map where keys are router names (e.g., "default", "router-1", "router-2")
// and values are RouterEntry structs containing endpoint and label information.
// This matches the YAML structure in the ConfigMap's "router" key.
type Router map[string]RouterEntry

// RouterEntry defines a single router endpoint configuration.
type RouterEntry struct {
	// Endpoint is the router's gRPC endpoint address (e.g., "router-0.example.com:443")
	Endpoint string `json:"endpoint" yaml:"endpoint"`

	// Labels are optional labels to associate with this router entry.
	// Used to distinguish between different router instances.
	Labels map[string]string `json:"labels,omitempty" yaml:"labels,omitempty"`
}

// ParseDuration is a helper to parse duration strings with better error messages.
func ParseDuration(s string) (time.Duration, error) {
	if s == "" {
		return 0, nil
	}
	return time.ParseDuration(s)
}
