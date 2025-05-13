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
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
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
		ClientRef: corev1.LocalObjectReference{
			Name: testClient.Name,
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
		setExporterOnlineConditions(context.Background(), testExporter1DutA.Name, metav1.ConditionTrue)
		setExporterOnlineConditions(context.Background(), testExporter2DutA.Name, metav1.ConditionTrue)
		setExporterOnlineConditions(context.Background(), testExporter3DutB.Name, metav1.ConditionTrue)
	})
	AfterEach(func() {
		ctx := context.Background()
		deleteExporters(ctx, testExporter1DutA, testExporter2DutA, testExporter3DutB)
		deleteLeases(ctx, "lease1", "lease2", "lease3")
	})

	When("trying to lease with an empty selector", func() {
		It("should fail right away", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Selector.MatchLabels = nil

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())

			Expect(meta.IsStatusConditionTrue(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypeInvalid),
			)).To(BeTrue())
		})
	})

	When("trying to lease an available exporter", func() {
		It("should acquire lease right away", func() {
			lease := leaseDutA2Sec.DeepCopy()

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			Expect(updatedLease.Status.ExporterRef.Name).To(BeElementOf([]string{testExporter1DutA.Name, testExporter2DutA.Name}))
			Expect(updatedLease.Status.BeginTime).NotTo(BeNil())

			updatedExporter := getExporter(ctx, updatedLease.Status.ExporterRef.Name)
			Expect(updatedExporter.Status.LeaseRef).NotTo(BeNil())
			Expect(updatedExporter.Status.LeaseRef.Name).To(Equal(lease.Name))
		})

		It("should be released after the lease time", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Duration.Duration = 100 * time.Millisecond

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())

			exporterName := updatedLease.Status.ExporterRef.Name

			time.Sleep(200 * time.Millisecond)
			_ = reconcileLease(ctx, lease)

			updatedLease = getLease(ctx, lease.Name)

			// exporter is retained for record purposes
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			// but the ended flag to be set
			Expect(updatedLease.Status.Ended).To(BeTrue())

			// the exporter should have no lease mark on status
			updatedExporter := getExporter(ctx, exporterName)
			Expect(updatedExporter.Status.LeaseRef).To(BeNil())

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
			Expect(updatedLease.Status.ExporterRef).To(BeNil())

			Expect(meta.IsStatusConditionTrue(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable),
			)).To(BeTrue())
		})
	})

	When("trying to lease an offline exporter", func() {
		It("should fail right away", func() {
			lease := leaseDutA2Sec.DeepCopy()

			ctx := context.Background()

			setExporterOnlineConditions(ctx, testExporter1DutA.Name, metav1.ConditionFalse)
			setExporterOnlineConditions(ctx, testExporter2DutA.Name, metav1.ConditionFalse)

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())

			Expect(meta.IsStatusConditionTrue(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable),
			)).To(BeTrue())
		})
	})

	When("trying to lease exporters, and some matching exporters are online and while others are offline", func() {
		It("should acquire lease for the online exporters", func() {
			lease := leaseDutA2Sec.DeepCopy()

			ctx := context.Background()

			setExporterOnlineConditions(ctx, testExporter1DutA.Name, metav1.ConditionFalse)

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			Expect(updatedLease.Status.ExporterRef.Name).To(BeElementOf([]string{testExporter2DutA.Name}))
			Expect(updatedLease.Status.BeginTime).NotTo(BeNil())

			updatedExporter := getExporter(ctx, updatedLease.Status.ExporterRef.Name)
			Expect(updatedExporter.Status.LeaseRef).NotTo(BeNil())
			Expect(updatedExporter.Status.LeaseRef.Name).To(Equal(lease.Name))
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
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			Expect(updatedLease.Status.ExporterRef.Name).To(Equal(testExporter3DutB.Name))

			updatedExporter := getExporter(ctx, updatedLease.Status.ExporterRef.Name)
			Expect(updatedExporter.Status.LeaseRef).NotTo(BeNil())
			Expect(updatedExporter.Status.LeaseRef.Name).To(Equal(lease.Name))

			// create another lease that attempts to acquire the only dut b exporter
			// which is already leased
			lease2 := leaseDutA2Sec.DeepCopy()
			lease2.Name = "lease2"
			lease2.Spec.Selector.MatchLabels["dut"] = "b"
			Expect(k8sClient.Create(ctx, lease2)).To(Succeed())
			_ = reconcileLease(ctx, lease2)

			updatedLease = getLease(ctx, lease2.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())

			Expect(meta.IsStatusConditionTrue(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypePending),
			)).To(BeTrue())
		})

		It("should be acquired when a valid exporter lease times out", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Selector.MatchLabels["dut"] = "b"
			lease.Spec.Duration.Duration = 500 * time.Millisecond

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			Expect(updatedLease.Status.ExporterRef.Name).To(Equal(testExporter3DutB.Name))

			updatedExporter := getExporter(ctx, updatedLease.Status.ExporterRef.Name)
			Expect(updatedExporter.Status.LeaseRef).NotTo(BeNil())
			Expect(updatedExporter.Status.LeaseRef.Name).To(Equal(lease.Name))

			// create another lease that attempts to acquire the only dut b exporter
			// which is already leased
			lease2 := leaseDutA2Sec.DeepCopy()
			lease2.Name = "lease2"
			lease2.Spec.Selector.MatchLabels["dut"] = "b"
			Expect(k8sClient.Create(ctx, lease2)).To(Succeed())
			_ = reconcileLease(ctx, lease2)

			updatedLease = getLease(ctx, lease2.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())
			// TODO: add and check status conditions of the lease to indicate that the lease is waiting

			time.Sleep(501 * time.Millisecond)
			_ = reconcileLease(ctx, lease)
			_ = reconcileLease(ctx, lease2)
			updatedLease = getLease(ctx, lease2.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())

		})
	})

	When("releasing a lease early", func() {
		It("should release the lease and exporter right away", func() {
			lease := leaseDutA2Sec.DeepCopy()

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())

			exporterName := updatedLease.Status.ExporterRef.Name

			// release the lease early
			// TODO: through the API we cannot set the status condition, we get this through the RPC,
			// we should consider adding a flag on the spec to do this, or look at the duration too
			updatedLease.Spec.Release = true

			Expect(k8sClient.Update(ctx, updatedLease)).To(Succeed())

			_ = reconcileLease(ctx, updatedLease)

			updatedLease = getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			Expect(updatedLease.Status.Ended).To(BeTrue())

			updatedExporter := getExporter(ctx, exporterName)
			Expect(updatedExporter.Status.LeaseRef).To(BeNil())
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

func setExporterOnlineConditions(ctx context.Context, name string, status metav1.ConditionStatus) {
	exporter := getExporter(ctx, name)
	meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
		Type:   string(jumpstarterdevv1alpha1.ExporterConditionTypeRegistered),
		Status: status,
		Reason: "dummy",
	})
	meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
		Type:   string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
		Status: status,
		Reason: "dummy",
	})
	if status == metav1.ConditionTrue {
		exporter.Status.Devices = []jumpstarterdevv1alpha1.Device{{}}
		exporter.Status.LastSeen = metav1.Now()
	} else {
		exporter.Status.Devices = nil
		exporter.Status.LastSeen = metav1.NewTime(metav1.Now().Add(-time.Minute * 2))
	}
	Expect(k8sClient.Status().Update(ctx, exporter)).To(Succeed())
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

	signer, err := oidc.NewSignerFromSeed([]byte{}, "https://example.com", "dummy")
	Expect(err).NotTo(HaveOccurred())

	exporterReconciler := &ExporterReconciler{
		Client: k8sClient,
		Scheme: k8sClient.Scheme(),
		Signer: signer,
	}

	res, err := leaseReconciler.Reconcile(ctx, reconcile.Request{
		NamespacedName: typeNamespacedName,
	})
	Expect(err).NotTo(HaveOccurred())

	for _, owner := range getLease(ctx, lease.Name).OwnerReferences {
		_, err := exporterReconciler.Reconcile(ctx, reconcile.Request{
			NamespacedName: types.NamespacedName{Namespace: lease.Namespace, Name: owner.Name},
		})
		Expect(err).NotTo(HaveOccurred())
	}

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

