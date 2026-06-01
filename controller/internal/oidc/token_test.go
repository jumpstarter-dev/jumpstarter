package oidc

import (
	"context"
	"fmt"
	"strings"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/authorization/authorizer"
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

func TestVerifyExporterObjectToken_PermissionDeniedIncludesExporterIdentity(t *testing.T) {
	authn := &stubAuthenticator{
		resp: &authenticator.Response{
			User: &user.DefaultInfo{Name: "test-user"},
		},
		ok: true,
	}
	attrs := &stubAttributesGetter{
		attrs: authorizer.AttributesRecord{
			User:      &user.DefaultInfo{Name: "test-user"},
			Namespace: "test-namespace",
			Resource:  "Exporter",
			Name:      "my-exporter",
		},
	}
	authz := &stubAuthorizer{
		decision: authorizer.DecisionDeny,
	}

	scheme := runtime.NewScheme()
	_ = jumpstarterdevv1alpha1.AddToScheme(scheme)
	fakeClient := fake.NewClientBuilder().WithScheme(scheme).Build()

	_, err := VerifyExporterObjectToken(context.Background(), authn, authz, attrs, fakeClient)
	if err == nil {
		t.Fatal("expected error, got nil")
	}

	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %T: %v", err, err)
	}
	if st.Code() != codes.PermissionDenied {
		t.Errorf("expected PermissionDenied code, got %v", st.Code())
	}
	if !strings.Contains(err.Error(), "my-exporter") {
		t.Errorf("error should contain exporter name 'my-exporter', got: %s", err.Error())
	}
	if !strings.Contains(err.Error(), "test-namespace") {
		t.Errorf("error should contain namespace 'test-namespace', got: %s", err.Error())
	}
}

func TestVerifyExporterObjectToken_GetFailureIncludesExporterIdentity(t *testing.T) {
	authn := &stubAuthenticator{
		resp: &authenticator.Response{
			User: &user.DefaultInfo{Name: "test-user"},
		},
		ok: true,
	}
	attrs := &stubAttributesGetter{
		attrs: authorizer.AttributesRecord{
			User:      &user.DefaultInfo{Name: "test-user"},
			Namespace: "prod-namespace",
			Resource:  "Exporter",
			Name:      "prod-exporter",
		},
	}
	authz := &stubAuthorizer{
		decision: authorizer.DecisionAllow,
	}

	scheme := runtime.NewScheme()
	_ = jumpstarterdevv1alpha1.AddToScheme(scheme)
	fakeClient := fake.NewClientBuilder().WithScheme(scheme).Build()

	_, err := VerifyExporterObjectToken(context.Background(), authn, authz, attrs, fakeClient)
	if err == nil {
		t.Fatal("expected error (exporter not found), got nil")
	}

	if !strings.Contains(err.Error(), "prod-exporter") {
		t.Errorf("error should contain exporter name 'prod-exporter', got: %s", err.Error())
	}
	if !strings.Contains(err.Error(), "prod-namespace") {
		t.Errorf("error should contain namespace 'prod-namespace', got: %s", err.Error())
	}
}

func TestVerifyExporterObjectToken_TokenFailureDoesNotIncludeIdentity(t *testing.T) {
	authn := &stubAuthenticator{
		err: fmt.Errorf("invalid token"),
	}
	attrs := &stubAttributesGetter{}
	authz := &stubAuthorizer{}

	scheme := runtime.NewScheme()
	_ = jumpstarterdevv1alpha1.AddToScheme(scheme)
	fakeClient := fake.NewClientBuilder().WithScheme(scheme).Build()

	_, err := VerifyExporterObjectToken(context.Background(), authn, authz, attrs, fakeClient)
	if err == nil {
		t.Fatal("expected error, got nil")
	}

	if err.Error() != "invalid token" {
		t.Errorf("expected raw error 'invalid token', got: %s", err.Error())
	}
}

