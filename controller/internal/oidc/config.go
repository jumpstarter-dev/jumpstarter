package oidc

import (
	"context"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/serializer"
	"k8s.io/apiserver/pkg/apis/apiserver"
	apiserverv1beta1 "k8s.io/apiserver/pkg/apis/apiserver/v1beta1"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	tokenunion "k8s.io/apiserver/pkg/authentication/token/union"
	"k8s.io/apiserver/pkg/server/dynamiccertificates"
	"k8s.io/apiserver/plugin/pkg/authenticator/token/oidc"
)

func LoadAuthenticationConfiguration(
	ctx context.Context,
	scheme *runtime.Scheme,
	configuration []byte,
	signer *Signer,
	prefix string,
	certificateAuthority string,
) (authenticator.Token, error) {
	var authenticationConfiguration jumpstarterdevv1alpha1.AuthenticationConfiguration
	if err := runtime.DecodeInto(
		serializer.NewCodecFactory(scheme, serializer.EnableStrict).
			UniversalDecoder(jumpstarterdevv1alpha1.GroupVersion),
		configuration,
		&authenticationConfiguration,
	); err != nil {
		return nil, err
	}

	authenticationConfiguration.JWT = append(authenticationConfiguration.JWT, apiserverv1beta1.JWTAuthenticator{
		Issuer: apiserverv1beta1.Issuer{
			URL:                  signer.Issuer(),
			CertificateAuthority: certificateAuthority,
			Audiences:            []string{signer.Audience()},
		},
		ClaimMappings: apiserverv1beta1.ClaimMappings{
			Username: apiserverv1beta1.PrefixedClaimOrExpression{
				Claim:  "sub",
				Prefix: &prefix,
			},
		},
	})

	return newJWTAuthenticator(
		ctx,
		scheme,
		authenticationConfiguration,
	)
}

// Reference: https://github.com/kubernetes/kubernetes/blob/v1.32.1/pkg/kubeapiserver/authenticator/config.go#L244
func newJWTAuthenticator(
	ctx context.Context,
	scheme *runtime.Scheme,
	config jumpstarterdevv1alpha1.AuthenticationConfiguration,
) (authenticator.Token, error) {
	var jwtAuthenticators []authenticator.Token
	for _, jwtAuthenticator := range config.JWT {
		var oidcCAContent oidc.CAContentProvider
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
		oidcAuth, err := oidc.New(ctx, oidc.Options{
			JWTAuthenticator:     jwtAuthenticatorUnversioned,
			CAContentProvider:    oidcCAContent,
			SupportedSigningAlgs: oidc.AllValidSigningAlgorithms(),
		})
		if err != nil {
			return nil, err
		}
		jwtAuthenticators = append(jwtAuthenticators, oidcAuth)
	}
	return tokenunion.NewFailOnError(jwtAuthenticators...), nil
}
