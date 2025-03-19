package config

import (
	"context"

	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apiserver/pkg/apis/apiserver"
	apiserverv1beta1 "k8s.io/apiserver/pkg/apis/apiserver/v1beta1"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	tokenunion "k8s.io/apiserver/pkg/authentication/token/union"
	"k8s.io/apiserver/pkg/server/dynamiccertificates"
	koidc "k8s.io/apiserver/plugin/pkg/authenticator/token/oidc"
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

	config.JWT = append(config.JWT, apiserverv1beta1.JWTAuthenticator{
		Issuer: apiserverv1beta1.Issuer{
			URL:                  signer.Issuer(),
			CertificateAuthority: certificateAuthority,
			Audiences:            []string{signer.Audience()},
		},
		ClaimMappings: apiserverv1beta1.ClaimMappings{
			Username: apiserverv1beta1.PrefixedClaimOrExpression{
				Claim:  "sub",
				Prefix: &config.Internal.Prefix,
			},
		},
	})

	authn, err := newJWTAuthenticator(
		ctx,
		scheme,
		config,
	)
	if err != nil {
		return nil, "", err
	}

	return authn, config.Internal.Prefix, nil
}

// Reference: https://github.com/kubernetes/kubernetes/blob/v1.32.1/pkg/kubeapiserver/authenticator/config.go#L244
func newJWTAuthenticator(
	ctx context.Context,
	scheme *runtime.Scheme,
	config Authentication,
) (authenticator.Token, error) {
	var jwtAuthenticators []authenticator.Token
	for _, jwtAuthenticator := range config.JWT {
		var oidcCAContent koidc.CAContentProvider
		if len(jwtAuthenticator.Issuer.CertificateAuthority) > 0 {
			var oidcCAError error
			oidcCAContent, oidcCAError = dynamiccertificates.NewStaticCAContent(
				"oidc-authenticator",
				[]byte(jwtAuthenticator.Issuer.CertificateAuthority),
			)
			if oidcCAError != nil {
				return nil, oidcCAError
			}
		}
		var jwtAuthenticatorUnversioned apiserver.JWTAuthenticator
		if err := scheme.Convert(&jwtAuthenticator, &jwtAuthenticatorUnversioned, nil); err != nil {
			return nil, err
		}
		oidcAuth, err := koidc.New(ctx, koidc.Options{
			JWTAuthenticator:     jwtAuthenticatorUnversioned,
			CAContentProvider:    oidcCAContent,
			SupportedSigningAlgs: koidc.AllValidSigningAlgorithms(),
		})
		if err != nil {
			return nil, err
		}
		jwtAuthenticators = append(jwtAuthenticators, oidcAuth)
	}
	return tokenunion.NewFailOnError(jwtAuthenticators...), nil
}
