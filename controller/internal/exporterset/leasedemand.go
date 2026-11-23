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
	"fmt"

	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"sigs.k8s.io/controller-runtime/pkg/client"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
)

// leaseUsage is what the Leases of a namespace ask of one ExporterSet and hold
// of its exporters. A nil *leaseUsage asks and holds nothing.
type leaseUsage struct {
	// pending is the demand for pool instances: one unit for each pending
	// scalar lease and for each applicable member of a pending member-form
	// lease whose selector agrees with the template labels of the set.
	pending int32
	// pendingMembers is the part of pending that comes from member-form
	// leases. Such a lease can wait for a member outside the set while an
	// exporter of the set is free, so this demand holds exporters back from
	// scale-down.
	pendingMembers int32
	// bound has the exporters that are a Bound member of a member-form lease.
	// It covers the time before the Exporter shows status.leaseRef.
	bound map[string]struct{}
	// lost has the exporters of Lost members of leases that did not end. They
	// have no status.leaseRef, but the claim of the lease holds them.
	lost map[string]struct{}
}

// inUse reports whether a lease gives access to the exporter now.
func (u *leaseUsage) inUse(exp *jumpstarterdevv1alpha1.Exporter) bool {
	if exp.Status.LeaseRef != nil {
		return true
	}
	if u == nil {
		return false
	}
	_, ok := u.bound[exp.Name]
	return ok
}

// leased reports whether the exporter is not free for a new lease: it is in
// use, or it is the exporter of a Lost member that the lease still holds.
func (u *leaseUsage) leased(exp *jumpstarterdevv1alpha1.Exporter) bool {
	if u.inUse(exp) {
		return true
	}
	if u == nil {
		return false
	}
	_, ok := u.lost[exp.Name]
	return ok
}

// hold moves the exporters that only the leases show as held from available to
// leased in a poolState that computePoolState made from status.leaseRef.
func (u *leaseUsage) hold(state poolState, exporters []jumpstarterdevv1alpha1.Exporter) poolState {
	if u == nil {
		return state
	}
	for i := range exporters {
		exp := &exporters[i]
		if exp.Status.LeaseRef != nil || !u.leased(exp) {
			continue
		}
		state.leased++
		if exp.IsEnabled() && meta.IsStatusConditionTrue(
			exp.Status.Conditions,
			string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
		) {
			state.available--
		}
	}
	return state
}

// computeLeaseUsage reads the Leases of the namespace of the set once.
func (r *ExporterSetReconciler) computeLeaseUsage(
	ctx context.Context,
	es *virtualtargetv1alpha1.ExporterSet,
) (*leaseUsage, error) {
	var leaseList jumpstarterdevv1alpha1.LeaseList
	if err := r.List(ctx, &leaseList, client.InNamespace(es.Namespace)); err != nil {
		return nil, fmt.Errorf("unable to list Leases: %w", err)
	}

	templateLabels := labels.Set(es.Spec.Template.Metadata.Labels)
	usage := &leaseUsage{
		bound: map[string]struct{}{},
		lost:  map[string]struct{}{},
	}

	for i := range leaseList.Items {
		lease := &leaseList.Items[i]
		if lease.Status.Ended {
			continue
		}

		demand := leaseDemand(lease, templateLabels)
		usage.pending += demand

		if !lease.IsMemberForm() {
			continue
		}
		usage.pendingMembers += demand
		for _, name := range lease.HeldExporterNames() {
			if lease.HasBoundExporter(name) {
				usage.bound[name] = struct{}{}
			} else {
				usage.lost[name] = struct{}{}
			}
		}
	}
	return usage, nil
}

// leaseDemand counts the pool instances that a lease waits for from a pool
// whose exporters carry templateLabels. A scalar lease is at most one unit. A
// member-form lease is one unit for each applicable member, so two members
// that select the same set are two units. A lease that ended, is bound or is
// not Pending is no demand.
func leaseDemand(lease *jumpstarterdevv1alpha1.Lease, templateLabels labels.Set) int32 {
	if lease.Status.Ended || lease.IsBound() {
		return 0
	}
	if !meta.IsStatusConditionTrue(lease.Status.Conditions,
		string(jumpstarterdevv1alpha1.LeaseConditionTypePending)) {
		return 0
	}

	var count int32
	for _, selector := range leasePoolSelectors(lease) {
		sel, err := metav1.LabelSelectorAsSelector(selector)
		if err != nil {
			continue
		}
		if sel.Matches(templateLabels) {
			count++
		}
	}
	return count
}

// leasePoolSelectors returns the selectors with which a lease can ask for a
// pool instance: spec.selector of a scalar lease, or the selector of each
// member that is not excluded and not pinned to a named exporter. An empty
// selector is left out: it converts to labels.Everything and would match
// every pool.
func leasePoolSelectors(lease *jumpstarterdevv1alpha1.Lease) []*metav1.LabelSelector {
	if !lease.IsMemberForm() {
		if lease.Spec.ExporterRef != nil || isEmptySelector(&lease.Spec.Selector) {
			return nil
		}
		return []*metav1.LabelSelector{&lease.Spec.Selector}
	}

	var selectors []*metav1.LabelSelector
	for i := range lease.Spec.Members {
		member := &lease.Spec.Members[i]
		if member.Excluded || member.ExporterRef != nil ||
			member.Selector == nil || isEmptySelector(member.Selector) {
			continue
		}
		selectors = append(selectors, member.Selector)
	}
	return selectors
}

func isEmptySelector(selector *metav1.LabelSelector) bool {
	return len(selector.MatchLabels) == 0 && len(selector.MatchExpressions) == 0
}
