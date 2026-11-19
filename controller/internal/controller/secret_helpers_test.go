package controller

import (
	"context"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/client/interceptor"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
)

// A Secret write (such as RotateToken) reaches the informer cache after a delay;
// reconciles that read the older copy in between must neither fail nor clobber it.
func TestExporterReconcileWithStaleSecretCache(t *testing.T) {
	for _, tc := range []struct {
		name        string
		cachedToken string
		wantPatches int
		wantResult  ctrl.Result
	}{
		{name: "unchanged credential", wantResult: ctrl.Result{RequeueAfter: tokenExpiryRequeueInterval}},
		{name: "regeneration from stale token", cachedToken: "invalid", wantPatches: 1, wantResult: ctrl.Result{Requeue: true}}, //nolint:staticcheck
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx := context.Background()
			scheme := runtime.NewScheme()
			if err := corev1.AddToScheme(scheme); err != nil {
				t.Fatal(err)
			}
			if err := jumpstarterdevv1alpha1.AddToScheme(scheme); err != nil {
				t.Fatal(err)
			}
			exporter := &jumpstarterdevv1alpha1.Exporter{ObjectMeta: metav1.ObjectMeta{Name: "e1", Namespace: "default", UID: "uid"}}
			var cached *corev1.Secret
			secretPatches := 0
			kclient := fake.NewClientBuilder().WithScheme(scheme).WithStatusSubresource(exporter).WithObjects(exporter).
				WithInterceptorFuncs(interceptor.Funcs{
					Get: func(ctx context.Context, c client.WithWatch, key client.ObjectKey, obj client.Object, opts ...client.GetOption) error {
						if secret, ok := obj.(*corev1.Secret); ok && cached != nil {
							cached.DeepCopyInto(secret)
							return nil
						}
						return c.Get(ctx, key, obj, opts...)
					},
					Patch: func(ctx context.Context, c client.WithWatch, obj client.Object, patch client.Patch, opts ...client.PatchOption) error {
						if _, ok := obj.(*corev1.Secret); ok {
							secretPatches++
						}
						return c.Patch(ctx, obj, patch, opts...)
					},
				}).Build()
			signer, err := oidc.NewSignerFromSeed([]byte{}, "https://example.com", "dummy")
			if err != nil {
				t.Fatal(err)
			}
			reconciler := &ExporterReconciler{Client: kclient, Scheme: scheme, Signer: signer}
			request := reconcile.Request{NamespacedName: client.ObjectKeyFromObject(exporter)}
			if _, err := reconciler.Reconcile(ctx, request); err != nil {
				t.Fatal(err)
			}

			key := client.ObjectKey{Namespace: "default", Name: exporter.CredentialSecretName()}
			var secret corev1.Secret
			if err := kclient.Get(ctx, key, &secret); err != nil {
				t.Fatal(err)
			}
			cached = secret.DeepCopy()
			if tc.cachedToken != "" {
				cached.Data[TokenKey] = []byte(tc.cachedToken)
			}
			rotated, err := signer.Token(exporter.InternalSubject())
			if err != nil {
				t.Fatal(err)
			}
			secret.Data[TokenKey] = []byte(rotated)
			if err := kclient.Update(ctx, &secret); err != nil {
				t.Fatal(err)
			}
			secretPatches = 0

			result, err := reconciler.Reconcile(ctx, request)
			if err != nil {
				t.Fatalf("Reconcile() error = %v", err)
			}
			if result != tc.wantResult {
				t.Fatalf("Reconcile() result = %+v, want %+v", result, tc.wantResult)
			}
			if secretPatches != tc.wantPatches {
				t.Fatalf("secret patches = %d, want %d", secretPatches, tc.wantPatches)
			}
			cached = nil
			if err := kclient.Get(ctx, key, &secret); err != nil {
				t.Fatal(err)
			}
			if got := string(secret.Data[TokenKey]); got != rotated {
				t.Fatalf("stored token = %q, want the rotated token", got)
			}
		})
	}
}
