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

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/event"
	"sigs.k8s.io/controller-runtime/pkg/predicate"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
)

const (
	lease1Name = "lease1"
	lease2Name = "lease2"
	lease3Name = "lease3"
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
		Duration: &metav1.Duration{
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
		deleteLeases(ctx, lease1Name, lease2Name, lease3Name)
	})

	When("trying to lease with an empty selector", func() {
		It("should fail right away", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Selector.MatchLabels = nil

			ctx := context.Background()
			err := k8sClient.Create(ctx, lease)
			Expect(err).To(HaveOccurred())
			Expect(err.Error()).To(ContainSubstring("one of selector or exporterRef.name is required"))
		})
	})

	When("trying to lease with a requested exporter and empty selector", func() {
		It("should acquire the requested exporter", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Selector.MatchLabels = nil
			lease.Spec.ExporterRef = &corev1.LocalObjectReference{Name: testExporter1DutA.Name}

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			Expect(updatedLease.Status.ExporterRef.Name).To(Equal(testExporter1DutA.Name))
			Expect(meta.IsStatusConditionTrue(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypeReady),
			)).To(BeTrue())
		})
	})

	When("trying to lease with a missing requested exporter", func() {
		It("should be unsatisfiable with ExporterNotFound reason", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Selector.MatchLabels = nil
			lease.Spec.ExporterRef = &corev1.LocalObjectReference{Name: "does-not-exist"}

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())
			condition := meta.FindStatusCondition(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable),
			)
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal("ExporterNotFound"))
			Expect(meta.IsStatusConditionTrue(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypePending),
			)).To(BeFalse())
		})
	})

	When("trying to lease with requested exporter that does not match selector", func() {
		It("should fail with SelectorMismatch reason", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Selector.MatchLabels["dut"] = "b"
			lease.Spec.ExporterRef = &corev1.LocalObjectReference{Name: testExporter1DutA.Name}

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())
			condition := meta.FindStatusCondition(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable),
			)
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal("SelectorMismatch"))
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
			lease.Spec.Duration = &metav1.Duration{Duration: 100 * time.Millisecond}

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())

			exporterName := updatedLease.Status.ExporterRef.Name

			// Poll until lease expires
			Eventually(func() bool {
				_ = reconcileLease(ctx, lease)
				updatedLease = getLease(ctx, lease.Name)
				return updatedLease.Status.Ended
			}).WithTimeout(2000 * time.Millisecond).WithPolling(50 * time.Millisecond).Should(BeTrue())

			// exporter is retained for record purposes
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())

			// the exporter should have no lease mark on status
			updatedExporter := getExporter(ctx, exporterName)
			Expect(updatedExporter.Status.LeaseRef).To(BeNil())

		})
	})

	When("trying to lease a non existing exporter", func() {
		It("should fail right away without policy descriptions", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Selector.MatchLabels["dut"] = "does-not-exist"

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())

			condition := meta.FindStatusCondition(updatedLease.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable))
			Expect(condition).NotTo(BeNil())
			Expect(condition.Status).To(Equal(metav1.ConditionTrue))
			// Without policies, the message should not contain "Matching policies:"
			Expect(condition.Message).NotTo(ContainSubstring("Matching policies:"))
		})
	})

	When("trying to lease approved exporters that are offline", func() {
		It("should set status to pending with offline reason", func() {
			lease := leaseDutA2Sec.DeepCopy()

			ctx := context.Background()

			// Create a policy that approves the exporters
			policy := &jumpstarterdevv1alpha1.ExporterAccessPolicy{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-policy",
					Namespace: "default",
				},
				Spec: jumpstarterdevv1alpha1.ExporterAccessPolicySpec{
					ExporterSelector: metav1.LabelSelector{
						MatchLabels: map[string]string{
							"dut": "a",
						},
					},
					Policies: []jumpstarterdevv1alpha1.Policy{
						{
							Priority: 0,
							From: []jumpstarterdevv1alpha1.From{
								{
									ClientSelector: metav1.LabelSelector{
										MatchLabels: map[string]string{
											"name": "client",
										},
									},
								},
							},
						},
					},
				},
			}
			Expect(k8sClient.Create(ctx, policy)).To(Succeed())
			DeferCleanup(func() { Expect(k8sClient.Delete(ctx, policy)).To(Succeed()) })

			// Set exporters offline while they are approved by policy
			setExporterOnlineConditions(ctx, testExporter1DutA.Name, metav1.ConditionFalse)
			setExporterOnlineConditions(ctx, testExporter2DutA.Name, metav1.ConditionFalse)

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())

			Expect(meta.IsStatusConditionTrue(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypePending),
			)).To(BeTrue())

			// Check that the condition has the correct reason
			condition := meta.FindStatusCondition(updatedLease.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypePending))
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal("Offline"))
			// The example exporter name must survive the online filter
			Expect(condition.Message).To(MatchRegexp(
				`^While there are 2 available exporters \(i\.e\. exporter[12]-dut-a\), none of them are online$`))
		})
	})

	When("trying to lease exporters that match selector but are not approved by any policy", func() {
		It("should set status to unsatisfiable with NoAccess reason", func() {
			lease := leaseDutA2Sec.DeepCopy()

			ctx := context.Background()

			// Create a policy that does NOT approve the exporters (different client selector)
			policy := &jumpstarterdevv1alpha1.ExporterAccessPolicy{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-policy",
					Namespace: "default",
				},
				Spec: jumpstarterdevv1alpha1.ExporterAccessPolicySpec{
					ExporterSelector: metav1.LabelSelector{
						MatchLabels: map[string]string{
							"dut": "a",
						},
					},
					Policies: []jumpstarterdevv1alpha1.Policy{
						{
							Description: "Requires different-client label",
							Priority:    0,
							From: []jumpstarterdevv1alpha1.From{
								{
									ClientSelector: metav1.LabelSelector{
										MatchLabels: map[string]string{
											"name": "different-client", // Different from testClient
										},
									},
								},
							},
						},
					},
				},
			}
			Expect(k8sClient.Create(ctx, policy)).To(Succeed())
			DeferCleanup(func() { Expect(k8sClient.Delete(ctx, policy)).To(Succeed()) })

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())

			Expect(meta.IsStatusConditionTrue(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable),
			)).To(BeTrue())

			// Check that the condition has the correct reason
			condition := meta.FindStatusCondition(updatedLease.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable))
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal("NoAccess"))
			Expect(condition.Message).To(ContainSubstring("none of them are approved by any policy"))
			Expect(condition.Message).To(ContainSubstring("Requires different-client label"))
		})
	})

	When("trying to lease with multiple policies containing descriptions, none matching client", func() {
		It("should include all policy descriptions in the unsatisfiable message", func() {
			lease := leaseDutA2Sec.DeepCopy()

			ctx := context.Background()

			// Create two policies with different descriptions, neither matching testClient
			policy1 := &jumpstarterdevv1alpha1.ExporterAccessPolicy{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-policy-admin",
					Namespace: "default",
				},
				Spec: jumpstarterdevv1alpha1.ExporterAccessPolicySpec{
					ExporterSelector: metav1.LabelSelector{
						MatchLabels: map[string]string{"dut": "a"},
					},
					Policies: []jumpstarterdevv1alpha1.Policy{
						{
							Description: "Administrators only",
							Priority:    20,
							From: []jumpstarterdevv1alpha1.From{{
								ClientSelector: metav1.LabelSelector{
									MatchLabels: map[string]string{"role": "admin"},
								},
							}},
						},
					},
				},
			}
			policy2 := &jumpstarterdevv1alpha1.ExporterAccessPolicy{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-policy-ci",
					Namespace: "default",
				},
				Spec: jumpstarterdevv1alpha1.ExporterAccessPolicySpec{
					ExporterSelector: metav1.LabelSelector{
						MatchLabels: map[string]string{"dut": "a"},
					},
					Policies: []jumpstarterdevv1alpha1.Policy{
						{
							Description: "CI pipelines only",
							Priority:    5,
							From: []jumpstarterdevv1alpha1.From{{
								ClientSelector: metav1.LabelSelector{
									MatchLabels: map[string]string{"role": "ci"},
								},
							}},
						},
					},
				},
			}
			Expect(k8sClient.Create(ctx, policy1)).To(Succeed())
			DeferCleanup(func() { Expect(k8sClient.Delete(ctx, policy1)).To(Succeed()) })
			Expect(k8sClient.Create(ctx, policy2)).To(Succeed())
			DeferCleanup(func() { Expect(k8sClient.Delete(ctx, policy2)).To(Succeed()) })

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())

			condition := meta.FindStatusCondition(updatedLease.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable))
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal("NoAccess"))
			Expect(condition.Message).To(ContainSubstring("Administrators only"))
			Expect(condition.Message).To(ContainSubstring("CI pipelines only"))
			Expect(condition.Message).To(ContainSubstring(";"))
		})
	})

	When("trying to lease with policies where some have empty descriptions", func() {
		It("should only include non-empty descriptions in the unsatisfiable message", func() {
			lease := leaseDutA2Sec.DeepCopy()

			ctx := context.Background()

			policy := &jumpstarterdevv1alpha1.ExporterAccessPolicy{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-policy-mixed",
					Namespace: "default",
				},
				Spec: jumpstarterdevv1alpha1.ExporterAccessPolicySpec{
					ExporterSelector: metav1.LabelSelector{
						MatchLabels: map[string]string{"dut": "a"},
					},
					Policies: []jumpstarterdevv1alpha1.Policy{
						{
							Description: "VIP access rule",
							Priority:    10,
							From: []jumpstarterdevv1alpha1.From{{
								ClientSelector: metav1.LabelSelector{
									MatchLabels: map[string]string{"role": "vip"},
								},
							}},
						},
						{
							// No description
							Priority: 1,
							From: []jumpstarterdevv1alpha1.From{{
								ClientSelector: metav1.LabelSelector{
									MatchLabels: map[string]string{"role": "other"},
								},
							}},
						},
					},
				},
			}
			Expect(k8sClient.Create(ctx, policy)).To(Succeed())
			DeferCleanup(func() { Expect(k8sClient.Delete(ctx, policy)).To(Succeed()) })

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			condition := meta.FindStatusCondition(updatedLease.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable))
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal("NoAccess"))
			// Only the non-empty description is listed: no empty entry or dangling separator
			Expect(condition.Message).To(HaveSuffix("Matching policies: VIP access rule"))
		})
	})

	When("trying to lease with a policy that has descriptions and matches the client", func() {
		It("should acquire the lease successfully regardless of description", func() {
			lease := leaseDutA2Sec.DeepCopy()

			ctx := context.Background()

			policy := &jumpstarterdevv1alpha1.ExporterAccessPolicy{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "test-policy-matching",
					Namespace: "default",
				},
				Spec: jumpstarterdevv1alpha1.ExporterAccessPolicySpec{
					ExporterSelector: metav1.LabelSelector{
						MatchLabels: map[string]string{"dut": "a"},
					},
					Policies: []jumpstarterdevv1alpha1.Policy{
						{
							Description: "Standard access for registered clients",
							Priority:    10,
							From: []jumpstarterdevv1alpha1.From{{
								ClientSelector: metav1.LabelSelector{
									MatchLabels: map[string]string{"name": "client"}, // Matches testClient
								},
							}},
						},
					},
				},
			}
			Expect(k8sClient.Create(ctx, policy)).To(Succeed())
			DeferCleanup(func() { Expect(k8sClient.Delete(ctx, policy)).To(Succeed()) })

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			Expect(updatedLease.Status.Priority).To(Equal(10))
			Expect(meta.IsStatusConditionTrue(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypeReady),
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

	When("trying to lease exporters that are online but not ready (e.g., running afterLease hook)", func() {
		It("should set status to pending with NotReady reason", func() {
			lease := leaseDutA2Sec.DeepCopy()

			ctx := context.Background()

			// Set both matching exporters to online but in AfterLeaseHook status
			// (simulates exporter cleaning up from a previous lease)
			setExporterNotReady(ctx, testExporter1DutA.Name, jumpstarterdevv1alpha1.ExporterStatusAfterLeaseHook)
			setExporterNotReady(ctx, testExporter2DutA.Name, jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHook)

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			result := reconcileLease(ctx, lease)

			// Should requeue to retry later
			Expect(result.RequeueAfter).To(Equal(time.Second))

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())

			Expect(meta.IsStatusConditionTrue(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypePending),
			)).To(BeTrue())

			condition := meta.FindStatusCondition(updatedLease.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypePending))
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal("NotReady"))
			Expect(condition.Message).To(ContainSubstring("none are ready"))
		})
	})

	When("trying to lease exporters where some are online+ready and others are online+not-ready", func() {
		It("should only lease from the ready exporter", func() {
			lease := leaseDutA2Sec.DeepCopy()

			ctx := context.Background()

			// exporter1 is still cleaning up, exporter2 is available
			setExporterNotReady(ctx, testExporter1DutA.Name, jumpstarterdevv1alpha1.ExporterStatusAfterLeaseHook)

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			Expect(updatedLease.Status.ExporterRef.Name).To(Equal(testExporter2DutA.Name))
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
			lease2.Name = lease2Name
			lease2.Spec.Selector.MatchLabels["dut"] = "b"
			Expect(k8sClient.Create(ctx, lease2)).To(Succeed())
			_ = reconcileLease(ctx, lease2)

			updatedLease = getLease(ctx, lease2Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())

			Expect(meta.IsStatusConditionTrue(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypePending),
			)).To(BeTrue())

			// Check that the condition has the correct reason and message format
			condition := meta.FindStatusCondition(updatedLease.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypePending))
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal("NotAvailable"))
			// The example exporter name must survive the leased filter
			Expect(condition.Message).To(Equal(
				"There are 1 approved exporters, (i.e. exporter3-dut-b) but all of them are already leased"))
		})

		It("should be acquired when a valid exporter lease times out", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Selector.MatchLabels["dut"] = "b"
			lease.Spec.Duration = &metav1.Duration{Duration: 500 * time.Millisecond}

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
			lease2.Name = lease2Name
			lease2.Spec.Selector.MatchLabels["dut"] = "b"
			Expect(k8sClient.Create(ctx, lease2)).To(Succeed())
			_ = reconcileLease(ctx, lease2)

			updatedLease = getLease(ctx, lease2Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())
			// TODO: add and check status conditions of the lease to indicate that the lease is waiting

			// Poll until first lease expires and second lease acquires exporter
			Eventually(func() bool {
				_ = reconcileLease(ctx, lease)
				_ = reconcileLease(ctx, lease2)
				updatedLease = getLease(ctx, lease2Name)
				return updatedLease.Status.ExporterRef != nil
			}).WithTimeout(2500 * time.Millisecond).WithPolling(50 * time.Millisecond).Should(BeTrue())

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

	When("trying to lease a disabled exporter by ExporterRef", func() {
		It("should fail with ExporterDisabled reason and helpful message", func() {
			ctx := context.Background()
			setExporterEnabled(ctx, testExporter1DutA.Name, false)

			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Selector.MatchLabels = nil
			lease.Spec.ExporterRef = &corev1.LocalObjectReference{Name: testExporter1DutA.Name}

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())
			condition := meta.FindStatusCondition(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable),
			)
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal("ExporterDisabled"))
			Expect(condition.Message).To(ContainSubstring("is disabled"))
			Expect(condition.Message).To(ContainSubstring("allowDisabled"))
			Expect(condition.Message).To(ContainSubstring("--allow-disabled"))

			// Restore for subsequent tests
			setExporterEnabled(ctx, testExporter1DutA.Name, true)
		})
	})

	When("trying to lease a disabled exporter with allowDisabled", func() {
		It("should succeed when ExporterRef and AllowDisabled are set", func() {
			ctx := context.Background()
			setExporterEnabled(ctx, testExporter1DutA.Name, false)

			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Selector.MatchLabels = nil
			lease.Spec.ExporterRef = &corev1.LocalObjectReference{Name: testExporter1DutA.Name}
			lease.Spec.AllowDisabled = true

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			Expect(updatedLease.Status.ExporterRef.Name).To(Equal(testExporter1DutA.Name))

			// Restore for subsequent tests
			setExporterEnabled(ctx, testExporter1DutA.Name, true)
		})
	})

	When("trying to lease a disabled exporter with a selector and allowDisabled", func() {
		It("should succeed when Selector and AllowDisabled are set", func() {
			ctx := context.Background()
			setExporterEnabled(ctx, testExporter1DutA.Name, false)
			setExporterEnabled(ctx, testExporter2DutA.Name, false)

			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.ExporterRef = nil
			lease.Spec.AllowDisabled = true

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())

			// Restore for subsequent tests
			setExporterEnabled(ctx, testExporter1DutA.Name, true)
			setExporterEnabled(ctx, testExporter2DutA.Name, true)
		})
	})

	When("trying to lease with selector and some exporters disabled", func() {
		It("should skip disabled exporters and lease an enabled one", func() {
			ctx := context.Background()
			// Disable one of the two dut=a exporters
			setExporterEnabled(ctx, testExporter1DutA.Name, false)

			lease := leaseDutA2Sec.DeepCopy()

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			// Should only get the enabled exporter
			Expect(updatedLease.Status.ExporterRef.Name).To(Equal(testExporter2DutA.Name))

			// Restore for subsequent tests
			setExporterEnabled(ctx, testExporter1DutA.Name, true)
		})

		It("should be unsatisfiable when all matching exporters are disabled", func() {
			ctx := context.Background()
			setExporterEnabled(ctx, testExporter1DutA.Name, false)
			setExporterEnabled(ctx, testExporter2DutA.Name, false)

			lease := leaseDutA2Sec.DeepCopy()

			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil())
			condition := meta.FindStatusCondition(
				updatedLease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable),
			)
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal("AllDisabled"))

			// Restore for subsequent tests
			setExporterEnabled(ctx, testExporter1DutA.Name, true)
			setExporterEnabled(ctx, testExporter2DutA.Name, true)
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
		exporter.Status.ExporterStatusValue = jumpstarterdevv1alpha1.ExporterStatusAvailable
		exporter.Status.StatusMessage = "Available for leasing"
	} else {
		exporter.Status.Devices = nil
		exporter.Status.LastSeen = metav1.NewTime(metav1.Now().Add(-time.Minute * 2))
		exporter.Status.ExporterStatusValue = jumpstarterdevv1alpha1.ExporterStatusOffline
		exporter.Status.StatusMessage = "Offline"
	}
	Expect(k8sClient.Status().Update(ctx, exporter)).To(Succeed())
}

