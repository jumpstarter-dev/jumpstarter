package authentication

import (
	"context"

	"k8s.io/apiserver/pkg/authentication/authenticator"
	"k8s.io/apiserver/pkg/authentication/user"
)

// internalUser is a user whose token the controller signed. Its name starts
// with the internal prefix, but so can a name mapped from an external issuer's
// claims, so the name cannot tell the two apart. Only the authenticator that
// verified the token can.
type internalUser struct {
	user.Info
}

// NewInternalUser marks info as authenticated by a token the controller signed.
func NewInternalUser(info user.Info) user.Info {
	return internalUser{info}
}

// IsInternal reports whether info was authenticated by a token the controller
// signed.
func IsInternal(info user.Info) bool {
	_, ok := info.(internalUser)
	return ok
}

// InternalTokenAuthenticator marks every user that auth authenticates as
// internal. auth must accept only tokens the controller signed.
func InternalTokenAuthenticator(auth authenticator.Token) authenticator.Token {
	return authenticator.TokenFunc(func(ctx context.Context, token string) (*authenticator.Response, bool, error) {
		resp, ok, err := auth.AuthenticateToken(ctx, token)
		if err != nil || !ok {
			return resp, ok, err
		}
		internal := *resp
		internal.User = NewInternalUser(resp.User)
		return &internal, true, nil
	})
}
