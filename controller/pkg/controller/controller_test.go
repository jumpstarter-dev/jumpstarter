package controller

import (
	"context"
	"testing"

	authv1 "k8s.io/api/authentication/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/kubernetes"
	apicorev1 "k8s.io/client-go/kubernetes/typed/core/v1"
	"sigs.k8s.io/controller-runtime/pkg/envtest"
)

func createServiceAccount(
	t *testing.T,
	client apicorev1.ServiceAccountInterface,
	name string,
) *corev1.ServiceAccount {
	sa, err := client.Create(
		context.TODO(),
		&corev1.ServiceAccount{
			ObjectMeta: metav1.ObjectMeta{
				Name: name,
			},
		},
		metav1.CreateOptions{},
	)
	if err != nil {
		t.Fatalf("failed to create exporter service account: %s", err)
	}
	return sa
}

func createToken(
	t *testing.T,
	client apicorev1.ServiceAccountInterface,
	sa *corev1.ServiceAccount,
	audience string,
	expiration int64) string {
	token, err := client.CreateToken(
		context.TODO(),
		sa.GetName(),
		&authv1.TokenRequest{
			Spec: authv1.TokenRequestSpec{
				Audiences:         []string{audience},
				ExpirationSeconds: &expiration,
			},
		},
		metav1.CreateOptions{},
	)
	if err != nil {
		t.Fatalf("failed to create service account token: %s", err)
	}
	return token.Status.Token
}

func TestController(t *testing.T) {
	env := &envtest.Environment{}

	cfg, err := env.Start()
	if err != nil {
		t.Fatalf("failed to start envtest: %s", err)
	}

	clientset, err := kubernetes.NewForConfig(cfg)
	if err != nil {
		t.Fatalf("failed to create k8s client: %s", err)
	}

	saclient := clientset.CoreV1().ServiceAccounts(corev1.NamespaceDefault)

	exporterServiceAccount := createServiceAccount(t, saclient, "exporter-01")

	t.Logf("%+v\n", exporterServiceAccount)

	exporterToken := createToken(t, saclient, exporterServiceAccount, "controller", 3600)

	review, err := clientset.AuthenticationV1().TokenReviews().Create(
		context.TODO(),
		&authv1.TokenReview{
			Spec: authv1.TokenReviewSpec{
				Token:     exporterToken,
				Audiences: []string{"controller"},
			},
		},
		metav1.CreateOptions{},
	)
	if err != nil {
		t.Fatalf("failed to create TokenReview: %s", err)
	}

	t.Logf("%+v\n", review.Status)

	env.Stop()
}
