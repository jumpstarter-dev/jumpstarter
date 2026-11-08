package config

import (
	"context"
	"fmt"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apiserver/pkg/authentication/authenticator"
)

func LoadAuthenticationConfiguration(
	ctx context.Context,
	scheme *runtime.Scheme,
	config Authentication,
	signer *oidc.Signer,
	certificateAuthority string,
) (authenticator.Token, string, error) {
	if config.Internal.Prefix == "" {
		config.Internal.Prefix = "internal:"
	}

	if config.Internal.TokenLifetime != "" {
		lifetime, err := ParseDuration(config.Internal.TokenLifetime)
		if err != nil {
			return nil, "", err
		}
		if lifetime <= 0 {
			return nil, "", fmt.Errorf("internal.tokenLifetime must be greater than 0")
		}
		signer.SetTokenLifetime(lifetime)
	}

	authn, err := oidc.NewAuthenticator(ctx, scheme, config.JWT, signer, certificateAuthority, config.Internal.Prefix)
	if err != nil {
		return nil, "", err
	}
	return authn, config.Internal.Prefix, nil
}
