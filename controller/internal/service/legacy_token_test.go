package service

import (
	"context"
	"errors"
	"testing"
	"time"

	api "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authorization"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/controller"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/grpc/metadata"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apiserver/pkg/authentication/authenticator"
	"k8s.io/apiserver/pkg/authentication/user"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestLegacyExporterTokenMigration(t *testing.T) {
	ctx := context.Background()
	scheme := runtime.NewScheme()
	if err := api.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	exporter := &api.Exporter{ObjectMeta: metav1.ObjectMeta{Name: "board", Namespace: "lab", UID: "uid"}}
	newSigner, err := oidc.NewSignerFromSeed([]byte("new"), "https://localhost:8085", "jumpstarter")
	if err != nil {
		t.Fatal(err)
	}
	oldSigner, err := oidc.NewSignerFromSeed([]byte("old"), newSigner.Issuer(), newSigner.Audience())
	if err != nil {
		t.Fatal(err)
	}
	oldToken, err := oldSigner.Token(exporter.InternalSubject())
	if err != nil {
		t.Fatal(err)
	}
	newToken, err := newSigner.Token(exporter.InternalSubject())
	if err != nil {
		t.Fatal(err)
	}
	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: exporter.CredentialSecretName(), Namespace: exporter.Namespace,
			Annotations: map[string]string{authentication.LegacyTokenHashAnnotation: controller.TokenHash(oldToken)}},
		Data: map[string][]byte{controller.TokenKey: []byte(newToken)},
	}
	kc := fake.NewClientBuilder().WithScheme(scheme).WithObjects(exporter, secret).Build()
	primary := authenticator.TokenFunc(func(_ context.Context, token string) (*authenticator.Response, bool, error) {
		subject, err := newSigner.ParseSubject(token)
		if err != nil {
			return nil, false, err
		}
		return &authenticator.Response{User: &user.DefaultInfo{Name: "internal:" + subject}}, true, nil
	})
	fallback := &authentication.StoredTokenAuthenticator{Primary: primary, Cache: kc, Reader: kc, Namespace: "lab", Issuer: newSigner.Issuer(), Audience: newSigner.Audience(), Prefix: "internal:"}
	svc := &ControllerService{
		Client: kc, Signer: newSigner, LegacyTokens: fallback,
		Authn: authentication.NewBearerTokenAuthenticator(fallback),
		Authz: authorization.NewBasicAuthorizer(kc, "internal:", false),
		Attr: authorization.NewMetadataAttributesGetter(authorization.MetadataAttributesGetterConfig{
			NamespaceKey: "jumpstarter-namespace", ResourceKey: "jumpstarter-kind", NameKey: "jumpstarter-name",
		}),
	}
	requestContext := func(token string) context.Context {
		return metadata.NewIncomingContext(ctx, metadata.Pairs("authorization", "Bearer "+token,
			"jumpstarter-namespace", "lab", "jumpstarter-kind", "Exporter", "jumpstarter-name", "board"))
	}
	// Migration is allowed even though the old token is outside the renewal window;
	// retries return the same replacement, rather than advancing the lineage.
	for range 2 {
		response, err := svc.RotateToken(requestContext(oldToken), &pb.RotateTokenRequest{})
		if err != nil || response.Token != newToken || response.Expiry == nil {
			t.Fatalf("migration response=%v err=%v", response, err)
		}
	}
	forged, err := oldSigner.Token(exporter.InternalSubject())
	if err != nil {
		t.Fatal(err)
	}
	if _, err := svc.RotateToken(requestContext(forged), &pb.RotateTokenRequest{}); err == nil {
		t.Fatal("re-signed old-key token accepted")
	}
	// A stale Secret cannot authorize a replacement resource with the same name.
	if err := kc.Delete(ctx, exporter); err != nil {
		t.Fatal(err)
	}
	exporter.UID = "replacement-uid"
	exporter.ResourceVersion = ""
	if err := kc.Create(ctx, exporter); err != nil {
		t.Fatal(err)
	}
	if _, err := svc.RotateToken(requestContext(oldToken), &pb.RotateTokenRequest{}); err == nil {
		t.Fatalf("recreated exporter: %v", err)
	}
	// Disabling the fallback leaves normal signature-based authentication working.
	strict := &ControllerService{Client: kc, Signer: newSigner, Authn: authentication.NewBearerTokenAuthenticator(primary), Authz: svc.Authz, Attr: svc.Attr}
	if _, err := strict.authenticateExporter(requestContext(oldToken)); err == nil {
		t.Fatal("disabled fallback accepted old token")
	}
	if _, ok, err := primary.AuthenticateToken(ctx, newToken); !ok || err != nil {
		t.Fatalf("new token rejected: %v", err)
	}
	if err := kc.Get(ctx, client.ObjectKeyFromObject(secret), secret); err != nil {
		t.Fatal(err)
	}
	if string(secret.Data[controller.TokenKey]) != newToken {
		t.Fatal("migration mutated the replacement")
	}
}

func TestStatusStreamAdvisesTokenRotation(t *testing.T) {
	svc, kc := leaseHooksService(t, api.ExporterStatus{})
	ctx, cancel := context.WithTimeout(exporterRPCContext(context.Background()), 5*time.Second)
	defer cancel()
	stream := &capturingStatusStream{ctx: ctx, sent: make(chan *pb.StatusResponse, 4)}
	done := make(chan error, 1)
	go func() { done <- svc.Status(&pb.StatusRequest{}, stream) }()
	for _, advised := range []bool{false, true, false} {
		select {
		case response := <-stream.sent:
			if response.TokenRotationAdvised != advised {
				t.Fatalf("token_rotation_advised=%t, want %t", response.TokenRotationAdvised, advised)
			}
		case err := <-done:
			t.Fatalf("Status ended before advisory: %v", err)
		case <-ctx.Done():
			t.Fatal("Status did not send the advisory update")
		}
		exporter := &api.Exporter{}
		if err := kc.Get(ctx, client.ObjectKey{Namespace: "default", Name: "test-exporter"}, exporter); err != nil {
			t.Fatal(err)
		}
		original := client.MergeFrom(exporter.DeepCopy())
		conditionStatus := metav1.ConditionTrue
		if advised {
			conditionStatus = metav1.ConditionFalse
		}
		meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
			Type:   string(api.ExporterConditionTypeCredentialUpdateRequired),
			Status: conditionStatus, Reason: "Migration", Message: "Signing key migration",
		})
		if err := kc.Status().Patch(ctx, exporter, original); err != nil {
			t.Fatal(err)
		}
	}
	cancel()
	if err := <-done; err != nil && !errors.Is(err, context.Canceled) {
		t.Fatalf("Status shutdown: %v", err)
	}
}