// setExporterEnabled updates an exporter's spec.enabled field.
func setExporterEnabled(ctx context.Context, name string, enabled bool) {
	exporter := getExporter(ctx, name)
	exporter.Spec.Enabled = &enabled
	Expect(k8sClient.Update(ctx, exporter)).To(Succeed())
}

// setExporterNotReady sets an exporter as online and registered but NOT ready
// for leasing (e.g., still running a hook from a previous lease).
func setExporterNotReady(ctx context.Context, name string, status string) {
	exporter := getExporter(ctx, name)
	meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
		Type:   string(jumpstarterdevv1alpha1.ExporterConditionTypeRegistered),
		Status: metav1.ConditionTrue,
		Reason: "dummy",
	})
	meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
		Type:   string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
		Status: metav1.ConditionTrue,
		Reason: "dummy",
	})
	exporter.Status.Devices = []jumpstarterdevv1alpha1.Device{{}}
	exporter.Status.LastSeen = metav1.Now()
	exporter.Status.ExporterStatusValue = status
	exporter.Status.StatusMessage = "Running hook"
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

// Pending leases with offline exporters requeue every 1 second with no backoff.
// With MaxConcurrentReconciles=1 (default), N stuck leases generate N reconcile
// cycles per second, starving new lease requests from being processed promptly.
var _ = Describe("Pending lease queue starvation", func() {
	const (
		starveLease1 = "starve-pending-1"
		starveLease2 = "starve-pending-2"
		starveLease3 = "starve-pending-3"
		starveLease4 = "starve-pending-4"
		starveLease5 = "starve-pending-5"
	)

	BeforeEach(func() {
		createExporters(context.Background(), testExporter1DutA, testExporter2DutA, testExporter3DutB)
	})
	AfterEach(func() {
		ctx := context.Background()
		deleteExporters(ctx, testExporter1DutA, testExporter2DutA, testExporter3DutB)
		deleteLeases(ctx,
			starveLease1, starveLease2, starveLease3, starveLease4, starveLease5,
		)
	})

	When("multiple leases are pending because all exporters are offline", func() {
		It("should use exponential backoff instead of fixed 1-second requeue", func() {
			ctx := context.Background()

			// All exporters offline
			setExporterOnlineConditions(ctx, testExporter1DutA.Name, metav1.ConditionFalse)
			setExporterOnlineConditions(ctx, testExporter2DutA.Name, metav1.ConditionFalse)
			setExporterOnlineConditions(ctx, testExporter3DutB.Name, metav1.ConditionFalse)

			pendingNames := []string{starveLease1, starveLease2, starveLease3, starveLease4, starveLease5}
			for _, name := range pendingNames {
				lease := leaseDutA2Sec.DeepCopy()
				lease.Name = name
				Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			}

			leaseReconciler := &LeaseReconciler{
				Client: k8sClient,
				Scheme: k8sClient.Scheme(),
			}

			// First reconcile sets the Pending condition with LastTransitionTime=now
			for _, name := range pendingNames {
				_, err := leaseReconciler.Reconcile(ctx, reconcile.Request{
					NamespacedName: types.NamespacedName{Name: name, Namespace: "default"},
				})
				Expect(err).NotTo(HaveOccurred())
			}

			// Simulate time passing: patch LastTransitionTime on the Pending
			// condition to 60 seconds ago. In production this happens naturally
			// as the lease sits pending; in tests we fast-forward the clock.
			pastTime := metav1.NewTime(time.Now().Add(-60 * time.Second))
			for _, name := range pendingNames {
				lease := getLease(ctx, name)
				for i := range lease.Status.Conditions {
					if lease.Status.Conditions[i].Type == string(jumpstarterdevv1alpha1.LeaseConditionTypePending) {
						lease.Status.Conditions[i].LastTransitionTime = pastTime
					}
				}
				Expect(k8sClient.Status().Update(ctx, lease)).To(Succeed())
			}

			// Now reconcile again — backoff should be well above 1 second
			for _, name := range pendingNames {
				result, err := leaseReconciler.Reconcile(ctx, reconcile.Request{
					NamespacedName: types.NamespacedName{Name: name, Namespace: "default"},
				})
				Expect(err).NotTo(HaveOccurred())
				Expect(result.RequeueAfter).To(BeNumerically(">", time.Second),
					"lease %s should back off beyond 1s after being pending for 60s", name)
				Expect(result.RequeueAfter).To(BeNumerically("<=", maxPendingRequeue),
					"lease %s backoff should not exceed the cap", name)
			}
		})
	})
})

