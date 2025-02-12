package authorization

import (
	"context"

	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/authorization/authorizer"
)

type ContextAttributesGetter interface {
	ContextAttributes(context.Context, user.Info) (authorizer.Attributes, error)
}
