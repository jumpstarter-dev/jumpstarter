package config

import (
	"fmt"
	"testing"

	api "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	apiserverinstall "k8s.io/apiserver/pkg/apis/apiserver/install"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestStoredTokenFallbackDefault(t *testing.T) {
	scheme := runtime.NewScheme()
	apiserverinstall.Install(scheme)
	if err := api.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	signer, err := oidc.NewSignerFromSeed([]byte("config-test"), "https://127.0.0.1:1", "jumpstarter")
	if err != nil {
		t.Fatal(err)
	}
	for _, format := range []string{"config", "authentication"} {
		for _, setting := range []string{"", "allowStoredTokenFallback: false", "allowStoredTokenFallback: true"} {
			t.Run(format+"/"+setting, func(t *testing.T) {
				ctx := t.Context()
				raw := fmt.Sprintf("authentication:\n  internal:\n    prefix: 'internal:'\n    %s\n", setting)
				if format == "authentication" {
					raw = fmt.Sprintf("apiVersion: %s\nkind: AuthenticationConfiguration\ninternal:\n  prefix: 'internal:'\n  %s\n", api.GroupVersion, setting)
				}
				cm := &corev1.ConfigMap{ObjectMeta: metav1.ObjectMeta{Name: "controller", Namespace: "lab"},
					Data: map[string]string{"router": "{}", format: raw}}
				kc := fake.NewClientBuilder().WithScheme(scheme).WithObjects(cm).Build()
				_, _, _, _, _, _, _, _, allowed, err := LoadConfiguration(ctx, kc, scheme, client.ObjectKeyFromObject(cm), signer, "")
				if err != nil {
					t.Fatal(err)
				}
				if allowed != (setting != "allowStoredTokenFallback: false") {
					t.Fatal("wrong stored-token fallback default or explicit override")
				}
			})
		}
	}
}
