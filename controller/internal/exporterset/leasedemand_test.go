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

package exporterset

import (
	"context"
	"testing"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
)

var poolLabels = map[string]string{"exporterset": "demo-set"}

func selectorMember(name string, selectorLabels map[string]string) jumpstarterdevv1alpha1.LeaseMember {
	return jumpstarterdevv1alpha1.LeaseMember{
		Name:     name,
		Selector: &metav1.LabelSelector{MatchLabels: selectorLabels},
	}
}

func pinnedMember(name, exporter string) jumpstarterdevv1alpha1.LeaseMember {
	return jumpstarterdevv1alpha1.LeaseMember{
		Name:        name,
		ExporterRef: &corev1.LocalObjectReference{Name: exporter},
	}
}

func makePendingMemberLease(name string, members ...jumpstarterdevv1alpha1.LeaseMember) *jumpstarterdevv1alpha1.Lease {
	lease := makePendingLease(name, nil)
	lease.Spec.Members = members
	return lease
}

func memberStatus(
	name string, state jumpstarterdevv1alpha1.LeaseMemberState, exporter string,
) jumpstarterdevv1alpha1.LeaseMemberStatus {
	status := jumpstarterdevv1alpha1.LeaseMemberStatus{Name: name, State: state}
	if exporter != "" {
		status.ExporterRef = &corev1.LocalObjectReference{Name: exporter}
	}
	return status
}

// makeBoundMemberLease is a member-form lease after its bind: not Pending,
// with the given member statuses.
func makeBoundMemberLease(
	name string,
	members []jumpstarterdevv1alpha1.LeaseMember,
	statuses ...jumpstarterdevv1alpha1.LeaseMemberStatus,
) *jumpstarterdevv1alpha1.Lease {
	lease := makePendingMemberLease(name, members...)
	lease.Status.Conditions = nil
	lease.Status.Members = statuses
	return lease
}

func demandSet(maxReplicas int32) *virtualtargetv1alpha1.ExporterSet {
	return makeExporterSet(func(es *virtualtargetv1alpha1.ExporterSet) {
		es.Spec.MinReplicas = 0
		es.Spec.MinAvailableReplicas = 0
		es.Spec.MaxReplicas = maxReplicas
	})
}

// scaleDownDueSet is a set whose surplus cooldown already elapsed, so the
// next reconcile disables an exporter unless something holds it back.
func scaleDownDueSet() *virtualtargetv1alpha1.ExporterSet {
	cooldown := 1 * time.Second
	return makeExporterSet(func(es *virtualtargetv1alpha1.ExporterSet) {
		es.Spec.MinReplicas = 0
		es.Spec.MinAvailableReplicas = 0
		es.Spec.ScaleDownCooldown = &metav1.Duration{Duration: cooldown}
		es.Annotations = map[string]string{
			annotationSurplusSince: time.Now().Add(-2 * cooldown).UTC().Format(time.RFC3339),
		}
	})
}

func countDisabled(exporters []jumpstarterdevv1alpha1.Exporter) int {
	n := 0
	for i := range exporters {
		if !exporters[i].IsEnabled() {
			n++
		}
	}
	return n
}

func TestLeaseDemand_countsEachApplicableMember(t *testing.T) {
	excluded := selectorMember("spare", poolLabels)
	excluded.Optional = true
	excluded.Excluded = true

	lease := makePendingMemberLease("rig",
		selectorMember("phone-a", poolLabels),
		selectorMember("phone-b", poolLabels),
		selectorMember("other-pool", map[string]string{"exporterset": "other"}),
		pinnedMember("bench", "bench-1"),
		excluded,
	)

	if got := leaseDemand(lease, labels.Set(poolLabels)); got != 2 {
		t.Fatalf("demand = %d, want 2 (two members select the set)", got)
	}
}

