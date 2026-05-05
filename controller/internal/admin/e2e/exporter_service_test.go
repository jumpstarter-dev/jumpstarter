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
	"io"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/identity"
	adminv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/admin/v1"
	jumpstarterv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"

	"k8s.io/apimachinery/pkg/types"
)

// admin.v1.ExporterService.CreateExporter blocks until the
// ExporterReconciler stamps Status.Credential — that controller is not run
// by this suite. The Get/List/Update/Delete/Watch RPCs do not depend on
// the reconciler, so we exercise them by seeding Exporter CRDs directly
// (helper in lease_service_test.go's seedExporter).
var _ = Describe("admin.v1.ExporterService", func() {
	var (
		ns     string
		conn   *grpc.ClientConn
		client adminv1pb.ExporterServiceClient
	)

	BeforeEach(func() {
		ns = makeNamespace(context.Background())
		applyClusterRoleBinding(context.Background(), usernameFor("alice"),
			[]string{"get", "list", "create", "update", "patch", "delete", "watch"},
			[]string{"exporters"})
		seedExporter(context.Background(), ns, "exp-a")
		seedExporter(context.Background(), ns, "exp-b")

		conn = dialAdmin(tokenFor("alice"))
		client = adminv1pb.NewExporterServiceClient(conn)
	})

	AfterEach(func() {
		_ = conn.Close()
	})

	It("gets a seeded exporter", func() {
		got, err := client.GetExporter(context.Background(),
			&jumpstarterv1pb.GetRequest{Name: exporterName(ns, "exp-a")})
		Expect(err).NotTo(HaveOccurred())
		Expect(got.GetName()).To(Equal(exporterName(ns, "exp-a")))
	})

	It("returns NotFound for unknown exporter", func() {
		_, err := client.GetExporter(context.Background(),
			&jumpstarterv1pb.GetRequest{Name: exporterName(ns, "missing")})
		Expect(grpcCode(err)).To(Equal(codes.NotFound))
	})

	It("lists exporters scoped to the namespace", func() {
		resp, err := client.ListExporters(context.Background(),
			&jumpstarterv1pb.ExporterListRequest{Parent: nsParent(ns)})
		Expect(err).NotTo(HaveOccurred())
		names := make([]string, 0, len(resp.GetExporters()))
		for _, e := range resp.GetExporters() {
			names = append(names, e.GetName())
		}
		Expect(names).To(ConsistOf(exporterName(ns, "exp-a"), exporterName(ns, "exp-b")))
	})

	It("filters by labels", func() {
		var e jumpstarterdevv1alpha1.Exporter
		Expect(k8sClient.Get(context.Background(),
			types.NamespacedName{Namespace: ns, Name: "exp-a"}, &e)).To(Succeed())
		e.Labels = map[string]string{"team": "qa"}
		Expect(k8sClient.Update(context.Background(), &e)).To(Succeed())

		resp, err := client.ListExporters(context.Background(),
			&jumpstarterv1pb.ExporterListRequest{Parent: nsParent(ns), Filter: "team=qa"})
		Expect(err).NotTo(HaveOccurred())
		Expect(resp.GetExporters()).To(HaveLen(1))
		Expect(resp.GetExporters()[0].GetName()).To(Equal(exporterName(ns, "exp-a")))
	})

	It("updates labels and re-stamps owner annotations", func() {
		_, err := client.UpdateExporter(context.Background(), &jumpstarterv1pb.ExporterUpdateRequest{
			Exporter: &jumpstarterv1pb.Exporter{
				Name:   exporterName(ns, "exp-a"),
				Labels: map[string]string{"region": "us-east-1"},
			},
		})
		Expect(err).NotTo(HaveOccurred())

		var e jumpstarterdevv1alpha1.Exporter
		Expect(k8sClient.Get(context.Background(),
			types.NamespacedName{Namespace: ns, Name: "exp-a"}, &e)).To(Succeed())
		Expect(e.Labels).To(HaveKeyWithValue("region", "us-east-1"))
		Expect(e.Annotations).To(HaveKeyWithValue(identity.OwnerAnnotation,
			ownerIdentity("alice").OwnerHash()))
	})

	It("deletes an exporter", func() {
		_, err := client.DeleteExporter(context.Background(),
			&jumpstarterv1pb.DeleteRequest{Name: exporterName(ns, "exp-b")})
		Expect(err).NotTo(HaveOccurred())

		var e jumpstarterdevv1alpha1.Exporter
		err = k8sClient.Get(context.Background(),
			types.NamespacedName{Namespace: ns, Name: "exp-b"}, &e)
		Expect(err).To(HaveOccurred())
	})

	It("denies operations the SAR does not allow", func() {
		// Use a different caller bound only to "list".
		applyNamespaceRoleBinding(context.Background(), ns, usernameFor("limited"),
			[]string{"list"}, []string{"exporters"})
		c := adminv1pb.NewExporterServiceClient(dialAdmin(tokenFor("limited")))
		_, err := c.DeleteExporter(context.Background(),
			&jumpstarterv1pb.DeleteRequest{Name: exporterName(ns, "exp-a")})
		Expect(grpcCode(err)).To(Equal(codes.PermissionDenied))
	})

	It("watches DELETED events", func() {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		stream, err := client.WatchExporters(ctx,
			&jumpstarterv1pb.WatchRequest{Parent: nsParent(ns)})
		Expect(err).NotTo(HaveOccurred())

		go func() {
			defer GinkgoRecover()
			time.Sleep(200 * time.Millisecond)
			_, _ = client.DeleteExporter(context.Background(),
				&jumpstarterv1pb.DeleteRequest{Name: exporterName(ns, "exp-a")})
		}()

		Expect(consumeExporterEvent(stream,
			adminv1pb.EventType_EVENT_TYPE_DELETED,
			exporterName(ns, "exp-a"))).To(Succeed())
	})
})

func consumeExporterEvent(stream adminv1pb.ExporterService_WatchExportersClient, want adminv1pb.EventType, name string) error {
	for {
		ev, err := stream.Recv()
		if err == io.EOF {
			return io.EOF
		}
		if err != nil {
			return err
		}
		if ev.GetEventType() == want && ev.GetExporter().GetName() == name {
			return nil
		}
	}
}
