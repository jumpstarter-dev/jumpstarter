package controller

import (
	"context"
	"crypto/elliptic"
	"crypto/sha256"
	"encoding/binary"
	"errors"
	"fmt"
	"math/rand"
	"testing"

	"filippo.io/keygen"
	api "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/client/interceptor"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
)

//nolint:gocyclo // Exercise the migration lifecycle for internal/external exporters and clients in one matrix.
func TestSigningKeyMigration(t *testing.T) {
	ctx := context.Background()
	seed := []byte("migration-test-key")
	hash := sha256.Sum256(seed)
	key, err := keygen.ECDSALegacy(elliptic.P256(), rand.New(rand.NewSource(int64(binary.BigEndian.Uint64(hash[:8])))))
	if err != nil {
		t.Fatal(err)
	}
	legacy := oidc.NewSigner(key, "https://localhost:8085", "jumpstarter")
	updated, err := oidc.NewSignerFromSeed(seed, legacy.Issuer(), legacy.Audience())
	if err != nil {
		t.Fatal(err)
	}
	for _, kind := range []string{"client", "exporter", "external-exporter"} {
		for _, disabled := range []bool{false, true} {
			t.Run(fmt.Sprintf("%s/disabled=%t", kind, disabled), func(t *testing.T) {
				kind := kind
				external := kind == "external-exporter"
				if external {
					kind = "exporter"
				}
				scheme := runtime.NewScheme()
				if err := errors.Join(corev1.AddToScheme(scheme), api.AddToScheme(scheme)); err != nil {
					t.Fatal(err)
				}
				objectMeta := metav1.ObjectMeta{Name: "device", Namespace: "lab", UID: "uid"}
				var owner client.Object = &api.Client{ObjectMeta: objectMeta}
				if kind == "exporter" {
					owner = &api.Exporter{ObjectMeta: objectMeta}
					if external {
						username := "external:user"
						exporter := owner.(*api.Exporter)
						exporter.Spec.Username = &username
						meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
							Type: string(api.ExporterConditionTypeCredentialUpdateRequired), Status: metav1.ConditionTrue,
							Reason: "SigningKeyChanged", Message: "stale migration condition",
						})
					}
				}
				subject := kind + ":lab:device:uid"
				oldToken, err := legacy.Token(subject)
				if err != nil {
					t.Fatal(err)
				}
				secretKey := client.ObjectKey{Name: "device-" + kind, Namespace: "lab"}
				secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Name: secretKey.Name, Namespace: secretKey.Namespace}, Data: map[string][]byte{TokenKey: []byte(oldToken)}}
				failNextStatusPatch := false
				kc := fake.NewClientBuilder().WithScheme(scheme).WithStatusSubresource(owner).WithObjects(owner, secret).
					WithInterceptorFuncs(interceptor.Funcs{
						SubResourcePatch: func(ctx context.Context, c client.Client, subResource string, obj client.Object,
							patch client.Patch, opts ...client.SubResourcePatchOption) error {
							if failNextStatusPatch {
								failNextStatusPatch = false
								return errors.New("status patch failed")
							}
							return c.SubResource(subResource).Patch(ctx, obj, patch, opts...)
						},
					}).Build()
				var reconciler reconcile.Reconciler = &ClientReconciler{Client: kc, Scheme: scheme, Signer: updated, DisableLegacyTokenAuthentication: disabled}
				if kind == "exporter" {
					reconciler = &ExporterReconciler{Client: kc, Scheme: scheme, Signer: updated, DisableLegacyTokenAuthentication: disabled}
				}
				request := reconcile.Request{NamespacedName: client.ObjectKeyFromObject(owner)}
				if _, err := reconciler.Reconcile(ctx, request); err != nil {
					t.Fatal(err)
				}
				if err := kc.Get(ctx, secretKey, secret); err != nil {
					t.Fatal(err)
				}
				replacement := string(secret.Data[TokenKey])
				if err := updated.Validate(replacement); err != nil {
					t.Fatal(err)
				}
				if (secret.Annotations[authentication.LegacyTokenHashAnnotation] == TokenHash(oldToken)) == disabled {
					t.Fatal("wrong retained credential")
				}
				fallback := &authentication.StoredTokenAuthenticator{Cache: kc, Reader: kc, Namespace: "lab", Issuer: updated.Issuer(), Audience: updated.Audience()}
				matched, _, err := fallback.Match(ctx, oldToken)
				if err != nil || (matched != nil) == disabled {
					t.Fatalf("migration match=%v error=%v", matched != nil, err)
				}
				// A restart/no-op reconcile must not overwrite or drop the retained hash.
				if _, err := reconciler.Reconcile(ctx, request); err != nil {
					t.Fatal(err)
				}
				if err := kc.Get(ctx, secretKey, secret); err != nil {
					t.Fatal(err)
				}
				if string(secret.Data[TokenKey]) != replacement {
					t.Fatal("replacement changed on restart")
				}
				if kind == "exporter" {
					exporter := &api.Exporter{}
					if err := kc.Get(ctx, request.NamespacedName, exporter); err != nil {
						t.Fatal(err)
					}
					if external {
						if secret.Annotations[credentialUpdateAnnotation] != "" ||
							meta.FindStatusCondition(exporter.Status.Conditions, string(api.ExporterConditionTypeCredentialUpdateRequired)) != nil {
							t.Fatal("external OIDC exporter has a migration advisory")
						}
						return
					}
					if !meta.IsStatusConditionTrue(exporter.Status.Conditions, string(api.ExporterConditionTypeCredentialUpdateRequired)) {
						t.Fatal("missing migration condition")
					}
					exporter.Annotations = map[string]string{AuthenticatedTokenHashAnnotation: TokenHash(replacement)}
					if err := kc.Update(ctx, exporter); err != nil {
						t.Fatal(err)
					}
					// Revoking the hash succeeds before the status update. A retry
					// must still clear the condition after a failed status patch.
					failNextStatusPatch = true
					if _, err := reconciler.Reconcile(ctx, request); err == nil {
						t.Fatal("expected status patch failure")
					}
					if _, err := reconciler.Reconcile(ctx, request); err != nil {
						t.Fatal(err)
					}
					if err := kc.Get(ctx, request.NamespacedName, exporter); err != nil {
						t.Fatal(err)
					}
					if !meta.IsStatusConditionFalse(exporter.Status.Conditions, string(api.ExporterConditionTypeCredentialUpdateRequired)) {
						t.Fatal("migration condition not cleared")
					}
				} else {
					reconciler.(*ClientReconciler).DisableLegacyTokenAuthentication = true
					if _, err := reconciler.Reconcile(ctx, request); err != nil {
						t.Fatal(err)
					}
				}
				matched, _, err = fallback.Match(ctx, oldToken)
				if err != nil || matched != nil {
					t.Fatalf("legacy credential still accepted: %v", err)
				}
			})
		}
	}
}