func TestLeaseDemand_ignoresBoundEndedAndNotPendingLeases(t *testing.T) {
	members := []jumpstarterdevv1alpha1.LeaseMember{
		selectorMember("phone-a", poolLabels),
		selectorMember("phone-b", poolLabels),
	}

	bound := makePendingMemberLease("bound", members...)
	bound.Status.Members = []jumpstarterdevv1alpha1.LeaseMemberStatus{
		memberStatus("phone-a", jumpstarterdevv1alpha1.LeaseMemberStateBound, "exp-1"),
		memberStatus("phone-b", jumpstarterdevv1alpha1.LeaseMemberStateLost, "exp-2"),
	}

	ended := makePendingMemberLease("ended", members...)
	ended.Status.Ended = true

	notPending := makePendingMemberLease("unsatisfiable", members...)
	notPending.Status.Conditions = nil

	waiting := makePendingMemberLease("waiting", members...)
	waiting.Status.Members = []jumpstarterdevv1alpha1.LeaseMemberStatus{
		memberStatus("phone-a", jumpstarterdevv1alpha1.LeaseMemberStatePending, ""),
		memberStatus("phone-b", jumpstarterdevv1alpha1.LeaseMemberStatePending, ""),
	}

	for _, tc := range []struct {
		lease *jumpstarterdevv1alpha1.Lease
		want  int32
	}{
		{bound, 0},
		{ended, 0},
		{notPending, 0},
		{waiting, 2},
	} {
		if got := leaseDemand(tc.lease, labels.Set(poolLabels)); got != tc.want {
			t.Errorf("lease %s: demand = %d, want %d", tc.lease.Name, got, tc.want)
		}
	}
}

func TestReconcile_memberDemand_twoMembersScaleOneExporterSetToTwo(t *testing.T) {
	lease := makePendingMemberLease("rig",
		selectorMember("phone-a", poolLabels),
		selectorMember("phone-b", poolLabels),
	)

	r, c := newReconciler(t, demandSet(5), makeVTC(), lease, makeExporter("exp-1", true, false, true))

	reconcileOnce(t, r)
	if got := len(listExporters(t, c)); got != 2 {
		t.Fatalf("expected 2 Exporters (two members, one free exporter), got %d", got)
	}

	// The new exporter is capacity while it boots: no third one.
	reconcileOnce(t, r)
	if got := len(listExporters(t, c)); got != 2 {
		t.Fatalf("expected 2 Exporters after the second reconcile, got %d", got)
	}
}

func TestReconcile_memberDemand_respectsMaxReplicas(t *testing.T) {
	lease := makePendingMemberLease("rig",
		selectorMember("phone-a", poolLabels),
		selectorMember("phone-b", poolLabels),
	)

	r, c := newReconciler(t, demandSet(1), makeVTC(), lease, makeExporter("exp-1", true, false, true))

	reconcileOnce(t, r)
	if got := len(listExporters(t, c)); got != 1 {
		t.Fatalf("expected 1 Exporter (maxReplicas=1), got %d", got)
	}
}

func TestReconcile_memberDemand_ignoresPinnedAndExcludedMembers(t *testing.T) {
	excluded := selectorMember("spare", poolLabels)
	excluded.Optional = true
	excluded.Excluded = true

	lease := makePendingMemberLease("rig", pinnedMember("bench", "bench-1"), excluded)

	r, c := newReconciler(t, demandSet(5), makeVTC(), lease)

	reconcileOnce(t, r)
	if got := len(listExporters(t, c)); got != 0 {
		t.Fatalf("expected 0 Exporters (no applicable member), got %d", got)
	}
}

