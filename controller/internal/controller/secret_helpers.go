package controller

import (
	"context"

	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

const TokenKey string = "token"

func ensureSecret(
	ctx context.Context,
	key client.ObjectKey,
	kclient client.Client,
	signer *oidc.Signer,
	username string,
) (*corev1.Secret, error) {
	var secret corev1.Secret
	if err := kclient.Get(ctx, key, &secret); err != nil {
		if !errors.IsNotFound(err) {
			return nil, err
		}
		// Secret not present
		token, err := signer.Token(username)
		if err != nil {
			return nil, err
		}
		secret = corev1.Secret{
			ObjectMeta: metav1.ObjectMeta{
				Namespace: key.Namespace,
				Name:      key.Name,
			},
			Type: corev1.SecretTypeOpaque,
			Data: map[string][]byte{
				TokenKey: []byte(token),
			},
		}
		return &secret, kclient.Create(ctx, &secret)
	} else {
		token, ok := secret.Data[TokenKey]
		if !ok || signer.UnsafeValidate(string(token)) != nil {
			// Secret present but invalid
			original := client.MergeFrom(secret.DeepCopy())
			token, err := signer.Token(username)
			if err != nil {
				return nil, err
			}
			secret.Data = map[string][]byte{
				TokenKey: []byte(token),
			}
			return &secret, kclient.Patch(ctx, &secret, original)
		} else {
			// Secret present and valid
			return &secret, nil
		}
	}
}
