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

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	"google.golang.org/grpc/codes"

	adminv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/admin/v1"
	jumpstarterv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"
)

var _ = Describe("admin AuthZ (SubjectAccessReview)", func() {
	var ns string

	BeforeEach(func() {
		ns = makeNamespace(context.Background())
	})

	Context("when the caller is authenticated but unbound", func() {
		It("denies the RPC with PermissionDenied", func() {
			conn := dialAdmin(tokenFor("nobody"))
			defer conn.Close()
			_, err := adminv1pb.NewLeaseServiceClient(conn).ListLeases(
				context.Background(),
				&jumpstarterv1pb.LeaseListRequest{Parent: nsParent(ns)},
			)
			Expect(grpcCode(err)).To(Equal(codes.PermissionDenied))
		})
	})

	Context("when granted via ClusterRoleBinding", func() {
		BeforeEach(func() {
			applyClusterRoleBinding(context.Background(), usernameFor("admin"),
				[]string{"list", "get", "create", "update", "delete", "watch"},
				[]string{"leases", "exporters", "clients", "webhooks"})
		})

		It("admits list across any namespace", func() {
			conn := dialAdmin(tokenFor("admin"))
			defer conn.Close()
			_, err := adminv1pb.NewLeaseServiceClient(conn).ListLeases(
				context.Background(),
				&jumpstarterv1pb.LeaseListRequest{Parent: nsParent(ns)},
			)
			Expect(err).NotTo(HaveOccurred())
		})

		It("admits list in a freshly-created namespace too", func() {
			other := makeNamespace(context.Background())
			conn := dialAdmin(tokenFor("admin"))
			defer conn.Close()
			_, err := adminv1pb.NewLeaseServiceClient(conn).ListLeases(
				context.Background(),
				&jumpstarterv1pb.LeaseListRequest{Parent: nsParent(other)},
			)
			Expect(err).NotTo(HaveOccurred())
		})
	})

	Context("namespace-scoped RoleBindings", func() {
		// adminauthz.NamespaceFromAdminRequest extracts the target
		// namespace from the request, so per-namespace RoleBindings
		// authorize admin RPCs in their own namespace and only there.

		var allowedNS, deniedNS string

		BeforeEach(func() {
			allowedNS = ns
			deniedNS = makeNamespace(context.Background())
			applyNamespaceRoleBinding(context.Background(), allowedNS, usernameFor("scoped"),
				[]string{"list"}, []string{"leases"})
		})

		It("admits list in the granted namespace", func() {
			conn := dialAdmin(tokenFor("scoped"))
			defer conn.Close()
			_, err := adminv1pb.NewLeaseServiceClient(conn).ListLeases(
				context.Background(),
				&jumpstarterv1pb.LeaseListRequest{Parent: nsParent(allowedNS)},
			)
			Expect(err).NotTo(HaveOccurred())
		})

		It("denies list in a different namespace", func() {
			conn := dialAdmin(tokenFor("scoped"))
			defer conn.Close()
			_, err := adminv1pb.NewLeaseServiceClient(conn).ListLeases(
				context.Background(),
				&jumpstarterv1pb.LeaseListRequest{Parent: nsParent(deniedNS)},
			)
			Expect(grpcCode(err)).To(Equal(codes.PermissionDenied))
		})

		It("denies a verb the role does not include", func() {
			conn := dialAdmin(tokenFor("scoped"))
			defer conn.Close()
			_, err := adminv1pb.NewLeaseServiceClient(conn).DeleteLease(
				context.Background(),
				&jumpstarterv1pb.DeleteRequest{Name: leaseName(allowedNS, "anything")},
			)
			Expect(grpcCode(err)).To(Equal(codes.PermissionDenied))
		})
	})

	Context("ClusterRoleBinding scoped to a single resource", func() {
		BeforeEach(func() {
			applyClusterRoleBinding(context.Background(), usernameFor("ro"),
				[]string{"list"}, []string{"leases"})
		})

		It("denies a verb the role does not include", func() {
			conn := dialAdmin(tokenFor("ro"))
			defer conn.Close()
			_, err := adminv1pb.NewLeaseServiceClient(conn).DeleteLease(
				context.Background(),
				&jumpstarterv1pb.DeleteRequest{Name: leaseName(ns, "anything")},
			)
			Expect(grpcCode(err)).To(Equal(codes.PermissionDenied))
		})

		It("denies a different resource type", func() {
			conn := dialAdmin(tokenFor("ro"))
			defer conn.Close()
			_, err := adminv1pb.NewExporterServiceClient(conn).ListExporters(
				context.Background(),
				&jumpstarterv1pb.ExporterListRequest{Parent: nsParent(ns)},
			)
			Expect(grpcCode(err)).To(Equal(codes.PermissionDenied))
		})

		It("admits the granted (verb, resource) combination", func() {
			conn := dialAdmin(tokenFor("ro"))
			defer conn.Close()
			_, err := adminv1pb.NewLeaseServiceClient(conn).ListLeases(
				context.Background(),
				&jumpstarterv1pb.LeaseListRequest{Parent: nsParent(ns)},
			)
			Expect(err).NotTo(HaveOccurred())
		})
	})
})