func TestReconcile_memberDemand_ignoresBoundLease(t *testing.T) {
	lease := makeBoundMemberLease("rig",
		[]jumpstarterdevv1alpha1.LeaseMember{
			selectorMember("phone-a", poolLabels),
			selectorMember("phone-b", poolLabels),
		},
		memberStatus("phone-a", jumpstarterdevv1alpha1.LeaseMemberStateBound, "exp-1"),
		memberStatus("phone-b", jumpstarterdevv1alpha1.LeaseMemberStateBound, "exp-2"),
	)
	// A bound lease that is still marked Pending is no demand either.
	lease.Status.Conditions = makePendingLease("x", nil).Status.Conditions

	r, c := newReconciler(t, demandSet(5), makeVTC(), lease,
		makeExporter("exp-1", true, true, true),
		makeExporter("exp-2", true, true, true),
	)

	reconcileOnce(t, r)
	if got := len(listExporters(t, c)); got != 2 {
		t.Fatalf("expected 2 Exporters (bound lease is no demand), got %d", got)
	}
}

func TestReconcile_scaleDown_keepsExporterForWaitingMember(t *testing.T) {
	// The lease waits for the pinned bench; the free exporter of the set is
	// what its other member will bind.
	lease := makePendingMemberLease("rig",
		selectorMember("phone", poolLabels),
		pinnedMember("bench", "bench-1"),
	)

	r, c := newReconciler(t, scaleDownDueSet(), makeVTC(), lease, makeExporter("exp-1", true, false, true))

	reconcileOnce(t, r)

	exporters := listExporters(t, c)
	if len(exporters) != 1 || countDisabled(exporters) != 0 {
		t.Fatalf("expected the exporter kept enabled for the waiting member, got %d exporters, %d disabled",
			len(exporters), countDisabled(exporters))
	}
	if _, ok := getExporterSet(t, c).Annotations[annotationSurplusSince]; ok {
		t.Fatal("expected no surplus annotation while a member can use the exporter")
	}
}

func TestReconcile_scaleDown_removesOnlyTheExportersNoMemberNeeds(t *testing.T) {
	lease := makePendingMemberLease("rig",
		selectorMember("phone", poolLabels),
		pinnedMember("bench", "bench-1"),
	)

	r, c := newReconciler(t, scaleDownDueSet(), makeVTC(), lease,
		makeExporter("exp-1", true, false, true),
		makeExporter("exp-2", true, false, true),
	)

	reconcileOnce(t, r)

	if got := countDisabled(listExporters(t, c)); got != 1 {
		t.Fatalf("expected 1 disabled Exporter (two free, one member), got %d", got)
	}
}

func TestReconcile_scaleDown_withoutMemberDemandStillDisables(t *testing.T) {
	// Same pool, but the lease is bound elsewhere: nothing holds the exporter.
	lease := makeBoundMemberLease("rig",
		[]jumpstarterdevv1alpha1.LeaseMember{selectorMember("phone", poolLabels)},
		memberStatus("phone", jumpstarterdevv1alpha1.LeaseMemberStateBound, "exp-other"),
	)

	r, c := newReconciler(t, scaleDownDueSet(), makeVTC(), lease, makeExporter("exp-1", true, false, true))

	reconcileOnce(t, r)

	if got := countDisabled(listExporters(t, c)); got != 1 {
		t.Fatalf("expected 1 disabled Exporter, got %d", got)
	}
}