var _ = Describe("orderApprovedExporters", func() {
	When("approved exporters are under a lease", func() {
		It("should put them last", func() {
			approvedExporters := []ApprovedExporter{
				{
					Policy:        jumpstarterdevv1alpha1.Policy{Priority: 0, SpotAccess: false},
					Exporter:      *testExporter1DutA,
					ExistingLease: &jumpstarterdevv1alpha1.Lease{},
				},
				{
					Policy:   jumpstarterdevv1alpha1.Policy{Priority: 0, SpotAccess: false},
					Exporter: *testExporter2DutA,
				},
			}
			ordered := orderApprovedExporters(approvedExporters)
			Expect(ordered[0].Exporter.Name).To(Equal(testExporter2DutA.Name))
			Expect(ordered[0].ExistingLease).To(BeNil())
			Expect(ordered[1].Exporter.Name).To(Equal(testExporter1DutA.Name))
			Expect(ordered[1].ExistingLease).NotTo(BeNil())
		})
	})

	When("some approved exporters are accessible in spot mode", func() {
		It("should put them last", func() {
			approvedExporters := []ApprovedExporter{
				{
					Policy:        jumpstarterdevv1alpha1.Policy{Priority: 0, SpotAccess: true},
					Exporter:      *testExporter1DutA,
					ExistingLease: &jumpstarterdevv1alpha1.Lease{},
				},
				{
					Policy:        jumpstarterdevv1alpha1.Policy{Priority: 0, SpotAccess: false},
					Exporter:      *testExporter2DutA,
					ExistingLease: &jumpstarterdevv1alpha1.Lease{},
				},
			}
			ordered := orderApprovedExporters(approvedExporters)
			Expect(ordered[0].Exporter.Name).To(Equal(testExporter2DutA.Name))
			Expect(ordered[0].Policy.SpotAccess).To(BeFalse())
			Expect(ordered[1].Exporter.Name).To(Equal(testExporter1DutA.Name))
			Expect(ordered[1].Policy.SpotAccess).To(BeTrue())
		})
	})

	When("some approved exporters have different policy priorities", func() {
		It("should order them by priority", func() {
			approvedExporters := []ApprovedExporter{
				{
					Policy:   jumpstarterdevv1alpha1.Policy{Priority: 5, SpotAccess: false},
					Exporter: *testExporter1DutA,
				},
				{
					Policy:   jumpstarterdevv1alpha1.Policy{Priority: 10, SpotAccess: false},
					Exporter: *testExporter2DutA,
				},
				{
					Policy:   jumpstarterdevv1alpha1.Policy{Priority: 100, SpotAccess: false},
					Exporter: *testExporter2DutA,
				},
			}
			ordered := orderApprovedExporters(approvedExporters)
			Expect(ordered[0].Policy.Priority).To(Equal(int(100)))
			Expect(ordered[1].Policy.Priority).To(Equal(int(10)))
			Expect(ordered[2].Policy.Priority).To(Equal(int(5)))

		})
	})

	When("some approved exporters have same policy priorities and no other traits", func() {
		It("should order them by name", func() {
			approvedExporters := []ApprovedExporter{
				{
					Policy:   jumpstarterdevv1alpha1.Policy{Priority: 5, SpotAccess: false},
					Exporter: *testExporter2DutA,
				},
				{
					Policy:   jumpstarterdevv1alpha1.Policy{Priority: 5, SpotAccess: false},
					Exporter: *testExporter1DutA,
				},
			}
			ordered := orderApprovedExporters(approvedExporters)

			Expect(ordered[0].Exporter.Name).To(Equal(testExporter1DutA.Name))
			Expect(ordered[1].Exporter.Name).To(Equal(testExporter2DutA.Name))
		})
	})

	When("mixed priorities, spot access, lease status are in the list", func() {
		It("should order them properly", func() {
			approvedExporters := []ApprovedExporter{
				{
					Policy:   jumpstarterdevv1alpha1.Policy{Priority: 5, SpotAccess: false},
					Exporter: *testExporter2DutA,
				},
				{
					Policy:        jumpstarterdevv1alpha1.Policy{Priority: 100, SpotAccess: true},
					Exporter:      *testExporter2DutA,
					ExistingLease: &jumpstarterdevv1alpha1.Lease{},
				},
				{
					Policy:   jumpstarterdevv1alpha1.Policy{Priority: 10, SpotAccess: false},
					Exporter: *testExporter1DutA,
				},
				{
					Policy:   jumpstarterdevv1alpha1.Policy{Priority: 5, SpotAccess: false},
					Exporter: *testExporter1DutA,
				},
				{
					Policy:   jumpstarterdevv1alpha1.Policy{Priority: 10, SpotAccess: true},
					Exporter: *testExporter2DutA,
				},
			}

			ordered := orderApprovedExporters(approvedExporters)
			Expect(ordered[0].Policy.Priority).To(Equal(int(10)))
			Expect(ordered[0].Policy.SpotAccess).To(BeFalse())
			Expect(ordered[0].Exporter.Name).To(Equal(testExporter1DutA.Name))

			Expect(ordered[1].Policy.Priority).To(Equal(int(5)))
			Expect(ordered[1].Policy.SpotAccess).To(BeFalse())
			Expect(ordered[1].Exporter.Name).To(Equal(testExporter1DutA.Name))

			Expect(ordered[2].Policy.Priority).To(Equal(int(5)))
			Expect(ordered[2].Policy.SpotAccess).To(BeFalse())
			Expect(ordered[2].Exporter.Name).To(Equal(testExporter2DutA.Name))

			Expect(ordered[3].Policy.Priority).To(Equal(int(10)))
			Expect(ordered[3].Policy.SpotAccess).To(BeTrue())
			Expect(ordered[3].Exporter.Name).To(Equal(testExporter2DutA.Name))

			Expect(ordered[4].Policy.Priority).To(Equal(int(100)))
			Expect(ordered[4].Policy.SpotAccess).To(BeTrue())
			Expect(ordered[4].Exporter.Name).To(Equal(testExporter2DutA.Name))

		})
	})
})
