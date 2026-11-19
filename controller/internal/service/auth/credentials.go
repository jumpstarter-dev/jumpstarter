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

// RotateCredential replaces the stored token for subject. The caller must
// present subject's own controller-signed token: any other credential that
// authenticated the owner, such as an OIDC token, would otherwise be exchanged
// for an internal token that outlives it.
func RotateCredential(
	ctx context.Context,
	kclient client.Client,
	signer *oidc.Signer,
	key client.ObjectKey,
	subject, credential string,
) (string, *timestamppb.Timestamp, error) {
	if signer == nil {
		return "", nil, status.Error(codes.FailedPrecondition, "token signer not configured")
	}
	if presented, err := signer.ParseSubject(credential); err != nil || presented != subject {
		return "", nil, status.Error(codes.PermissionDenied, "only internal client tokens can be rotated")
	}
	token, err := signer.Token(subject)
	if err != nil {
		return "", nil, status.Errorf(codes.Internal, "failed to sign token: %s", err)
	}
	expiry, err := credentialExpiry(signer, token)
	if err != nil {
		return "", nil, err
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

func credentialExpiry(signer *oidc.Signer, token string) (*timestamppb.Timestamp, error) {
	expiresAt, err := signer.TokenExpiry(token)
	if err != nil {
		return nil, status.Errorf(codes.Internal, "failed to parse token claims: %s", err)
	}
	if expiresAt.IsZero() {
		return nil, nil
	}
	return timestamppb.New(expiresAt), nil
}