func TestFindExporterSetsForLease_memberSelectors(t *testing.T) {
	other := makeExporterSet(func(es *virtualtargetv1alpha1.ExporterSet) {
		es.Name = "other-set"
		es.UID = "es-uid-other"
		es.Spec.Template.Metadata.Labels = map[string]string{"exporterset": "other-set"}
	})
	unrelated := makeExporterSet(func(es *virtualtargetv1alpha1.ExporterSet) {
		es.Name = "unrelated-set"
		es.UID = "es-uid-unrelated"
		es.Spec.Template.Metadata.Labels = map[string]string{"exporterset": "unrelated-set"}
	})
	r, _ := newReconciler(t, makeExporterSet(), other, unrelated, makeVTC())

	excluded := selectorMember("spare", map[string]string{"exporterset": "unrelated-set"})
	excluded.Optional = true
	excluded.Excluded = true

	lease := makePendingMemberLease("rig",
		selectorMember("phone", poolLabels),
		selectorMember("tablet", map[string]string{"exporterset": "other-set"}),
		pinnedMember("bench", "bench-1"),
		excluded,
	)

	names := map[string]bool{}
	for _, req := range r.findExporterSetsForLease(context.Background(), lease) {
		names[req.Name] = true
	}
	if len(names) != 2 || !names["demo-set"] || !names["other-set"] {
		t.Fatalf("got requests for %v, want demo-set and other-set", names)
	}

	// The end of a lease only matters to the sets when it frees the exporter
	// of a Lost member.
	lease.Status.Ended = true
	if requests := r.findExporterSetsForLease(context.Background(), lease); len(requests) != 0 {
		t.Fatalf("got %d requests for an ended lease, want 0", len(requests))
	}
	lease.Status.Members = []jumpstarterdevv1alpha1.LeaseMemberStatus{
		memberStatus("phone", jumpstarterdevv1alpha1.LeaseMemberStateLost, "exp-1"),
	}
	if requests := r.findExporterSetsForLease(context.Background(), lease); len(requests) != 2 {
		t.Fatalf("got %d requests for an ended lease with a Lost member, want 2", len(requests))
	}
}

func TestFindExporterSetsForLease_ignoresLeaseOfPinnedMembers(t *testing.T) {
	r, _ := newReconciler(t, makeExporterSet(), makeVTC())

	lease := makePendingMemberLease("rig", pinnedMember("bench", "bench-1"))
	if requests := r.findExporterSetsForLease(context.Background(), lease); len(requests) != 0 {
		t.Fatalf("got %d requests, want 0", len(requests))
	}
}

// An exporter that the lease shows as a Bound member is in use even while
// the Exporter does not show status.leaseRef yet.
func TestReconcile_boundMember_countsAsLeased(t *testing.T) {
	lease := makeBoundMemberLease("rig",
		[]jumpstarterdevv1alpha1.LeaseMember{selectorMember("phone", poolLabels)},
		memberStatus("phone", jumpstarterdevv1alpha1.LeaseMemberStateBound, "exp-1"),
	)

	updated := reconcileAndGet(t, demandSet(5), makeVTC(), lease,
		makeExporter("exp-1", true, false, true),
		makeExporter("exp-2", true, true, true),
	)

	assertInt32(t, "LeasedReplicas", updated.Status.LeasedReplicas, 2)
	assertInt32(t, "AvailableReplicas", updated.Status.AvailableReplicas, 0)
	assertInt32(t, "ExportersActive", updated.Status.ExportersActive, 2)
	assertInt32(t, "ExportersIdle", updated.Status.ExportersIdle, 0)
}

func TestReconcile_boundMember_notScaledDown(t *testing.T) {
	lease := makeBoundMemberLease("rig",
		[]jumpstarterdevv1alpha1.LeaseMember{selectorMember("phone", poolLabels)},
		memberStatus("phone", jumpstarterdevv1alpha1.LeaseMemberStateBound, "exp-1"),
	)

	r, c := newReconciler(t, scaleDownDueSet(), makeVTC(), lease, makeExporter("exp-1", true, false, true))

	reconcileOnce(t, r)

	if got := countDisabled(listExporters(t, c)); got != 0 {
		t.Fatalf("expected the bound exporter kept enabled, got %d disabled", got)
	}
}

func TestReconcile_boundMember_disabledExporterNotDeleted(t *testing.T) {
	lease := makeBoundMemberLease("rig",
		[]jumpstarterdevv1alpha1.LeaseMember{selectorMember("phone", poolLabels)},
		memberStatus("phone", jumpstarterdevv1alpha1.LeaseMemberStateBound, "exp-1"),
	)

	r, c := newReconciler(t, demandSet(5), makeVTC(), lease, makeExporter("exp-1", true, false, false))

	reconcileOnce(t, r)

	if got := len(listExporters(t, c)); got != 1 {
		t.Fatalf("expected the disabled bound exporter kept, got %d", got)
	}
}

