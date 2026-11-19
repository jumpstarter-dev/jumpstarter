package auth

import (
	"context"
	"crypto/ecdsa"
	"crypto/elliptic"
	"crypto/rand"
	"errors"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
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

type exporterRotationFixture struct {
	signer     *oidc.Signer
	privateKey *ecdsa.PrivateKey
	subject    string
	key        client.ObjectKey
	scheme     *runtime.Scheme
}

func newExporterRotationFixture(t *testing.T) *exporterRotationFixture {
	t.Helper()
	privateKey, err := ecdsa.GenerateKey(elliptic.P256(), rand.Reader)
	if err != nil {
		t.Fatal(err)
	}
	scheme := runtime.NewScheme()
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	return &exporterRotationFixture{
		signer: oidc.NewSigner(privateKey, "https://test", "test-audience"), privateKey: privateKey,
		subject: "exporter:default:test:uid", key: client.ObjectKey{Namespace: "default", Name: "test-exporter"}, scheme: scheme,
	}
}

func (f *exporterRotationFixture) token(t *testing.T, lifetime, remaining time.Duration) string {
	t.Helper()
	expiresAt := time.Now().Add(remaining).Truncate(time.Second)
	token, err := jwt.NewWithClaims(jwt.SigningMethodES256, jwt.RegisteredClaims{
		Issuer: f.signer.Issuer(), Audience: []string{f.signer.Audience()}, Subject: f.subject,
		IssuedAt: jwt.NewNumericDate(expiresAt.Add(-lifetime)), ExpiresAt: jwt.NewNumericDate(expiresAt),
	}).SignedString(f.privateKey)
	if err != nil {
		t.Fatal(err)
	}
	return token
}

func (f *exporterRotationFixture) secret(token string) *corev1.Secret {
	return &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Namespace: f.key.Namespace, Name: f.key.Name, Annotations: map[string]string{"unrelated": "preserved"}},
		Data:       map[string][]byte{controller.TokenKey: []byte(token), "other": []byte("preserved")},
	}
}

func TestExporterRotationWindow(t *testing.T) {
	for _, tc := range []struct {
		name      string
		remaining time.Duration
		want      codes.Code
	}{
		{name: "half lifetime", remaining: time.Hour, want: codes.OK},
		{name: "inside window", remaining: 59 * time.Minute, want: codes.OK},
		{name: "outside window", remaining: 61 * time.Minute, want: codes.PermissionDenied},
		{name: "expired", remaining: -time.Minute, want: codes.PermissionDenied},
	} {
		t.Run(tc.name, func(t *testing.T) {
			f := newExporterRotationFixture(t)
			credential := f.token(t, 2*time.Hour, tc.remaining)
			kclient := fake.NewClientBuilder().WithScheme(f.scheme).WithObjects(f.secret(credential)).Build()
			token, _, err := RotateExporterCredential(context.Background(), kclient, f.signer, f.key, f.subject, credential)
			if status.Code(err) != tc.want {
				t.Fatalf("code = %v, want %v: %v", status.Code(err), tc.want, err)
			}
			var secret corev1.Secret
			if err := kclient.Get(context.Background(), f.key, &secret); err != nil {
				t.Fatal(err)
			}
			if tc.want == codes.OK {
				if token == "" || string(secret.Data[controller.TokenKey]) != token {
					t.Fatal("rotation did not persist its token")
				}
			} else if token != "" || string(secret.Data[controller.TokenKey]) != credential {
				t.Fatal("denied rotation changed credential")
			}
		})
	}
}

func TestExporterRotationRecoversLostResponseWithoutWritingAgain(t *testing.T) {
	f := newExporterRotationFixture(t)
	credential := f.token(t, 2*time.Hour, 30*time.Minute)
	writes := 0
	kclient := fake.NewClientBuilder().WithScheme(f.scheme).WithObjects(f.secret(credential)).WithInterceptorFuncs(interceptor.Funcs{
		Patch: func(ctx context.Context, c client.WithWatch, obj client.Object, patch client.Patch, opts ...client.PatchOption) error {
			writes++
			return c.Patch(ctx, obj, patch, opts...)
		},
	}).Build()
	ctx := context.Background()
	replacement, expiry, err := RotateExporterCredential(ctx, kclient, f.signer, f.key, f.subject, credential)
	if err != nil {
		t.Fatal(err)
	}
	for range 3 {
		recovered, recoveredExpiry, err := RotateExporterCredential(ctx, kclient, f.signer, f.key, f.subject, credential)
		if err != nil {
			t.Fatal(err)
		}
		if recovered != replacement || !recoveredExpiry.AsTime().Equal(expiry.AsTime()) {
			t.Fatal("retry advanced the token lineage")
		}
	}
	if writes != 1 {
		t.Fatalf("rotation retries performed %d writes, want 1", writes)
	}
	var secret corev1.Secret
	if err := kclient.Get(ctx, f.key, &secret); err != nil {
		t.Fatal(err)
	}
	if secret.Annotations[controller.PreviousTokenHashAnnotation] != controller.TokenHash(credential) {
		t.Fatal("previous token hash was not stored")
	}
	if strings.Contains(secret.Annotations[controller.PreviousTokenHashAnnotation], credential) {
		t.Fatal("annotation contains raw credential")
	}
	if secret.Annotations["unrelated"] != "preserved" || string(secret.Data["other"]) != "preserved" {
		t.Fatal("rotation changed unrelated secret fields")
	}
	_, _, err = RotateExporterCredential(ctx, kclient, f.signer, f.key, f.subject, replacement)
	if status.Code(err) != codes.PermissionDenied || writes != 1 {
		t.Fatalf("fresh replacement could rotate immediately: %v, writes=%d", err, writes)
	}
}

