package auth

import (
	"context"
	"crypto/subtle"
	"time"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/controller"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/timestamppb"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/client-go/util/retry"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

var ErrExporterTokenLineage = status.Error(codes.PermissionDenied, "credential is outside the current or previous exporter token lineage")

// RotateExporterCredential requires a validated internal exporter credential.
// Previous-token retries recover the stored replacement without advancing its lineage.
func RotateExporterCredential(
	ctx context.Context,
	kclient client.Client,
	signer *oidc.Signer,
	key client.ObjectKey,
	subject, credential string,
) (string, *timestamppb.Timestamp, error) {
	if signer == nil {
		return "", nil, status.Error(codes.FailedPrecondition, "token signer not configured")
	}
	lifetime, err := signer.TokenLifetime(credential)
	if err != nil || lifetime <= 0 {
		return "", nil, status.Error(codes.PermissionDenied, "credential has no positive token lifetime")
	}
	expiresAt, err := signer.TokenExpiry(credential)
	if err != nil || expiresAt.IsZero() || time.Until(expiresAt) <= 0 || time.Until(expiresAt) > lifetime/2 {
		return "", nil, status.Error(codes.PermissionDenied, "credential is outside the renewal window")
	}
	presentedHash := controller.TokenHash(credential)
	var token string
	var expiry *timestamppb.Timestamp
	err = retry.RetryOnConflict(retry.DefaultRetry, func() error {
		var secret corev1.Secret
		if err := kclient.Get(ctx, key, &secret); err != nil {
			return status.Errorf(codes.Internal, "failed to get credential secret: %s", err)
		}
		current := string(secret.Data[controller.TokenKey])
		if !sameTokenHash(presentedHash, controller.TokenHash(current)) {
			if !sameTokenHash(presentedHash, secret.Annotations[controller.PreviousTokenHashAnnotation]) {
				return ErrExporterTokenLineage
			}
			currentSubject, err := signer.ParseSubject(current)
			if err != nil || currentSubject != subject {
				return status.Error(codes.FailedPrecondition, "stored exporter credential is invalid")
			}
			token = current
			expiry, err = credentialExpiry(signer, token)
			return err
		}
		original := client.MergeFromWithOptions(secret.DeepCopy(), client.MergeFromWithOptimisticLock{})
		var err error
		token, err = signer.Token(subject)
		if err != nil {
			return status.Errorf(codes.Internal, "failed to sign token: %s", err)
		}
		expiry, err = credentialExpiry(signer, token)
		if err != nil {
			return err
		}
		secret.Data[controller.TokenKey] = []byte(token)
		if secret.Annotations == nil {
			secret.Annotations = map[string]string{}
		}
		secret.Annotations[controller.PreviousTokenHashAnnotation] = presentedHash
		// The resource-version precondition also serializes rotations across replicas.
		if err := kclient.Patch(ctx, &secret, original); err != nil {
			if apierrors.IsConflict(err) {
				return err
			}
			return status.Errorf(codes.Internal, "failed to update credential secret: %s", err)
		}
		return nil
	})
	if err != nil {
		if apierrors.IsConflict(err) {
			return "", nil, status.Error(codes.Aborted, "exporter credential changed concurrently; retry rotation")
		}
		return "", nil, err
	}
	return token, expiry, nil
}

func sameTokenHash(a, b string) bool {
	return subtle.ConstantTimeCompare([]byte(a), []byte(b)) == 1
}
