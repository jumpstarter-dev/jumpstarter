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

func NewBasicAuthorizer(reader client.Reader, prefix string) *BasicAuthorizer {
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
		if ExporterAuthorizedUsername(&e, b.prefix) == attributes.GetUser().GetName() {
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
		if ClientAuthorizedUsername(&c, b.prefix) == attributes.GetUser().GetName() {
			return authorizer.DecisionAllow, "", nil
		} else {
			return authorizer.DecisionDeny, "", nil
		}
	default:
		return authorizer.DecisionDeny, "invalid object kind", nil
	}
}

func ClientAuthorizedUsername(c *jumpstarterdevv1alpha1.Client, prefix string) string {
	if c.Spec.Username == nil {
		return prefix + "client:" + c.Namespace + ":" + c.Name + ":" + string(c.UID)
	} else {
		return *c.Spec.Username
	}
}

func ExporterAuthorizedUsername(e *jumpstarterdevv1alpha1.Exporter, prefix string) string {
	if e.Spec.Username == nil {
		return prefix + "exporter:" + e.Namespace + ":" + e.Name + ":" + string(e.UID)
	} else {
		return *e.Spec.Username
	}
}

var _ = authorizer.Authorizer(&BasicAuthorizer{})
