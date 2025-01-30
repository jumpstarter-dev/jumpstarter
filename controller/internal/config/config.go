package config

import (
	"context"
	"fmt"

	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

func LoadConfiguration(
	ctx context.Context,
	client client.Reader,
	scheme *runtime.Scheme,
	key client.ObjectKey,
	signer *oidc.Signer,
	certificateAuthority string,
) (authenticator.Token, error) {
	var configmap corev1.ConfigMap
	if err := client.Get(ctx, key, &configmap); err != nil {
		return nil, err
	}

	rawAuthenticationConfiguration, ok := configmap.Data["authentication"]
	if !ok {
		return nil, fmt.Errorf("LoadConfiguration: missing authentication section")
	}

	authenticator, err := oidc.LoadAuthenticationConfiguration(
		ctx,
		scheme,
		[]byte(rawAuthenticationConfiguration),
		signer,
		certificateAuthority,
	)
	if err != nil {
		return nil, err
	}

	return authenticator, nil
}
