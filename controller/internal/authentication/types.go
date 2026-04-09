package authentication

import (
	"context"

	"k8s.io/apiserver/pkg/authentication/authenticator"
)

type ContextAuthenticator interface {
	AuthenticateContext(context.Context) (*authenticator.Response, bool, error)
}
