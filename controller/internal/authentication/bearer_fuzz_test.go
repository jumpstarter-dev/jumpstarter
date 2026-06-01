package authentication

import (
	"context"
	"strings"
	"testing"

	"google.golang.org/grpc/metadata"
)

func FuzzBearerTokenExtraction(f *testing.F) {
	f.Add("Bearer valid-token-here")
	f.Add("bearer lowercase-token")
	f.Add("BEARER uppercase-token")
	f.Add("BeArEr mixed-case")
	f.Add("Basic not-a-bearer")
	f.Add("")
	f.Add("Bearer ")
	f.Add("Bearer")
	f.Add("Bearertoken-no-space")
	f.Add("  Bearer leading-space")
	f.Add("Bear")

	f.Fuzz(func(t *testing.T, authHeader string) {
		md := metadata.Pairs("authorization", authHeader)
		ctx := metadata.NewIncomingContext(context.Background(), md)
		token, err := BearerTokenFromContext(ctx)

		if len(authHeader) >= 7 && strings.EqualFold(authHeader[:7], "Bearer ") {
			if err != nil {
				t.Errorf("BearerTokenFromContext with valid 'Bearer ' prefix %q returned error: %v", authHeader, err)
			}
			expected := authHeader[7:]
			if token != expected {
				t.Errorf("BearerTokenFromContext(%q) = %q, expected %q", authHeader, token, expected)
			}
		} else {
			if err == nil {
				t.Errorf("BearerTokenFromContext(%q) should have returned error for non-Bearer header", authHeader)
			}
		}
	})
}
