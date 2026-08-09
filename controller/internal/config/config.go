package config

import (
	"cmp"
	"context"
	"fmt"
	"net"
	"os"
	"time"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/keepalive"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/yaml"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

func LoadRouterConfiguration(
	ctx context.Context,
	client client.Reader,
	key client.ObjectKey,
) ([]grpc.ServerOption, error) {
	var configmap corev1.ConfigMap
	if err := client.Get(ctx, key, &configmap); err != nil {
		return nil, err
	}

	rawConfig, ok := configmap.Data["config"]
	if !ok {
		return nil, fmt.Errorf("LoadRouterConfiguration: missing config section")
	}

	var config Config
	err := yaml.UnmarshalStrict([]byte(rawConfig), &config)
	if err != nil {
		return nil, err
	}

	serverOptions, err := LoadGrpcConfiguration(config.Grpc)
	if err != nil {
		return nil, err
	}

	return serverOptions, nil
}

// resolveTelemetryConfig validates and resolves the telemetry endpoint for a
// Telemetry config block. The GRPC_TELEMETRY_ENDPOINT env var takes priority
// over the ConfigMap value, allowing operators to override at the pod level.
// Returns nil when t is nil or disabled.
func resolveTelemetryConfig(t *Telemetry) (*Telemetry, error) {
	if t == nil || !t.Enabled {
		return nil, nil
	}
	if err := t.Validate(); err != nil {
		return nil, err
	}
	// Env var takes priority over ConfigMap, allowing operators to override
	// at the pod level without modifying the ConfigMap. Resolving here ensures
	// LoadedConfig.Telemetry.Endpoint is always the complete value — callers
	// don't need to re-check the env var.
	t.Endpoint = cmp.Or(os.Getenv("GRPC_TELEMETRY_ENDPOINT"), t.Endpoint)
	if ep := t.Endpoint; ep != "" {
		host, _, err := net.SplitHostPort(ep)
		if err != nil {
			return nil, fmt.Errorf("telemetry endpoint %q is not a valid host:port: %w", ep, err)
		}
		if host == "" {
			return nil, fmt.Errorf("telemetry endpoint %q has no host", ep)
		}
	}
	return t, nil
}

func LoadConfiguration(
	ctx context.Context,
	client client.Reader,
	scheme *runtime.Scheme,
	key client.ObjectKey,
	signer *oidc.Signer,
	certificateAuthority string,
) (*LoadedConfig, error) {
	var configmap corev1.ConfigMap
	if err := client.Get(ctx, key, &configmap); err != nil {
		return nil, err
	}

	rawRouter, ok := configmap.Data["router"]
	if !ok {
		return nil, fmt.Errorf("LoadConfiguration: missing router section")
	}

	var router Router
	if err := yaml.Unmarshal([]byte(rawRouter), &router); err != nil {
		return nil, err
	}

	rawAuthenticationConfiguration, ok := configmap.Data["authentication"]
	if ok {
		// backwards compatibility
		// TODO: remove in 0.7.0
		auth, prefix, err := oidc.LoadAuthenticationConfiguration(
			ctx,
			scheme,
			[]byte(rawAuthenticationConfiguration),
			signer,
			certificateAuthority,
		)
		if err != nil {
			return nil, err
		}

		return &LoadedConfig{
			Authenticator: auth,
			Prefix:        prefix,
			Router:        router,
			ServerOptions: []grpc.ServerOption{
				grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{
					MinTime:             1 * time.Second,
					PermitWithoutStream: true,
				}),
			},
			Provisioning: &Provisioning{Enabled: false},
			LeasePolicy:  &LeasePolicy{MaxTags: 10},
		}, nil
	}

	rawConfig, ok := configmap.Data["config"]
	if !ok {
		return nil, fmt.Errorf("LoadConfiguration: missing config section")
	}

	var config Config
	if err := yaml.UnmarshalStrict([]byte(rawConfig), &config); err != nil {
		return nil, err
	}

	auth, prefix, err := LoadAuthenticationConfiguration(
		ctx,
		scheme,
		config.Authentication,
		signer,
		certificateAuthority,
	)
	if err != nil {
		return nil, err
	}

	serverOptions, err := LoadGrpcConfiguration(config.Grpc)
	if err != nil {
		return nil, err
	}

	telemetry, err := resolveTelemetryConfig(config.Telemetry)
	if err != nil {
		return nil, err
	}

	return &LoadedConfig{
		Authenticator:    auth,
		Prefix:           prefix,
		Router:           router,
		ServerOptions:    serverOptions,
		Provisioning:     &config.Provisioning,
		LeasePolicy:      &config.LeasePolicy,
		HiddenLabels:     &config.HiddenLabels,
		DeprecatedLabels: &config.DeprecatedLabels,
		Telemetry:        telemetry,
	}, nil
}