func TestReconcile_exitAndReplace_keepsTerminalBoundMember(t *testing.T) {
	es := demandSet(5)
	es.Spec.RecycleStrategy = virtualtargetv1alpha1.RecycleStrategyExitAndReplace

	lease := makeBoundMemberLease("rig",
		[]jumpstarterdevv1alpha1.LeaseMember{selectorMember("phone", poolLabels)},
		memberStatus("phone", jumpstarterdevv1alpha1.LeaseMemberStateBound, "exp-1"),
	)

	r, c := newReconciler(t, es, makeVTC(), lease,
		makeExporter("exp-1", false, false, true),
		makePod("exp-1", corev1.PodSucceeded),
	)

	reconcileOnce(t, r)

	if got := len(listExporters(t, c)); got != 1 {
		t.Fatalf("expected the bound exporter kept, got %d", got)
	}
}

// Lost is final, so under ExitAndReplace a terminal exporter of a Lost member
// is recycled while the lease still runs.
func TestReconcile_exitAndReplace_recyclesTerminalLostMember(t *testing.T) {
	es := demandSet(5)
	es.Spec.RecycleStrategy = virtualtargetv1alpha1.RecycleStrategyExitAndReplace

	lease := makeBoundMemberLease("rig",
		[]jumpstarterdevv1alpha1.LeaseMember{selectorMember("phone", poolLabels)},
		memberStatus("phone", jumpstarterdevv1alpha1.LeaseMemberStateLost, "exp-1"),
	)

	r, c := newReconciler(t, es, makeVTC(), lease,
		makeExporter("exp-1", false, false, true),
		makePod("exp-1", corev1.PodSucceeded),
	)

	reconcileOnce(t, r)

	if got := len(listExporters(t, c)); got != 0 {
		t.Fatalf("expected the terminal exporter of the Lost member deleted, got %d", got)
	}
}

// Under InPlaceReuse the exporter of a Lost member stays and can be online
// again, but the claim of the lease holds it: it is not free capacity and not
// a scale-down candidate until the lease ends.
func TestReconcile_lostMember_heldUntilLeaseEnds(t *testing.T) {
	lost := makeBoundMemberLease("rig",
		[]jumpstarterdevv1alpha1.LeaseMember{selectorMember("phone", poolLabels)},
		memberStatus("phone", jumpstarterdevv1alpha1.LeaseMemberStateLost, "exp-1"),
	)
	waiting := makePendingLease("pending-1", poolLabels)

	es := scaleDownDueSet()
	es.Spec.MaxReplicas = 5
	r, c := newReconciler(t, es, makeVTC(), lost, waiting, makeExporter("exp-1", true, false, true))

	reconcileOnce(t, r)

	exporters := listExporters(t, c)
	if len(exporters) != 2 {
		t.Fatalf("expected a second Exporter for the pending lease (the first is held), got %d", len(exporters))
	}
	if got := countDisabled(exporters); got != 0 {
		t.Fatalf("expected the held exporter kept enabled, got %d disabled", got)
	}
	updated := getExporterSet(t, c)
	assertInt32(t, "LeasedReplicas", updated.Status.LeasedReplicas, 1)
	assertInt32(t, "AvailableReplicas", updated.Status.AvailableReplicas, 0)
}

func TestReconcile_lostMember_freeAfterLeaseEnds(t *testing.T) {
	lost := makeBoundMemberLease("rig",
		[]jumpstarterdevv1alpha1.LeaseMember{selectorMember("phone", poolLabels)},
		memberStatus("phone", jumpstarterdevv1alpha1.LeaseMemberStateLost, "exp-1"),
	)
	lost.Status.Ended = true

	updated := reconcileAndGet(t, demandSet(5), makeVTC(), lost, makeExporter("exp-1", true, false, true))

	assertInt32(t, "LeasedReplicas", updated.Status.LeasedReplicas, 0)
	assertInt32(t, "AvailableReplicas", updated.Status.AvailableReplicas, 1)
}
