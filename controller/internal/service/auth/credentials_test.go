package auth

import (
	"context"
	"errors"
	"testing"
	"time"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/controller"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/client/interceptor"
)

func TestRotateCredential(t *testing.T) {
	for _, tc := range []struct {
		name                                                  string
		missingSigner, missingSecret, emptyData, patchFailure bool
		want                                                  codes.Code
	}{
		{name: "rotates and preserves other data", want: codes.OK},
		{name: "initializes secret data", emptyData: true, want: codes.OK},
		{name: "missing signer", missingSigner: true, want: codes.FailedPrecondition},
		{name: "missing secret", missingSecret: true, want: codes.Internal},
		{name: "patch failure", patchFailure: true, want: codes.Internal},
	} {
		t.Run(tc.name, func(t *testing.T) {
			ctx := context.Background()
			scheme := runtime.NewScheme()
			if err := corev1.AddToScheme(scheme); err != nil {
				t.Fatal(err)
			}
			key := client.ObjectKey{Namespace: "default", Name: "credential"}
			secret := &corev1.Secret{ObjectMeta: metav1.ObjectMeta{Namespace: key.Namespace, Name: key.Name}}
			if !tc.emptyData {
				secret.Data = map[string][]byte{controller.TokenKey: []byte("old-token"), "other": []byte("preserved")}
			}
			builder := fake.NewClientBuilder().WithScheme(scheme)
			if !tc.missingSecret {
				builder.WithObjects(secret)
			}
			if tc.patchFailure {
				builder.WithInterceptorFuncs(interceptor.Funcs{Patch: func(context.Context, client.WithWatch, client.Object, client.Patch, ...client.PatchOption) error {
					return errors.New("patch failed")
				}})
			}
			kclient := builder.Build()
			signer, err := oidc.NewSignerFromSeed([]byte("test-seed"), "https://test", "test-audience")
			if err != nil {
				t.Fatal(err)
			}
			signer.SetTokenLifetime(time.Hour)
			if tc.missingSigner {
				signer = nil
			}
			token, expiry, err := RotateCredential(ctx, kclient, signer, key, "exporter:default:test:uid")
			if status.Code(err) != tc.want {
				t.Fatalf("code = %v, want %v: %v", status.Code(err), tc.want, err)
			}
			if tc.want != codes.OK {
				if token != "" || expiry != nil {
					t.Fatal("failed rotation returned credentials")
				}
				if !tc.missingSecret {
					var stored corev1.Secret
					if err := kclient.Get(ctx, key, &stored); err != nil {
						t.Fatal(err)
					}
					if string(stored.Data[controller.TokenKey]) != "old-token" {
						t.Fatal("failed rotation changed stored token")
					}
				}
				return
			}
			if token == "" || expiry == nil {
				t.Fatal("rotation returned no token or expiry")
			}
			subject, err := signer.ParseSubject(token)
			if err != nil || subject != "exporter:default:test:uid" {
				t.Fatalf("subject = %q, err = %v", subject, err)
			}
			parsedExpiry, err := signer.TokenExpiry(token)
			if err != nil || !parsedExpiry.Equal(expiry.AsTime()) {
				t.Fatalf("expiry mismatch: %v", err)
			}
			var stored corev1.Secret
			if err := kclient.Get(ctx, key, &stored); err != nil {
				t.Fatal(err)
			}
			if string(stored.Data[controller.TokenKey]) != token {
				t.Fatal("new token was not stored")
			}
			if !tc.emptyData && string(stored.Data["other"]) != "preserved" {
				t.Fatal("rotation changed unrelated secret data")
			}
		})
	}
}
