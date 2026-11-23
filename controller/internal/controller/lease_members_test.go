/*
Copyright 2026.

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
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	k8serrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes/scheme"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
)

// claimHookClient runs a hook before each create of an ExporterClaim, at the
// point where another reconcile or another controller process can interleave.
type claimHookClient struct {
	client.Client
	beforeCreate func(claim *jumpstarterdevv1alpha1.ExporterClaim)
}

func (c *claimHookClient) Create(ctx context.Context, obj client.Object, opts ...client.CreateOption) error {
	if claim, ok := obj.(*jumpstarterdevv1alpha1.ExporterClaim); ok && c.beforeCreate != nil {
		c.beforeCreate(claim)
	}
	return c.Client.Create(ctx, obj, opts...)
}

func mxExporter(name, group string, labels map[string]string) *jumpstarterdevv1alpha1.Exporter {
	return &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "default", Labels: labels},
		Spec:       jumpstarterdevv1alpha1.ExporterSpec{ExclusionGroup: group},
	}
}

func selectorMember(name string, labels map[string]string) jumpstarterdevv1alpha1.LeaseMember {
	return jumpstarterdevv1alpha1.LeaseMember{
		Name:     name,
		Selector: &metav1.LabelSelector{MatchLabels: labels},
	}
}

func refMember(name, exporter string) jumpstarterdevv1alpha1.LeaseMember {
	return jumpstarterdevv1alpha1.LeaseMember{
		Name:        name,
		ExporterRef: &corev1.LocalObjectReference{Name: exporter},
	}
}

func memberLease(name string, members ...jumpstarterdevv1alpha1.LeaseMember) *jumpstarterdevv1alpha1.Lease {
	return &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "default"},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			ClientRef: corev1.LocalObjectReference{Name: testClient.Name},
			Duration:  &metav1.Duration{Duration: time.Hour},
			Members:   members,
		},
	}
}

func scalarRefLease(name, exporter string) *jumpstarterdevv1alpha1.Lease {
	return &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "default"},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			ClientRef:   corev1.LocalObjectReference{Name: testClient.Name},
			Duration:    &metav1.Duration{Duration: time.Hour},
			ExporterRef: &corev1.LocalObjectReference{Name: exporter},
		},
	}
}

func newLeaseReconciler(c client.Client) *LeaseReconciler {
	return &LeaseReconciler{Client: c, APIReader: k8sClient, Scheme: k8sClient.Scheme()}
}

func reconcileLeaseWith(ctx context.Context, r *LeaseReconciler, name string) reconcile.Result {
	res, err := r.Reconcile(ctx, reconcile.Request{
		NamespacedName: types.NamespacedName{Name: name, Namespace: "default"},
	})
	Expect(err).NotTo(HaveOccurred())
	return res
}

func reconcileLeaseOnly(ctx context.Context, name string) reconcile.Result {
	return reconcileLeaseWith(ctx, newLeaseReconciler(k8sClient), name)
}

func reconcileExporter(ctx context.Context, name string) {
	signer, err := oidc.NewSignerFromSeed([]byte{}, "https://example.com", "dummy")
	Expect(err).NotTo(HaveOccurred())
	r := &ExporterReconciler{Client: k8sClient, Scheme: k8sClient.Scheme(), Signer: signer}
	_, err = r.Reconcile(ctx, reconcile.Request{
		NamespacedName: types.NamespacedName{Name: name, Namespace: "default"},
	})
	Expect(err).NotTo(HaveOccurred())
}

func setExporterStatusValue(ctx context.Context, name, value string) {
	exporter := getExporter(ctx, name)
	exporter.Status.ExporterStatusValue = value
	Expect(k8sClient.Status().Update(ctx, exporter)).To(Succeed())
}

// claimsByKey returns the claims of the namespace by exporter name, and by
// "group:" and the group name.
func claimsByKey(ctx context.Context) map[string]jumpstarterdevv1alpha1.ExporterClaim {
	var claims jumpstarterdevv1alpha1.ExporterClaimList
	Expect(k8sClient.List(ctx, &claims, client.InNamespace("default"))).To(Succeed())
	out := map[string]jumpstarterdevv1alpha1.ExporterClaim{}
	for _, claim := range claims.Items {
		if claim.Spec.ExclusionGroup != "" {
			out["group:"+claim.Spec.ExclusionGroup] = claim
		} else {
			out[claim.Spec.Exporter] = claim
		}
	}
	return out
}

// createClaimFor creates a claim as the controller does, without its checks.
func createClaimFor(
	ctx context.Context, exporter, leaseName string, leaseUID types.UID, attempt int64,
) *jumpstarterdevv1alpha1.ExporterClaim {
	claim := &jumpstarterdevv1alpha1.ExporterClaim{
		ObjectMeta: metav1.ObjectMeta{
			Name:      jumpstarterdevv1alpha1.ExporterClaimName(exporter),
			Namespace: "default",
		},
		Spec: jumpstarterdevv1alpha1.ExporterClaimSpec{
			Exporter: exporter,
			LeaseRef: jumpstarterdevv1alpha1.ClaimLeaseReference{Name: leaseName, UID: leaseUID, Attempt: attempt},
		},
	}
	Expect(k8sClient.Create(ctx, claim)).To(Succeed())
	return claim
}

// bindWithoutClaim gives a scalar lease its exporter the way a controller
// without claims did.
func bindWithoutClaim(ctx context.Context, name, exporter string) *jumpstarterdevv1alpha1.Lease {
	lease := getLease(ctx, name)
	lease.Status.ExporterRef = &corev1.LocalObjectReference{Name: exporter}
	lease.Status.BeginTime = &metav1.Time{Time: time.Now()}
	lease.SetStatusReady(true, "Ready", "An exporter has been acquired for the client")
	Expect(k8sClient.Status().Update(ctx, lease)).To(Succeed())
	return lease
}

func leaseCondition(lease *jumpstarterdevv1alpha1.Lease, t jumpstarterdevv1alpha1.LeaseConditionType) *metav1.Condition {
	return meta.FindStatusCondition(lease.Status.Conditions, string(t))
}

func memberExporter(lease *jumpstarterdevv1alpha1.Lease, role string) string {
	status := lease.MemberStatus(role)
	if status == nil || status.ExporterRef == nil {
		return ""
	}
	return status.ExporterRef.Name
}

var _ = Describe("Member-form leases", func() {
	const (
		leaseA = "mx-lease-a"
		leaseB = "mx-lease-b"
		leaseC = "mx-lease-c"
	)
	phone := map[string]string{"mx": "phone"}
	var ctx context.Context
	var exporters []*jumpstarterdevv1alpha1.Exporter

	// online creates exporters that are registered, online and Available.
	online := func(list ...*jumpstarterdevv1alpha1.Exporter) {
		createExporters(ctx, list...)
		for _, exporter := range list {
			setExporterOnlineConditions(ctx, exporter.Name, metav1.ConditionTrue)
		}
		exporters = append(exporters, list...)
	}

	BeforeEach(func() {
		ctx = context.Background()
		exporters = nil
		// envtest has no garbage collector: the specs before left the claims
		// of their deleted leases.
		Expect(k8sClient.DeleteAllOf(ctx, &jumpstarterdevv1alpha1.ExporterClaim{},
			client.InNamespace("default"))).To(Succeed())
	})
	AfterEach(func() {
		for _, exporter := range exporters {
			_ = k8sClient.Delete(ctx, exporter)
			_ = k8sClient.Delete(ctx, &corev1.Secret{
				ObjectMeta: metav1.ObjectMeta{Name: exporter.Name + "-exporter", Namespace: "default"},
			})
		}
		deleteLeases(ctx, leaseA, leaseB, leaseC)
		// envtest has no garbage collector for the claims of deleted leases.
		Expect(k8sClient.DeleteAllOf(ctx, &jumpstarterdevv1alpha1.ExporterClaim{},
			client.InNamespace("default"))).To(Succeed())
	})

	When("two members have the same selector", func() {
		It("binds two different exporters, with claims, in one status update", func() {
			online(mxExporter("mx-phone-1", "", phone), mxExporter("mx-phone-2", "", phone))
			lease := memberLease(leaseA, selectorMember("phone", phone), selectorMember("phone-2", phone))
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			Expect(updated.Status.ExporterRef).To(BeNil())
			Expect(memberExporter(updated, "phone")).To(Equal("mx-phone-1"))
			Expect(memberExporter(updated, "phone-2")).To(Equal("mx-phone-2"))
			Expect(updated.MemberStatus("phone").State).To(Equal(jumpstarterdevv1alpha1.LeaseMemberStateBound))
			Expect(updated.Status.BeginTime).NotTo(BeNil())
			Expect(updated.Status.BindAttempt).To(Equal(int64(1)))
			Expect(leaseCondition(updated, jumpstarterdevv1alpha1.LeaseConditionTypePending).Status).
				To(Equal(metav1.ConditionFalse))

			claims := claimsByKey(ctx)
			Expect(claims).To(HaveLen(2))
			for _, name := range []string{"mx-phone-1", "mx-phone-2"} {
				Expect(claims[name].Spec.LeaseRef.UID).To(Equal(updated.UID))
				Expect(claims[name].Spec.LeaseRef.Attempt).To(Equal(int64(1)))
				Expect(metav1.IsControlledBy(ptrTo(claims[name]), updated)).To(BeTrue())
			}

			// One owner reference for each bound member, none as controller,
			// and the client, which keeps the lease when exporters are deleted.
			Expect(metav1.GetControllerOf(updated)).To(BeNil())
			owners := map[string]string{}
			for _, ref := range updated.OwnerReferences {
				owners[ref.Name] = ref.Kind
			}
			Expect(owners).To(Equal(map[string]string{
				"mx-phone-1": "Exporter", "mx-phone-2": "Exporter", testClient.Name: "Client",
			}))

			for _, name := range []string{"mx-phone-1", "mx-phone-2"} {
				reconcileExporter(ctx, name)
				Expect(getExporter(ctx, name).Status.LeaseRef).To(Equal(&corev1.LocalObjectReference{Name: leaseA}))
			}
		})

		It("has no status.exporterRef with one member", func() {
			online(mxExporter("mx-phone-1", "", phone))
			Expect(k8sClient.Create(ctx, memberLease(leaseA, selectorMember("phone", phone)))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			Expect(updated.Status.ExporterRef).To(BeNil())
			Expect(memberExporter(updated, "phone")).To(Equal("mx-phone-1"))
			Expect(updated.IsBound()).To(BeTrue())
		})

		It("claims nothing and ends when there are too few exporters", func() {
			online(mxExporter("mx-phone-1", "", phone))
			lease := memberLease(leaseA, selectorMember("phone", phone), selectorMember("phone-2", phone))
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			condition := leaseCondition(updated, jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable)
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonInsufficientExporters))
			Expect(condition.Message).To(Equal(
				"roles `phone`, `phone-2` must have 2 different exporters; only 1 is available: `mx-phone-1`"))
			Expect(updated.Status.Ended).To(BeTrue())
			Expect(updated.IsBound()).To(BeFalse())
			Expect(updated.Status.BindAttempt).To(BeZero())
			Expect(claimsByKey(ctx)).To(BeEmpty())
		})

		It("waits with Pending and claims nothing while an exporter is offline", func() {
			online(mxExporter("mx-phone-1", "", phone), mxExporter("mx-phone-2", "", phone))
			setExporterOnlineConditions(ctx, "mx-phone-2", metav1.ConditionFalse)
			lease := memberLease(leaseA, selectorMember("phone", phone), selectorMember("phone-2", phone))
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			res := reconcileLeaseOnly(ctx, leaseA)
			Expect(res.RequeueAfter).To(BeNumerically(">", 0))

			updated := getLease(ctx, leaseA)
			condition := leaseCondition(updated, jumpstarterdevv1alpha1.LeaseConditionTypePending)
			Expect(condition.Status).To(Equal(metav1.ConditionTrue))
			Expect(condition.Reason).To(Equal("Offline"))
			Expect(condition.Message).To(ContainSubstring("`phone`, `phone-2`"))
			Expect(updated.Status.Ended).To(BeFalse())
			Expect(updated.MemberStatus("phone").State).To(Equal(jumpstarterdevv1alpha1.LeaseMemberStatePending))
			Expect(claimsByKey(ctx)).To(BeEmpty())

			setExporterOnlineConditions(ctx, "mx-phone-2", metav1.ConditionTrue)
			reconcileLeaseOnly(ctx, leaseA)
			Expect(getLease(ctx, leaseA).BoundExporterNames()).To(ConsistOf("mx-phone-1", "mx-phone-2"))
		})

		It("keeps the lease Pending when the roles select an ExporterSet", func() {
			Expect(virtualtargetv1alpha1.AddToScheme(scheme.Scheme)).To(Succeed())
			set := &virtualtargetv1alpha1.ExporterSet{
				ObjectMeta: metav1.ObjectMeta{Name: "mx-phones", Namespace: "default"},
				Spec: virtualtargetv1alpha1.ExporterSetSpec{
					VirtualTargetClassName: "qemu-class",
					Selector:               metav1.LabelSelector{MatchLabels: phone},
					Template: virtualtargetv1alpha1.ExporterSetTemplate{
						Metadata: virtualtargetv1alpha1.EmbeddedObjectMeta{Labels: phone},
					},
				},
			}
			Expect(k8sClient.Create(ctx, set)).To(Succeed())
			DeferCleanup(func() { Expect(k8sClient.Delete(ctx, set)).To(Succeed()) })

			online(mxExporter("mx-phone-1", "", phone))
			lease := memberLease(leaseA, selectorMember("phone", phone), selectorMember("phone-2", phone))
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			condition := leaseCondition(updated, jumpstarterdevv1alpha1.LeaseConditionTypePending)
			Expect(condition).NotTo(BeNil())
			Expect(condition.Status).To(Equal(metav1.ConditionTrue))
			Expect(condition.Reason).To(Equal("NotAvailable"))
			Expect(updated.Status.Ended).To(BeFalse())
			Expect(leaseCondition(updated, jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable)).To(BeNil())
			Expect(claimsByKey(ctx)).To(BeEmpty())

			// The set created a second exporter.
			online(mxExporter("mx-phone-2", "", phone))
			reconcileLeaseOnly(ctx, leaseA)
			Expect(getLease(ctx, leaseA).BoundExporterNames()).To(ConsistOf("mx-phone-1", "mx-phone-2"))
		})

		It("is unsatisfiable when a role selects a set that has no exporter", func() {
			lease := memberLease(leaseA, selectorMember("phone", phone))
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			condition := leaseCondition(getLease(ctx, leaseA), jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable)
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal("NoAccess"))
			Expect(condition.Message).To(HavePrefix("role `phone`: "))
		})
	})

	When("members are optional, excluded or constrained", func() {
		It("omits optional members that cannot bind and members that the request left out", func() {
			online(mxExporter("mx-phone-1", "", phone))
			optional := selectorMember("phone-2", phone)
			optional.Optional = true
			missing := refMember("probe", "mx-does-not-exist")
			missing.Optional = true
			excluded := selectorMember("spare", phone)
			excluded.Optional, excluded.Excluded = true, true
			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				optional, selectorMember("phone", phone), missing, excluded))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			Expect(memberExporter(updated, "phone")).To(Equal("mx-phone-1"))
			for role, reason := range map[string]string{
				"phone-2": "NotAvailable",
				"probe":   "ExporterNotFound",
				"spare":   jumpstarterdevv1alpha1.LeaseMemberReasonExcluded,
			} {
				status := updated.MemberStatus(role)
				Expect(status.State).To(Equal(jumpstarterdevv1alpha1.LeaseMemberStateOmitted), role)
				Expect(status.Reason).To(Equal(reason), role)
			}
			Expect(claimsByKey(ctx)).To(HaveLen(1))
		})

		It("selects a shared label value at bind time", func() {
			online(
				mxExporter("mx-ecu-1", "", map[string]string{"mx": "ecu", "bus": "can-1"}),
				mxExporter("mx-ecu-2", "", map[string]string{"mx": "ecu", "bus": "can-2"}),
				mxExporter("mx-probe-0", "", map[string]string{"mx": "probe"}),
				mxExporter("mx-probe-2", "", map[string]string{"mx": "probe", "bus": "can-2"}),
			)
			lease := memberLease(leaseA,
				selectorMember("ecu", map[string]string{"mx": "ecu"}),
				selectorMember("probe", map[string]string{"mx": "probe"}))
			lease.Spec.Constraints = []jumpstarterdevv1alpha1.LeaseConstraint{
				{Members: []string{"ecu", "probe"}, SameLabel: "bus"},
			}
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			Expect(memberExporter(updated, "ecu")).To(Equal("mx-ecu-2"))
			Expect(memberExporter(updated, "probe")).To(Equal("mx-probe-2"))
		})

		It("is unsatisfiable when no value satisfies the constraint", func() {
			online(
				mxExporter("mx-ecu-1", "", map[string]string{"mx": "ecu", "bus": "can-1"}),
				mxExporter("mx-probe-2", "", map[string]string{"mx": "probe", "bus": "can-2"}),
			)
			lease := memberLease(leaseA,
				selectorMember("ecu", map[string]string{"mx": "ecu"}),
				selectorMember("probe", map[string]string{"mx": "probe"}))
			lease.Spec.Constraints = []jumpstarterdevv1alpha1.LeaseConstraint{
				{Members: []string{"ecu", "probe"}, SameLabel: "bus"},
			}
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			condition := leaseCondition(getLease(ctx, leaseA), jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable)
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonConstraintUnsatisfiable))
			Expect(claimsByKey(ctx)).To(BeEmpty())
		})
	})

	When("access policies apply", func() {
		var shared *jumpstarterdevv1alpha1.Client

		policy := func(name string, priority int, exporterLabels, clientLabels map[string]string) {
			p := &jumpstarterdevv1alpha1.ExporterAccessPolicy{
				ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "default"},
				Spec: jumpstarterdevv1alpha1.ExporterAccessPolicySpec{
					ExporterSelector: metav1.LabelSelector{MatchLabels: exporterLabels},
					Policies: []jumpstarterdevv1alpha1.Policy{{
						Priority: priority,
						From: []jumpstarterdevv1alpha1.From{{
							ClientSelector: metav1.LabelSelector{MatchLabels: clientLabels},
						}},
					}},
				},
			}
			Expect(k8sClient.Create(ctx, p)).To(Succeed())
			DeferCleanup(func() { Expect(k8sClient.Delete(ctx, p)).To(Succeed()) })
		}

		BeforeEach(func() {
			shared = &jumpstarterdevv1alpha1.Client{ObjectMeta: metav1.ObjectMeta{
				Name: "mx-shared", Namespace: "default", Labels: map[string]string{"name": "mx-shared"},
			}}
			Expect(k8sClient.Create(ctx, shared)).To(Succeed())
			DeferCleanup(func() { Expect(k8sClient.Delete(ctx, shared)).To(Succeed()) })
			online(
				mxExporter("mx-ecu-1", "", map[string]string{"mx": "ecu"}),
				mxExporter("mx-probe-1", "", map[string]string{"mx": "probe"}),
			)
			policy("mx-ecu", 10, map[string]string{"mx": "ecu"}, map[string]string{"name": testClient.Name})
			policy("mx-probe", 5, map[string]string{"mx": "probe"}, map[string]string{"name": testClient.Name})
		})

		It("gives the lease the minimum member priority", func() {
			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				refMember("ecu", "mx-ecu-1"), refMember("probe", "mx-probe-1")))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			Expect(updated.MemberStatus("ecu").Priority).To(Equal(10))
			Expect(updated.MemberStatus("probe").Priority).To(Equal(5))
			Expect(updated.Status.Priority).To(Equal(5))
		})

		It("lists a shared client only if the policy lets it use every bound member", func() {
			policy("mx-shared-ecu", 0, map[string]string{"mx": "ecu"}, map[string]string{"name": "mx-shared"})
			lease := memberLease(leaseA, refMember("ecu", "mx-ecu-1"), refMember("probe", "mx-probe-1"))
			lease.Spec.SharedWith = []string{"mx-shared"}
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)
			Expect(getLease(ctx, leaseA).IsBound()).To(BeTrue())
			Expect(getLease(ctx, leaseA).Status.SharedWith).To(BeEmpty())

			policy("mx-shared-probe", 0, map[string]string{"mx": "probe"}, map[string]string{"name": "mx-shared"})
			reconcileLeaseOnly(ctx, leaseA)
			Expect(getLease(ctx, leaseA).Status.SharedWith).To(Equal([]string{"mx-shared"}))
		})

		It("does not use an exporter whose policy does not permit the duration", func() {
			limited := &jumpstarterdevv1alpha1.ExporterAccessPolicy{
				ObjectMeta: metav1.ObjectMeta{Name: "mx-short", Namespace: "default"},
				Spec: jumpstarterdevv1alpha1.ExporterAccessPolicySpec{
					ExporterSelector: metav1.LabelSelector{MatchLabels: map[string]string{"mx": "short"}},
					Policies: []jumpstarterdevv1alpha1.Policy{{
						MaximumDuration: &metav1.Duration{Duration: time.Minute},
						From: []jumpstarterdevv1alpha1.From{{
							ClientSelector: metav1.LabelSelector{MatchLabels: map[string]string{"name": testClient.Name}},
						}},
					}},
				},
			}
			Expect(k8sClient.Create(ctx, limited)).To(Succeed())
			DeferCleanup(func() { Expect(k8sClient.Delete(ctx, limited)).To(Succeed()) })
			online(mxExporter("mx-short-1", "", map[string]string{"mx": "short"}))

			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				refMember("ecu", "mx-ecu-1"), refMember("short", "mx-short-1")))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			condition := leaseCondition(getLease(ctx, leaseA), jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable)
			Expect(condition).NotTo(BeNil())
			Expect(condition.Reason).To(Equal("NoAccess"))
			Expect(condition.Message).To(HavePrefix("role `short`: "))
			Expect(claimsByKey(ctx)).To(BeEmpty())
		})
	})

	When("leases compete for claims", func() {
		BeforeEach(func() {
			online(mxExporter("mx-phone-1", "", phone), mxExporter("mx-phone-2", "", phone))
		})

		It("deletes the claims of its attempt when a create fails", func() {
			// Lease B is active and holds the claim that lease A creates second.
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseB, "mx-phone-1"))).To(Succeed())
			other := getLease(ctx, leaseB)
			names := []string{
				jumpstarterdevv1alpha1.ExporterClaimName("mx-phone-1"),
				jumpstarterdevv1alpha1.ExporterClaimName("mx-phone-2"),
			}
			second := "mx-phone-2"
			if names[0] > names[1] {
				second = "mx-phone-1"
			}

			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				refMember("one", "mx-phone-1"), refMember("two", "mx-phone-2")))).To(Succeed())
			created := 0
			hooked := &claimHookClient{Client: k8sClient}
			hooked.beforeCreate = func(claim *jumpstarterdevv1alpha1.ExporterClaim) {
				created++
				if created == 2 {
					// Lease A holds its first claim at this point.
					Expect(claimsByKey(ctx)).To(HaveLen(1))
					createClaimFor(ctx, second, other.Name, other.UID, 1)
				}
			}
			res := reconcileLeaseWith(ctx, newLeaseReconciler(hooked), leaseA)
			Expect(res.RequeueAfter).To(BeNumerically(">", 0))

			updated := getLease(ctx, leaseA)
			Expect(updated.IsBound()).To(BeFalse())
			condition := leaseCondition(updated, jumpstarterdevv1alpha1.LeaseConditionTypePending)
			Expect(condition.Status).To(Equal(metav1.ConditionTrue))
			Expect(condition.Reason).To(Equal("NotAvailable"))
			claims := claimsByKey(ctx)
			Expect(claims).To(HaveLen(1))
			Expect(claims[second].Spec.LeaseRef.UID).To(Equal(other.UID))
		})

		It("completes two leases with the same exporters in opposite member order", func() {
			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				refMember("one", "mx-phone-1"), refMember("two", "mx-phone-2")))).To(Succeed())
			Expect(k8sClient.Create(ctx, memberLease(leaseB,
				refMember("one", "mx-phone-2"), refMember("two", "mx-phone-1")))).To(Succeed())

			// Lease B is reconciled while lease A is about to create its
			// first claim. Both create the same claim first.
			interleaved := false
			hooked := &claimHookClient{Client: k8sClient}
			hooked.beforeCreate = func(*jumpstarterdevv1alpha1.ExporterClaim) {
				if !interleaved {
					interleaved = true
					reconcileLeaseOnly(ctx, leaseB)
				}
			}
			reconcileLeaseWith(ctx, newLeaseReconciler(hooked), leaseA)

			a, b := getLease(ctx, leaseA), getLease(ctx, leaseB)
			Expect(b.BoundExporterNames()).To(ConsistOf("mx-phone-1", "mx-phone-2"))
			Expect(a.IsBound()).To(BeFalse())
			claims := claimsByKey(ctx)
			Expect(claims).To(HaveLen(2))
			for _, claim := range claims {
				Expect(claim.Spec.LeaseRef.UID).To(Equal(b.UID))
			}

			// When lease B ends, its claims go and lease A binds.
			b.Spec.Release = true
			Expect(k8sClient.Update(ctx, b)).To(Succeed())
			reconcileLeaseOnly(ctx, leaseB)
			Expect(claimsByKey(ctx)).To(BeEmpty())
			reconcileLeaseOnly(ctx, leaseA)
			Expect(getLease(ctx, leaseA).BoundExporterNames()).To(ConsistOf("mx-phone-1", "mx-phone-2"))
		})

		It("replaces the claims of leases that ended or do not exist", func() {
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseB, "mx-phone-2"))).To(Succeed())
			ended := getLease(ctx, leaseB)
			ended.Status.Ended = true
			Expect(k8sClient.Status().Update(ctx, ended)).To(Succeed())
			createClaimFor(ctx, "mx-phone-1", "mx-deleted-lease", "00000000-0000-0000-0000-000000000001", 3)
			createClaimFor(ctx, "mx-phone-2", ended.Name, ended.UID, 1)

			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				selectorMember("phone", phone), selectorMember("phone-2", phone)))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			Expect(updated.BoundExporterNames()).To(ConsistOf("mx-phone-1", "mx-phone-2"))
			for _, claim := range claimsByKey(ctx) {
				Expect(claim.Spec.LeaseRef.UID).To(Equal(updated.UID))
			}
		})

		It("does not replace the claim of an active lease", func() {
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseB, "mx-phone-1"))).To(Succeed())
			holder := getLease(ctx, leaseB)
			claim := createClaimFor(ctx, "mx-phone-1", holder.Name, holder.UID, 1)

			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				selectorMember("phone", phone), selectorMember("phone-2", phone)))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			Expect(updated.IsBound()).To(BeFalse())
			Expect(leaseCondition(updated, jumpstarterdevv1alpha1.LeaseConditionTypePending).Reason).
				To(Equal("NotAvailable"))
			claims := claimsByKey(ctx)
			Expect(claims).To(HaveLen(1))
			Expect(claims["mx-phone-1"].UID).To(Equal(claim.UID))
		})

		It("cleans the attempt of a reconcile that stopped", func() {
			online(mxExporter("mx-phone-3", "", map[string]string{"mx": "other"}))
			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				selectorMember("phone", phone), selectorMember("phone-2", phone)))).To(Succeed())
			// A reconcile started attempt 1, created one claim and stopped.
			lease := getLease(ctx, leaseA)
			lease.Status.BindAttempt = 1
			Expect(k8sClient.Status().Update(ctx, lease)).To(Succeed())
			createClaimFor(ctx, "mx-phone-3", lease.Name, lease.UID, 1)

			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			Expect(updated.Status.BindAttempt).To(Equal(int64(2)))
			Expect(updated.BoundExporterNames()).To(ConsistOf("mx-phone-1", "mx-phone-2"))
			claims := claimsByKey(ctx)
			Expect(claims).To(HaveLen(2))
			Expect(claims).NotTo(HaveKey("mx-phone-3"))
			for _, claim := range claims {
				Expect(claim.Spec.LeaseRef.Attempt).To(Equal(int64(2)))
			}
		})

		It("cleans the claims of a lease that is not bound and cannot bind now", func() {
			setExporterOnlineConditions(ctx, "mx-phone-2", metav1.ConditionFalse)
			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				selectorMember("phone", phone), selectorMember("phone-2", phone)))).To(Succeed())
			lease := getLease(ctx, leaseA)
			lease.Status.BindAttempt = 1
			Expect(k8sClient.Status().Update(ctx, lease)).To(Succeed())
			createClaimFor(ctx, "mx-phone-1", lease.Name, lease.UID, 1)

			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			Expect(updated.IsBound()).To(BeFalse())
			Expect(updated.Status.BindAttempt).To(Equal(int64(2)))
			Expect(claimsByKey(ctx)).To(BeEmpty())
		})

		It("keeps the claims of the bound lease when an old reconcile continues", func() {
			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				selectorMember("phone", phone), selectorMember("phone-2", phone)))).To(Succeed())

			// The old leader created its first claim and is about to create
			// the second one when a new leader binds the lease.
			created := 0
			hooked := &claimHookClient{Client: k8sClient}
			hooked.beforeCreate = func(*jumpstarterdevv1alpha1.ExporterClaim) {
				created++
				if created == 2 {
					reconcileLeaseOnly(ctx, leaseA)
					Expect(getLease(ctx, leaseA).IsBound()).To(BeTrue())
				}
			}
			_, err := newLeaseReconciler(hooked).Reconcile(ctx, reconcile.Request{
				NamespacedName: types.NamespacedName{Name: leaseA, Namespace: "default"},
			})
			Expect(err).NotTo(HaveOccurred())

			updated := getLease(ctx, leaseA)
			Expect(updated.Status.BindAttempt).To(Equal(int64(2)))
			Expect(updated.BoundExporterNames()).To(ConsistOf("mx-phone-1", "mx-phone-2"))
			claims := claimsByKey(ctx)
			Expect(claims).To(HaveLen(2))
			for _, claim := range claims {
				Expect(claim.Spec.LeaseRef.UID).To(Equal(updated.UID))
				Expect(claim.Spec.LeaseRef.Attempt).To(Equal(int64(2)))
			}
		})

		It("deletes a claim of a smaller attempt on the reconcile of the bound lease", func() {
			online(mxExporter("mx-phone-3", "", map[string]string{"mx": "other"}))
			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				selectorMember("phone", phone), selectorMember("phone-2", phone)))).To(Succeed())
			lease := getLease(ctx, leaseA)
			lease.Status.BindAttempt = 4
			Expect(k8sClient.Status().Update(ctx, lease)).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)
			Expect(getLease(ctx, leaseA).Status.BindAttempt).To(Equal(int64(5)))

			// An old leader creates a claim after the bind.
			createClaimFor(ctx, "mx-phone-3", lease.Name, lease.UID, 3)
			reconcileLeaseOnly(ctx, leaseA)

			claims := claimsByKey(ctx)
			Expect(claims).To(HaveLen(2))
			Expect(claims).NotTo(HaveKey("mx-phone-3"))
			Expect(getLease(ctx, leaseA).Status.BindAttempt).To(Equal(int64(5)))
		})

		It("deletes the claims when the lease ends", func() {
			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				selectorMember("phone", phone), selectorMember("phone-2", phone)))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)
			Expect(claimsByKey(ctx)).To(HaveLen(2))

			lease := getLease(ctx, leaseA)
			lease.Spec.Release = true
			Expect(k8sClient.Update(ctx, lease)).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			Expect(getLease(ctx, leaseA).Status.Ended).To(BeTrue())
			Expect(claimsByKey(ctx)).To(BeEmpty())
			for _, name := range []string{"mx-phone-1", "mx-phone-2"} {
				reconcileExporter(ctx, name)
				Expect(getExporter(ctx, name).Status.LeaseRef).To(BeNil())
			}
		})
	})

	When("exporters have an exclusion group", func() {
		dut := map[string]string{"mx": "dut"}

		BeforeEach(func() {
			online(
				mxExporter("mx-dut-power", "mx-dut-1", dut),
				mxExporter("mx-dut-serial", "mx-dut-1", dut),
				mxExporter("mx-dut-flash", "mx-dut-1", dut),
			)
		})

		It("lets one lease bind several exporters of the group and no other lease the rest", func() {
			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				refMember("power", "mx-dut-power"), refMember("serial", "mx-dut-serial")))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			Expect(updated.BoundExporterNames()).To(ConsistOf("mx-dut-power", "mx-dut-serial"))
			Expect(updated.Status.ExclusionGroups).To(Equal([]string{"mx-dut-1"}))
			claims := claimsByKey(ctx)
			Expect(claims).To(HaveLen(3))
			Expect(claims["group:mx-dut-1"].Spec.LeaseRef.UID).To(Equal(updated.UID))

			// A scalar lease for the exporter that lease A did not bind.
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseB, "mx-dut-flash"))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseB)
			second := getLease(ctx, leaseB)
			Expect(second.Status.ExporterRef).To(BeNil())
			Expect(leaseCondition(second, jumpstarterdevv1alpha1.LeaseConditionTypePending).Reason).
				To(Equal("NotAvailable"))
			Expect(claimsByKey(ctx)).To(HaveLen(3))

			// The exporter of the group that lease A did not bind gets no lease.
			reconcileExporter(ctx, "mx-dut-flash")
			Expect(getExporter(ctx, "mx-dut-flash").Status.LeaseRef).To(BeNil())

			updated.Spec.Release = true
			Expect(k8sClient.Update(ctx, updated)).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)
			reconcileLeaseOnly(ctx, leaseB)
			second = getLease(ctx, leaseB)
			Expect(second.Status.ExporterRef).NotTo(BeNil())
			Expect(second.Status.ExclusionGroups).To(Equal([]string{"mx-dut-1"}))
		})

		It("does not bind an exporter while an exporter of its group is in cleanup", func() {
			setExporterStatusValue(ctx, "mx-dut-serial", jumpstarterdevv1alpha1.ExporterStatusAfterLeaseHook)
			Expect(k8sClient.Create(ctx, memberLease(leaseA, refMember("power", "mx-dut-power")))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			Expect(updated.IsBound()).To(BeFalse())
			Expect(leaseCondition(updated, jumpstarterdevv1alpha1.LeaseConditionTypePending).Reason).
				To(Equal("NotReady"))
			Expect(claimsByKey(ctx)).To(BeEmpty())

			setExporterStatusValue(ctx, "mx-dut-serial", jumpstarterdevv1alpha1.ExporterStatusAvailable)
			reconcileLeaseOnly(ctx, leaseA)
			Expect(getLease(ctx, leaseA).IsBound()).To(BeTrue())
		})

		It("rejects a group change on an exporter that has a lease", func() {
			Expect(k8sClient.Create(ctx, memberLease(leaseA, refMember("power", "mx-dut-power")))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)
			reconcileExporter(ctx, "mx-dut-power")

			exporter := getExporter(ctx, "mx-dut-power")
			Expect(exporter.Status.LeaseRef).NotTo(BeNil())
			exporter.Spec.ExclusionGroup = "mx-dut-2"
			err := k8sClient.Update(ctx, exporter)
			Expect(err).To(HaveOccurred())
			Expect(err.Error()).To(ContainSubstring("spec.exclusionGroup cannot change"))
		})
	})

	When("the members are bound", func() {
		var roles = []string{"mx-phone-1", "mx-phone-2"}

		BeforeEach(func() {
			online(mxExporter("mx-phone-1", "", phone), mxExporter("mx-phone-2", "", phone))
			Expect(k8sClient.Create(ctx, memberLease(leaseA,
				selectorMember("phone", phone), selectorMember("phone-2", phone)))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)
			Expect(getLease(ctx, leaseA).BoundExporterNames()).To(ConsistOf(roles))
		})

		ready := func() *metav1.Condition {
			return leaseCondition(getLease(ctx, leaseA), jumpstarterdevv1alpha1.LeaseConditionTypeReady)
		}

		It("is Ready only when every member reports LeaseReady", func() {
			Expect(ready().Status).To(Equal(metav1.ConditionFalse))
			Expect(ready().Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonBeforeLeaseHook))
			Expect(ready().Message).To(ContainSubstring("`phone`, `phone-2`"))

			setExporterStatusValue(ctx, "mx-phone-1", jumpstarterdevv1alpha1.ExporterStatusLeaseReady)
			setExporterStatusValue(ctx, "mx-phone-2", jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHook)
			reconcileLeaseOnly(ctx, leaseA)
			Expect(ready().Status).To(Equal(metav1.ConditionFalse))
			Expect(ready().Message).To(ContainSubstring("`phone-2`"))
			Expect(ready().Message).NotTo(ContainSubstring("`phone`,"))

			setExporterStatusValue(ctx, "mx-phone-2", jumpstarterdevv1alpha1.ExporterStatusLeaseReady)
			reconcileLeaseOnly(ctx, leaseA)
			Expect(ready().Status).To(Equal(metav1.ConditionTrue))
			Expect(getLease(ctx, leaseA).HasLostMember()).To(BeFalse())
		})

		It("reports a beforeLease hook that failed", func() {
			setExporterStatusValue(ctx, "mx-phone-1", jumpstarterdevv1alpha1.ExporterStatusLeaseReady)
			setExporterStatusValue(ctx, "mx-phone-2", jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHookFailed)
			reconcileLeaseOnly(ctx, leaseA)
			Expect(ready().Status).To(Equal(metav1.ConditionFalse))
			Expect(ready().Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonBeforeLeaseHookFailed))
			Expect(ready().Message).To(ContainSubstring("`phone-2`"))
		})

		// expectLost checks the effects of a lost member: the lease is
		// Degraded and keeps its other member, the exporter of the lost member
		// has no lease, and its claim stays.
		expectLost := func(reason string) {
			updated := getLease(ctx, leaseA)
			lost := updated.MemberStatus("phone-2")
			Expect(lost.State).To(Equal(jumpstarterdevv1alpha1.LeaseMemberStateLost))
			Expect(lost.Reason).To(Equal(reason))
			Expect(lost.ExporterRef.Name).To(Equal("mx-phone-2"))
			Expect(updated.MemberStatus("phone").State).To(Equal(jumpstarterdevv1alpha1.LeaseMemberStateBound))
			Expect(updated.Status.Ended).To(BeFalse())

			degraded := leaseCondition(updated, jumpstarterdevv1alpha1.LeaseConditionTypeDegraded)
			Expect(degraded).NotTo(BeNil())
			Expect(degraded.Status).To(Equal(metav1.ConditionTrue))
			Expect(degraded.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonMemberLost))
			Expect(degraded.Message).To(ContainSubstring("`phone-2`"))
			Expect(ready().Status).To(Equal(metav1.ConditionFalse))
			Expect(ready().Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonMemberLost))

			claims := claimsByKey(ctx)
			Expect(claims).To(HaveLen(2))
			Expect(claims["mx-phone-2"].Spec.LeaseRef.UID).To(Equal(updated.UID))

			reconcileExporter(ctx, "mx-phone-1")
			Expect(getExporter(ctx, "mx-phone-1").Status.LeaseRef).To(Equal(&corev1.LocalObjectReference{Name: leaseA}))
			owners := []string{}
			for _, ref := range updated.OwnerReferences {
				owners = append(owners, ref.Name)
			}
			Expect(owners).To(ConsistOf("mx-phone-1", testClient.Name))
		}

		It("loses a member whose exporter goes offline", func() {
			for _, name := range roles {
				setExporterStatusValue(ctx, name, jumpstarterdevv1alpha1.ExporterStatusLeaseReady)
			}
			reconcileLeaseOnly(ctx, leaseA)
			Expect(ready().Status).To(Equal(metav1.ConditionTrue))

			setExporterOnlineConditions(ctx, "mx-phone-2", metav1.ConditionFalse)
			reconcileLeaseOnly(ctx, leaseA)
			expectLost(jumpstarterdevv1alpha1.LeaseMemberReasonOffline)
			reconcileExporter(ctx, "mx-phone-2")
			Expect(getExporter(ctx, "mx-phone-2").Status.LeaseRef).To(BeNil())

			// Lost is final: the exporter comes back, the member does not.
			setExporterOnlineConditions(ctx, "mx-phone-2", metav1.ConditionTrue)
			reconcileLeaseOnly(ctx, leaseA)
			expectLost(jumpstarterdevv1alpha1.LeaseMemberReasonOffline)
			reconcileExporter(ctx, "mx-phone-2")
			Expect(getExporter(ctx, "mx-phone-2").Status.LeaseRef).To(BeNil())

			// No other lease gets the exporter while this lease runs.
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseB, "mx-phone-2"))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseB)
			Expect(getLease(ctx, leaseB).Status.ExporterRef).To(BeNil())
		})

		It("loses a member whose exporter is deleted", func() {
			Expect(k8sClient.Delete(ctx, getExporter(ctx, "mx-phone-2"))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)
			expectLost(jumpstarterdevv1alpha1.LeaseMemberReasonExporterDeleted)

			// Further reconciles do not fail and do not change the member.
			before := getLease(ctx, leaseA).MemberStatus("phone-2").DeepCopy()
			reconcileLeaseOnly(ctx, leaseA)
			expectLost(jumpstarterdevv1alpha1.LeaseMemberReasonExporterDeleted)
			Expect(getLease(ctx, leaseA).MemberStatus("phone-2")).To(Equal(before))
		})

		It("reacts to a member that the exporter released", func() {
			// The controller service marks the member when the exporter
			// releases the lease.
			lease := getLease(ctx, leaseA)
			Expect(lease.MarkMemberLost("phone-2",
				jumpstarterdevv1alpha1.LeaseMemberReasonExporterReleased, "released")).To(BeTrue())
			Expect(k8sClient.Status().Update(ctx, lease)).To(Succeed())

			reconcileLeaseOnly(ctx, leaseA)
			expectLost(jumpstarterdevv1alpha1.LeaseMemberReasonExporterReleased)
			reconcileExporter(ctx, "mx-phone-2")
			Expect(getExporter(ctx, "mx-phone-2").Status.LeaseRef).To(BeNil())

			// The exporter of a lost member can be deleted while the lease
			// runs: nothing fails, and the claim stays until the lease ends.
			Expect(k8sClient.Delete(ctx, getExporter(ctx, "mx-phone-2"))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)
			expectLost(jumpstarterdevv1alpha1.LeaseMemberReasonExporterReleased)
			reconcileExporter(ctx, "mx-phone-2")

			lease = getLease(ctx, leaseA)
			lease.Spec.Release = true
			Expect(k8sClient.Update(ctx, lease)).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)
			Expect(getLease(ctx, leaseA).Status.Ended).To(BeTrue())
			Expect(claimsByKey(ctx)).To(BeEmpty())
		})

		It("follows the exporters of its bound members only", func() {
			lease := getLease(ctx, leaseA)
			Expect(leaseFollowsExporter(lease, getExporter(ctx, "mx-phone-1"))).To(BeTrue())
			Expect(lease.MarkMemberLost("phone-2", jumpstarterdevv1alpha1.LeaseMemberReasonOffline, "")).To(BeTrue())
			Expect(leaseFollowsExporter(lease, getExporter(ctx, "mx-phone-2"))).To(BeFalse())
			Expect(mapLeaseToExporters(ctx, lease)).To(HaveLen(2))
		})
	})

	When("the exporter reconciler sets status.leaseRef", func() {
		BeforeEach(func() {
			online(mxExporter("mx-phone-1", "", phone))
		})

		leaseRef := func() *corev1.LocalObjectReference {
			reconcileExporter(ctx, "mx-phone-1")
			return getExporter(ctx, "mx-phone-1").Status.LeaseRef
		}

		It("needs the claim and the bound status together", func() {
			Expect(k8sClient.Create(ctx, memberLease(leaseA, selectorMember("phone", phone)))).To(Succeed())
			lease := getLease(ctx, leaseA)

			// A claim alone: the bind did not happen yet.
			claim := createClaimFor(ctx, "mx-phone-1", lease.Name, lease.UID, 1)
			Expect(leaseRef()).To(BeNil())

			// The bound status without the claim of this lease.
			Expect(k8sClient.Delete(ctx, claim)).To(Succeed())
			lease.Status.Members = []jumpstarterdevv1alpha1.LeaseMemberStatus{{
				Name:        "phone",
				State:       jumpstarterdevv1alpha1.LeaseMemberStateBound,
				ExporterRef: &corev1.LocalObjectReference{Name: "mx-phone-1"},
			}}
			Expect(k8sClient.Status().Update(ctx, lease)).To(Succeed())
			Expect(leaseRef()).To(BeNil())

			createClaimFor(ctx, "mx-phone-1", lease.Name, lease.UID, 1)
			Expect(leaseRef()).To(Equal(&corev1.LocalObjectReference{Name: leaseA}))
		})

		It("keeps the lease of a scalar lease that has no claim yet", func() {
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseA, "mx-phone-1"))).To(Succeed())
			bindWithoutClaim(ctx, leaseA, "mx-phone-1")
			Expect(leaseRef()).To(Equal(&corev1.LocalObjectReference{Name: leaseA}))
		})

		It("follows the claim when two leases show the exporter as bound", func() {
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseA, "mx-phone-1"))).To(Succeed())
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseB, "mx-phone-1"))).To(Succeed())
			first := bindWithoutClaim(ctx, leaseA, "mx-phone-1")
			bindWithoutClaim(ctx, leaseB, "mx-phone-1")
			createClaimFor(ctx, "mx-phone-1", first.Name, first.UID, 0)
			Expect(leaseRef()).To(Equal(&corev1.LocalObjectReference{Name: leaseA}))
		})
	})

	When("leases were bound without claims", func() {
		BeforeEach(func() {
			online(mxExporter("mx-phone-1", "", phone), mxExporter("mx-phone-2", "", phone))
		})

		It("creates their claims before it binds a new lease", func() {
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseA, "mx-phone-1"))).To(Succeed())
			old := bindWithoutClaim(ctx, leaseA, "mx-phone-1")
			Expect(claimsByKey(ctx)).To(BeEmpty())

			Expect(k8sClient.Create(ctx, memberLease(leaseB, selectorMember("phone", phone)))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseB)

			claims := claimsByKey(ctx)
			Expect(claims["mx-phone-1"].Spec.LeaseRef.UID).To(Equal(old.UID))
			Expect(memberExporter(getLease(ctx, leaseB), "phone")).To(Equal("mx-phone-2"))
			reconcileExporter(ctx, "mx-phone-1")
			Expect(getExporter(ctx, "mx-phone-1").Status.LeaseRef).To(Equal(&corev1.LocalObjectReference{Name: leaseA}))
		})

		It("creates the claim on the reconcile of the bound lease", func() {
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseA, "mx-phone-1"))).To(Succeed())
			old := bindWithoutClaim(ctx, leaseA, "mx-phone-1")
			reconcileLeaseOnly(ctx, leaseA)
			Expect(claimsByKey(ctx)["mx-phone-1"].Spec.LeaseRef.UID).To(Equal(old.UID))
			Expect(getLease(ctx, leaseA).Status.Ended).To(BeFalse())
		})

		It("gives the claim to the older of two leases that hold one exporter", func() {
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseA, "mx-phone-1"))).To(Succeed())
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseB, "mx-phone-1"))).To(Succeed())
			older := bindWithoutClaim(ctx, leaseA, "mx-phone-1")
			bindWithoutClaim(ctx, leaseB, "mx-phone-1")

			// The backfill runs before the bind of a new lease.
			Expect(k8sClient.Create(ctx, memberLease(leaseC, selectorMember("phone", phone)))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseC)

			Expect(claimsByKey(ctx)["mx-phone-1"].Spec.LeaseRef.UID).To(Equal(older.UID))
			newer := getLease(ctx, leaseB)
			invalid := leaseCondition(newer, jumpstarterdevv1alpha1.LeaseConditionTypeInvalid)
			Expect(invalid).NotTo(BeNil())
			Expect(invalid.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonExporterConflict))

			reconcileLeaseOnly(ctx, leaseB)
			Expect(getLease(ctx, leaseB).Status.Ended).To(BeTrue())
			Expect(getLease(ctx, leaseA).Status.Ended).To(BeFalse())
			Expect(claimsByKey(ctx)["mx-phone-1"].Spec.LeaseRef.UID).To(Equal(older.UID))
			reconcileExporter(ctx, "mx-phone-1")
			Expect(getExporter(ctx, "mx-phone-1").Status.LeaseRef).To(Equal(&corev1.LocalObjectReference{Name: leaseA}))
		})

		It("takes the claim from the newer lease when that lease got it first", func() {
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseA, "mx-phone-1"))).To(Succeed())
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseB, "mx-phone-1"))).To(Succeed())
			older := bindWithoutClaim(ctx, leaseA, "mx-phone-1")
			newer := bindWithoutClaim(ctx, leaseB, "mx-phone-1")

			reconcileLeaseOnly(ctx, leaseB)
			Expect(claimsByKey(ctx)["mx-phone-1"].Spec.LeaseRef.UID).To(Equal(newer.UID))

			res := reconcileLeaseOnly(ctx, leaseA)
			Expect(res.RequeueAfter).To(BeNumerically(">", 0))
			Expect(leaseCondition(getLease(ctx, leaseB), jumpstarterdevv1alpha1.LeaseConditionTypeInvalid).Reason).
				To(Equal(jumpstarterdevv1alpha1.LeaseReasonExporterConflict))
			reconcileLeaseOnly(ctx, leaseB)
			Expect(getLease(ctx, leaseB).Status.Ended).To(BeTrue())
			reconcileLeaseOnly(ctx, leaseA)

			Expect(getLease(ctx, leaseA).Status.Ended).To(BeFalse())
			Expect(claimsByKey(ctx)["mx-phone-1"].Spec.LeaseRef.UID).To(Equal(older.UID))
		})
	})

	When("a scalar lease binds", func() {
		It("gets a claim and status.exporterRef, and loses the claim when it ends", func() {
			online(mxExporter("mx-phone-1", "", phone))
			Expect(k8sClient.Create(ctx, scalarRefLease(leaseA, "mx-phone-1"))).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)

			updated := getLease(ctx, leaseA)
			Expect(updated.Status.ExporterRef.Name).To(Equal("mx-phone-1"))
			Expect(updated.Status.Members).To(BeEmpty())
			Expect(metav1.GetControllerOf(updated).Name).To(Equal("mx-phone-1"))
			claims := claimsByKey(ctx)
			Expect(claims).To(HaveLen(1))
			Expect(claims["mx-phone-1"].Spec.LeaseRef.Attempt).To(Equal(updated.Status.BindAttempt))

			updated.Spec.Release = true
			Expect(k8sClient.Update(ctx, updated)).To(Succeed())
			reconcileLeaseOnly(ctx, leaseA)
			Expect(claimsByKey(ctx)).To(BeEmpty())
			err := k8sClient.Get(ctx, client.ObjectKeyFromObject(ptrTo(claims["mx-phone-1"])),
				&jumpstarterdevv1alpha1.ExporterClaim{})
			Expect(k8serrors.IsNotFound(err)).To(BeTrue())
		})
	})
})

func ptrTo[T any](v T) *T {
	return &v
}

var _ = Describe("lease acquisition metric for member-form leases", func() {
	It("counts the bind of the members once as a success", func() {
		lease := memberLease("metric", selectorMember("phone", map[string]string{"mx": "phone"}))
		_, recorded := leaseAcquisitionResult(false, false, lease)
		Expect(recorded).To(BeFalse())

		lease.Status.Members = []jumpstarterdevv1alpha1.LeaseMemberStatus{{
			Name:        "phone",
			State:       jumpstarterdevv1alpha1.LeaseMemberStateBound,
			ExporterRef: &corev1.LocalObjectReference{Name: "mx-phone-1"},
		}}
		result, recorded := leaseAcquisitionResult(false, false, lease)
		Expect(recorded).To(BeTrue())
		Expect(result).To(Equal("success"))
		_, recorded = leaseAcquisitionResult(true, false, lease)
		Expect(recorded).To(BeFalse())
	})
})
