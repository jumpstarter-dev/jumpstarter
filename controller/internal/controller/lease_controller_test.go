/*
Copyright 2024.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
*/

package controller

import (
	"context"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
)

var leaseDutA2Sec = &jumpstarterdevv1alpha1.Lease{
	ObjectMeta: metav1.ObjectMeta{
		Name:      "lease1",
		Namespace: "default",
	},
	Spec: jumpstarterdevv1alpha1.LeaseSpec{
		Client: &corev1.ObjectReference{
			Name:      testClient.Name,
			Namespace: testClient.Namespace,
		},
		Selector: metav1.LabelSelector{
			MatchLabels: map[string]string{
				"dut": "a",
			},
		},
		Duration: metav1.Duration{
			Duration: 2 * time.Second,
		},
	},
}
var _ = Describe("Lease Controller", func() {
	BeforeEach(func() {
		createExporters(context.Background(), testExporter1DutA, testExporter2DutA, testExporter3DutB)
	})
	AfterEach(func() {
		ctx := context.Background()
		deleteExporters(ctx, testExporter1DutA, testExporter2DutA, testExporter3DutB)
		deleteLeases(ctx, "lease1", "lease2", "lease3")
	})

	When("trying to lease an available exporter", func() {
		It("should acquire lease right away", func() {
			lease := leaseDutA2Sec.DeepCopy()

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.Exporter).NotTo(BeNil())
			Expect(updatedLease.Status.Exporter.Name).To(BeElementOf([]string{testExporter1DutA.Name, testExporter2DutA.Name}))
			Expect(updatedLease.Status.BeginTime).NotTo(BeNil())
			Expect(updatedLease.Status.EndTime).NotTo(BeNil())

			updatedExporter := getExporter(ctx, updatedLease.Status.Exporter.Name)
			Expect(updatedExporter.Status.Lease).NotTo(BeNil())
			Expect(updatedExporter.Status.Lease.Name).To(Equal(lease.Name))
		})

		It("should be released after the lease time", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Duration.Duration = 100 * time.Millisecond

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.Exporter).NotTo(BeNil())

			exporterName := updatedLease.Status.Exporter.Name

			time.Sleep(200 * time.Millisecond)
			_ = reconcileLease(ctx, lease)

			updatedLease = getLease(ctx, lease.Name)

			// exporter is retained for record purposes
			Expect(updatedLease.Status.Exporter).NotTo(BeNil())
			// but the ended flag to be set
			Expect(updatedLease.Status.Ended).To(BeTrue())

			// the exporter should have no lease mark on status
			updatedExporter := getExporter(ctx, exporterName)
			Expect(updatedExporter.Status.Lease).To(BeNil())

		})
	})

	When("trying to lease a non existing exporter", func() {
		It("should fail right away", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Selector.MatchLabels["dut"] = "does-not-exist"

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.Exporter).To(BeNil())

			// TODO: add and check status conditions of the lease to indicate
			// that no exporter exists for the given selector

		})
	})

	When("trying to lease an offline exporter", func() {
		It("should fail right away", func() {
			// TODO: add and check an Online status condition to the exporters
			// and a status condition to the lease to indicate failure to acquire
			Skip("Not implemented")
		})
	})

	When("trying to lease exporters, and some matching exporters are online and while others are offline", func() {
		It("should acquire lease for the online exporters", func() {
			// TODO: add and check an Online status condition to the exporters
			Skip("Not implemented")
		})
	})

	When("trying to lease a busy exporter", func() {
		It("should not be acquired", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Selector.MatchLabels["dut"] = "b"

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.Exporter).NotTo(BeNil())
			Expect(updatedLease.Status.Exporter.Name).To(Equal(testExporter3DutB.Name))

			updatedExporter := getExporter(ctx, updatedLease.Status.Exporter.Name)
			Expect(updatedExporter.Status.Lease).NotTo(BeNil())
			Expect(updatedExporter.Status.Lease.Name).To(Equal(lease.Name))

			// create another lease that attempts to acquire the only dut b exporter
			// which is already leased
			lease2 := leaseDutA2Sec.DeepCopy()
			lease2.Name = "lease2"
			lease2.Spec.Selector.MatchLabels["dut"] = "b"
			Expect(k8sClient.Create(ctx, lease2)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease = getLease(ctx, lease2.Name)
			Expect(updatedLease.Status.Exporter).To(BeNil())
			// TODO: add and check status conditions of the lease to indicate that the lease is waiting

		})

		It("should be acquired when a valid exporter lease times out", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Selector.MatchLabels["dut"] = "b"
			lease.Spec.Duration.Duration = 500 * time.Millisecond

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.Exporter).NotTo(BeNil())
			Expect(updatedLease.Status.Exporter.Name).To(Equal(testExporter3DutB.Name))

			updatedExporter := getExporter(ctx, updatedLease.Status.Exporter.Name)
			Expect(updatedExporter.Status.Lease).NotTo(BeNil())
			Expect(updatedExporter.Status.Lease.Name).To(Equal(lease.Name))

			// create another lease that attempts to acquire the only dut b exporter
			// which is already leased
			lease2 := leaseDutA2Sec.DeepCopy()
			lease2.Name = "lease2"
			lease2.Spec.Selector.MatchLabels["dut"] = "b"
			Expect(k8sClient.Create(ctx, lease2)).To(Succeed())
			_ = reconcileLease(ctx, lease2)

			updatedLease = getLease(ctx, lease2.Name)
			Expect(updatedLease.Status.Exporter).To(BeNil())
			// TODO: add and check status conditions of the lease to indicate that the lease is waiting

			time.Sleep(501 * time.Millisecond)
			_ = reconcileLease(ctx, lease)
			_ = reconcileLease(ctx, lease2)
			updatedLease = getLease(ctx, lease2.Name)
			Expect(updatedLease.Status.Exporter).NotTo(BeNil())

		})
	})

	When("releasing a lease early", func() {
		It("should release the lease and exporter right away", func() {
			lease := leaseDutA2Sec.DeepCopy()

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.Exporter).NotTo(BeNil())

			exporterName := updatedLease.Status.Exporter.Name

			// release the lease early
			// TODO: through the API we cannot set the status condition, we get this through the RPC,
			// we should consider adding a flag on the spec to do this, or look at the duration too
			updatedLease.Spec.Release = true

			endTime := updatedLease.Status.EndTime

			Expect(k8sClient.Update(ctx, updatedLease)).To(Succeed())

			_ = reconcileLease(ctx, updatedLease)

			updatedLease = getLease(ctx, lease.Name)
			Expect(updatedLease.Status.Exporter).NotTo(BeNil())
			Expect(updatedLease.Status.Ended).To(BeTrue())
			Expect(updatedLease.Status.EndTime).ToNot(Equal(endTime))

			updatedExporter := getExporter(ctx, exporterName)
			Expect(updatedExporter.Status.Lease).To(BeNil())
		})
	})
})

