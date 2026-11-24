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
	"fmt"
	"slices"
	"strings"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/leasebind"
	corev1 "k8s.io/api/core/v1"
	k8serrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// memberSpec is what one member of a lease asks for. A scalar lease is
// handled as one member without a role name.
type memberSpec struct {
	name string
	// selector is nil when the member has only an exporterRef.
	selector      labels.Selector
	exporterRef   *corev1.LocalObjectReference
	optional      bool
	excluded      bool
	allowDisabled bool
}

// memberFailure is the result of a check that a member fails by itself.
type memberFailure struct {
	invalid bool
	reason  string
	message string
}

// stagedMember has the exporters of a member after each filter stage. The
// lists can have an exporter more than once, with different policies.
type stagedMember struct {
	spec      memberSpec
	approved  []ApprovedExporter
	ordered   []ApprovedExporter // online, in order of preference
	available []ApprovedExporter
	ready     []ApprovedExporter
}

// bindEnvironment is the state of the namespace that the filter stages use.
type bindEnvironment struct {
	// exporterHolder and groupHolder have the other active lease that holds
	// an exporter or an exclusion group.
	exporterHolder map[string]*jumpstarterdevv1alpha1.Lease
	groupHolder    map[string]*jumpstarterdevv1alpha1.Lease
	// cleanupGroups are the exclusion groups with an exporter in cleanup.
	cleanupGroups map[string]bool
}

func leaseMemberSpecs(lease *jumpstarterdevv1alpha1.Lease) ([]memberSpec, error) {
	if !lease.IsMemberForm() {
		selector, err := lease.GetExporterSelector()
		if err != nil {
			return nil, fmt.Errorf("failed to get exporter selector: %w", err)
		}
		return []memberSpec{{
			selector:      selector,
			exporterRef:   lease.Spec.ExporterRef,
			allowDisabled: lease.Spec.AllowDisabled,
		}}, nil
	}
	specs := make([]memberSpec, 0, len(lease.Spec.Members))
	for i := range lease.Spec.Members {
		member := &lease.Spec.Members[i]
		spec := memberSpec{
			name:        member.Name,
			exporterRef: member.ExporterRef,
			optional:    member.Optional,
			excluded:    member.Excluded,
			// For a member with a selector the field has no effect.
			allowDisabled: member.AllowDisabled && member.ExporterRef != nil,
		}
		if member.Selector != nil {
			selector, err := metav1.LabelSelectorAsSelector(member.Selector)
			if err != nil {
				return nil, fmt.Errorf("failed to get the exporter selector of role %s: %w", member.Name, err)
			}
			spec.selector = selector
		}
		specs = append(specs, spec)
	}
	return specs, nil
}

func (s *memberSpec) hasSelector() bool {
	return s.selector != nil && !s.selector.Empty()
}

