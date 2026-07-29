package auth

import (
	"context"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authentication"
	"google.golang.org/grpc/metadata"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

type stubAuthenticator struct {
	resp *authenticator.Response
	ok   bool
	err  error
}

func (s *stubAuthenticator) AuthenticateContext(_ context.Context) (*authenticator.Response, bool, error) {
	return s.resp, s.ok, s.err
}

type stubAttributesGetter struct {
	attrs authorizer.Attributes
	err   error
}

func (s *stubAttributesGetter) ContextAttributes(_ context.Context, _ user.Info) (authorizer.Attributes, error) {
	return s.attrs, s.err
}

type stubAuthorizer struct {
	decision authorizer.Decision
	reason   string
	err      error
}

func (s *stubAuthorizer) Authorize(_ context.Context, _ authorizer.Attributes) (authorizer.Decision, string, error) {
	return s.decision, s.reason, s.err
}

func buildScheme() *runtime.Scheme {
	scheme := runtime.NewScheme()
	_ = jumpstarterdevv1alpha1.AddToScheme(scheme)
	return scheme
}

func newFakeClient(objs ...kclient.Object) kclient.Client {
	return fake.NewClientBuilder().
		WithScheme(buildScheme()).
		WithObjects(objs...).
		Build()
}

func newAuth(authn authentication.ContextAuthenticator, authz *stubAuthorizer, attr *stubAttributesGetter, objs ...kclient.Object) *Auth {
	return NewAuth(newFakeClient(objs...), authn, authz, attr)
}

func TestIsExporter(t *testing.T) {
	authn := &stubAuthenticator{}
	attr := &stubAttributesGetter{}
	authz := &stubAuthorizer{}
	a := newAuth(authn, authz, attr)

	tests := []struct {
		name     string
		metadata metadata.MD
		want     bool
	}{
		{
			name:     "exporter kind",
			metadata: metadata.Pairs("jumpstarter-kind", "Exporter"),
			want:     true,
		},
		{
			name:     "client kind",
			metadata: metadata.Pairs("jumpstarter-kind", "Client"),
			want:     false,
		},
		{
			name:     "no metadata",
			metadata: metadata.MD{},
			want:     false,
		},
		{
			name:     "missing kind",
			metadata: metadata.Pairs("jumpstarter-namespace", "default"),
			want:     false,
		},
		{
			name:     "multiple kind values",
			metadata: metadata.Pairs("jumpstarter-kind", "Exporter", "jumpstarter-kind", "Client"),
			want:     false,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ctx := context.Background()
			if len(tt.metadata) > 0 {
				ctx = metadata.NewIncomingContext(ctx, tt.metadata)
			}
			got := a.IsExporter(ctx)
			if got != tt.want {
				t.Errorf("IsExporter() = %v, want %v", got, tt.want)
			}
		})
	}
}
