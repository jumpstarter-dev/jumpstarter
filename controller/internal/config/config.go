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
) (authenticator.Token, string, Router, []grpc.ServerOption, *Provisioning, error) {
	var configmap corev1.ConfigMap
	if err := client.Get(ctx, key, &configmap); err != nil {
		return nil, "", nil, nil, nil, err
	}

	rawRouter, ok := configmap.Data["router"]
	if !ok {
		return nil, "", nil, nil, nil, fmt.Errorf("LoadConfiguration: missing router section")
	}

	var router Router
	if err := yaml.Unmarshal([]byte(rawRouter), &router); err != nil {
		return nil, "", nil, nil, nil, err
	}

	rawAuthenticationConfiguration, ok := configmap.Data["authentication"]
	if ok {
		// backwards compatibility
		// TODO: remove in 0.7.0
		authenticator, prefix, err := oidc.LoadAuthenticationConfiguration(
			ctx,
			scheme,
			[]byte(rawAuthenticationConfiguration),
			signer,
			certificateAuthority,
		)
		if err != nil {
			return nil, "", nil, nil, nil, err
		}

		return authenticator, prefix, router, []grpc.ServerOption{
			grpc.KeepaliveEnforcementPolicy(keepalive.EnforcementPolicy{
				MinTime:             1 * time.Second,
				PermitWithoutStream: true,
			}),
		}, &Provisioning{Enabled: false}, nil
	}

	rawConfig, ok := configmap.Data["config"]
	if !ok {
		return nil, "", nil, nil, nil, fmt.Errorf("LoadConfiguration: missing config section")
	}

	var config Config
	if err := yaml.UnmarshalStrict([]byte(rawConfig), &config); err != nil {
		return nil, "", nil, nil, nil, err
	}

	authenticator, prefix, err := LoadAuthenticationConfiguration(
		ctx,
		scheme,
		config.Authentication,
		signer,
		certificateAuthority,
	)
	if err != nil {
		return nil, "", nil, nil, nil, err
	}

	serverOptions, err := LoadGrpcConfiguration(config.Grpc)
	if err != nil {
		return nil, "", nil, nil, nil, err
	}

	return authenticator, prefix, router, serverOptions, &config.Provisioning, nil
}
