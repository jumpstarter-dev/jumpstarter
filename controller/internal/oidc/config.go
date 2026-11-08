package oidc

import (
	"context"
	"os"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authentication"
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
	certificateAuthority string,
) (authenticator.Token, string, error) {
	var authenticationConfiguration jumpstarterdevv1alpha1.AuthenticationConfiguration
	if err := runtime.DecodeInto(
		serializer.NewCodecFactory(scheme, serializer.EnableStrict).
			UniversalDecoder(jumpstarterdevv1alpha1.GroupVersion),
		configuration,
		&authenticationConfiguration,
	); err != nil {
		return nil, "", err
	}

	if authenticationConfiguration.Internal.Prefix == "" {
		authenticationConfiguration.Internal.Prefix = "internal:"
	}

	var authenticators []authenticator.Token
	for _, jwtAuthenticator := range authenticationConfiguration.JWT {
		authn, err := newJWTAuthenticator(ctx, scheme, jwtAuthenticator)
		if err != nil {
			return nil, "", err
		}
		authenticators = append(authenticators, authn)
	}

	internal, err := newJWTAuthenticator(ctx, scheme, apiserverv1beta1.JWTAuthenticator{
		Issuer: apiserverv1beta1.Issuer{
			URL:                  signer.Issuer(),
			CertificateAuthority: certificateAuthority,
			Audiences:            []string{signer.Audience()},
		},
		ClaimMappings: apiserverv1beta1.ClaimMappings{
			Username: apiserverv1beta1.PrefixedClaimOrExpression{
				Claim:  "sub",
				Prefix: &authenticationConfiguration.Internal.Prefix,
			},
		},
	})
	if err != nil {
		return nil, "", err
	}
	authenticators = append(authenticators, authentication.InternalTokenAuthenticator(internal))

	return tokenunion.NewFailOnError(authenticators...), authenticationConfiguration.Internal.Prefix, nil
}

// Reference: https://github.com/kubernetes/kubernetes/blob/v1.32.1/pkg/kubeapiserver/authenticator/config.go#L244
func newJWTAuthenticator(
	ctx context.Context,
	scheme *runtime.Scheme,
	jwtAuthenticator apiserverv1beta1.JWTAuthenticator,
) (authenticator.Token, error) {
	var oidcCAContent oidc.CAContentProvider
	if len(jwtAuthenticator.Issuer.CertificateAuthority) > 0 {
		var oidcCAError error
		if _, err := os.Stat(jwtAuthenticator.Issuer.CertificateAuthority); err == nil {
			oidcCAContent, oidcCAError = dynamiccertificates.NewDynamicCAContentFromFile(
				"oidc-authenticator",
				jwtAuthenticator.Issuer.CertificateAuthority,
			)
			jwtAuthenticator.Issuer.CertificateAuthority = ""
		} else {
			oidcCAContent, oidcCAError = dynamiccertificates.NewStaticCAContent(
				"oidc-authenticator",
				[]byte(jwtAuthenticator.Issuer.CertificateAuthority),
			)
		}
		if oidcCAError != nil {
			return nil, oidcCAError
		}
	}
	var jwtAuthenticatorUnversioned apiserver.JWTAuthenticator
	if err := scheme.Convert(&jwtAuthenticator, &jwtAuthenticatorUnversioned, nil); err != nil {
		return nil, err
	}
	return oidc.New(ctx, oidc.Options{
		JWTAuthenticator:     jwtAuthenticatorUnversioned,
		CAContentProvider:    oidcCAContent,
		SupportedSigningAlgs: oidc.AllValidSigningAlgorithms(),
	})
}
