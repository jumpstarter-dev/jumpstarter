package controller

import (
	"context"

	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

const TokenKey string = "token"

func ensureSecret(
	ctx context.Context,
	key client.ObjectKey,
	kclient client.Client,
	signer *oidc.Signer,
	username string,
) (*corev1.Secret, error) {
	logger := log.FromContext(ctx).WithName("ensureSecret")
	var secret corev1.Secret
	if err := kclient.Get(ctx, key, &secret); err != nil {
		if !errors.IsNotFound(err) {
			logger.Error(err, "failed to get secret")
			return nil, err
		}
		// Secret not present
		logger.Info("secret not present, creating")
		token, err := signer.Token(username)
		if err != nil {
			logger.Info("failed to sign token")
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
		if err = kclient.Create(ctx, &secret); err != nil {
			logger.Error(err, "failed to create secret")
			return nil, err
		}
		return &secret, nil
	} else {
		token, ok := secret.Data[TokenKey]
		if !ok || signer.UnsafeValidate(string(token)) != nil {
			// Secret present but invalid
			logger.Info("secret present but invalid, updating")
			original := client.MergeFrom(secret.DeepCopy())
			token, err := signer.Token(username)
			if err != nil {
				logger.Info("failed to sign token")
				return nil, err
			}
			secret.Data = map[string][]byte{
				TokenKey: []byte(token),
			}
			if err = kclient.Patch(ctx, &secret, original); err != nil {
				logger.Error(err, "failed to update secret")
				return nil, err
			}
			return &secret, nil
		} else {
			// Secret present and valid
			return &secret, nil
		}
	}
}