// reconcileStatusMembers binds the exporters of a lease and manages
// LeaseConditionTypeUnsatisfiable, LeaseConditionTypePending and, for a
// member-form lease, LeaseConditionTypeReady and LeaseConditionTypeDegraded.
// A scalar lease is bound as one member and gets status.exporterRef; a
// member-form lease gets status.members.
func (r *LeaseReconciler) reconcileStatusMembers(
	ctx context.Context,
	result *ctrl.Result,
	lease *jumpstarterdevv1alpha1.Lease,
	state *bindState,
) error {
	logger := log.FromContext(ctx)

	// Do not attempt to reconcile if the lease is already ended/invalid/etc
	if lease.Status.Ended {
		return nil
	}

	if lease.IsBound() {
		return r.reconcileBoundLease(ctx, result, lease, state)
	}

	if err := r.backfillClaims(ctx, lease.Namespace); err != nil {
		return err
	}

	claims, err := r.listClaims(ctx, lease.Namespace)
	if err != nil {
		return err
	}
	// A lease that is not bound keeps no claims and no marks: these are from
	// a reconcile that stopped before it wrote the bind.
	marks, err := r.leaseHasMarks(ctx, lease)
	if err != nil {
		return err
	}
	if marks || len(claimsOf(claims, lease)) > 0 {
		if claims, err = r.startAttempt(ctx, lease, state); err != nil {
			return err
		}
	}

	setPendingMembers(lease)
	setPendingForwards(lease)

	// For scheduled leases: only assign exporter if requested BeginTime has arrived
	if lease.Spec.BeginTime != nil {
		now := time.Now()
		if lease.Spec.BeginTime.After(now) {
			// Requested BeginTime is in the future, wait until then
			waitDuration := lease.Spec.BeginTime.Sub(now)
			logger.Info("Lease is scheduled for the future, waiting",
				"lease", lease.Name,
				"requestedBeginTime", lease.Spec.BeginTime,
				"waitDuration", waitDuration)
			result.RequeueAfter = waitDuration
			return nil
		}
	}
	logger.Info("Looking for a matching exporter for lease", "lease", lease.Name, "client", lease.GetClientName(), "selector", lease.Spec.Selector)

	specs, err := leaseMemberSpecs(lease)
	if err != nil {
		return fmt.Errorf("reconcileStatusMembers: %w", err)
	}

	// Checks for one member. A required member that fails ends the lease; an
	// optional member that fails is omitted.
	omitted := map[string]memberFailure{}
	var members []*stagedMember
	for _, spec := range specs {
		if spec.excluded {
			continue
		}
		approved, failure, err := r.resolveMember(ctx, lease, &spec)
		if err != nil {
			return err
		}
		if failure != nil {
			if !spec.optional {
				setMemberFailure(lease, &spec, failure)
				return nil
			}
			omitted[spec.name] = *failure
			continue
		}
		members = append(members, &stagedMember{spec: spec, approved: approved})
	}

	env, err := r.loadBindEnvironment(ctx, lease, claims)
	if err != nil {
		return err
	}

	solveMembers := make([]leasebind.Member, 0, len(members))
	kept := members[:0]
	for _, member := range members {
		member.applyStages(env)
		if failure := member.spotAccessFailure(); failure != nil {
			if !member.spec.optional {
				setMemberFailure(lease, &member.spec, failure)
				return nil
			}
			omitted[member.spec.name] = *failure
			continue
		}
		kept = append(kept, member)
		solveMembers = append(solveMembers, member.solveMember())
	}
	members = kept

	constraints := make([]leasebind.Constraint, 0, len(lease.Spec.Constraints))
	for _, c := range lease.Spec.Constraints {
		constraints = append(constraints, leasebind.Constraint{Members: c.Members, SameLabel: c.SameLabel})
	}

	solved := leasebind.Solve(solveMembers, constraints)
	if solved.Failure != nil {
		return r.setBindFailure(ctx, result, lease, members, solved.Failure)
	}

	bound := map[string]ApprovedExporter{}
	// selected has the exclusion group that the assignment used for each
	// exporter.
	selected := map[string]string{}
	var exporters, groups []string
	for _, member := range members {
		name, ok := solved.Bound[member.spec.name]
		if !ok {
			continue
		}
		entry := member.readyEntry(name)
		bound[member.spec.name] = entry
		exporters = append(exporters, name)
		group := entry.Exporter.Spec.ExclusionGroup
		selected[name] = group
		if group != "" && !slices.Contains(groups, group) {
			groups = append(groups, group)
		}
	}
	slices.Sort(groups)

	// A lease in which no member binds holds nothing and never begins. The
	// API does not admit a lease without a required member; this keeps the
	// reconciler safe for one that exists.
	if len(bound) == 0 {
		lease.SetStatusUnsatisfiable(jumpstarterdevv1alpha1.LeaseReasonInsufficientExporters,
			"No member of the lease can bind an exporter")
		return nil
	}

	// The assignment exists. Mark the exporters, take the claims, and only
	// then write the bind.
	if !state.attemptStarted {
		if _, err := r.startAttempt(ctx, lease, state); err != nil {
			return err
		}
	}

	ok, cause, err := r.markExporters(ctx, lease, state, selected)
	if err != nil {
		return err
	}
	if !ok {
		r.rollbackAttempt(ctx, lease, state)
		lease.SetStatusPending("NotAvailable", "%s, trying again", cause)
		result.RequeueAfter = pendingRequeueAfter(lease)
		return nil
	}

	ok, blocked, err := r.createBindClaims(ctx, lease, state, claimKeys(exporters, groups))
	if err != nil {
		return err
	}
	if !ok {
		r.rollbackAttempt(ctx, lease, state)
		lease.SetStatusPending("NotAvailable",
			"Another lease got the claim for %s first, trying again", blocked)
		result.RequeueAfter = pendingRequeueAfter(lease)
		return nil
	}

	lease.Status.ExclusionGroups = groups
	if !lease.IsMemberForm() {
		entry := bound[""]
		lease.Status.Priority = entry.Policy.Priority
		lease.Status.SpotAccess = entry.Policy.SpotAccess
		lease.Status.ExporterRef = &corev1.LocalObjectReference{
			Name: entry.Exporter.Name,
		}
		return nil
	}

	statuses := make([]jumpstarterdevv1alpha1.LeaseMemberStatus, 0, len(specs))
	first := true
	lease.Status.SpotAccess = false
	for _, spec := range specs {
		status := jumpstarterdevv1alpha1.LeaseMemberStatus{Name: spec.name}
		if entry, ok := bound[spec.name]; ok {
			status.State = jumpstarterdevv1alpha1.LeaseMemberStateBound
			status.ExporterRef = &corev1.LocalObjectReference{Name: entry.Exporter.Name}
			status.Priority = entry.Policy.Priority
			status.SpotAccess = entry.Policy.SpotAccess
			// The lease priority is the minimum of the member priorities.
			if first || status.Priority < lease.Status.Priority {
				lease.Status.Priority = status.Priority
			}
			first = false
			lease.Status.SpotAccess = lease.Status.SpotAccess || status.SpotAccess
		} else {
			status.State = jumpstarterdevv1alpha1.LeaseMemberStateOmitted
			if spec.excluded {
				status.Reason = jumpstarterdevv1alpha1.LeaseMemberReasonExcluded
				status.Message = "The request left this role out"
			} else if failure, ok := omitted[spec.name]; ok {
				status.Reason = failure.reason
				status.Message = failure.message
			} else {
				omission := solved.Omitted[spec.name]
				status.Reason = omission.Reason
				status.Message = omission.Message
			}
		}
		statuses = append(statuses, status)
	}
	lease.Status.Members = statuses
	lease.SetStatusCondition(jumpstarterdevv1alpha1.LeaseConditionTypePending, false,
		"Bound", "The members of the lease have their exporters")
	return r.reconcileMemberConditions(ctx, result, lease)
}

