package authorization

import (
	"context"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authentication"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

type BasicAuthorizer struct {
	client       client.Client
	prefix       string
	provisioning bool
}

func NewBasicAuthorizer(client client.Client, prefix string, provisioning bool) authorizer.Authorizer {
	return &BasicAuthorizer{
		client:       client,
		prefix:       prefix,
		provisioning: provisioning,
	}
}

func (b *BasicAuthorizer) Authorize(
	ctx context.Context,
	attributes authorizer.Attributes,
) (authorizer.Decision, string, error) {
	switch attributes.GetResource() {
	case "Exporter":
		var e jumpstarterdevv1alpha1.Exporter
		if err := b.client.Get(ctx, client.ObjectKey{
			Namespace: attributes.GetNamespace(),
			Name:      attributes.GetName(),
		}, &e); err != nil {
			return authorizer.DecisionDeny, "failed to get exporter", err
		}
		if b.identifies(attributes.GetUser(), e.InternalSubject(), e.Spec.Username) {
			return authorizer.DecisionAllow, "", nil
		} else {
			return authorizer.DecisionDeny, "", nil
		}
	case "Client":
		var c jumpstarterdevv1alpha1.Client
		err := b.client.Get(ctx, client.ObjectKey{
			Namespace: attributes.GetNamespace(),
			Name:      attributes.GetName(),
		}, &c)
		if err != nil {
			// An internal token names a Client that existed when it was issued.
			// If that Client is gone, so is the token's authority.
			if apierrors.IsNotFound(err) && b.provisioning && !authentication.IsInternal(attributes.GetUser()) {
				c = jumpstarterdevv1alpha1.Client{
					ObjectMeta: metav1.ObjectMeta{
						Namespace: attributes.GetNamespace(),
						Name:      attributes.GetName(),
					},
					Spec: jumpstarterdevv1alpha1.ClientSpec{
						Username: new(attributes.GetUser().GetName()),
					},
				}
				if err := b.client.Create(ctx, &c); err != nil {
					return authorizer.DecisionDeny, "failed to provision client", err
				}
			} else {
				return authorizer.DecisionDeny, "failed to get client", err
			}
		}

		if b.identifies(attributes.GetUser(), c.InternalSubject(), c.Spec.Username) {
			return authorizer.DecisionAllow, "", nil
		} else {
			return authorizer.DecisionDeny, "", nil
		}
	default:
		return authorizer.DecisionDeny, "invalid object kind", nil
	}
}

// identifies reports whether u is the object with internalSubject and the
// optional username from its spec. A token the controller signed identifies
// an object only by its internal subject, and a token from an external issuer
// only by the username, so neither can stand in for the other.
func (b *BasicAuthorizer) identifies(u user.Info, internalSubject string, username *string) bool {
	if authentication.IsInternal(u) {
		return u.GetName() == b.prefix+internalSubject
	}
	return username != nil && u.GetName() == *username
}