var testExporter1DutA = &jumpstarterdevv1alpha1.Exporter{
	ObjectMeta: metav1.ObjectMeta{
		Name:      "exporter1-dut-a",
		Namespace: "default",
		Labels: map[string]string{
			"dut": "a",
		},
	},
}

var testExporter2DutA = &jumpstarterdevv1alpha1.Exporter{
	ObjectMeta: metav1.ObjectMeta{
		Name:      "exporter2-dut-a",
		Namespace: "default",
		Labels: map[string]string{
			"dut": "a",
		},
	},
}

var testExporter3DutB = &jumpstarterdevv1alpha1.Exporter{
	ObjectMeta: metav1.ObjectMeta{
		Name:      "exporter3-dut-b",
		Namespace: "default",
		Labels: map[string]string{
			"dut": "b",
		},
	},
}

func reconcileLease(ctx context.Context, lease *jumpstarterdevv1alpha1.Lease) reconcile.Result {

	// reconcile the exporters
	typeNamespacedName := types.NamespacedName{
		Name:      lease.Name,
		Namespace: "default",
	}

	leaseReconciler := &LeaseReconciler{
		Client: k8sClient,
		Scheme: k8sClient.Scheme(),
	}

	res, err := leaseReconciler.Reconcile(ctx, reconcile.Request{
		NamespacedName: typeNamespacedName,
	})
	Expect(err).NotTo(HaveOccurred())
	return res
}

func getLease(ctx context.Context, name string) *jumpstarterdevv1alpha1.Lease {
	lease := &jumpstarterdevv1alpha1.Lease{}
	err := k8sClient.Get(ctx, types.NamespacedName{
		Name:      name,
		Namespace: "default",
	}, lease)
	Expect(err).NotTo(HaveOccurred())
	return lease
}

func getExporter(ctx context.Context, name string) *jumpstarterdevv1alpha1.Exporter {
	exporter := &jumpstarterdevv1alpha1.Exporter{}
	err := k8sClient.Get(ctx, types.NamespacedName{
		Name:      name,
		Namespace: "default",
	}, exporter)
	Expect(err).NotTo(HaveOccurred())
	return exporter
}

func deleteLeases(ctx context.Context, leases ...string) {
	for _, lease := range leases {
		leaseObj := &jumpstarterdevv1alpha1.Lease{
			ObjectMeta: metav1.ObjectMeta{
				Name:      lease,
				Namespace: "default",
			},
		}
		_ = k8sClient.Delete(ctx, leaseObj)
	}
}