// setPendingMembers gives a member-form lease that is not bound its member
// statuses: every member waits, except those that the request left out.
func setPendingMembers(lease *jumpstarterdevv1alpha1.Lease) {
	if !lease.IsMemberForm() {
		return
	}
	statuses := make([]jumpstarterdevv1alpha1.LeaseMemberStatus, 0, len(lease.Spec.Members))
	for i := range lease.Spec.Members {
		member := &lease.Spec.Members[i]
		status := jumpstarterdevv1alpha1.LeaseMemberStatus{
			Name:  member.Name,
			State: jumpstarterdevv1alpha1.LeaseMemberStatePending,
		}
		if member.Excluded {
			status.State = jumpstarterdevv1alpha1.LeaseMemberStateOmitted
			status.Reason = jumpstarterdevv1alpha1.LeaseMemberReasonExcluded
			status.Message = "The request left this role out"
		}
		statuses = append(statuses, status)
	}
	lease.Status.Members = statuses
}

func setMemberFailure(lease *jumpstarterdevv1alpha1.Lease, spec *memberSpec, failure *memberFailure) {
	message := failure.message
	if spec.name != "" {
		message = fmt.Sprintf("role `%s`: %s", spec.name, message)
	}
	if failure.invalid {
		lease.SetStatusInvalid(failure.reason, "%s", message)
		return
	}
	lease.SetStatusUnsatisfiable(failure.reason, "%s", message)
}

