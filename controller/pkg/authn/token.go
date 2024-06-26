package authn

import (
	"context"
	"net/url"
	"slices"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	authnv1 "k8s.io/api/authentication/v1"
	v1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	authnv1c "k8s.io/client-go/kubernetes/typed/authentication/v1"
)

func BearerTokenFromContext(ctx context.Context) (string, error) {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return "", status.Errorf(codes.InvalidArgument, "missing metadata")
	}

	authorizations := md.Get("authorization")

	if len(authorizations) < 1 {
		return "", status.Errorf(codes.Unauthenticated, "missing authorization header")
	}

	// Reference: https://www.rfc-editor.org/rfc/rfc7230#section-3.2.2
	// A sender MUST NOT generate multiple header fields with the same field name in a message
	if len(authorizations) > 1 {
		return "", status.Errorf(codes.InvalidArgument, "multiple authorization headers")
	}

	// Invariant: len(authorizations) == 1
	authorization := authorizations[0]

	// Reference: https://github.com/golang-jwt/jwt/blob/62e504c2/request/extractor.go#L93
	if len(authorization) < 7 || !strings.EqualFold(authorization[:7], "Bearer ") {
		return "", status.Errorf(codes.InvalidArgument, "malformed authorization header")
	}

	// Invariant: len(authorization) >= 7
	return authorization[7:], nil
}

func Authenticate(
	ctx context.Context,
	client authnv1c.AuthenticationV1Interface,
	token string,
	scheme string,
	host string,
	group string,
) (*url.URL, *time.Time, error) {
	parser := jwt.NewParser(jwt.WithExpirationRequired())

	parsed, _, err := parser.ParseUnverified(token, &jwt.RegisteredClaims{})
	if err != nil {
		return nil, nil, status.Errorf(codes.InvalidArgument, "invalid jwt token")
	}

	audiences, err := parsed.Claims.GetAudience()
	if err != nil {
		return nil, nil, status.Errorf(codes.InvalidArgument, "invalid jwt audience")
	}

	expiration, err := parsed.Claims.GetExpirationTime()
	if err != nil {
		return nil, nil, status.Errorf(codes.InvalidArgument, "invalid jwt expiration")
	}

	var matched []*url.URL

	for _, audience := range audiences {
		aud, err := url.Parse(audience)
		// skip unrecognized audiences
		if err != nil {
			continue
		}
		// skip non local audiences
		if aud.Scheme != scheme || aud.Host != host {
			continue
		}
		// add local audience to matched list
		matched = append(matched, aud)
	}

	if len(matched) != 1 {
		return nil, nil, status.Errorf(codes.InvalidArgument, "invalid number of local jwt audience")
	}

	// Invariant: len(matched) == 1
	audience := matched[0]

	review, err := client.TokenReviews().Create(
		ctx,
		&authnv1.TokenReview{
			Spec: authnv1.TokenReviewSpec{
				Token:     token,
				Audiences: []string{audience.String()},
			},
		},
		v1.CreateOptions{},
	)
	if err != nil ||
		!review.Status.Authenticated ||
		!slices.Contains(review.Status.Audiences, audience.String()) ||
		!slices.Contains(review.Status.User.Groups, group) {
		return nil, nil, status.Errorf(codes.Unauthenticated, "unauthenticated jwt token")
	}

	return audience, &expiration.Time, nil
}
