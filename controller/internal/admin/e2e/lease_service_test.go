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
	"google.golang.org/protobuf/types/known/durationpb"
	"google.golang.org/protobuf/types/known/timestamppb"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/identity"
	adminv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/admin/v1"
	jumpstarterv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

var _ = Describe("admin.v1.LeaseService", func() {
	var (
		ns     string
		conn   *grpc.ClientConn
		client adminv1pb.LeaseServiceClient
	)

	BeforeEach(func() {
		ns = makeNamespace(context.Background())
		applyClusterRoleBinding(context.Background(), usernameFor("alice"),
			[]string{"get", "list", "create", "update", "patch", "delete", "watch"},
			[]string{"leases", "clients"})
		makeClientCRDForCaller(context.Background(), ns, "alice")
		seedExporter(context.Background(), ns, "exp-1")

		conn = dialAdmin(tokenFor("alice"))
		client = adminv1pb.NewLeaseServiceClient(conn)
	})

	AfterEach(func() {
		_ = conn.Close()
	})

	It("creates a lease pinned to an exporter", func() {
		got, err := client.CreateLease(context.Background(), &jumpstarterv1pb.LeaseCreateRequest{
			Parent:  nsParent(ns),
			LeaseId: "lease-pin",
			Lease: &jumpstarterv1pb.Lease{
				ExporterName: ptr(exporterName(ns, "exp-1")),
				Duration:     durationpb.New(5 * time.Minute),
			},
		})
		Expect(err).NotTo(HaveOccurred())
		Expect(got.GetName()).To(Equal(leaseName(ns, "lease-pin")))

		// Owner attribution: same hash as the JWT identity.
		var l jumpstarterdevv1alpha1.Lease
		Expect(k8sClient.Get(context.Background(),
			types.NamespacedName{Namespace: ns, Name: "lease-pin"}, &l)).To(Succeed())
		Expect(l.Annotations).To(HaveKeyWithValue(identity.OwnerAnnotation,
			ownerIdentity("alice").OwnerHash()))
	})

	It("creates a lease via selector when no exporter is pinned", func() {
		_, err := client.CreateLease(context.Background(), &jumpstarterv1pb.LeaseCreateRequest{
			Parent:  nsParent(ns),
			LeaseId: "lease-sel",
			Lease: &jumpstarterv1pb.Lease{
				Selector: "type=qemu",
				Duration: durationpb.New(time.Minute),
			},
		})
		Expect(err).NotTo(HaveOccurred())
	})

	It("rejects create with neither selector nor exporter_name", func() {
		_, err := client.CreateLease(context.Background(), &jumpstarterv1pb.LeaseCreateRequest{
			Parent:  nsParent(ns),
			LeaseId: "lease-empty",
			Lease:   &jumpstarterv1pb.Lease{},
		})
		Expect(grpcCode(err)).To(Equal(codes.InvalidArgument))
	})

	It("returns NotFound for unknown lease IDs", func() {
		_, err := client.GetLease(context.Background(),
			&jumpstarterv1pb.GetRequest{Name: leaseName(ns, "does-not-exist")})
		Expect(grpcCode(err)).To(Equal(codes.NotFound))
	})

	It("lists leases scoped to the parent namespace only", func() {
		// Two leases here, one in another namespace; List should return one.
		_, err := client.CreateLease(context.Background(), &jumpstarterv1pb.LeaseCreateRequest{
			Parent: nsParent(ns), LeaseId: "lease-a",
			Lease: &jumpstarterv1pb.Lease{Selector: "x=1"},
		})
		Expect(err).NotTo(HaveOccurred())
		other := makeNamespace(context.Background())
		makeClientCRDForCaller(context.Background(), other, "alice")
		_, err = client.CreateLease(context.Background(), &jumpstarterv1pb.LeaseCreateRequest{
			Parent: nsParent(other), LeaseId: "lease-other",
			Lease: &jumpstarterv1pb.Lease{Selector: "x=1"},
		})
		Expect(err).NotTo(HaveOccurred())

		resp, err := client.ListLeases(context.Background(),
			&jumpstarterv1pb.LeaseListRequest{Parent: nsParent(ns)})
		Expect(err).NotTo(HaveOccurred())
		names := make([]string, 0, len(resp.GetLeases()))
		for _, l := range resp.GetLeases() {
			names = append(names, l.GetName())
		}
		Expect(names).To(ContainElement(leaseName(ns, "lease-a")))
		Expect(names).NotTo(ContainElement(leaseName(other, "lease-other")))
	})

	It("updates labels and end_time", func() {
		_, err := client.CreateLease(context.Background(), &jumpstarterv1pb.LeaseCreateRequest{
			Parent: nsParent(ns), LeaseId: "lease-up",
			Lease: &jumpstarterv1pb.Lease{Selector: "x=1"},
		})
		Expect(err).NotTo(HaveOccurred())

		newEnd := time.Now().Add(2 * time.Hour).UTC()
		_, err = client.UpdateLease(context.Background(), &jumpstarterv1pb.LeaseUpdateRequest{
			Lease: &jumpstarterv1pb.Lease{
				Name:    leaseName(ns, "lease-up"),
				Labels:  map[string]string{"team": "qa"},
				EndTime: timestampOf(newEnd),
			},
		})
		Expect(err).NotTo(HaveOccurred())

		var l jumpstarterdevv1alpha1.Lease
		Expect(k8sClient.Get(context.Background(),
			types.NamespacedName{Namespace: ns, Name: "lease-up"}, &l)).To(Succeed())
		Expect(l.Labels).To(HaveKeyWithValue("team", "qa"))
		Expect(l.Spec.EndTime).NotTo(BeNil())
	})

	It("delete sets Spec.Release true (soft delete)", func() {
		_, err := client.CreateLease(context.Background(), &jumpstarterv1pb.LeaseCreateRequest{
			Parent: nsParent(ns), LeaseId: "lease-del",
			Lease: &jumpstarterv1pb.Lease{Selector: "x=1"},
		})
		Expect(err).NotTo(HaveOccurred())
		_, err = client.DeleteLease(context.Background(),
			&jumpstarterv1pb.DeleteRequest{Name: leaseName(ns, "lease-del")})
		Expect(err).NotTo(HaveOccurred())

		var l jumpstarterdevv1alpha1.Lease
		Expect(k8sClient.Get(context.Background(),
			types.NamespacedName{Namespace: ns, Name: "lease-del"}, &l)).To(Succeed())
		Expect(l.Spec.Release).To(BeTrue())
	})

	It("watches lease ADDED events", func() {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		stream, err := client.WatchLeases(ctx,
			&jumpstarterv1pb.WatchRequest{Parent: nsParent(ns)})
		Expect(err).NotTo(HaveOccurred())

		go func() {
			defer GinkgoRecover()
			time.Sleep(200 * time.Millisecond)
			_, _ = client.CreateLease(context.Background(), &jumpstarterv1pb.LeaseCreateRequest{
				Parent: nsParent(ns), LeaseId: "lease-watch",
				Lease: &jumpstarterv1pb.Lease{Selector: "x=1"},
			})
		}()

		Expect(consumeLeaseAdded(stream, leaseName(ns, "lease-watch"))).To(Succeed())
	})
})