func TestVerifyExporterObjectToken_AuthorizeErrorPreservesGRPCCode(t *testing.T) {
	authn := &stubAuthenticator{
		resp: &authenticator.Response{
			User: &user.DefaultInfo{Name: "test-user"},
		},
		ok: true,
	}
	attrs := &stubAttributesGetter{
		attrs: authorizer.AttributesRecord{
			User:      &user.DefaultInfo{Name: "test-user"},
			Namespace: "ns",
			Resource:  "Exporter",
			Name:      "exp",
		},
	}
	authz := &stubAuthorizer{
		err: status.Errorf(codes.Unavailable, "backend down"),
	}

	scheme := runtime.NewScheme()
	_ = jumpstarterdevv1alpha1.AddToScheme(scheme)
	fakeClient := fake.NewClientBuilder().WithScheme(scheme).Build()

	_, err := VerifyExporterObjectToken(context.Background(), authn, authz, attrs, fakeClient)
	if err == nil {
		t.Fatal("expected error, got nil")
	}

	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %T: %v", err, err)
	}
	if st.Code() != codes.Unavailable {
		t.Errorf("expected Unavailable, got %v", st.Code())
	}
	if !strings.Contains(err.Error(), "exp") {
		t.Errorf("error should contain exporter name, got: %s", err.Error())
	}
	if !strings.Contains(err.Error(), "ns") {
		t.Errorf("error should contain namespace, got: %s", err.Error())
	}
}

func TestVerifyExporterObjectToken_GetFailurePreservesGRPCCode(t *testing.T) {
	authn := &stubAuthenticator{
		resp: &authenticator.Response{
			User: &user.DefaultInfo{Name: "test-user"},
		},
		ok: true,
	}
	attrs := &stubAttributesGetter{
		attrs: authorizer.AttributesRecord{
			User:      &user.DefaultInfo{Name: "test-user"},
			Namespace: "prod-namespace",
			Resource:  "Exporter",
			Name:      "prod-exporter",
		},
	}
	authz := &stubAuthorizer{
		decision: authorizer.DecisionAllow,
	}

	scheme := runtime.NewScheme()
	_ = jumpstarterdevv1alpha1.AddToScheme(scheme)
	fakeClient := fake.NewClientBuilder().WithScheme(scheme).Build()

	_, err := VerifyExporterObjectToken(context.Background(), authn, authz, attrs, fakeClient)
	if err == nil {
		t.Fatal("expected error (exporter not found), got nil")
	}

	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %T: %v", err, err)
	}
	if st.Code() != codes.NotFound {
		t.Errorf("expected NotFound, got %v", st.Code())
	}
	if !strings.Contains(err.Error(), "prod-exporter") {
		t.Errorf("error should contain exporter name, got: %s", err.Error())
	}
}

func TestVerifyExporterObjectToken_PermissionDeniedPreservesGRPCCode(t *testing.T) {
	authn := &stubAuthenticator{
		resp: &authenticator.Response{
			User: &user.DefaultInfo{Name: "test-user"},
		},
		ok: true,
	}
	attrs := &stubAttributesGetter{
		attrs: authorizer.AttributesRecord{
			User:      &user.DefaultInfo{Name: "test-user"},
			Namespace: "ns",
			Resource:  "Exporter",
			Name:      "exp",
		},
	}
	authz := &stubAuthorizer{
		decision: authorizer.DecisionDeny,
	}

	scheme := runtime.NewScheme()
	_ = jumpstarterdevv1alpha1.AddToScheme(scheme)
	fakeClient := fake.NewClientBuilder().WithScheme(scheme).Build()

	_, err := VerifyExporterObjectToken(context.Background(), authn, authz, attrs, fakeClient)
	if err == nil {
		t.Fatal("expected error, got nil")
	}

	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %T: %v", err, err)
	}
	if st.Code() != codes.PermissionDenied {
		t.Errorf("expected PermissionDenied, got %v", st.Code())
	}
}