// resolveMember returns the exporters that the member selects, that are
// enabled, and that a policy approves for the client of the lease.
func (r *LeaseReconciler) resolveMember(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	spec *memberSpec,
) ([]ApprovedExporter, *memberFailure, error) {
	if !spec.hasSelector() && spec.exporterRef == nil {
		return nil, &memberFailure{
			invalid: true,
			reason:  "InvalidSelector",
			message: "The selector for the lease is empty, a selector is required",
		}, nil
	}

	var matchingExporters []jumpstarterdevv1alpha1.Exporter
	if spec.exporterRef != nil {
		var exporter jumpstarterdevv1alpha1.Exporter
		if err := r.Get(ctx, types.NamespacedName{
			Namespace: lease.Namespace,
			Name:      spec.exporterRef.Name,
		}, &exporter); err != nil {
			if k8serrors.IsNotFound(err) {
				return nil, &memberFailure{
					reason:  "ExporterNotFound",
					message: fmt.Sprintf("Requested exporter %s was not found", spec.exporterRef.Name),
				}, nil
			}
			return nil, nil, fmt.Errorf("resolveMember: failed to get requested exporter: %w", err)
		}
		// Only a scalar lease can have a selector with its exporterRef.
		if spec.hasSelector() && !spec.selector.Matches(labels.Set(exporter.Labels)) {
			return nil, &memberFailure{
				reason: "SelectorMismatch",
				message: fmt.Sprintf("Requested exporter %s does not match selector %s",
					exporter.Name, metav1.FormatLabelSelector(&lease.Spec.Selector)),
			}, nil
		}
		if err := jumpstarterdevv1alpha1.ValidateExporterEnabledForLease(&exporter, spec.allowDisabled); err != nil {
			return nil, &memberFailure{reason: "ExporterDisabled", message: err.Error()}, nil
		}
		matchingExporters = []jumpstarterdevv1alpha1.Exporter{exporter}
	} else {
		// List all exporters matching selector
		listed, err := r.ListMatchingExporters(ctx, lease, spec.selector)
		if err != nil {
			return nil, nil, fmt.Errorf("resolveMember: failed to list matching exporters: %w", err)
		}
		// Filter out disabled exporters from selector-based listing
		matchingExporters = filterOutDisabledExporters(listed.Items, spec.allowDisabled)
		if len(matchingExporters) == 0 && len(listed.Items) > 0 {
			return nil, &memberFailure{
				reason:  "AllDisabled",
				message: fmt.Sprintf("All %d exporters matching the selector are disabled", len(listed.Items)),
			}, nil
		}
	}

	approvedExporters, unmatchedDescriptions, err := r.attachMatchingPolicies(ctx, lease, matchingExporters)
	if err != nil {
		return nil, nil, fmt.Errorf("resolveMember: failed to handle policy approval: %w", err)
	}

	if len(approvedExporters) == 0 {
		message := fmt.Sprintf(
			"While there are %d exporters matching the selector, none of them are approved by any policy for your client",
			len(matchingExporters))
		if len(unmatchedDescriptions) > 0 {
			desc := strings.Join(unmatchedDescriptions, "; ")
			if len(desc) > 4096 {
				desc = desc[:4096] + "..."
			}
			message = fmt.Sprintf("%s. Matching policies: %s", message, desc)
		}
		return nil, &memberFailure{reason: "NoAccess", message: message}, nil
	}
	return approvedExporters, nil, nil
}