// consumeLeaseAdded blocks until the stream emits an ADDED event for the
// named lease or the stream ends. Replaces an Eventually-around-Recv
// pattern that leaks goroutines on retry.
func consumeLeaseAdded(stream adminv1pb.LeaseService_WatchLeasesClient, want string) error {
	for {
		ev, err := stream.Recv()
		if err == io.EOF {
			return io.EOF
		}
		if err != nil {
			return err
		}
		if ev.GetEventType() == adminv1pb.EventType_EVENT_TYPE_ADDED &&
			ev.GetLease().GetName() == want {
			return nil
		}
	}
}

// seedExporter creates a placeholder Exporter CRD so Lease.Create can pin
// to it. We bypass the admin RPC because creating an Exporter through it
// blocks waiting for the ExporterReconciler — which the e2e suite does not
// run — and that's a dependency we don't need for lease tests.
func seedExporter(ctx context.Context, ns, name string) {
	GinkgoHelper()
	username := "alice"
	exp := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Namespace:   ns,
			Name:        name,
			Annotations: map[string]string{identity.OwnerAnnotation: ownerIdentity("alice").OwnerHash()},
		},
		Spec: jumpstarterdevv1alpha1.ExporterSpec{Username: &username},
	}
	Expect(k8sClient.Create(ctx, exp)).To(Succeed())
}

// timestampOf is a tiny adapter so test specs read straight-line.
func timestampOf(t time.Time) *timestamppb.Timestamp { return timestamppb.New(t) }

// ptr returns a pointer to v. Used for proto optional string fields where
// callers must supply a *string.
func ptr[T any](v T) *T { return &v }
