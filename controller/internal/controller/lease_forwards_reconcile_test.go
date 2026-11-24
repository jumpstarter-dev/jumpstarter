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
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/event"
)

// The forwards of a lease in the lease reconciler (JEP-0016): the validation
// after the bind, status.forwards, the ForwardsReady, Ready and Degraded
// conditions, the reports of the ends and the report time limit.
var _ = Describe("Lease forwards", func() {
	const leaseName = "fwd-lease"
	const (
		headunitExporter = "fwd-headunit"
		peerExporter     = "fwd-peer"
		loggerExporter   = "fwd-logger"
	)
	headunitLabel := map[string]string{"fwd": "headunit"}
	peerLabel := map[string]string{"fwd": "peer"}
	loggerLabel := map[string]string{"fwd": "logger"}
	var ctx context.Context
	var exporters []string

	// online creates an exporter that is registered, online and Available, and
	// that reports these ports.
	online := func(name string, labels map[string]string, ports ...jumpstarterdevv1alpha1.DevicePort) {
		createExporters(ctx, mxExporter(name, "", labels))
		setExporterOnlineConditions(ctx, name, metav1.ConditionTrue)
		exporter := getExporter(ctx, name)
		exporter.Status.Devices = []jumpstarterdevv1alpha1.Device{{Uuid: name, Ports: ports}}
		Expect(k8sClient.Status().Update(ctx, exporter)).To(Succeed())
		exporters = append(exporters, name)
	}
	// bind creates the lease, lets the controller bind it, and completes the
	// beforeLease hooks of its exporters.
	bind := func(lease *jumpstarterdevv1alpha1.Lease) {
		Expect(k8sClient.Create(ctx, lease)).To(Succeed())
		reconcileLeaseOnly(ctx, leaseName)
		for _, name := range exporters {
			setExporterStatusValue(ctx, name, jumpstarterdevv1alpha1.ExporterStatusLeaseReady)
		}
		reconcileLeaseOnly(ctx, leaseName)
	}
	setPorts := func(name string, ports ...jumpstarterdevv1alpha1.DevicePort) {
		exporter := getExporter(ctx, name)
		exporter.Status.Devices = []jumpstarterdevv1alpha1.Device{{Uuid: name, Ports: ports}}
		Expect(k8sClient.Status().Update(ctx, exporter)).To(Succeed())
	}
	// btLease is a lease with the forward bt from headunit.rootcanal
	// (provides) to peer.controller (requires).
	btLease := func(more ...jumpstarterdevv1alpha1.LeaseForward) *jumpstarterdevv1alpha1.Lease {
		lease := memberLease(leaseName, selectorMember("headunit", headunitLabel), selectorMember("peer", peerLabel))
		lease.Spec.Forwards = append([]jumpstarterdevv1alpha1.LeaseForward{
			forward("bt", "headunit", "rootcanal", "peer", "controller"),
		}, more...)
		return lease
	}
	twoExporters := func() {
		online(headunitExporter, headunitLabel, devicePort("rootcanal", provides))
		online(peerExporter, peerLabel, devicePort("controller", requires))
	}
	// setup writes the time of a setup instruction of an end, as Listen does.
	setup := func(forwardName, member string, at time.Time) {
		lease := getLease(ctx, leaseName)
		lease.EnsureForwardEnd(forwardName, member).RecordSetup(at)
		Expect(k8sClient.Status().Update(ctx, lease)).To(Succeed())
	}
	// report writes the report of an end to the lease status, as
	// ReportForward does, after a setup. Each report has a larger number.
	var sequence uint64
	report := func(forwardName, member string, state jumpstarterdevv1alpha1.LeaseForwardEndState, message string) {
		lease := getLease(ctx, leaseName)
		end := lease.EnsureForwardEnd(forwardName, member)
		if end.SetupTime == nil {
			end.RecordSetup(time.Now().Add(-time.Second))
		}
		sequence++
		_, accepted := end.RecordReport(state, message, sequence, time.Now())
		Expect(accepted).To(BeTrue())
		Expect(k8sClient.Status().Update(ctx, lease)).To(Succeed())
	}
	endStates := func(name string) map[string]jumpstarterdevv1alpha1.LeaseForwardEndState {
		states := map[string]jumpstarterdevv1alpha1.LeaseForwardEndState{}
		for _, end := range getLease(ctx, leaseName).ForwardStatus(name).Ends {
			states[end.Member] = end.State
		}
		return states
	}
	condition := func(t jumpstarterdevv1alpha1.LeaseConditionType) *metav1.Condition {
		return leaseCondition(getLease(ctx, leaseName), t)
	}
	forwardStatus := func(name string) *jumpstarterdevv1alpha1.LeaseForwardStatus {
		status := getLease(ctx, leaseName).ForwardStatus(name)
		Expect(status).NotTo(BeNil(), "forward %s has no status", name)
		return status
	}

	BeforeEach(func() {
		ctx = context.Background()
		exporters = nil
		sequence = 0
		Expect(k8sClient.DeleteAllOf(ctx, &jumpstarterdevv1alpha1.ExporterClaim{},
			client.InNamespace("default"))).To(Succeed())
	})
	AfterEach(func() {
		for _, name := range exporters {
			_ = k8sClient.Delete(ctx, &jumpstarterdevv1alpha1.Exporter{
				ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "default"}})
			_ = k8sClient.Delete(ctx, &corev1.Secret{
				ObjectMeta: metav1.ObjectMeta{Name: name + "-exporter", Namespace: "default"}})
		}
		deleteLeases(ctx, leaseName)
		Expect(k8sClient.DeleteAllOf(ctx, &jumpstarterdevv1alpha1.ExporterClaim{},
			client.InNamespace("default"))).To(Succeed())
	})

	It("keeps the forwards Pending while the lease waits for its members", func() {
		Expect(k8sClient.Create(ctx, btLease())).To(Succeed())
		reconcileLeaseOnly(ctx, leaseName)

		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStatePending))
		ready := condition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady)
		Expect(ready).NotTo(BeNil())
		Expect(ready.Status).To(Equal(metav1.ConditionFalse))
		Expect(ready.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardsPending))
	})

	It("follows the reports of the ends: Pending, Listening, Connected, Failed, and back", func() {
		twoExporters()
		bind(btLease())

		// The bind validated the ports. No end reported.
		lease := getLease(ctx, leaseName)
		Expect(memberExporter(lease, "headunit")).To(Equal(headunitExporter))
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStatePending))
		Expect(forwardStatus("bt").Message).To(ContainSubstring("`headunit`, `peer`"))
		forwards := condition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady)
		Expect(forwards.Status).To(Equal(metav1.ConditionFalse))
		Expect(forwards.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardsPending))
		ready := condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady)
		Expect(ready.Status).To(Equal(metav1.ConditionFalse))
		Expect(ready.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardsPending))
		Expect(ready.Message).To(ContainSubstring("`bt`"))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded)).To(BeNil())

		// One end is ready: the forward waits for the other.
		report("bt", "headunit", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		reconcileLeaseOnly(ctx, leaseName)
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStatePending))
		Expect(forwardStatus("bt").Message).To(ContainSubstring("`peer`"))

		// Each bound end is ready.
		report("bt", "peer", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		reconcileLeaseOnly(ctx, leaseName)
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateListening))
		Expect(forwardStatus("bt").Message).To(BeEmpty())
		forwards = condition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady)
		Expect(forwards.Status).To(Equal(metav1.ConditionTrue))
		Expect(forwards.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardsReady))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady).Status).To(Equal(metav1.ConditionTrue))

		// A reconcile that changes nothing writes nothing.
		version := getLease(ctx, leaseName).ResourceVersion
		reconcileLeaseOnly(ctx, leaseName)
		Expect(getLease(ctx, leaseName).ResourceVersion).To(Equal(version))

		// One end has a connection.
		report("bt", "peer", jumpstarterdevv1alpha1.LeaseForwardEndStateConnected, "")
		reconcileLeaseOnly(ctx, leaseName)
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateConnected))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady).Status).To(Equal(metav1.ConditionTrue))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady).Status).To(Equal(metav1.ConditionTrue))

		// One failure report fails the forward and degrades the lease. The
		// lease does not end, and its members stay bound.
		report("bt", "peer", jumpstarterdevv1alpha1.LeaseForwardEndStateFailed, "address already in use")
		reconcileLeaseOnly(ctx, leaseName)
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateFailed))
		Expect(forwardStatus("bt").Message).To(ContainSubstring("role `peer`: address already in use"))
		forwards = condition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady)
		Expect(forwards.Status).To(Equal(metav1.ConditionFalse))
		Expect(forwards.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardFailed))
		degraded := condition(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded)
		Expect(degraded).NotTo(BeNil())
		Expect(degraded.Status).To(Equal(metav1.ConditionTrue))
		Expect(degraded.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardFailed))
		Expect(degraded.Message).To(ContainSubstring("`bt`"))
		ready = condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady)
		Expect(ready.Status).To(Equal(metav1.ConditionFalse))
		Expect(ready.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardFailed))
		lease = getLease(ctx, leaseName)
		Expect(lease.Status.Ended).To(BeFalse())
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeInvalid)).To(BeNil())
		Expect(lease.MemberStatus("peer").State).To(Equal(jumpstarterdevv1alpha1.LeaseMemberStateBound))
		Expect(lease.MemberStatus("headunit").State).To(Equal(jumpstarterdevv1alpha1.LeaseMemberStateBound))

		// A later report removes the failure.
		report("bt", "peer", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		reconcileLeaseOnly(ctx, leaseName)
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateListening))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded)).To(BeNil())
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady).Status).To(Equal(metav1.ConditionTrue))
	})

	It("fails a forward whose port is not on the bound exporter, and keeps the lease", func() {
		online(headunitExporter, headunitLabel, devicePort("rootcanal", provides))
		online(peerExporter, peerLabel)
		bind(btLease())

		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateFailed))
		Expect(forwardStatus("bt").Message).To(ContainSubstring("reports no port `controller`"))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady).Reason).
			To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardFailed))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded).Status).To(Equal(metav1.ConditionTrue))
		ready := condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady)
		Expect(ready.Status).To(Equal(metav1.ConditionFalse))
		Expect(ready.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardFailed))
		lease := getLease(ctx, leaseName)
		Expect(lease.Status.Ended).To(BeFalse())
		Expect(lease.BoundExporterNames()).To(ConsistOf(headunitExporter, peerExporter))

		// The exporter registers again with the port: the forward is valid.
		setPorts(peerExporter, devicePort("controller", requires))
		reconcileLeaseOnly(ctx, leaseName)
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStatePending))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded)).To(BeNil())
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady).Reason).
			To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardsPending))
	})

	It("fails two provides ports and ports that are not socket and stream", func() {
		wired := devicePort("can", provides)
		wired.Attachment = jumpstarterdevv1alpha1.PortAttachmentWired
		online(headunitExporter, headunitLabel, devicePort("rootcanal", provides), wired)
		online(peerExporter, peerLabel, devicePort("service", provides), devicePort("can-remote", requires))
		lease := memberLease(leaseName, selectorMember("headunit", headunitLabel), selectorMember("peer", peerLabel))
		lease.Spec.Forwards = []jumpstarterdevv1alpha1.LeaseForward{
			forward("both", "headunit", "rootcanal", "peer", "service"),
			forward("bus", "headunit", "can", "peer", "can-remote"),
		}
		bind(lease)

		Expect(forwardStatus("both").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateFailed))
		Expect(forwardStatus("both").Message).To(ContainSubstring("More than one endpoint is a `provides` port"))
		Expect(forwardStatus("bus").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateFailed))
		Expect(forwardStatus("bus").Message).To(ContainSubstring("attachment `wired`"))
		degraded := condition(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded)
		Expect(degraded.Message).To(ContainSubstring("`both`"))
		Expect(degraded.Message).To(ContainSubstring("`bus`"))
		Expect(getLease(ctx, leaseName).Status.Ended).To(BeFalse())
	})

	It("disables a forward with an Omitted member, and the lease becomes Ready", func() {
		twoExporters()
		// No exporter has the label of the optional logger: it is Omitted.
		lease := memberLease(leaseName, selectorMember("headunit", headunitLabel), selectorMember("peer", peerLabel))
		logger := selectorMember("logger", loggerLabel)
		logger.Optional = true
		lease.Spec.Members = append(lease.Spec.Members, logger)
		lease.Spec.Forwards = []jumpstarterdevv1alpha1.LeaseForward{
			forward("bt", "headunit", "rootcanal", "peer", "controller", "logger", "controller"),
			forward("log", "peer", "controller", "logger", "console"),
		}
		bind(lease)

		Expect(getLease(ctx, leaseName).MemberStatus("logger").State).To(Equal(jumpstarterdevv1alpha1.LeaseMemberStateOmitted))
		// log lost its only other end: Disabled, with the role in the message.
		Expect(forwardStatus("log").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateDisabled))
		Expect(forwardStatus("log").Message).To(ContainSubstring("`logger`"))
		// bt serves its other endpoints. The Disabled forward does not count
		// for the port that the two forwards name.
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStatePending))

		report("bt", "headunit", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		report("bt", "peer", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		reconcileLeaseOnly(ctx, leaseName)
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateListening))
		Expect(forwardStatus("log").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateDisabled))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady).Status).To(Equal(metav1.ConditionTrue))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady).Status).To(Equal(metav1.ConditionTrue))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded)).To(BeNil())
	})

	It("is Ready with only Disabled forwards", func() {
		online(headunitExporter, headunitLabel, devicePort("rootcanal", provides))
		lease := memberLease(leaseName, selectorMember("headunit", headunitLabel))
		peer := selectorMember("peer", peerLabel)
		peer.Optional = true
		lease.Spec.Members = append(lease.Spec.Members, peer)
		lease.Spec.Forwards = []jumpstarterdevv1alpha1.LeaseForward{forward("bt", "headunit", "rootcanal", "peer", "controller")}
		bind(lease)

		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateDisabled))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady).Status).To(Equal(metav1.ConditionTrue))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady).Status).To(Equal(metav1.ConditionTrue))
	})

	It("waits for the beforeLease hooks before the forwards decide Ready", func() {
		twoExporters()
		Expect(k8sClient.Create(ctx, btLease())).To(Succeed())
		reconcileLeaseOnly(ctx, leaseName)
		setExporterStatusValue(ctx, headunitExporter, jumpstarterdevv1alpha1.ExporterStatusLeaseReady)
		setExporterStatusValue(ctx, peerExporter, jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHook)
		reconcileLeaseOnly(ctx, leaseName)
		ready := condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady)
		Expect(ready.Status).To(Equal(metav1.ConditionFalse))
		Expect(ready.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonBeforeLeaseHook))
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStatePending))

		setExporterStatusValue(ctx, peerExporter, jumpstarterdevv1alpha1.ExporterStatusLeaseReady)
		reconcileLeaseOnly(ctx, leaseName)
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady).Reason).
			To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardsPending))

		// The hooks stay complete while the lease waits for its forwards.
		setExporterStatusValue(ctx, peerExporter, jumpstarterdevv1alpha1.ExporterStatusAfterLeaseHook)
		reconcileLeaseOnly(ctx, leaseName)
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady).Reason).
			To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardsPending))
	})

	It("fails a forward whose end sends no report in the time limit, and a later report removes the failure", func() {
		twoExporters()
		bind(btLease())

		// The two ends got their setup now. One reports.
		report("bt", "headunit", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		setup("bt", "peer", time.Now())
		Expect(endStates("bt")).To(HaveKeyWithValue("peer", jumpstarterdevv1alpha1.LeaseForwardEndStatePending))
		result := reconcileLeaseOnly(ctx, leaseName)
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStatePending))
		// The reconciler looks again when the time limit passes.
		Expect(result.RequeueAfter).To(BeNumerically(">", 50*time.Second))
		Expect(result.RequeueAfter).To(BeNumerically("<=", DefaultForwardReportTimeout))

		// With a short limit, the time limit passed.
		short := newLeaseReconciler(k8sClient)
		short.ForwardReportTimeout = time.Millisecond
		time.Sleep(5 * time.Millisecond)
		reconcileLeaseWith(ctx, short, leaseName)
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateFailed))
		Expect(forwardStatus("bt").Message).To(ContainSubstring("Role `peer` sent no report"))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded).Status).To(Equal(metav1.ConditionTrue))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady).Reason).
			To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardFailed))

		// A later READY report from that endpoint removes the failure.
		report("bt", "peer", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		reconcileLeaseWith(ctx, short, leaseName)
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateListening))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded)).To(BeNil())
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady).Status).To(Equal(metav1.ConditionTrue))
	})

	It("fails a forward whose necessary member is Lost, and keeps a forward that has other requires endpoints", func() {
		online(headunitExporter, headunitLabel, devicePort("rootcanal", provides), devicePort("console", provides))
		online(peerExporter, peerLabel, devicePort("controller", requires), devicePort("console", requires))
		online(loggerExporter, loggerLabel, devicePort("console", requires))
		lease := memberLease(leaseName, selectorMember("headunit", headunitLabel),
			selectorMember("peer", peerLabel), selectorMember("logger", loggerLabel))
		lease.Spec.Forwards = []jumpstarterdevv1alpha1.LeaseForward{
			forward("bt", "headunit", "rootcanal", "peer", "controller"),
			forward("console", "headunit", "console", "peer", "console", "logger", "console"),
		}
		bind(lease)
		for _, end := range [][2]string{{"bt", "headunit"}, {"bt", "peer"},
			{"console", "headunit"}, {"console", "peer"}, {"console", "logger"}} {
			report(end[0], end[1], jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		}
		reconcileLeaseOnly(ctx, leaseName)
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady).Status).To(Equal(metav1.ConditionTrue))

		setExporterOnlineConditions(ctx, peerExporter, metav1.ConditionFalse)
		reconcileLeaseOnly(ctx, leaseName)

		updated := getLease(ctx, leaseName)
		Expect(updated.MemberStatus("peer").State).To(Equal(jumpstarterdevv1alpha1.LeaseMemberStateLost))
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateFailed))
		Expect(forwardStatus("bt").Message).To(ContainSubstring("Role `peer` is Lost"))
		// The lost member is one of many requires endpoints of console.
		Expect(forwardStatus("console").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateListening))
		degraded := condition(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded)
		Expect(degraded.Status).To(Equal(metav1.ConditionTrue))
		Expect(degraded.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseReasonMemberLost))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady).Reason).
			To(Equal(jumpstarterdevv1alpha1.LeaseReasonMemberLost))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady).Reason).
			To(Equal(jumpstarterdevv1alpha1.LeaseReasonForwardFailed))
		Expect(updated.Status.Ended).To(BeFalse())
	})

	It("removes the states of the ends when the lease ends", func() {
		twoExporters()
		bind(btLease())
		report("bt", "headunit", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		Expect(getLease(ctx, leaseName).ForwardStatus("bt").Ends).To(HaveLen(2))

		lease := getLease(ctx, leaseName)
		lease.Spec.Release = true
		Expect(k8sClient.Update(ctx, lease)).To(Succeed())
		reconcileLeaseOnly(ctx, leaseName)

		ended := getLease(ctx, leaseName)
		Expect(ended.Status.Ended).To(BeTrue())
		Expect(ended.Status.Forwards).To(HaveLen(1))
		Expect(ended.Status.Forwards[0].Ends).To(BeEmpty())
	})

	It("has a Pending end for each bound endpoint, in the order of the endpoints", func() {
		online(headunitExporter, headunitLabel, devicePort("rootcanal", provides), devicePort("console", provides))
		online(peerExporter, peerLabel, devicePort("controller", requires))
		lease := btLease()
		logger := selectorMember("logger", loggerLabel)
		logger.Optional = true
		lease.Spec.Members = append(lease.Spec.Members, logger)
		lease.Spec.Forwards = []jumpstarterdevv1alpha1.LeaseForward{
			forward("bt", "peer", "controller", "headunit", "rootcanal"),
			forward("console", "headunit", "console", "logger", "console"),
		}
		bind(lease)

		ends := forwardStatus("bt").Ends
		Expect(ends).To(HaveLen(2))
		Expect(ends[0]).To(Equal(jumpstarterdevv1alpha1.LeaseForwardEndStatus{
			Member: "peer", State: jumpstarterdevv1alpha1.LeaseForwardEndStatePending}))
		Expect(ends[1]).To(Equal(jumpstarterdevv1alpha1.LeaseForwardEndStatus{
			Member: "headunit", State: jumpstarterdevv1alpha1.LeaseForwardEndStatePending}))
		// A Disabled forward gets no setup: it has no ends.
		Expect(forwardStatus("console").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateDisabled))
		Expect(forwardStatus("console").Ends).To(BeEmpty())

		// A report that arrives before the lease controller knows the end
		// goes to its place.
		report("bt", "headunit", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		reconcileLeaseOnly(ctx, leaseName)
		ends = forwardStatus("bt").Ends
		Expect(ends).To(HaveLen(2))
		Expect(ends[0].Member).To(Equal("peer"))
		Expect(ends[1].Member).To(Equal("headunit"))
		Expect(ends[1].State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardEndStateReady))
		Expect(ends[1].Sequence).NotTo(BeZero())
		Expect(ends[1].SetupTime).NotTo(BeNil())
		Expect(ends[1].ReportTime).NotTo(BeNil())
	})

	It("keeps the ends and their sequence numbers over a restart of the controller", func() {
		twoExporters()
		bind(btLease())
		report("bt", "headunit", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		report("bt", "peer", jumpstarterdevv1alpha1.LeaseForwardEndStateConnected, "")
		reconcileLeaseOnly(ctx, leaseName)
		before := getLease(ctx, leaseName)
		Expect(before.ForwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateConnected))

		// A new controller has no memory: it has only the lease. Its first
		// reconcile keeps each end as it is, and writes nothing.
		restarted := newLeaseReconciler(k8sClient)
		restarted.StartedAt = time.Now()
		reconcileLeaseWith(ctx, restarted, leaseName)
		after := getLease(ctx, leaseName)
		Expect(after.ResourceVersion).To(Equal(before.ResourceVersion))
		Expect(after.Status.Forwards).To(Equal(before.Status.Forwards))
		Expect(after.ForwardEnd("bt", "headunit").Sequence).To(Equal(uint64(1)))
		Expect(after.ForwardEnd("bt", "peer").Sequence).To(Equal(uint64(2)))
		Expect(after.ForwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateConnected))
	})

	It("does not lose a report that arrives during a reconcile", func() {
		twoExporters()
		bind(btLease())
		report("bt", "headunit", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		reconcileLeaseOnly(ctx, leaseName)
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStatePending))

		// The reconcile reads the lease, and then the report of the other
		// end arrives. The status update of the reconcile has the old
		// resource version: the API server rejects it, and the reconcile is
		// done again. It does not write its old copy of the ends.
		stale := &staleLeaseClient{Client: k8sClient, lease: getLease(ctx, leaseName)}
		report("bt", "peer", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		result := reconcileLeaseWith(ctx, newLeaseReconciler(stale), leaseName)
		Expect(result.Requeue).To(BeTrue()) //nolint:staticcheck
		Expect(endStates("bt")).To(Equal(map[string]jumpstarterdevv1alpha1.LeaseForwardEndState{
			"headunit": jumpstarterdevv1alpha1.LeaseForwardEndStateReady,
			"peer":     jumpstarterdevv1alpha1.LeaseForwardEndStateReady,
		}))
		Expect(getLease(ctx, leaseName).ForwardEnd("bt", "peer").Sequence).To(Equal(uint64(2)))
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStatePending))

		reconcileLeaseOnly(ctx, leaseName)
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateListening))
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady).Status).To(Equal(metav1.ConditionTrue))
	})

	It("removes the end of a member that is Lost", func() {
		twoExporters()
		bind(btLease())
		report("bt", "headunit", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		report("bt", "peer", jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "")
		reconcileLeaseOnly(ctx, leaseName)

		setExporterOnlineConditions(ctx, peerExporter, metav1.ConditionFalse)
		reconcileLeaseOnly(ctx, leaseName)
		Expect(getLease(ctx, leaseName).MemberStatus("peer").State).To(Equal(jumpstarterdevv1alpha1.LeaseMemberStateLost))
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateFailed))
		Expect(endStates("bt")).To(Equal(map[string]jumpstarterdevv1alpha1.LeaseForwardEndState{
			"headunit": jumpstarterdevv1alpha1.LeaseForwardEndStateReady,
		}))
	})

	It("ignores the annotation of an earlier build, and deletes it", func() {
		twoExporters()
		bind(btLease())
		lease := getLease(ctx, leaseName)
		lease.Annotations = map[string]string{
			jumpstarterdevv1alpha1.LegacyLeaseForwardEndsAnnotation: `{"bt/headunit":{"state":"Ready","sequence":5},"bt/peer":{"state":"Ready","sequence":6}}`,
		}
		Expect(k8sClient.Update(ctx, lease)).To(Succeed())

		reconcileLeaseOnly(ctx, leaseName)
		updated := getLease(ctx, leaseName)
		Expect(updated.Annotations).NotTo(HaveKey(jumpstarterdevv1alpha1.LegacyLeaseForwardEndsAnnotation))
		Expect(forwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStatePending))
		Expect(endStates("bt")).To(Equal(map[string]jumpstarterdevv1alpha1.LeaseForwardEndState{
			"headunit": jumpstarterdevv1alpha1.LeaseForwardEndStatePending,
			"peer":     jumpstarterdevv1alpha1.LeaseForwardEndStatePending,
		}))
	})

	It("reconciles a lease after a write of its status only", func() {
		// A report and a setup change only the status of the lease. The
		// lease controller has no predicate on the generation: the change
		// makes it reconcile.
		oldLease := btLease()
		newLease := btLease()
		newLease.EnsureForwardEnd("bt", "peer")
		Expect(skipEndedPredicate().Update(event.UpdateEvent{ObjectOld: oldLease, ObjectNew: newLease})).To(BeTrue())
	})

	It("gives a lease without forwards no forward status and no ForwardsReady condition", func() {
		twoExporters()
		lease := memberLease(leaseName, selectorMember("headunit", headunitLabel), selectorMember("peer", peerLabel))
		bind(lease)

		updated := getLease(ctx, leaseName)
		Expect(updated.Status.Forwards).To(BeEmpty())
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady)).To(BeNil())
		Expect(condition(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded)).To(BeNil())
		ready := condition(jumpstarterdevv1alpha1.LeaseConditionTypeReady)
		Expect(ready.Status).To(Equal(metav1.ConditionTrue))
		Expect(ready.Reason).To(Equal("Ready"))
	})

	It("reconciles the leases of an exporter whose ports change", func() {
		oldExporter := portExporter("fwd-watch", devicePort("rootcanal", provides))
		same := portExporter("fwd-watch", devicePort("rootcanal", provides))
		changed := portExporter("fwd-watch", devicePort("rootcanal", provides), devicePort("console", provides))
		none := portExporter("fwd-watch")
		update := exporterChangedForPendingLeases().UpdateFunc
		Expect(update(event.UpdateEvent{ObjectOld: oldExporter, ObjectNew: same})).To(BeFalse())
		Expect(update(event.UpdateEvent{ObjectOld: oldExporter, ObjectNew: changed})).To(BeTrue())
		Expect(update(event.UpdateEvent{ObjectOld: oldExporter, ObjectNew: none})).To(BeTrue())
	})
})