// loadBindEnvironment reads what the other leases hold. Leases and claims come
// from the API server: the cache sees a write only some time after it
// succeeds. A claim counts only while its lease is active.
func (r *LeaseReconciler) loadBindEnvironment(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	claims []jumpstarterdevv1alpha1.ExporterClaim,
) (*bindEnvironment, error) {
	env := &bindEnvironment{
		exporterHolder: map[string]*jumpstarterdevv1alpha1.Lease{},
		groupHolder:    map[string]*jumpstarterdevv1alpha1.Lease{},
		cleanupGroups:  map[string]bool{},
	}

	activeLeases, err := r.ListActiveLeases(ctx, lease.Namespace)
	if err != nil {
		return nil, fmt.Errorf("loadBindEnvironment: failed to list active leases: %w", err)
	}
	active := map[types.UID]*jumpstarterdevv1alpha1.Lease{}
	for i := range activeLeases.Items {
		other := &activeLeases.Items[i]
		if other.UID == lease.UID || other.Status.Ended {
			continue
		}
		active[other.UID] = other
		for _, name := range other.HeldExporterNames() {
			env.exporterHolder[name] = other
		}
		for _, group := range other.Status.ExclusionGroups {
			env.groupHolder[group] = other
		}
	}
	for i := range claims {
		claim := &claims[i]
		holder, ok := active[claim.Spec.LeaseRef.UID]
		if !ok {
			continue
		}
		if claim.Spec.ExclusionGroup != "" {
			env.groupHolder[claim.Spec.ExclusionGroup] = holder
		} else {
			env.exporterHolder[claim.Spec.Exporter] = holder
		}
	}

	var exporters jumpstarterdevv1alpha1.ExporterList
	if err := r.List(ctx, &exporters, client.InNamespace(lease.Namespace)); err != nil {
		return nil, fmt.Errorf("loadBindEnvironment: failed to list exporters: %w", err)
	}
	for i := range exporters.Items {
		exporter := &exporters.Items[i]
		group := exporter.Spec.ExclusionGroup
		if group != "" && exporterInCleanup(exporter) {
			env.cleanupGroups[group] = true
		}
		// The mark of another active lease: that lease is in a bind attempt
		// for the exporter and its group.
		if ref := exporter.Status.ClaimRef; ref != nil {
			if holder, ok := active[ref.UID]; ok {
				if _, held := env.exporterHolder[exporter.Name]; !held {
					env.exporterHolder[exporter.Name] = holder
				}
				if _, held := env.groupHolder[group]; !held && group != "" {
					env.groupHolder[group] = holder
				}
			}
		}
	}
	return env, nil
}

func exporterOnline(exporter *jumpstarterdevv1alpha1.Exporter) bool {
	return meta.IsStatusConditionTrue(
		exporter.Status.Conditions,
		string(jumpstarterdevv1alpha1.ExporterConditionTypeRegistered),
	) && meta.IsStatusConditionTrue(
		exporter.Status.Conditions,
		string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
	)
}

// exporterInCleanup reports whether an online exporter runs or owes the
// afterLease hook of its previous lease. Its DUT is still in use, so the other
// exporters of its exclusion group are not given to a new lease. An offline
// exporter does not block its group.
func exporterInCleanup(exporter *jumpstarterdevv1alpha1.Exporter) bool {
	if !exporterOnline(exporter) {
		return false
	}
	status := exporter.Status
	return status.ExporterStatusValue == jumpstarterdevv1alpha1.ExporterStatusAfterLeaseHook ||
		(status.RecordsLeaseHooks && status.LeaseHooks.OwesAfterLease())
}

// attachHolders attaches the other lease that holds the exporter or its
// exclusion group.
func attachHolders(exporters []ApprovedExporter, env *bindEnvironment) []ApprovedExporter {
	for i := range exporters {
		exporter := &exporters[i].Exporter
		if holder, ok := env.exporterHolder[exporter.Name]; ok {
			exporters[i].ExistingLease = holder
		} else if holder, ok := env.groupHolder[exporter.Spec.ExclusionGroup]; ok && exporter.Spec.ExclusionGroup != "" {
			exporters[i].ExistingLease = holder
		}
	}
	return exporters
}

// filterOutGroupCleanup filters out the exporters of an exclusion group that
// has an exporter in cleanup.
func filterOutGroupCleanup(exporters []ApprovedExporter, cleanupGroups map[string]bool) []ApprovedExporter {
	return slices.DeleteFunc(slices.Clone(exporters), func(ae ApprovedExporter) bool {
		return cleanupGroups[ae.Exporter.Spec.ExclusionGroup]
	})
}

func (m *stagedMember) applyStages(env *bindEnvironment) {
	m.approved = attachHolders(m.approved, env)
	m.ordered = orderApprovedExporters(filterOutOfflineExporters(m.approved))
	m.available = filterOutLeasedExporters(m.ordered)
	m.ready = filterOutGroupCleanup(filterOutNotReadyExporters(m.available), env.cleanupGroups)
}

func (m *stagedMember) spotAccessFailure() *memberFailure {
	if len(m.ordered) == 0 || !m.ordered[0].Policy.SpotAccess {
		return nil
	}
	return &memberFailure{
		reason: "SpotAccess",
		message: fmt.Sprintf(
			"The only possible exporters are under spot access (i.e. %s), but spot access is still not implemented",
			m.ordered[0].Exporter.Name),
	}
}