var _ = Describe("orderApprovedExporters", func() {
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

var _ = Describe("filterOutDisabledExporters", func() {
	boolTrue := true
	boolFalse := false

	It("should keep exporters with nil Enabled (backward compat)", func() {
		exporters := []jumpstarterdevv1alpha1.Exporter{
			{ObjectMeta: metav1.ObjectMeta{Name: "nil-enabled"}},
		}
		result := filterOutDisabledExporters(exporters, false)
		Expect(result).To(HaveLen(1))
		Expect(result[0].Name).To(Equal("nil-enabled"))
	})

	It("should keep exporters with Enabled=true", func() {
		exporters := []jumpstarterdevv1alpha1.Exporter{
			{
				ObjectMeta: metav1.ObjectMeta{Name: "enabled"},
				Spec:       jumpstarterdevv1alpha1.ExporterSpec{Enabled: &boolTrue},
			},
		}
		result := filterOutDisabledExporters(exporters, false)
		Expect(result).To(HaveLen(1))
		Expect(result[0].Name).To(Equal("enabled"))
	})

	It("should remove exporters with Enabled=false", func() {
		exporters := []jumpstarterdevv1alpha1.Exporter{
			{
				ObjectMeta: metav1.ObjectMeta{Name: "disabled"},
				Spec:       jumpstarterdevv1alpha1.ExporterSpec{Enabled: &boolFalse},
			},
		}
		result := filterOutDisabledExporters(exporters, false)
		Expect(result).To(BeEmpty())
	})

	It("should keep disabled exporters when allowDisabled is true", func() {
		exporters := []jumpstarterdevv1alpha1.Exporter{
			{
				ObjectMeta: metav1.ObjectMeta{Name: "disabled"},
				Spec:       jumpstarterdevv1alpha1.ExporterSpec{Enabled: &boolFalse},
			},
		}
		result := filterOutDisabledExporters(exporters, true)
		Expect(result).To(HaveLen(1))
		Expect(result[0].Name).To(Equal("disabled"))
	})

	It("should filter correctly with a mix of nil, true, and false", func() {
		exporters := []jumpstarterdevv1alpha1.Exporter{
			{ObjectMeta: metav1.ObjectMeta{Name: "nil-enabled"}},
			{
				ObjectMeta: metav1.ObjectMeta{Name: "enabled"},
				Spec:       jumpstarterdevv1alpha1.ExporterSpec{Enabled: &boolTrue},
			},
			{
				ObjectMeta: metav1.ObjectMeta{Name: "disabled"},
				Spec:       jumpstarterdevv1alpha1.ExporterSpec{Enabled: &boolFalse},
			},
			{
				ObjectMeta: metav1.ObjectMeta{Name: "also-disabled"},
				Spec:       jumpstarterdevv1alpha1.ExporterSpec{Enabled: &boolFalse},
			},
		}
		result := filterOutDisabledExporters(exporters, false)
		Expect(result).To(HaveLen(2))
		Expect(result[0].Name).To(Equal("nil-enabled"))
		Expect(result[1].Name).To(Equal("enabled"))
	})

	It("should not modify the original slice", func() {
		exporters := []jumpstarterdevv1alpha1.Exporter{
			{ObjectMeta: metav1.ObjectMeta{Name: "enabled"}},
			{
				ObjectMeta: metav1.ObjectMeta{Name: "disabled"},
				Spec:       jumpstarterdevv1alpha1.ExporterSpec{Enabled: &boolFalse},
			},
		}
		result := filterOutDisabledExporters(exporters, false)
		Expect(result).To(HaveLen(1))
		Expect(exporters).To(HaveLen(2)) // original unchanged
	})
})

var _ = Describe("skipEndedPredicate", func() {
	var skipEnded predicate.Funcs

	BeforeEach(func() {
		skipEnded = skipEndedPredicate()
	})

	It("should admit creates for non-ended leases", func() {
		lease := &jumpstarterdevv1alpha1.Lease{}
		Expect(skipEnded.Create(event.CreateEvent{Object: lease})).To(BeTrue())
	})

	It("should reject creates for ended leases", func() {
		lease := &jumpstarterdevv1alpha1.Lease{
			ObjectMeta: metav1.ObjectMeta{
				Labels: map[string]string{
					string(jumpstarterdevv1alpha1.LeaseLabelEnded): jumpstarterdevv1alpha1.LeaseLabelEndedValue,
				},
			},
		}
		Expect(skipEnded.Create(event.CreateEvent{Object: lease})).To(BeFalse())
	})

	It("should reject updates where new object has ended label", func() {
		oldLease := &jumpstarterdevv1alpha1.Lease{}
		newLease := &jumpstarterdevv1alpha1.Lease{
			ObjectMeta: metav1.ObjectMeta{
				Labels: map[string]string{
					string(jumpstarterdevv1alpha1.LeaseLabelEnded): jumpstarterdevv1alpha1.LeaseLabelEndedValue,
				},
			},
		}
		Expect(skipEnded.Update(event.UpdateEvent{ObjectOld: oldLease, ObjectNew: newLease})).To(BeFalse())
	})

	It("should admit updates for non-ended leases", func() {
		oldLease := &jumpstarterdevv1alpha1.Lease{}
		newLease := &jumpstarterdevv1alpha1.Lease{}
		Expect(skipEnded.Update(event.UpdateEvent{ObjectOld: oldLease, ObjectNew: newLease})).To(BeTrue())
	})

	It("should admit updates for ended-in-status but unlabeled leases so label can be backfilled", func() {
		oldLease := &jumpstarterdevv1alpha1.Lease{}
		newLease := &jumpstarterdevv1alpha1.Lease{
			Status: jumpstarterdevv1alpha1.LeaseStatus{
				Ended: true,
			},
		}
		// No ended label → predicate admits → reconciler runs → backfills label
		Expect(skipEnded.Update(event.UpdateEvent{ObjectOld: oldLease, ObjectNew: newLease})).To(BeTrue())
	})

	It("should always admit deletes", func() {
		lease := &jumpstarterdevv1alpha1.Lease{
			ObjectMeta: metav1.ObjectMeta{
				Labels: map[string]string{
					string(jumpstarterdevv1alpha1.LeaseLabelEnded): jumpstarterdevv1alpha1.LeaseLabelEndedValue,
				},
			},
		}
		Expect(skipEnded.Delete(event.DeleteEvent{Object: lease})).To(BeTrue())
	})

})

var _ = Describe("Scheduled Leases", func() {
	BeforeEach(func() {
		createExporters(context.Background(), testExporter1DutA, testExporter2DutA, testExporter3DutB)
		setExporterOnlineConditions(context.Background(), testExporter1DutA.Name, metav1.ConditionTrue)
		setExporterOnlineConditions(context.Background(), testExporter2DutA.Name, metav1.ConditionTrue)
		setExporterOnlineConditions(context.Background(), testExporter3DutB.Name, metav1.ConditionTrue)
	})
	AfterEach(func() {
		ctx := context.Background()
		deleteExporters(ctx, testExporter1DutA, testExporter2DutA, testExporter3DutB)
		deleteLeases(ctx, lease1Name, lease2Name, lease3Name)
	})

	When("creating lease with Duration only (immediate lease)", func() {
		It("should acquire exporter immediately and set effective begin time", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Duration = &metav1.Duration{Duration: 2 * time.Second}
			lease.Spec.BeginTime = nil
			lease.Spec.EndTime = nil

			ctx := context.Background()
			beforeCreate := time.Now().Truncate(time.Second)
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)
			afterReconcile := time.Now().Truncate(time.Second)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Spec.BeginTime).To(BeNil(), "Spec.BeginTime should remain nil for immediate leases")
			Expect(updatedLease.Spec.EndTime).To(BeNil(), "Spec.EndTime should remain nil")
			Expect(updatedLease.Status.BeginTime).NotTo(BeNil(), "Status.BeginTime should be set")
			Expect(updatedLease.Status.BeginTime.Time).To(BeTemporally(">=", beforeCreate))
			Expect(updatedLease.Status.BeginTime.Time).To(BeTemporally("<=", afterReconcile))
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil(), "Should have acquired exporter immediately")
		})
	})

	When("creating lease with BeginTime + Duration (scheduled lease)", func() {
		It("should wait until BeginTime before acquiring exporter", func() {
			lease := leaseDutA2Sec.DeepCopy()
			futureTime := metav1.NewTime(time.Now().Add(2 * time.Second).Truncate(time.Second))
			lease.Spec.BeginTime = &futureTime
			lease.Spec.Duration = &metav1.Duration{Duration: 1 * time.Second}
			lease.Spec.EndTime = nil

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			result := reconcileLease(ctx, lease)

			// Should requeue for future time
			Expect(result.RequeueAfter).To(BeNumerically(">", 0))
			Expect(result.RequeueAfter).To(BeNumerically("<=", 2*time.Second))

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil(), "Should not have acquired exporter yet")
			Expect(updatedLease.Status.BeginTime).To(BeNil(), "Status.BeginTime should not be set yet")

			// Poll until BeginTime passes and exporter is acquired
			Eventually(func() bool {
				_ = reconcileLease(ctx, lease)
				updatedLease = getLease(ctx, lease.Name)
				return updatedLease.Status.ExporterRef != nil
			}).WithTimeout(3*time.Second).WithPolling(50*time.Millisecond).Should(BeTrue(), "Should have acquired exporter after BeginTime")

			Expect(updatedLease.Status.BeginTime).NotTo(BeNil(), "Status.BeginTime should be set")
			Expect(updatedLease.Status.BeginTime.Time).To(BeTemporally(">=", futureTime.Time))
		})
	})

	When("creating a lease whose BeginTime is already in the past", func() {
		It("should use the acquisition time as the effective begin time", func() {
			lease := leaseDutA2Sec.DeepCopy()
			pastBeginTime := time.Now().Truncate(time.Second).Add(-10 * time.Second)
			futureEndTime := time.Now().Truncate(time.Second).Add(20 * time.Second)

			lease.Spec.BeginTime = &metav1.Time{Time: pastBeginTime}
			lease.Spec.EndTime = &metav1.Time{Time: futureEndTime}
			lease.Spec.Duration = &metav1.Duration{Duration: futureEndTime.Sub(pastBeginTime)}

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())

			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil(), "Should acquire exporter immediately")
			Expect(updatedLease.Status.BeginTime).NotTo(BeNil(), "Status.BeginTime should be set")

			// Status.BeginTime should be the actual acquisition time (now), not Spec.BeginTime
			// Allow generous tolerance for CI environments with second-precision timestamps
			now := time.Now().Truncate(time.Second)
			Expect(updatedLease.Status.BeginTime.Time).To(BeTemporally(">=", now.Add(-2*time.Second)))
			Expect(updatedLease.Status.BeginTime.Time).To(BeTemporally("<=", now.Add(2*time.Second)))

			// EffectiveDuration should be based on actual Status.BeginTime, not Spec.BeginTime
			// Since timestamps have second precision, allow up to 1 second tolerance
			pbLease := updatedLease.ToProtobuf()
			Expect(pbLease.EffectiveDuration).NotTo(BeNil())
			actualDuration := pbLease.EffectiveDuration.AsDuration()
			// Should be small (just acquired), allowing for second-precision truncation
			Expect(actualDuration).To(BeNumerically("<=", 2*time.Second))
			Expect(actualDuration).To(BeNumerically(">=", 0))
		})
	})

	When("creating a scheduled lease whose whole window is already in the past", func() {
		It("should end on the first reconcile", func() {
			lease := leaseDutA2Sec.DeepCopy()
			// BeginTime + Duration is already behind us
			pastBeginTime := metav1.NewTime(time.Now().Truncate(time.Second).Add(-2 * time.Second))
			lease.Spec.BeginTime = &pastBeginTime
			lease.Spec.Duration = &metav1.Duration{Duration: 1 * time.Second}

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			result := reconcileLease(ctx, lease)
			Expect(result.RequeueAfter).To(BeZero())

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.Ended).To(BeTrue())
			Expect(updatedLease.Status.EndTime).NotTo(BeNil())
		})
	})

	// RequeueAfter on an acquired lease is the computed expiry deadline, so asserting
	// it pins which time fields win without waiting for the lease to end.
	DescribeTable("expiry deadline of an acquired lease",
		func(begin, end, duration *time.Duration, expected time.Duration) {
			now := time.Now().Truncate(time.Second)
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Duration = nil
			if begin != nil {
				lease.Spec.BeginTime = &metav1.Time{Time: now.Add(*begin)}
			}
			if end != nil {
				lease.Spec.EndTime = &metav1.Time{Time: now.Add(*end)}
			}
			if duration != nil {
				lease.Spec.Duration = &metav1.Duration{Duration: *duration}
			}

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			result := reconcileLease(ctx, lease)
			elapsed := time.Since(now)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			Expect(updatedLease.Status.Ended).To(BeFalse())
			// Deadlines are anchored at or after now, and the reconcile computes
			// RequeueAfter later still, so it can only fall short of expected, by at
			// most the time elapsed since now.
			Expect(result.RequeueAfter).To(And(
				BeNumerically("<=", expected),
				BeNumerically(">=", expected-elapsed),
			))
		},
		Entry("Duration only expires at Status.BeginTime + Duration",
			nil, nil, new(30*time.Second), 30*time.Second),
		Entry("BeginTime + Duration expires at Spec.BeginTime + Duration, not acquisition + Duration",
			new(-10*time.Second), nil, new(30*time.Second), 20*time.Second),
		Entry("EndTime only expires at EndTime",
			nil, new(10*time.Second), nil, 10*time.Second),
		Entry("EndTime wins over Status.BeginTime + Duration",
			nil, new(10*time.Second), new(30*time.Second), 10*time.Second),
		Entry("EndTime wins over Spec.BeginTime + Duration",
			new(-10*time.Second), new(10*time.Second), new(30*time.Second), 10*time.Second),
	)

	When("checking EffectiveDuration on active lease", func() {
		It("should calculate EffectiveDuration as current time minus Status.BeginTime", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Duration = &metav1.Duration{Duration: 10 * time.Second} // Long duration so it doesn't expire

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			Expect(updatedLease.Status.BeginTime).NotTo(BeNil())
			Expect(updatedLease.Status.EndTime).To(BeNil(), "Active lease should not have EndTime")

			// Check EffectiveDuration on active lease
			beforeCheck := time.Now().Truncate(time.Second)
			pbLease := updatedLease.ToProtobuf()
			afterCheck := time.Now().Truncate(time.Second).Add(time.Second)

			Expect(pbLease.EffectiveBeginTime).NotTo(BeNil())
			Expect(pbLease.EffectiveEndTime).To(BeNil(), "Active lease should not have EffectiveEndTime")
			Expect(pbLease.EffectiveDuration).NotTo(BeNil())

			// EffectiveDuration should be approximately now() - BeginTime
			expectedMinDuration := beforeCheck.Sub(updatedLease.Status.BeginTime.Time)
			expectedMaxDuration := afterCheck.Sub(updatedLease.Status.BeginTime.Time)
			actualDuration := pbLease.EffectiveDuration.AsDuration()
			Expect(actualDuration).To(BeNumerically(">=", expectedMinDuration))
			Expect(actualDuration).To(BeNumerically("<=", expectedMaxDuration))
		})
	})

	When("multiple leases with different BeginTimes", func() {
		It("should acquire exporters at their respective BeginTimes", func() {
			ctx := context.Background()

			// Immediate lease
			lease1 := leaseDutA2Sec.DeepCopy()
			lease1.Name = lease1Name
			lease1.Spec.Duration = &metav1.Duration{Duration: 5 * time.Second}
			Expect(k8sClient.Create(ctx, lease1)).To(Succeed())
			_ = reconcileLease(ctx, lease1)

			updatedLease1 := getLease(ctx, lease1Name)
			Expect(updatedLease1.Status.ExporterRef).NotTo(BeNil())
			exporter1 := updatedLease1.Status.ExporterRef.Name

			// Scheduled lease 2s in future
			lease2 := leaseDutA2Sec.DeepCopy()
			lease2.Name = lease2Name
			futureTime := metav1.NewTime(time.Now().Truncate(time.Second).Add(2 * time.Second))
			lease2.Spec.BeginTime = &futureTime
			lease2.Spec.Duration = &metav1.Duration{Duration: 1 * time.Second}
			Expect(k8sClient.Create(ctx, lease2)).To(Succeed())
			_ = reconcileLease(ctx, lease2)

			updatedLease2 := getLease(ctx, lease2Name)
			Expect(updatedLease2.Status.ExporterRef).To(BeNil(), "Scheduled lease should wait")

			// Poll until lease2's BeginTime passes and exporter is acquired
			Eventually(func() bool {
				_ = reconcileLease(ctx, lease2)
				updatedLease2 = getLease(ctx, lease2Name)
				return updatedLease2.Status.ExporterRef != nil
			}).WithTimeout(2200*time.Millisecond).WithPolling(50*time.Millisecond).Should(BeTrue(), "Should acquire after BeginTime")
			exporter2 := updatedLease2.Status.ExporterRef.Name

			// Should have acquired different exporters (both dut:a exporters)
			Expect(exporter2).NotTo(Equal(exporter1))
			Expect([]string{exporter1, exporter2}).To(ConsistOf(testExporter1DutA.Name, testExporter2DutA.Name))
		})
	})

	// EndTime in the past
	When("creating lease with EndTime already in the past", func() {
		It("should create but expire immediately", func() {
			lease := leaseDutA2Sec.DeepCopy()
			pastEndTime := metav1.NewTime(time.Now().Truncate(time.Second).Add(-500 * time.Millisecond))
			lease.Spec.EndTime = &pastEndTime
			lease.Spec.Duration = nil

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			// Should acquire exporter (or try to)
			// Then immediately expire because EndTime is in the past
			Expect(updatedLease.Status.Ended).To(BeTrue(), "Lease should be ended immediately")
			Expect(updatedLease.Status.EndTime).NotTo(BeNil())
		})
	})

	// Early release scenarios
	When("releasing a scheduled lease before it starts", func() {
		It("should cancel the scheduled lease", func() {
			lease := leaseDutA2Sec.DeepCopy()
			// Far enough ahead that BeginTime cannot arrive during the test
			futureTime := metav1.NewTime(time.Now().Add(time.Hour))
			lease.Spec.BeginTime = &futureTime
			lease.Spec.Duration = &metav1.Duration{Duration: time.Hour}

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil(), "Should not have acquired yet")
			Expect(updatedLease.Status.Ended).To(BeFalse())

			// Release before BeginTime
			updatedLease.Spec.Release = true
			Expect(k8sClient.Update(ctx, updatedLease)).To(Succeed())
			_ = reconcileLease(ctx, updatedLease)

			updatedLease = getLease(ctx, lease.Name)
			Expect(updatedLease.Status.Ended).To(BeTrue(), "Should be cancelled/ended")
			Expect(updatedLease.Status.ExporterRef).To(BeNil(), "Should never have acquired exporter")
		})
	})

	When("releasing an active lease early", func() {
		It("should have EffectiveDuration matching actual time held", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Duration = &metav1.Duration{Duration: 10 * time.Second} // Long duration

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil())
			Expect(updatedLease.Status.BeginTime).NotTo(BeNil())
			beginTime := updatedLease.Status.BeginTime.Time

			// Brief wait to ensure some time has passed
			time.Sleep(50 * time.Millisecond)

			// Release early
			updatedLease = getLease(ctx, lease.Name)
			updatedLease.Spec.Release = true
			Expect(k8sClient.Update(ctx, updatedLease)).To(Succeed())
			_ = reconcileLease(ctx, updatedLease)

			updatedLease = getLease(ctx, lease.Name)
			Expect(updatedLease.Status.Ended).To(BeTrue())
			Expect(updatedLease.Status.EndTime).NotTo(BeNil())

			// EffectiveDuration should be actual time held, not 10 seconds
			// Allow generous tolerance for CI environments with second-precision timestamps
			pbLease := updatedLease.ToProtobuf()
			Expect(pbLease.EffectiveDuration).NotTo(BeNil())
			actualDuration := pbLease.EffectiveDuration.AsDuration()
			expectedDuration := updatedLease.Status.EndTime.Sub(beginTime)
			Expect(actualDuration).To(BeNumerically("~", expectedDuration, 1*time.Second))
			Expect(actualDuration).To(BeNumerically("<=", 2*time.Second), "Should be much less than 10s")
		})
	})

	When("an ended lease has the ended label", func() {
		It("should be a no-op on subsequent reconciles", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Duration = &metav1.Duration{Duration: 100 * time.Millisecond}

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			Eventually(func() bool {
				_ = reconcileLease(ctx, lease)
				return getLease(ctx, lease.Name).Status.Ended
			}).WithTimeout(500 * time.Millisecond).WithPolling(50 * time.Millisecond).Should(BeTrue())

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Labels).To(HaveKeyWithValue(
				string(jumpstarterdevv1alpha1.LeaseLabelEnded),
				jumpstarterdevv1alpha1.LeaseLabelEndedValue,
			))

			// Reconciling again should be a no-op
			result := reconcileLease(ctx, lease)
			Expect(result.RequeueAfter).To(BeZero())
		})
	})

	When("an ended lease is missing the ended label", func() {
		It("should backfill the label on the next reconcile", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Duration = &metav1.Duration{Duration: 100 * time.Millisecond}

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			Eventually(func() bool {
				_ = reconcileLease(ctx, lease)
				return getLease(ctx, lease.Name).Status.Ended
			}).WithTimeout(500 * time.Millisecond).WithPolling(50 * time.Millisecond).Should(BeTrue())

			// Simulate split-brain: remove the ended label as if metadata update failed
			updatedLease := getLease(ctx, lease.Name)
			delete(updatedLease.Labels, string(jumpstarterdevv1alpha1.LeaseLabelEnded))
			Expect(k8sClient.Update(ctx, updatedLease)).To(Succeed())

			// Verify label is gone
			updatedLease = getLease(ctx, lease.Name)
			Expect(updatedLease.Labels).NotTo(HaveKey(string(jumpstarterdevv1alpha1.LeaseLabelEnded)))

			// Reconcile should backfill the label
			_ = reconcileLease(ctx, lease)
			updatedLease = getLease(ctx, lease.Name)
			Expect(updatedLease.Labels).To(HaveKeyWithValue(
				string(jumpstarterdevv1alpha1.LeaseLabelEnded),
				jumpstarterdevv1alpha1.LeaseLabelEndedValue,
			))
		})
	})

	When("extending an active lease by updating EndTime", func() {
		It("should move the expiry deadline to the new EndTime", func() {
			now := time.Now().Truncate(time.Second)
			lease := leaseDutA2Sec.DeepCopy()
			endTime := metav1.NewTime(now.Add(time.Hour))
			lease.Spec.EndTime = &endTime
			lease.Spec.Duration = nil

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil(), "Should be active")

			newEndTime := metav1.NewTime(now.Add(2 * time.Hour))
			updatedLease.Spec.EndTime = &newEndTime
			Expect(k8sClient.Update(ctx, updatedLease)).To(Succeed())

			result := reconcileLease(ctx, lease)
			elapsed := time.Since(now)

			Expect(getLease(ctx, lease.Name).Status.Ended).To(BeFalse())
			// Same bounds as the expiry table: the deadline follows the updated EndTime
			Expect(result.RequeueAfter).To(And(
				BeNumerically("<=", 2*time.Hour),
				BeNumerically(">=", 2*time.Hour-elapsed),
			))
		})
	})

	When("shortening an active lease by updating Duration", func() {
		It("should shorten the lease duration", func() {
			lease := leaseDutA2Sec.DeepCopy()
			lease.Spec.Duration = &metav1.Duration{Duration: 1 * time.Second}

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil(), "Should be active")
			Expect(updatedLease.Status.BeginTime).NotTo(BeNil())

			// Shorten to 200ms total duration
			updatedLease.Spec.Duration = &metav1.Duration{Duration: 200 * time.Millisecond}
			Expect(k8sClient.Update(ctx, updatedLease)).To(Succeed())

			// Poll until lease expires after shortened duration
			Eventually(func() bool {
				_ = reconcileLease(ctx, lease)
				updatedLease = getLease(ctx, lease.Name)
				return updatedLease.Status.Ended
			}).WithTimeout(500*time.Millisecond).WithPolling(50*time.Millisecond).Should(BeTrue(), "Should expire after shortened duration")
		})
	})

	// Additional edge cases
	When("two scheduled leases compete for the same exporter", func() {
		It("should acquire first lease at BeginTime, then second after first is released", func() {
			ctx := context.Background()

			// Give lease1 an earlier BeginTime to ensure deterministic ordering
			// Use a 2 second gap to ensure lease1 acquires exporter before lease2's BeginTime passes
			lease1BeginTime := metav1.NewTime(time.Now().Truncate(time.Second).Add(2 * time.Second))
			lease2BeginTime := metav1.NewTime(time.Now().Truncate(time.Second).Add(4 * time.Second))

			// Both leases target dut:b (only one exporter available)
			lease1 := leaseDutA2Sec.DeepCopy()
			lease1.Name = lease1Name
			lease1.Spec.Selector.MatchLabels["dut"] = "b"
			lease1.Spec.BeginTime = &lease1BeginTime
			lease1.Spec.Duration = &metav1.Duration{Duration: 10 * time.Second} // Long duration, but we'll release early

			lease2 := leaseDutA2Sec.DeepCopy()
			lease2.Name = lease2Name
			lease2.Spec.Selector.MatchLabels["dut"] = "b"
			lease2.Spec.BeginTime = &lease2BeginTime
			lease2.Spec.Duration = &metav1.Duration{Duration: 10 * time.Second}

			Expect(k8sClient.Create(ctx, lease1)).To(Succeed())
			Expect(k8sClient.Create(ctx, lease2)).To(Succeed())

			// Both should be waiting
			_ = reconcileLease(ctx, lease1)
			_ = reconcileLease(ctx, lease2)

			updatedLease1 := getLease(ctx, lease1Name)
			updatedLease2 := getLease(ctx, lease2Name)
			Expect(updatedLease1.Status.ExporterRef).To(BeNil())
			Expect(updatedLease2.Status.ExporterRef).To(BeNil())

			// Poll until lease1's BeginTime passes and it acquires exporter
			Eventually(func() bool {
				_ = reconcileLease(ctx, lease1)
				_ = reconcileLease(ctx, lease2)
				updatedLease1 = getLease(ctx, lease1Name)
				return updatedLease1.Status.ExporterRef != nil
			}).WithTimeout(3*time.Second).WithPolling(50*time.Millisecond).Should(BeTrue(), "lease1 should acquire exporter")

			updatedLease2 = getLease(ctx, lease2Name)
			Expect(updatedLease2.Status.ExporterRef).To(BeNil(), "lease2 should still be waiting")

			// Explicitly release lease1
			updatedLease1 = getLease(ctx, lease1Name)
			updatedLease1.Spec.Release = true
			Expect(k8sClient.Update(ctx, updatedLease1)).To(Succeed())

			// Poll until lease1 is released and lease2 acquires exporter
			// lease2's BeginTime is at T+4s, so we need enough time to wait for it
			Eventually(func() bool {
				_ = reconcileLease(ctx, lease1)
				_ = reconcileLease(ctx, lease2)
				updatedLease1 = getLease(ctx, lease1Name)
				updatedLease2 = getLease(ctx, lease2Name)
				return updatedLease1.Status.Ended && updatedLease2.Status.ExporterRef != nil
			}).WithTimeout(4*time.Second).WithPolling(50*time.Millisecond).Should(BeTrue(), "lease1 should be released and lease2 should acquire exporter after its BeginTime")
		})
	})

	When("updating scheduled lease to make BeginTime in the past", func() {
		It("should start immediately after update", func() {
			lease := leaseDutA2Sec.DeepCopy()
			futureTime := metav1.NewTime(time.Now().Truncate(time.Second).Add(5 * time.Second))
			lease.Spec.BeginTime = &futureTime
			lease.Spec.Duration = &metav1.Duration{Duration: 1 * time.Second}

			ctx := context.Background()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			_ = reconcileLease(ctx, lease)

			updatedLease := getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).To(BeNil(), "Should not have started yet")

			// Update BeginTime to be in the past
			pastTime := metav1.NewTime(time.Now().Truncate(time.Second).Add(-100 * time.Millisecond))
			updatedLease.Spec.BeginTime = &pastTime
			Expect(k8sClient.Update(ctx, updatedLease)).To(Succeed())

			// Should acquire immediately now
			_ = reconcileLease(ctx, updatedLease)

			updatedLease = getLease(ctx, lease.Name)
			Expect(updatedLease.Status.ExporterRef).NotTo(BeNil(), "Should acquire immediately after BeginTime moved to past")
			Expect(updatedLease.Status.BeginTime).NotTo(BeNil())

			// Verify that actual BeginTime is before the original futureTime (started early)
			Expect(updatedLease.Status.BeginTime.Time).To(BeTemporally("<", futureTime.Time), "Should have started before the original scheduled time")
		})
	})
})

