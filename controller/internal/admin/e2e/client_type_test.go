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

	"google.golang.org/grpc"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	adminv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/admin/v1"
	jumpstarterv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// admin.v1 surfaces a ClientType so consumers can render the auto-
// provisioned identity Client (OIDC bearer = the principal itself)
// distinctly from bot/service Clients (static bootstrap token).
var _ = Describe("admin.v1.ClientService Type classification", func() {
	var (
		ns   string
		conn *grpc.ClientConn
		cli  adminv1pb.ClientServiceClient
	)

	BeforeEach(func() {
		ns = makeNamespace(context.Background())
		applyClusterRoleBinding(context.Background(), usernameFor("alice"),
			[]string{"get", "list", "create", "update", "patch", "delete", "watch"},
			[]string{"clients"})
		conn = dialAdmin(tokenFor("alice"))
		cli = adminv1pb.NewClientServiceClient(conn)
	})

	AfterEach(func() {
		_ = conn.Close()
	})

	It("classifies an auto-provisioned-shaped Client as OIDC", func() {
		// The legacy client.v1 reconciler stamps spec.username from the
		// OIDC subject and never sets the admin owner annotation —
		// reproduce that exact shape here.
		username := "dex:alice"
		c := &jumpstarterdevv1alpha1.Client{
			ObjectMeta: metav1.ObjectMeta{Namespace: ns, Name: "auto-provisioned"},
			Spec:       jumpstarterdevv1alpha1.ClientSpec{Username: &username},
		}
		Expect(k8sClient.Create(context.Background(), c)).To(Succeed())

		got, err := cli.GetClient(context.Background(), &jumpstarterv1pb.GetRequest{
			Name: clientName(ns, "auto-provisioned"),
		})
		Expect(err).NotTo(HaveOccurred())
		Expect(got.GetType()).To(Equal(jumpstarterv1pb.ClientType_CLIENT_TYPE_OIDC))
	})

	It("classifies an admin.v1-stamped Client as TOKEN", func() {
		// seedClient sets the admin owner annotation, mirroring what
		// admin.v1.CreateClient does internally.
		seedClient(context.Background(), ns, "bot")

		got, err := cli.GetClient(context.Background(), &jumpstarterv1pb.GetRequest{
			Name: clientName(ns, "bot"),
		})
		Expect(err).NotTo(HaveOccurred())
		Expect(got.GetType()).To(Equal(jumpstarterv1pb.ClientType_CLIENT_TYPE_TOKEN))
	})

	It("classifies a bare kubectl-applied Client as TOKEN", func() {
		c := &jumpstarterdevv1alpha1.Client{
			ObjectMeta: metav1.ObjectMeta{Namespace: ns, Name: "bare"},
		}
		Expect(k8sClient.Create(context.Background(), c)).To(Succeed())

		got, err := cli.GetClient(context.Background(), &jumpstarterv1pb.GetRequest{
			Name: clientName(ns, "bare"),
		})
		Expect(err).NotTo(HaveOccurred())
		Expect(got.GetType()).To(Equal(jumpstarterv1pb.ClientType_CLIENT_TYPE_TOKEN))
	})
})