// readyEntry returns the best entry of the exporter among the ready ones.
func (m *stagedMember) readyEntry(name string) ApprovedExporter {
	for _, entry := range m.ready {
		if entry.Exporter.Name == name {
			return entry
		}
	}
	return ApprovedExporter{}
}

func exporterNames(exporters []ApprovedExporter, keep func(*ApprovedExporter) bool) map[string]bool {
	names := map[string]bool{}
	for i := range exporters {
		if keep == nil || keep(&exporters[i]) {
			names[exporters[i].Exporter.Name] = true
		}
	}
	return names
}

// solveMember gives each exporter of the member once, in order of preference,
// with the last filter stage that it passes.
func (m *stagedMember) solveMember() leasebind.Member {
	online := exporterNames(m.ordered, nil)
	available := exporterNames(m.available, nil)
	ready := exporterNames(m.ready, nil)
	free := exporterNames(m.ready, func(ae *ApprovedExporter) bool { return ae.ExistingLease == nil })

	member := leasebind.Member{Name: m.spec.name, Optional: m.spec.optional}
	seen := map[string]bool{}
	for _, entry := range orderApprovedExporters(slices.Clone(m.approved)) {
		name := entry.Exporter.Name
		if seen[name] {
			continue
		}
		seen[name] = true
		candidate := leasebind.Candidate{Name: name, Labels: entry.Exporter.Labels, Stage: leasebind.StageApproved}
		switch {
		case free[name]:
			candidate.Stage = leasebind.StageFree
		case ready[name]:
			candidate.Stage = leasebind.StageReady
		case available[name]:
			candidate.Stage = leasebind.StageUnclaimed
		case online[name]:
			candidate.Stage = leasebind.StageOnline
		}
		member.Candidates = append(member.Candidates, candidate)
	}
	return member
}

// setBindFailure reports why the members cannot be assigned now. The lease
// holds no claims.
func (r *LeaseReconciler) setBindFailure(
	ctx context.Context,
	result *ctrl.Result,
	lease *jumpstarterdevv1alpha1.Lease,
	members []*stagedMember,
	failure *leasebind.Failure,
) error {
	if failure.Unsatisfiable() {
		if failure.Reason == leasebind.ReasonInsufficientExporters {
			waits, err := r.rolesSelectExporterSet(ctx, lease, members, failure.Roles)
			if err != nil {
				return err
			}
			if waits {
				// The set can create the exporters that are missing.
				lease.SetStatusPending("NotAvailable",
					"%s; waiting for an ExporterSet to create exporters", failure.Message)
				result.RequeueAfter = pendingRequeueAfter(lease)
				return nil
			}
		}
		lease.SetStatusUnsatisfiable(failure.Reason, "%s", failure.Message)
		return nil
	}

	if lease.IsMemberForm() {
		lease.SetStatusPending(failure.Reason, "%s", failure.Message)
		result.RequeueAfter = pendingRequeueAfter(lease)
		return nil
	}

	member := members[0]
	switch failure.Stage {
	case leasebind.StageOnline:
		lease.SetStatusPending(
			"Offline",
			"While there are %d available exporters (i.e. %s), none of them are online",
			len(member.approved),
			member.approved[0].Exporter.Name,
		)
	case leasebind.StageUnclaimed:
		lease.SetStatusPending("NotAvailable",
			"There are %d approved exporters, (i.e. %s) but all of them are already leased",
			len(member.ordered),
			member.ordered[0].Exporter.Name,
		)
	case leasebind.StageReady:
		lease.SetStatusPending(
			"NotReady",
			"There are %d online exporters, but none are ready (still cleaning up previous lease)",
			len(member.available),
		)
	default:
		// TODO: Implement eviction of spot access leases
		lease.SetStatusPending("NotAvailable",
			"Exporter %s is already leased by another client under spot access, but spot access eviction still not implemented",
			member.ready[0].Exporter.Name)
	}
	result.RequeueAfter = pendingRequeueAfter(lease)
	return nil
}

var exporterSetListGVK = schema.GroupVersionKind{
	Group:   "virtualtarget.jumpstarter.dev",
	Version: "v1alpha1",
	Kind:    "ExporterSetList",
}

