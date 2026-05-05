/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package e2e

import (
	"context"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/identity"
	adminv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/admin/v1"
	jumpstarterv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"

	"k8s.io/apimachinery/pkg/types"
)

var _ = Describe("admin AuthN (multi-issuer OIDC)", func() {
	var ns string

	BeforeEach(func() {
		ns = makeNamespace(context.Background())
	})

	It("rejects requests with no Authorization metadata", func() {
		conn := dialAdminNoToken()
		defer conn.Close()
		_, err := adminv1pb.NewLeaseServiceClient(conn).ListLeases(
			context.Background(),
			&jumpstarterv1pb.LeaseListRequest{Parent: nsParent(ns)},
		)
		Expect(grpcCode(err)).To(Equal(codes.Unauthenticated))
	})

	It("rejects requests with garbage tokens", func() {
		conn := dialAdmin("not-a-jwt")
		defer conn.Close()
		_, err := adminv1pb.NewLeaseServiceClient(conn).ListLeases(
			context.Background(),
			&jumpstarterv1pb.LeaseListRequest{Parent: nsParent(ns)},
		)
		// The bearer authenticator returns InvalidArgument for malformed
		// "Bearer "-prefixed strings (BearerTokenFromContext) and
		// Unauthenticated for tokens that fail signature verification.
		// "not-a-jwt" passes the prefix gate but fails signature.
		Expect(grpcCode(err)).To(Or(Equal(codes.Unauthenticated), Equal(codes.InvalidArgument)))
	})

	It("rejects tokens signed by a different keypair under the same issuer", func() {
		bad := tokenFor("alice", withAltSigner())
		conn := dialAdmin(bad)
		defer conn.Close()
		_, err := adminv1pb.NewLeaseServiceClient(conn).ListLeases(
			context.Background(),
			&jumpstarterv1pb.LeaseListRequest{Parent: nsParent(ns)},
		)
		Expect(grpcCode(err)).To(Equal(codes.Unauthenticated))
	})

	It("rejects tokens with the wrong audience", func() {
		bad := tokenFor("alice", withAudience("not-jumpstarter"))
		conn := dialAdmin(bad)
		defer conn.Close()
		_, err := adminv1pb.NewLeaseServiceClient(conn).ListLeases(
			context.Background(),
			&jumpstarterv1pb.LeaseListRequest{Parent: nsParent(ns)},
		)
		Expect(grpcCode(err)).To(Equal(codes.Unauthenticated))
	})

	It("rejects tokens with an unknown issuer", func() {
		bad := tokenFor("alice", withIssuer("https://untrusted.example.com"))
		conn := dialAdmin(bad)
		defer conn.Close()
		_, err := adminv1pb.NewLeaseServiceClient(conn).ListLeases(
			context.Background(),
			&jumpstarterv1pb.LeaseListRequest{Parent: nsParent(ns)},
		)
		Expect(grpcCode(err)).To(Equal(codes.Unauthenticated))
	})

	It("rejects expired tokens", func() {
		bad := tokenFor("alice", withTTL(-1*time.Minute))
		conn := dialAdmin(bad)
		defer conn.Close()
		_, err := adminv1pb.NewLeaseServiceClient(conn).ListLeases(
			context.Background(),
			&jumpstarterv1pb.LeaseListRequest{Parent: nsParent(ns)},
		)
		Expect(grpcCode(err)).To(Equal(codes.Unauthenticated))
	})

	It("admits a valid token and propagates identity to created CRDs", func() {
		applyClusterRoleBinding(context.Background(), usernameFor("alice"),
			[]string{"create", "get", "list"}, []string{"clients", "leases"})

		token := tokenFor("alice")
		conn := dialAdmin(token)
		defer conn.Close()

		client := adminv1pb.NewClientServiceClient(conn)
		// CreateClient stamps owner annotations from the JWT identity. We
		// compare what we observe against the production OwnerHash for
		// (issuer, sub) to prove identity propagation end-to-end.
		_, err := client.CreateClient(context.Background(), &jumpstarterv1pb.ClientCreateRequest{
			Parent:   nsParent(ns),
			ClientId: "ownership-probe",
			Client:   &jumpstarterv1pb.Client{},
		})
		// The handler waits for ExporterReconciler/ClientReconciler to
		// provision a Secret. Our suite does not run those reconcilers, so
		// CreateClient times out — but only AFTER stamping the owner
		// annotation. Ignore DeadlineExceeded; success is observing the
		// stamped CRD.
		_ = err

		var c jumpstarterdevv1alpha1.Client
		Eventually(func(g Gomega) {
			g.Expect(k8sClient.Get(context.Background(),
				types.NamespacedName{Namespace: ns, Name: "ownership-probe"}, &c)).To(Succeed())
		}, 10*time.Second, 100*time.Millisecond).Should(Succeed())

		expected := identity.Identity{
			Issuer:   signer.Issuer(),
			Subject:  "alice",
			Username: usernameFor("alice"),
		}.OwnerHash()
		Expect(c.Annotations).To(HaveKeyWithValue(identity.OwnerAnnotation, expected))
		Expect(c.Annotations).To(HaveKeyWithValue(identity.OwnerIssuerAnnotation, signer.Issuer()))
		Expect(c.Annotations).To(HaveKeyWithValue(identity.CreatedByAnnotation, usernameFor("alice")))
	})
})

// grpcCode unwraps a gRPC status code from an RPC error, returning Unknown
// when the error has no status. Test predicates compare to typed codes so
// failure messages name the actual code on mismatch.
func grpcCode(err error) codes.Code {
	if err == nil {
		return codes.OK
	}
	if s, ok := status.FromError(err); ok {
		return s.Code()
	}
	return codes.Unknown
}
