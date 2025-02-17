package authorization

import (
	"context"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

type BasicAuthorizer struct {
	reader client.Reader
	prefix string
}

func NewBasicAuthorizer(reader client.Reader, prefix string) authorizer.Authorizer {
	return &BasicAuthorizer{reader: reader, prefix: prefix}
}

func (b *BasicAuthorizer) Authorize(
	ctx context.Context,
	attributes authorizer.Attributes,
) (authorizer.Decision, string, error) {
	switch attributes.GetResource() {
	case "Exporter":
		var e jumpstarterdevv1alpha1.Exporter
		if err := b.reader.Get(ctx, client.ObjectKey{
			Namespace: attributes.GetNamespace(),
			Name:      attributes.GetName(),
		}, &e); err != nil {
			return authorizer.DecisionDeny, "failed to get exporter", err
		}
		if e.Username(b.prefix) == attributes.GetUser().GetName() {
			return authorizer.DecisionAllow, "", nil
		} else {
			return authorizer.DecisionDeny, "", nil
		}
	case "Client":
		var c jumpstarterdevv1alpha1.Client
		if err := b.reader.Get(ctx, client.ObjectKey{
			Namespace: attributes.GetNamespace(),
			Name:      attributes.GetName(),
		}, &c); err != nil {
			return authorizer.DecisionDeny, "failed to get client", err
		}
		if c.Username(b.prefix) == attributes.GetUser().GetName() {
			return authorizer.DecisionAllow, "", nil
		} else {
			return authorizer.DecisionDeny, "", nil
		}
	default:
		return authorizer.DecisionDeny, "invalid object kind", nil
	}
}
