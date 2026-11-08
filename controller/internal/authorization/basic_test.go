package authorization

import (
	"context"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authentication"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestBasicAuthorizer(t *testing.T) {
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Namespace: "default", Name: "my-exporter", UID: "exporter-uid"},
		Spec:       jumpstarterdevv1alpha1.ExporterSpec{Username: new("dex:exporter-sa")},
	}
	jclient := &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{Namespace: "default", Name: "my-client", UID: "client-uid"},
		Spec:       jumpstarterdevv1alpha1.ClientSpec{Username: new("dex:alice")},
	}
	internal := func(name string) user.Info {
		return authentication.NewInternalUser(&user.DefaultInfo{Name: name})
	}
	external := func(name string) user.Info {
		return &user.DefaultInfo{Name: name}
	}

	testcases := []struct {
		name     string
		user     user.Info
		resource string
		object   string
		want     authorizer.Decision
	}{
		{
			name:     "internal token for the exporter's subject",
			user:     internal("internal:" + exporter.InternalSubject()),
			resource: "Exporter",
			object:   "my-exporter",
			want:     authorizer.DecisionAllow,
		},
		{
			name:     "external token for the exporter's username",
			user:     external("dex:exporter-sa"),
			resource: "Exporter",
			object:   "my-exporter",
			want:     authorizer.DecisionAllow,
		},
		{
			name:     "internal token for the client's subject",
			user:     internal("internal:" + jclient.InternalSubject()),
			resource: "Client",
			object:   "my-client",
			want:     authorizer.DecisionAllow,
		},
		{
			name:     "external token for the client's username",
			user:     external("dex:alice"),
			resource: "Client",
			object:   "my-client",
			want:     authorizer.DecisionAllow,
		},
		{
			name:     "external token named like the exporter's subject",
			user:     external("internal:" + exporter.InternalSubject()),
			resource: "Exporter",
			object:   "my-exporter",
			want:     authorizer.DecisionDeny,
		},
		{
			name:     "external token named like the client's subject",
			user:     external("internal:" + jclient.InternalSubject()),
			resource: "Client",
			object:   "my-client",
			want:     authorizer.DecisionDeny,
		},
		{
			name:     "internal token named like the client's username",
			user:     internal("dex:alice"),
			resource: "Client",
			object:   "my-client",
			want:     authorizer.DecisionDeny,
		},
	}

	scheme := runtime.NewScheme()
	if err := jumpstarterdevv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	for _, tc := range testcases {
		t.Run(tc.name, func(t *testing.T) {
			kclient := fake.NewClientBuilder().WithScheme(scheme).WithObjects(exporter.DeepCopy(), jclient.DeepCopy()).Build()
			authz := NewBasicAuthorizer(kclient, "internal:", false)
			got, _, err := authz.Authorize(context.Background(), authorizer.AttributesRecord{
				User:      tc.user,
				Namespace: "default",
				Resource:  tc.resource,
				Name:      tc.object,
			})
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if got != tc.want {
				t.Errorf("got %v, want %v", got, tc.want)
			}
		})
	}
}

func TestBasicAuthorizer_Provisioning(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := jumpstarterdevv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	authorize := func(kclient client.Client, u user.Info) (authorizer.Decision, error) {
		decision, _, err := NewBasicAuthorizer(kclient, "internal:", true).Authorize(context.Background(), authorizer.AttributesRecord{
			User:      u,
			Namespace: "default",
			Resource:  "Client",
			Name:      "my-client",
		})
		return decision, err
	}

	t.Run("external user is provisioned", func(t *testing.T) {
		kclient := fake.NewClientBuilder().WithScheme(scheme).Build()
		decision, err := authorize(kclient, &user.DefaultInfo{Name: "dex:my-client"})
		if err != nil || decision != authorizer.DecisionAllow {
			t.Fatalf("got %v %v, want allow", decision, err)
		}
		var provisioned jumpstarterdevv1alpha1.Client
		if err := kclient.Get(context.Background(), client.ObjectKey{Namespace: "default", Name: "my-client"}, &provisioned); err != nil {
			t.Fatal(err)
		}
		if provisioned.Spec.Username == nil || *provisioned.Spec.Username != "dex:my-client" {
			t.Errorf("provisioned username %v, want dex:my-client", provisioned.Spec.Username)
		}
	})

	// Deleting a Client revokes its internal tokens; provisioning must not
	// bring the Client back for them.
	t.Run("internal token of a deleted client is not provisioned", func(t *testing.T) {
		kclient := fake.NewClientBuilder().WithScheme(scheme).Build()
		deleted := &jumpstarterdevv1alpha1.Client{
			ObjectMeta: metav1.ObjectMeta{Namespace: "default", Name: "my-client", UID: "deleted-uid"},
		}
		decision, _ := authorize(kclient, authentication.NewInternalUser(&user.DefaultInfo{Name: "internal:" + deleted.InternalSubject()}))
		if decision == authorizer.DecisionAllow {
			t.Errorf("got allow, want deny")
		}
		err := kclient.Get(context.Background(), client.ObjectKey{Namespace: "default", Name: "my-client"}, &jumpstarterdevv1alpha1.Client{})
		if !apierrors.IsNotFound(err) {
			t.Errorf("client was provisioned for an internal token: %v", err)
		}
	})
}
