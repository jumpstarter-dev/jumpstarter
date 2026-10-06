package auth

import (
	"context"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/controller"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/timestamppb"
	corev1 "k8s.io/api/core/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// RotateCredential replaces the stored token after the caller authenticates its owner.
func RotateCredential(
	ctx context.Context,
	kclient client.Client,
	signer *oidc.Signer,
	key client.ObjectKey,
	subject string,
) (string, *timestamppb.Timestamp, error) {
	if signer == nil {
		return "", nil, status.Error(codes.FailedPrecondition, "token signer not configured")
	}
	token, err := signer.Token(subject)
	if err != nil {
		return "", nil, status.Errorf(codes.Internal, "failed to sign token: %s", err)
	}
	expiresAt, err := signer.TokenExpiry(token)
	if err != nil {
		return "", nil, status.Errorf(codes.Internal, "failed to parse token claims: %s", err)
	}
	var expiry *timestamppb.Timestamp
	if !expiresAt.IsZero() {
		expiry = timestamppb.New(expiresAt)
	}
	var secret corev1.Secret
	if err := kclient.Get(ctx, key, &secret); err != nil {
		return "", nil, status.Errorf(codes.Internal, "failed to get credential secret: %s", err)
	}
	original := client.MergeFrom(secret.DeepCopy())
	if secret.Data == nil {
		secret.Data = map[string][]byte{}
	}
	secret.Data[controller.TokenKey] = []byte(token)
	if err := kclient.Patch(ctx, &secret, original); err != nil {
		return "", nil, status.Errorf(codes.Internal, "failed to update credential secret: %s", err)
	}
	return token, expiry, nil
}
