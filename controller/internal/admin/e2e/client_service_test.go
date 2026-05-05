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

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

// admin.v1.ClientService.CreateClient — like CreateExporter — blocks
// waiting for a reconciler to mint the bootstrap Secret. The suite does
// not run that reconciler, so Get/List/Update/Delete/Watch are exercised
// by seeding Client CRDs directly. CreateClient is exercised in the
// authn_test owner-stamping spec, with an Eventually loop reading the CRD
// after the RPC times out.
var _ = Describe("admin.v1.ClientService", func() {
	var (
		ns     string
		conn   *grpc.ClientConn
		client adminv1pb.ClientServiceClient
	)

	BeforeEach(func() {
		ns = makeNamespace(context.Background())
		applyClusterRoleBinding(context.Background(), usernameFor("alice"),
			[]string{"get", "list", "create", "update", "patch", "delete", "watch"},
			[]string{"clients"})
		seedClient(context.Background(), ns, "client-a")
		seedClient(context.Background(), ns, "client-b")

		conn = dialAdmin(tokenFor("alice"))
		client = adminv1pb.NewClientServiceClient(conn)
	})

	AfterEach(func() {
		_ = conn.Close()
	})

	It("gets a seeded client", func() {
		got, err := client.GetClient(context.Background(),
			&jumpstarterv1pb.GetRequest{Name: clientName(ns, "client-a")})
		Expect(err).NotTo(HaveOccurred())
		Expect(got.GetName()).To(Equal(clientName(ns, "client-a")))
	})

	It("returns NotFound for unknown client", func() {
		_, err := client.GetClient(context.Background(),
			&jumpstarterv1pb.GetRequest{Name: clientName(ns, "missing")})
		Expect(grpcCode(err)).To(Equal(codes.NotFound))
	})

	It("lists clients scoped to the namespace", func() {
		resp, err := client.ListClients(context.Background(),
			&jumpstarterv1pb.ClientListRequest{Parent: nsParent(ns)})
		Expect(err).NotTo(HaveOccurred())
		names := make([]string, 0, len(resp.GetClients()))
		for _, c := range resp.GetClients() {
			names = append(names, c.GetName())
		}
		Expect(names).To(ConsistOf(clientName(ns, "client-a"), clientName(ns, "client-b")))
	})

	It("updates client labels", func() {
		_, err := client.UpdateClient(context.Background(), &jumpstarterv1pb.ClientUpdateRequest{
			Client: &jumpstarterv1pb.Client{
				Name:   clientName(ns, "client-a"),
				Labels: map[string]string{"team": "qa"},
			},
		})
		Expect(err).NotTo(HaveOccurred())

		var c jumpstarterdevv1alpha1.Client
		Expect(k8sClient.Get(context.Background(),
			types.NamespacedName{Namespace: ns, Name: "client-a"}, &c)).To(Succeed())
		Expect(c.Labels).To(HaveKeyWithValue("team", "qa"))
		Expect(c.Annotations).To(HaveKeyWithValue(identity.OwnerAnnotation,
			ownerIdentity("alice").OwnerHash()))
	})

	It("deletes a client", func() {
		_, err := client.DeleteClient(context.Background(),
			&jumpstarterv1pb.DeleteRequest{Name: clientName(ns, "client-b")})
		Expect(err).NotTo(HaveOccurred())

		var c jumpstarterdevv1alpha1.Client
		err = k8sClient.Get(context.Background(),
			types.NamespacedName{Namespace: ns, Name: "client-b"}, &c)
		Expect(err).To(HaveOccurred())
	})

	It("watches MODIFIED events", func() {
		ctx, cancel := context.WithTimeout(context.Background(), 15*time.Second)
		defer cancel()
		stream, err := client.WatchClients(ctx,
			&jumpstarterv1pb.WatchRequest{Parent: nsParent(ns)})
		Expect(err).NotTo(HaveOccurred())

		go func() {
			defer GinkgoRecover()
			time.Sleep(200 * time.Millisecond)
			_, _ = client.UpdateClient(context.Background(), &jumpstarterv1pb.ClientUpdateRequest{
				Client: &jumpstarterv1pb.Client{
					Name:   clientName(ns, "client-a"),
					Labels: map[string]string{"watched": "yes"},
				},
			})
		}()

		Expect(consumeClientEvent(stream,
			adminv1pb.EventType_EVENT_TYPE_MODIFIED,
			clientName(ns, "client-a"))).To(Succeed())
	})
})

func consumeClientEvent(stream adminv1pb.ClientService_WatchClientsClient, want adminv1pb.EventType, name string) error {
	for {
		ev, err := stream.Recv()
		if err == io.EOF {
			return io.EOF
		}
		if err != nil {
			return err
		}
		if ev.GetEventType() == want && ev.GetClient().GetName() == name {
			return nil
		}
	}
}

// seedClient creates a Client CRD owned by alice. We bypass the admin
// CreateClient RPC because it blocks on the ClientReconciler we don't run.
func seedClient(ctx context.Context, ns, name string) {
	GinkgoHelper()
	username := "alice"
	c := &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{
			Namespace:   ns,
			Name:        name,
			Annotations: map[string]string{identity.OwnerAnnotation: ownerIdentity("alice").OwnerHash()},
		},
		Spec: jumpstarterdevv1alpha1.ClientSpec{Username: &username},
	}
	Expect(k8sClient.Create(ctx, c)).To(Succeed())
}