// rolesSelectExporterSet reports whether one of these roles selects the
// labels that an ExporterSet gives to its exporters. The set then creates
// exporters for the lease, so too few exporters is not final.
func (r *LeaseReconciler) rolesSelectExporterSet(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	members []*stagedMember,
	roles []string,
) (bool, error) {
	var selectors []labels.Selector
	for _, member := range members {
		if slices.Contains(roles, member.spec.name) && member.spec.exporterRef == nil && member.spec.hasSelector() {
			selectors = append(selectors, member.spec.selector)
		}
	}
	if len(selectors) == 0 {
		return false, nil
	}

	// The ExporterSet API is optional in a cluster and is not in the scheme of
	// every caller, thus it is read without its Go type.
	sets := &unstructured.UnstructuredList{}
	sets.SetGroupVersionKind(exporterSetListGVK)
	if err := r.APIReader.List(ctx, sets, client.InNamespace(lease.Namespace)); err != nil {
		if meta.IsNoMatchError(err) || k8serrors.IsNotFound(err) {
			return false, nil
		}
		return false, fmt.Errorf("rolesSelectExporterSet: failed to list exporter sets: %w", err)
	}
	for i := range sets.Items {
		templateLabels, _, err := unstructured.NestedStringMap(
			sets.Items[i].Object, "spec", "template", "metadata", "labels")
		if err != nil || len(templateLabels) == 0 {
			continue
		}
		for _, selector := range selectors {
			if selector.Matches(labels.Set(templateLabels)) {
				return true, nil
			}
		}
	}
	return false, nil
}

// reconcileBoundLease keeps the claims and the marks of a bound lease and, for
// a member-form lease, finds lost members and sets the Ready and Degraded
// conditions. It never assigns again: a bound lease keeps its exporters until
// it ends.
func (r *LeaseReconciler) reconcileBoundLease(
	ctx context.Context,
	result *ctrl.Result,
	lease *jumpstarterdevv1alpha1.Lease,
	state *bindState,
) error {
	claims, err := r.listClaims(ctx, lease.Namespace)
	if err != nil {
		return err
	}
	// A reconcile that lost the race for the bind can create a claim after it.
	if claims, err = r.deleteSmallerAttempts(ctx, lease, claims); err != nil {
		return err
	}
	outcome, err := r.ensureBoundClaims(ctx, lease, claims, state)
	if err != nil {
		return err
	}
	switch outcome {
	case boundClaimsConflict:
		return nil
	case boundClaimsRetry:
		requeueWithin(result, time.Second)
	case boundClaimsHeld:
		// An old leader can mark an exporter after the bind.
		if err := r.removeStrayMarks(ctx, lease); err != nil {
			return err
		}
	}

	if !lease.IsMemberForm() {
		return nil
	}
	return r.reconcileMemberConditions(ctx, result, lease)
}

// requeueWithin makes the result ask for a reconcile after at most this time.
// A requeue that is already sooner stays.
func requeueWithin(result *ctrl.Result, after time.Duration) {
	if after <= 0 {
		return
	}
	if result.RequeueAfter <= 0 || after < result.RequeueAfter {
		result.RequeueAfter = after
	}
}

// processStart is when this controller process started, to the precision that
// the offline grace needs.
var processStart = time.Now()

// offlineGrace returns how long the reconciler must still wait before it can
// take an offline exporter for lost. After a start the controller has no
// current heartbeat data: the exporters reconnect, and the exporter
// reconciler needs the offline time limit to show which of them did not.
func (r *LeaseReconciler) offlineGrace() time.Duration {
	start := r.StartedAt
	if start.IsZero() {
		start = processStart
	}
	return time.Until(start.Add(exporterOfflineThreshold))
}

