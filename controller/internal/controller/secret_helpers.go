package controller

import (
	"context"

	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

const TokenKey string = "token"

func ensureSecret(
	ctx context.Context,
	key client.ObjectKey,
	kclient client.Client,
	scheme *runtime.Scheme,
	signer *oidc.Signer,
	subject string,
	owner metav1.Object,
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
		token, err := signer.Token(subject)
		if err != nil {
			logger.Error(err, "failed to sign token")
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
		if err := controllerutil.SetControllerReference(owner, &secret, scheme); err != nil {
			logger.Error(err, "failed to set controller reference")
			return nil, err
		}
		if err = kclient.Create(ctx, &secret); err != nil {
			logger.Error(err, "failed to create secret")
			return nil, err
		}
		return &secret, nil
	} else {
		original := client.MergeFrom(secret.DeepCopy())
		if err := controllerutil.SetControllerReference(owner, &secret, scheme); err != nil {
			logger.Error(err, "failed to set controller reference")
			return nil, err
		}
		token, ok := secret.Data[TokenKey]
		if !ok || signer.Validate(string(token)) != nil {
			// Secret present but invalid
			logger.Info("secret present but invalid, updating")
			token, err := signer.Token(subject)
			if err != nil {
				logger.Error(err, "failed to sign token")
				return nil, err
			}
			secret.Data = map[string][]byte{
				TokenKey: []byte(token),
			}
		}
		if err = kclient.Patch(ctx, &secret, original); err != nil {
			logger.Error(err, "failed to update secret")
			return nil, err
		}
		return &secret, nil
	}
}
