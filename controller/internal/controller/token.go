package controller

import (
	"fmt"
	"time"

	"github.com/golang-jwt/jwt/v5"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/util/uuid"
	"sigs.k8s.io/controller-runtime/pkg/client/apiutil"
)

type JumpstarterClaims struct {
	jwt.RegisteredClaims
	corev1.ObjectReference
}

func SignObjectToken(
	issuer string,
	audience []string,
	method jwt.SigningMethod,
	key interface{},
	object metav1.Object,
	scheme *runtime.Scheme,
) (string, error) {
	ro, ok := object.(runtime.Object)
	if !ok {
		return "", fmt.Errorf("%T is not a runtime.Object, cannot call SignObjectToken", object)
	}

	gvk, err := apiutil.GVKForObject(ro, scheme)
	if err != nil {
		return "", err
	}

	return jwt.NewWithClaims(method, JumpstarterClaims{
		RegisteredClaims: jwt.RegisteredClaims{
			Issuer:   issuer,
			Subject:  string(object.GetUID()),
			Audience: audience,
			// ExpiresAt: token are valid for the entire lifetime of the object
			NotBefore: jwt.NewNumericDate(time.Now()),
			IssuedAt:  jwt.NewNumericDate(time.Now()),
			ID:        string(uuid.NewUUID()),
		},
		ObjectReference: corev1.ObjectReference{
			Kind:       gvk.Kind,
			Namespace:  object.GetNamespace(),
			Name:       object.GetName(),
			UID:        object.GetUID(),
			APIVersion: gvk.GroupVersion().String(),
		},
	}).SignedString(key)
}

func VerifyObjectToken(
	token string,
	issuer string,
	audience string,
	keyFunc jwt.Keyfunc,
) (*corev1.ObjectReference, error) {
	parsed, err := jwt.ParseWithClaims(
		token,
		&JumpstarterClaims{},
		keyFunc,
		jwt.WithIssuer(issuer),
		jwt.WithAudience(audience),
		jwt.WithIssuedAt(),
		jwt.WithValidMethods([]string{
			jwt.SigningMethodHS256.Name,
			jwt.SigningMethodHS384.Name,
			jwt.SigningMethodHS512.Name,
		}),
	)
	if err != nil {
		return nil, err
	} else if claims, ok := parsed.Claims.(*JumpstarterClaims); ok {
		return &claims.ObjectReference, nil
	} else {
		return nil, fmt.Errorf("%T is not a JumpstarterClaims", parsed.Claims)
	}
}
