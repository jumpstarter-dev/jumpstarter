package authorization

import (
	"context"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func newFakeClient(t *testing.T) client.Client {
	t.Helper()
	scheme := runtime.NewScheme()
	if err := jumpstarterdevv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	return fake.NewClientBuilder().WithScheme(scheme).Build()
}

func clientAttributes(username, name string) authorizer.Attributes {
	return authorizer.AttributesRecord{
		User:      &user.DefaultInfo{Name: username},
		Namespace: "lab",
		Resource:  "Client",
		Name:      name,
	}
}

func TestBasicAuthorizer_ProvisionsTheNameDerivedFromTheUsername(t *testing.T) {
	kc := newFakeClient(t)

	decision, _, err := NewBasicAuthorizer(kc, "internal:", true).
		Authorize(context.Background(), clientAttributes("keycloak:alice", "alice"))
	if err != nil || decision != authorizer.DecisionAllow {
		t.Fatalf("decision=%v err=%v", decision, err)
	}

	var provisioned jumpstarterdevv1alpha1.Client
	if err := kc.Get(context.Background(), client.ObjectKey{Namespace: "lab", Name: "alice"}, &provisioned); err != nil {
		t.Fatal(err)
	}
	if *provisioned.Spec.Username != "keycloak:alice" {
		t.Errorf("provisioned for %q", *provisioned.Spec.Username)
	}
}

func TestBasicAuthorizer_DoesNotProvisionOtherNames(t *testing.T) {
	for _, username := range []string{"keycloak:alice", "keycloak:system:serviceaccount:x:y", "internal:mallory"} {
		t.Run(username, func(t *testing.T) {
			kc := newFakeClient(t)

			decision, _, err := NewBasicAuthorizer(kc, "internal:", true).
				Authorize(context.Background(), clientAttributes(username, "ci-bot"))
			if decision != authorizer.DecisionDeny || !apierrors.IsNotFound(err) {
				t.Errorf("decision=%v err=%v", decision, err)
			}

			var squatted jumpstarterdevv1alpha1.Client
			err = kc.Get(context.Background(), client.ObjectKey{Namespace: "lab", Name: "ci-bot"}, &squatted)
			if !apierrors.IsNotFound(err) {
				t.Errorf("provisioned ci-bot for %s", username)
			}
		})
	}
}