// reconcileMemberConditions marks the bound members whose exporter is gone as
// Lost and sets the Ready and Degraded conditions of a member-form lease.
// Ready waits for the beforeLease hook of every bound member.
func (r *LeaseReconciler) reconcileMemberConditions(
	ctx context.Context,
	result *ctrl.Result,
	lease *jumpstarterdevv1alpha1.Lease,
) error {
	logger := log.FromContext(ctx)

	var waiting, failed, offline []string
	for i := range lease.Status.Members {
		member := &lease.Status.Members[i]
		if member.State != jumpstarterdevv1alpha1.LeaseMemberStateBound || member.ExporterRef == nil {
			continue
		}
		var exporter jumpstarterdevv1alpha1.Exporter
		err := r.Get(ctx, types.NamespacedName{Namespace: lease.Namespace, Name: member.ExporterRef.Name}, &exporter)
		switch {
		case k8serrors.IsNotFound(err):
			logger.Info("Member lost: exporter deleted", "lease_member", member.Name, "exporter", member.ExporterRef.Name)
			lease.MarkMemberLost(member.Name, jumpstarterdevv1alpha1.LeaseMemberReasonExporterDeleted,
				fmt.Sprintf("Exporter %s was deleted", member.ExporterRef.Name))
		case err != nil:
			return fmt.Errorf("reconcileMemberConditions: failed to get exporter of role %s: %w", member.Name, err)
		case !exporterOnline(&exporter):
			if grace := r.offlineGrace(); grace > 0 {
				// The controller started a moment ago: the exporter can be
				// about to reconnect. Look again when the grace ends.
				offline = append(offline, member.Name)
				requeueWithin(result, grace)
				continue
			}
			logger.Info("Member lost: exporter offline", "lease_member", member.Name, "exporter", exporter.Name)
			lease.MarkMemberLost(member.Name, jumpstarterdevv1alpha1.LeaseMemberReasonOffline,
				fmt.Sprintf("Exporter %s went offline", exporter.Name))
		default:
			switch exporter.Status.ExporterStatusValue {
			// Unset: an old exporter that does not report its status has no hooks.
			case jumpstarterdevv1alpha1.ExporterStatusLeaseReady, jumpstarterdevv1alpha1.ExporterStatusUnspecified, "":
			case jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHookFailed:
				failed = append(failed, member.Name)
			default:
				waiting = append(waiting, member.Name)
			}
		}
	}

	if lease.HasLostMember() {
		var lost []string
		for i := range lease.Status.Members {
			member := &lease.Status.Members[i]
			if member.State == jumpstarterdevv1alpha1.LeaseMemberStateLost {
				lost = append(lost, fmt.Sprintf("`%s` (%s)", member.Name, member.Reason))
			}
		}
		message := "Lost roles: " + strings.Join(lost, ", ")
		lease.SetStatusCondition(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded, true,
			jumpstarterdevv1alpha1.LeaseReasonMemberLost, "%s", message)
		lease.SetStatusReady(false, jumpstarterdevv1alpha1.LeaseReasonMemberLost, "%s", message)
		if lease.HasForwards() {
			return r.reconcileForwards(ctx, result, lease, false)
		}
		return nil
	}

	// A hook that is complete stays complete: the status that an exporter
	// reports later in the lease does not make the lease wait again. A lease
	// that waits only for its forwards has its hooks complete too.
	membersReady := memberHooksComplete(lease)
	if !membersReady {
		switch {
		case len(failed) > 0:
			lease.SetStatusReady(false, jumpstarterdevv1alpha1.LeaseReasonBeforeLeaseHookFailed,
				"The beforeLease hook failed for roles: %s", quoteRoles(failed))
		case len(waiting) > 0:
			lease.SetStatusReady(false, jumpstarterdevv1alpha1.LeaseReasonBeforeLeaseHook,
				"Waiting for the beforeLease hook of roles: %s", quoteRoles(waiting))
		case len(offline) > 0:
			lease.SetStatusReady(false, jumpstarterdevv1alpha1.LeaseReasonBeforeLeaseHook,
				"Waiting for the exporters of roles: %s", quoteRoles(offline))
		default:
			membersReady = true
		}
	}
	// The forwards of a lease are one more condition for Ready (JEP-0016).
	if lease.HasForwards() {
		return r.reconcileForwards(ctx, result, lease, membersReady)
	}
	if membersReady && !meta.IsStatusConditionTrue(lease.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypeReady)) {
		lease.SetStatusReady(true, "Ready", "The exporters of all members are ready")
	}
	return nil
}

func quoteRoles(roles []string) string {
	quoted := make([]string, len(roles))
	for i, role := range roles {
		quoted[i] = "`" + role + "`"
	}
	return strings.Join(quoted, ", ")
}
