package authentication

import (
	"context"
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
		// BearerTokenFromContext must not panic.
		_, _ = BearerTokenFromContext(ctx)
	})
}
