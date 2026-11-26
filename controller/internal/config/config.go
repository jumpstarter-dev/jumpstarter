package config

import (
	"context"
	"fmt"
	"time"

	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	"google.golang.org/grpc"
	"google.golang.org/grpc/keepalive"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/yaml"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	"k8s.io/utils/ptr"
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

func LoadConfiguration(
	ctx context.Context,
	client client.Reader,
	scheme *runtime.Scheme,
	key client.ObjectKey,
	signer *oidc.Signer,
	certificateAuthority string,
) (authenticator.Token, string, Router, []grpc.ServerOption, *Provisioning, *LeasePolicy, *HiddenLabels, *DeprecatedLabels, bool, error) {
	var configmap corev1.ConfigMap
	if err := client.Get(ctx, key, &configmap); err != nil {
		return nil, "", nil, nil, nil, nil, nil, nil, false, err
	}

	rawRouter, ok := configmap.Data["router"]
	if !ok {
		return nil, "", nil, nil, nil, nil, nil, nil, false, fmt.Errorf("LoadConfiguration: missing router section")
	}

	var router Router
	if err := yaml.Unmarshal([]byte(rawRouter), &router); err != nil {
		return nil, "", nil, nil, nil, nil, nil, nil, false, err
	}

	rawAuthenticationConfiguration, ok := configmap.Data["authentication"]
	if ok {
		// backwards compatibility
		// TODO: remove in 0.7.0
		authenticator, prefix, allowStoredTokens, err := oidc.LoadAuthenticationConfiguration(
			ctx,
			scheme,
			[]byte(rawAuthenticationConfiguration),
			signer,
			certificateAuthority,
		)
		if err != nil {
			return nil, "", nil, nil, nil, nil, nil, nil, false, err
		}

		return authenticator, prefix, router, []grpc.ServerOption{
			grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{
				MinTime:             1 * time.Second,
				PermitWithoutStream: true,
			}),
		}, &Provisioning{Enabled: false}, &LeasePolicy{MaxTags: 10}, nil, nil, allowStoredTokens, nil
	}

	rawConfig, ok := configmap.Data["config"]
	if !ok {
		return nil, "", nil, nil, nil, nil, nil, nil, false, fmt.Errorf("LoadConfiguration: missing config section")
	}

	var config Config
	if err := yaml.UnmarshalStrict([]byte(rawConfig), &config); err != nil {
		return nil, "", nil, nil, nil, nil, nil, nil, false, err
	}

	authenticator, prefix, err := LoadAuthenticationConfiguration(
		ctx,
		scheme,
		config.Authentication,
		signer,
		certificateAuthority,
	)
	if err != nil {
		return nil, "", nil, nil, nil, nil, nil, nil, false, err
	}

	serverOptions, err := LoadGrpcConfiguration(config.Grpc)
	if err != nil {
		return nil, "", nil, nil, nil, nil, nil, nil, false, err
	}

	return authenticator, prefix, router, serverOptions, &config.Provisioning, &config.LeasePolicy, &config.HiddenLabels, &config.DeprecatedLabels, ptr.Deref(config.Authentication.Internal.AllowStoredTokenFallback, true), nil
}
