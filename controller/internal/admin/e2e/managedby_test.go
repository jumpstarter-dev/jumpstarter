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
	"google.golang.org/grpc/codes"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	adminv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/admin/v1"
	jumpstarterv1pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"

	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

// admin.v1 must refuse Update/Delete on resources tracked by an external
// tool (ArgoCD/Helm/kustomize-controller/Flux) — any change would be
// reverted on the next reconciliation. The signal is the well-known
// `app.kubernetes.io/managed-by` label, with ArgoCD's tracking-id and
// Helm's release-name annotations as belt-and-suspenders fallbacks.
var _ = Describe("admin.v1 externally-managed protection", func() {
	var (
		ns   string
		conn *grpc.ClientConn
	)

	BeforeEach(func() {
		ns = makeNamespace(context.Background())
		// Cluster-wide grant — tests below assert that even a
		// cluster-admin caller can't bypass the managed-by check.
		applyClusterRoleBinding(context.Background(), usernameFor("alice"),
			[]string{"get", "list", "create", "update", "patch", "delete", "watch"},
			[]string{"clients", "exporters", "leases", "webhooks"})
		conn = dialAdmin(tokenFor("alice"))
	})

	AfterEach(func() {
		_ = conn.Close()
	})

	Context("Client", func() {
		It("metadata.externally_managed reflects the managed-by label", func() {
			seedManagedClient(context.Background(), ns, "argo-owned",
				map[string]string{"app.kubernetes.io/managed-by": "argocd"}, nil)
			seedManagedClient(context.Background(), ns, "ours", nil, nil)

			cli := adminv1pb.NewClientServiceClient(conn)
			argo, err := cli.GetClient(context.Background(), &jumpstarterv1pb.GetRequest{
				Name: clientName(ns, "argo-owned"),
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(argo.GetMetadata().GetExternallyManaged()).To(BeTrue())

			ours, err := cli.GetClient(context.Background(), &jumpstarterv1pb.GetRequest{
				Name: clientName(ns, "ours"),
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(ours.GetMetadata().GetExternallyManaged()).To(BeFalse())
		})

		It("Update is refused with FailedPrecondition when managed-by=argocd", func() {
			seedManagedClient(context.Background(), ns, "argo-owned",
				map[string]string{"app.kubernetes.io/managed-by": "argocd"}, nil)

			cli := adminv1pb.NewClientServiceClient(conn)
			_, err := cli.UpdateClient(context.Background(), &jumpstarterv1pb.ClientUpdateRequest{
				Client: &jumpstarterv1pb.Client{
					Name:   clientName(ns, "argo-owned"),
					Labels: map[string]string{"x": "y"},
				},
			})
			Expect(grpcCode(err)).To(Equal(codes.FailedPrecondition))
		})

		It("Delete is refused with FailedPrecondition when managed-by=argocd", func() {
			seedManagedClient(context.Background(), ns, "argo-owned",
				map[string]string{"app.kubernetes.io/managed-by": "argocd"}, nil)

			cli := adminv1pb.NewClientServiceClient(conn)
			_, err := cli.DeleteClient(context.Background(), &jumpstarterv1pb.DeleteRequest{
				Name: clientName(ns, "argo-owned"),
			})
			Expect(grpcCode(err)).To(Equal(codes.FailedPrecondition))
		})

		It("ArgoCD tracking-id annotation alone is also detected", func() {
			seedManagedClient(context.Background(), ns, "argo-tracking", nil,
				map[string]string{"argocd.argoproj.io/tracking-id": "demo:apps/Deployment:default/x"})

			cli := adminv1pb.NewClientServiceClient(conn)
			got, err := cli.GetClient(context.Background(), &jumpstarterv1pb.GetRequest{
				Name: clientName(ns, "argo-tracking"),
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(got.GetMetadata().GetExternallyManaged()).To(BeTrue())

			_, err = cli.DeleteClient(context.Background(), &jumpstarterv1pb.DeleteRequest{
				Name: clientName(ns, "argo-tracking"),
			})
			Expect(grpcCode(err)).To(Equal(codes.FailedPrecondition))
		})

		It("Helm release-name annotation alone is also detected", func() {
			seedManagedClient(context.Background(), ns, "helm-owned", nil,
				map[string]string{"meta.helm.sh/release-name": "lab-release"})

			cli := adminv1pb.NewClientServiceClient(conn)
			_, err := cli.UpdateClient(context.Background(), &jumpstarterv1pb.ClientUpdateRequest{
				Client: &jumpstarterv1pb.Client{
					Name:   clientName(ns, "helm-owned"),
					Labels: map[string]string{"x": "y"},
				},
			})
			Expect(grpcCode(err)).To(Equal(codes.FailedPrecondition))
		})

		It("Update on a Client with no managed-by signal still works", func() {
			seedClient(context.Background(), ns, "ours-mutable")

			cli := adminv1pb.NewClientServiceClient(conn)
			_, err := cli.UpdateClient(context.Background(), &jumpstarterv1pb.ClientUpdateRequest{
				Client: &jumpstarterv1pb.Client{
					Name:   clientName(ns, "ours-mutable"),
					Labels: map[string]string{"team": "qa"},
				},
			})
			Expect(err).NotTo(HaveOccurred())
		})
	})

	Context("Exporter", func() {
		It("Update + Delete are refused when managed-by is set", func() {
			seedManagedExporter(context.Background(), ns, "argo-exp",
				map[string]string{"app.kubernetes.io/managed-by": "Helm"})

			cli := adminv1pb.NewExporterServiceClient(conn)
			got, err := cli.GetExporter(context.Background(), &jumpstarterv1pb.GetRequest{
				Name: exporterName(ns, "argo-exp"),
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(got.GetMetadata().GetExternallyManaged()).To(BeTrue())

			_, err = cli.UpdateExporter(context.Background(), &jumpstarterv1pb.ExporterUpdateRequest{
				Exporter: &jumpstarterv1pb.Exporter{
					Name:   exporterName(ns, "argo-exp"),
					Labels: map[string]string{"x": "y"},
				},
			})
			Expect(grpcCode(err)).To(Equal(codes.FailedPrecondition))

			_, err = cli.DeleteExporter(context.Background(), &jumpstarterv1pb.DeleteRequest{
				Name: exporterName(ns, "argo-exp"),
			})
			Expect(grpcCode(err)).To(Equal(codes.FailedPrecondition))
		})
	})

	Context("Lease", func() {
		It("Update + Delete are refused when managed-by is set", func() {
			seedExporter(context.Background(), ns, "exp-pin")
			makeClientCRDForCaller(context.Background(), ns, "alice")
			seedManagedLease(context.Background(), ns, "argo-lease",
				map[string]string{"app.kubernetes.io/managed-by": "kustomize-controller"})

			cli := adminv1pb.NewLeaseServiceClient(conn)
			got, err := cli.GetLease(context.Background(), &jumpstarterv1pb.GetRequest{
				Name: leaseName(ns, "argo-lease"),
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(got.GetMetadata().GetExternallyManaged()).To(BeTrue())

			_, err = cli.UpdateLease(context.Background(), &jumpstarterv1pb.LeaseUpdateRequest{
				Lease: &jumpstarterv1pb.Lease{
					Name:   leaseName(ns, "argo-lease"),
					Labels: map[string]string{"x": "y"},
				},
			})
			Expect(grpcCode(err)).To(Equal(codes.FailedPrecondition))

			_, err = cli.DeleteLease(context.Background(), &jumpstarterv1pb.DeleteRequest{
				Name: leaseName(ns, "argo-lease"),
			})
			Expect(grpcCode(err)).To(Equal(codes.FailedPrecondition))
		})
	})

	Context("Webhook", func() {
		It("Update + Delete are refused when managed-by is set", func() {
			makeSecret(context.Background(), ns, "wh-secret", "key", "abc")
			seedManagedWebhook(context.Background(), ns, "argo-wh",
				map[string]string{"app.kubernetes.io/managed-by": "argocd"})

			cli := adminv1pb.NewWebhookServiceClient(conn)
			got, err := cli.GetWebhook(context.Background(), &jumpstarterv1pb.GetRequest{
				Name: webhookName(ns, "argo-wh"),
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(got.GetMetadata().GetExternallyManaged()).To(BeTrue())

			_, err = cli.UpdateWebhook(context.Background(), &adminv1pb.WebhookUpdateRequest{
				Webhook: &adminv1pb.Webhook{
					Name: webhookName(ns, "argo-wh"),
					Url:  "https://other.invalid/h",
				},
			})
			Expect(grpcCode(err)).To(Equal(codes.FailedPrecondition))

			_, err = cli.DeleteWebhook(context.Background(), &jumpstarterv1pb.DeleteRequest{
				Name: webhookName(ns, "argo-wh"),
			})
			Expect(grpcCode(err)).To(Equal(codes.FailedPrecondition))
		})
	})
})

// --- seed helpers for the externally-managed suite ---
//
// These bypass the admin RPCs (which would never let us stamp the
// foreign labels in the first place — Create handlers don't blindly
// pass through arbitrary labels) and apply the CRD directly.

func seedManagedClient(ctx context.Context, ns, name string, labels, annotations map[string]string) {
	GinkgoHelper()
	c := &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{
			Namespace:   ns,
			Name:        name,
			Labels:      labels,
			Annotations: annotations,
		},
	}
	Expect(k8sClient.Create(ctx, c)).To(Succeed())
}

func seedManagedExporter(ctx context.Context, ns, name string, labels map[string]string) {
	GinkgoHelper()
	username := "alice"
	exp := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: ns,
			Name:      name,
			Labels:    labels,
		},
		Spec: jumpstarterdevv1alpha1.ExporterSpec{Username: &username},
	}
	Expect(k8sClient.Create(ctx, exp)).To(Succeed())
}

func seedManagedLease(ctx context.Context, ns, name string, labels map[string]string) {
	GinkgoHelper()
	l := &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: ns,
			Name:      name,
			Labels:    labels,
		},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			Selector: metav1.LabelSelector{MatchLabels: map[string]string{"any": "any"}},
		},
	}
	Expect(k8sClient.Create(ctx, l)).To(Succeed())
}

func seedManagedWebhook(ctx context.Context, ns, name string, labels map[string]string) {
	GinkgoHelper()
	w := &jumpstarterdevv1alpha1.Webhook{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: ns,
			Name:      name,
			Labels:    labels,
		},
		Spec: jumpstarterdevv1alpha1.WebhookSpec{
			URL: "https://example.invalid/h",
			SecretRef: corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{Name: "wh-secret"},
				Key:                  "key",
			},
			Events: []string{jumpstarterdevv1alpha1.WebhookEventLeaseCreated},
		},
	}
	Expect(k8sClient.Create(ctx, w)).To(Succeed())
}