var _ = Describe("pendingRequeueAfter", func() {
	It("should return 1s when no Pending condition exists", func() {
		lease := &jumpstarterdevv1alpha1.Lease{}
		Expect(pendingRequeueAfter(lease)).To(Equal(time.Second))
	})

	DescribeTable("should back off exponentially based on pending duration",
		func(elapsed, expected time.Duration) {
			lease := &jumpstarterdevv1alpha1.Lease{}
			meta.SetStatusCondition(&lease.Status.Conditions, metav1.Condition{
				Type:               string(jumpstarterdevv1alpha1.LeaseConditionTypePending),
				Status:             metav1.ConditionTrue,
				Reason:             "Offline",
				LastTransitionTime: metav1.NewTime(time.Now().Add(-elapsed)),
			})
			Expect(pendingRequeueAfter(lease)).To(Equal(expected))
		},
		Entry("just pending", 500*time.Millisecond, time.Second),
		Entry("1.5s", 1500*time.Millisecond, 2*time.Second),
		Entry("3s", 3*time.Second, 4*time.Second),
		Entry("6s", 6*time.Second, 8*time.Second),
		Entry("12s", 12*time.Second, 16*time.Second),
		Entry("25s (hits cap)", 25*time.Second, 30*time.Second),
		Entry("5m (capped)", 5*time.Minute, 30*time.Second),
	)
})