func TestExporterRotationRejectsOlderLineage(t *testing.T) {
	f := newExporterRotationFixture(t)
	current, err := f.signer.Token(f.subject)
	if err != nil {
		t.Fatal(err)
	}
	previous := f.token(t, 2*time.Hour, 30*time.Minute)
	older := f.token(t, 4*time.Hour, 20*time.Minute)
	secret := f.secret(current)
	secret.Annotations[controller.PreviousTokenHashAnnotation] = controller.TokenHash(previous)
	kclient := fake.NewClientBuilder().WithScheme(f.scheme).WithObjects(secret).Build()
	token, _, err := RotateExporterCredential(context.Background(), kclient, f.signer, f.key, f.subject, older)
	if err != ErrExporterTokenLineage || token != "" {
		t.Fatalf("older token was not rejected: %v", err)
	}
	var stored corev1.Secret
	if err := kclient.Get(context.Background(), f.key, &stored); err != nil {
		t.Fatal(err)
	}
	if string(stored.Data[controller.TokenKey]) != current || stored.Annotations[controller.PreviousTokenHashAnnotation] != controller.TokenHash(previous) {
		t.Fatal("denied rotation changed lineage")
	}
}

func TestConcurrentExporterRotationsReturnOneReplacement(t *testing.T) {
	f := newExporterRotationFixture(t)
	credential := f.token(t, 2*time.Hour, 30*time.Minute)
	ready := make(chan struct{}, 2)
	release := make(chan struct{})
	var reads, writes atomic.Int32
	kclient := fake.NewClientBuilder().WithScheme(f.scheme).WithObjects(f.secret(credential)).WithInterceptorFuncs(interceptor.Funcs{
		Get: func(ctx context.Context, c client.WithWatch, key client.ObjectKey, obj client.Object, opts ...client.GetOption) error {
			err := c.Get(ctx, key, obj, opts...)
			if reads.Add(1) <= 2 {
				ready <- struct{}{}
				<-release
			}
			return err
		},
		Patch: func(ctx context.Context, c client.WithWatch, obj client.Object, patch client.Patch, opts ...client.PatchOption) error {
			err := c.Patch(ctx, obj, patch, opts...)
			if err == nil {
				writes.Add(1)
			}
			return err
		},
	}).Build()
	type result struct {
		token string
		err   error
	}
	results := make(chan result, 2)
	for range 2 {
		go func() {
			token, _, err := RotateExporterCredential(context.Background(), kclient, f.signer, f.key, f.subject, credential)
			results <- result{token, err}
		}()
	}
	defer close(release)
	for range 2 {
		select {
		case <-ready:
		case <-time.After(5 * time.Second):
			t.Fatal("concurrent readers did not reach barrier")
		}
	}
	release <- struct{}{}
	release <- struct{}{}
	first, second := <-results, <-results
	if first.err != nil || second.err != nil {
		t.Fatalf("concurrent rotation failed: %v, %v", first.err, second.err)
	}
	if first.token == "" || first.token != second.token || writes.Load() != 1 {
		t.Fatalf("concurrent rotations diverged or wrote more than once: writes=%d", writes.Load())
	}
}

func TestExporterRotationWriteFailurePreservesRetry(t *testing.T) {
	f := newExporterRotationFixture(t)
	credential := f.token(t, 2*time.Hour, 30*time.Minute)
	attempts := 0
	kclient := fake.NewClientBuilder().WithScheme(f.scheme).WithObjects(f.secret(credential)).WithInterceptorFuncs(interceptor.Funcs{
		Patch: func(ctx context.Context, c client.WithWatch, obj client.Object, patch client.Patch, opts ...client.PatchOption) error {
			attempts++
			if attempts == 1 {
				return errors.New("write failed")
			}
			return c.Patch(ctx, obj, patch, opts...)
		},
	}).Build()
	ctx := context.Background()
	token, _, err := RotateExporterCredential(ctx, kclient, f.signer, f.key, f.subject, credential)
	if token != "" || status.Code(err) != codes.Internal {
		t.Fatalf("failed write returned credential: %v", err)
	}
	var secret corev1.Secret
	if err := kclient.Get(ctx, f.key, &secret); err != nil {
		t.Fatal(err)
	}
	if string(secret.Data[controller.TokenKey]) != credential || secret.Annotations[controller.PreviousTokenHashAnnotation] != "" {
		t.Fatal("failed write changed token lineage")
	}
	token, _, err = RotateExporterCredential(ctx, kclient, f.signer, f.key, f.subject, credential)
	if err != nil || token == "" || attempts != 2 {
		t.Fatalf("retry could not rotate: %v", err)
	}
}

func TestPreviousExporterTokenCannotRecoverInvalidCredential(t *testing.T) {
	for _, stored := range []string{"malformed", "expired", "wrong subject"} {
		t.Run(stored, func(t *testing.T) {
			f := newExporterRotationFixture(t)
			credential := f.token(t, 2*time.Hour, 30*time.Minute)
			current := "bad-token"
			if stored == "expired" {
				current = f.token(t, time.Hour, -time.Minute)
			}
			if stored == "wrong subject" {
				var err error
				current, err = f.signer.Token("exporter:default:another:uid")
				if err != nil {
					t.Fatal(err)
				}
			}
			secret := f.secret(current)
			secret.Annotations[controller.PreviousTokenHashAnnotation] = controller.TokenHash(credential)
			kclient := fake.NewClientBuilder().WithScheme(f.scheme).WithObjects(secret).Build()
			token, _, err := RotateExporterCredential(context.Background(), kclient, f.signer, f.key, f.subject, credential)
			if token != "" || status.Code(err) != codes.FailedPrecondition {
				t.Fatalf("recovered invalid stored token: %v", err)
			}
		})
	}
}
