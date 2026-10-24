package controller

import (
	"context"
	"crypto/sha256"
	"encoding/hex"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

const TokenKey string = "token"

const AuthenticatedTokenHashAnnotation = "jumpstarter.dev/authenticated-token-hash"

const PreviousTokenHashAnnotation = "jumpstarter.dev/previous-token-hash"

const expiredTokenAnnotation = "jumpstarter.dev/expired-token-at"
const regeneratedTokenAnnotation = "jumpstarter.dev/credential-regenerated-at"

func ensureSecret(
	ctx context.Context,
	key client.ObjectKey,
	kclient client.Client,
	scheme *runtime.Scheme,
	signer *oidc.Signer,
	subject string,
	owner metav1.Object,
) (*corev1.Secret, time.Time, error) {
	logger := log.FromContext(ctx).WithName("ensureSecret")
	var secret corev1.Secret
	if err := kclient.Get(ctx, key, &secret); err != nil {
		if !errors.IsNotFound(err) {
			logger.Error(err, "failed to get secret")
			return nil, time.Time{}, err
		}
		// Secret not present
		logger.Info("secret not present, creating")
		token, err := signer.Token(subject)
		if err != nil {
			logger.Error(err, "failed to sign token")
			return nil, time.Time{}, err
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
			return nil, time.Time{}, err
		}
		if err = kclient.Create(ctx, &secret); err != nil {
			logger.Error(err, "failed to create secret")
			return nil, time.Time{}, err
		}
		expiry, _ := signer.TokenExpiry(token)
		return &secret, expiry, nil
	} else {
		original := client.MergeFromWithOptions(secret.DeepCopy(), client.MergeFromWithOptimisticLock{})
		if err := controllerutil.SetControllerReference(owner, &secret, scheme); err != nil {
			logger.Error(err, "failed to set controller reference")
			return nil, time.Time{}, err
		}
		token, ok := secret.Data[TokenKey]
		if !ok || signer.Validate(string(token)) != nil {
			// Secret present but invalid
			logger.Info("secret present but invalid, updating")
			newToken, err := signer.Token(subject)
			if err != nil {
				logger.Error(err, "failed to sign token")
				return nil, time.Time{}, err
			}
			if _, isExporter := owner.(*jumpstarterdevv1alpha1.Exporter); isExporter {
				delete(secret.Annotations, PreviousTokenHashAnnotation)
				if expiredAt, err := signer.TokenExpiry(string(token)); err == nil && !expiredAt.IsZero() && expiredAt.Before(time.Now()) {
					if secret.Annotations == nil {
						secret.Annotations = map[string]string{}
					}
					secret.Annotations[expiredTokenAnnotation] = expiredAt.UTC().Format(time.RFC3339)
					secret.Annotations[regeneratedTokenAnnotation] = time.Now().UTC().Format(time.RFC3339Nano)
				}
			}
			secret.Data = map[string][]byte{
				TokenKey: []byte(newToken),
			}
			token = []byte(newToken)
		}
		if err = kclient.Patch(ctx, &secret, original); err != nil {
			logger.Error(err, "failed to update secret")
			return nil, time.Time{}, err
		}
		expiry, _ := signer.TokenExpiry(string(token))
		return &secret, expiry, nil
	}
}

func TokenHash(token string) string {
	digest := sha256.Sum256([]byte(token))
	return hex.EncodeToString(digest[:])
}
