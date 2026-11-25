package authorization

import (
	"context"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"google.golang.org/grpc/metadata"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func newFakeClient(t *testing.T, objects ...client.Object) client.Client {
	t.Helper()
	scheme := runtime.NewScheme()
	if err := jumpstarterdevv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	return fake.NewClientBuilder().WithScheme(scheme).WithObjects(objects...).Build()
}

// authorizeClient sends a request for the Client called name through both
// layers the controller uses: ContextAttributes, then BasicAuthorizer with
// auto-provisioning enabled.
func authorizeClient(t *testing.T, kc client.Client, username, name string) authorizer.Decision {
	t.Helper()
	ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs(
		"jumpstarter-namespace", "lab", "jumpstarter-kind", "Client", "jumpstarter-name", name))
	attributes, err := NewMetadataAttributesGetter(MetadataAttributesGetterConfig{
		NamespaceKey: "jumpstarter-namespace", ResourceKey: "jumpstarter-kind", NameKey: "jumpstarter-name",
	}).ContextAttributes(ctx, &user.DefaultInfo{Name: username})
	if err != nil {
		return authorizer.DecisionDeny
	}
	decision, _, _ := NewBasicAuthorizer(kc, "internal:", true).Authorize(ctx, attributes)
	return decision
}

func getClient(t *testing.T, kc client.Client, name string) (*jumpstarterdevv1alpha1.Client, bool) {
	t.Helper()
	var c jumpstarterdevv1alpha1.Client
	err := kc.Get(context.Background(), client.ObjectKey{Namespace: "lab", Name: name}, &c)
	if apierrors.IsNotFound(err) {
		return nil, false
	}
	if err != nil {
		t.Fatal(err)
	}
	return &c, true
}

func TestBasicAuthorizer_ProvisionsTheNameDerivedFromTheUsername(t *testing.T) {
	kc := newFakeClient(t)

	if decision := authorizeClient(t, kc, "keycloak:alice", "alice"); decision != authorizer.DecisionAllow {
		t.Fatalf("decision=%v", decision)
	}

	provisioned, ok := getClient(t, kc, "alice")
	if !ok {
		t.Fatal("no Client named alice was provisioned")
	}
	if *provisioned.Spec.Username != "keycloak:alice" {
		t.Errorf("provisioned for %q", *provisioned.Spec.Username)
	}
}

func TestBasicAuthorizer_DoesNotProvisionOtherNames(t *testing.T) {
	for _, username := range []string{"keycloak:alice", "keycloak:system:serviceaccount:x:y", "internal:mallory"} {
		t.Run(username, func(t *testing.T) {
			kc := newFakeClient(t)

			if decision := authorizeClient(t, kc, username, "ci-bot"); decision == authorizer.DecisionAllow {
				t.Errorf("allowed ci-bot for %s", username)
			}
			if _, ok := getClient(t, kc, "ci-bot"); ok {
				t.Errorf("provisioned ci-bot for %s", username)
			}
		})
	}
}

func TestBasicAuthorizer_DoesNotProvisionForInternalOrServiceAccountUsernames(t *testing.T) {
	for _, tc := range []struct{ username, derivedName string }{
		{"internal:alice", "alice"},
		{"keycloak:system:serviceaccount:x:y", "x-y"},
	} {
		t.Run(tc.username, func(t *testing.T) {
			kc := newFakeClient(t)

			if decision := authorizeClient(t, kc, tc.username, tc.derivedName); decision == authorizer.DecisionAllow {
				t.Errorf("allowed %s for %s", tc.derivedName, tc.username)
			}
			if _, ok := getClient(t, kc, tc.derivedName); ok {
				t.Errorf("provisioned %s for %s", tc.derivedName, tc.username)
			}
		})
	}
}

func TestPreCreatedObjectIsUsableUnderItsOwnName(t *testing.T) {
	username := "keycloak:developer-1"
	getter := NewMetadataAttributesGetter(MetadataAttributesGetterConfig{
		NamespaceKey: "jumpstarter-namespace", ResourceKey: "jumpstarter-kind", NameKey: "jumpstarter-name",
	})
	for _, tc := range []struct {
		kind string
		obj  client.Object
	}{
		{"Client", &jumpstarterdevv1alpha1.Client{
			ObjectMeta: metav1.ObjectMeta{Namespace: "lab", Name: "test-client"},
			Spec:       jumpstarterdevv1alpha1.ClientSpec{Username: &username},
		}},
		{"Exporter", &jumpstarterdevv1alpha1.Exporter{
			ObjectMeta: metav1.ObjectMeta{Namespace: "lab", Name: "test-exporter"},
			Spec:       jumpstarterdevv1alpha1.ExporterSpec{Username: &username},
		}},
	} {
		t.Run(tc.kind, func(t *testing.T) {
			ctx := metadata.NewIncomingContext(context.Background(), metadata.Pairs(
				"jumpstarter-namespace", "lab", "jumpstarter-kind", tc.kind, "jumpstarter-name", tc.obj.GetName()))

			attrs, err := getter.ContextAttributes(ctx, &user.DefaultInfo{Name: username})
			if err != nil {
				t.Fatal(err)
			}
			decision, _, err := NewBasicAuthorizer(newFakeClient(t, tc.obj), "internal:", false).Authorize(ctx, attrs)
			if err != nil || decision != authorizer.DecisionAllow || attrs.GetName() != tc.obj.GetName() {
				t.Errorf("decision=%v name=%q err=%v", decision, attrs.GetName(), err)
			}
		})
	}
}
