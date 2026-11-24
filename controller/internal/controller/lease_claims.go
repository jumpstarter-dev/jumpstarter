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

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	k8serrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporterclaims,verbs=get;list;watch;create;delete
// +kubebuilder:rbac:groups=virtualtarget.jumpstarter.dev,resources=exportersets,verbs=get;list;watch

// An ExporterClaim gives one lease exclusive hold of one exporter or one
// exclusion group: its name comes from the exporter or the group, and the API
// server lets only one object with that name exist. The functions in this file
// are the claim protocol of the bind procedure:
//
//   - A bind attempt starts by incrementing status.bindAttempt in a status
//     update. Each claim records the attempt that created it.
//   - A reconcile deletes only claims of its own lease, and of those only its
//     own attempt or a smaller one. The claims of a bound lease have the
//     largest attempt, so a reconcile that lost the race for the status update
//     (a former leader, for example) cannot delete them.
//   - Every delete has a UID precondition, because all attempts of a lease
//     use the same claim names.
//   - A claim of another lease is replaced only after a read from the API
//     server shows that the lease ended or does not exist.

// bindState is what one reconcile did in the claim protocol.
type bindState struct {
	// attemptStarted is set once this reconcile incremented status.bindAttempt.
	attemptStarted bool
	// created are the claims this reconcile created. They are deleted when
	// the reconcile does not write the bind to the lease status.
	created []*jumpstarterdevv1alpha1.ExporterClaim
	// marked are the exporters on which this reconcile wrote
	// status.claimRef. The marks are removed with the claims in created.
	marked []string
	// ensured are the claims this reconcile created for a lease that was
	// bound without them. They stay unless the lease ended meanwhile.
	ensured []*jumpstarterdevv1alpha1.ExporterClaim
}

// claimKey is an exporter or an exclusion group that a lease holds.
type claimKey struct {
	exporter string
	group    string
}

func (k claimKey) name() string {
	if k.group != "" {
		return jumpstarterdevv1alpha1.ExclusionGroupClaimName(k.group)
	}
	return jumpstarterdevv1alpha1.ExporterClaimName(k.exporter)
}

func (k claimKey) String() string {
	if k.group != "" {
		return "exclusion group " + k.group
	}
	return "exporter " + k.exporter
}

// claimKeys returns the keys for these exporters and groups in the order of
// their claim names. All leases create claims in this order, so of two leases
// that need the same claims, the one that fails holds nothing the other needs.
func claimKeys(exporters, groups []string) []claimKey {
	var keys []claimKey
	for _, name := range exporters {
		keys = append(keys, claimKey{exporter: name})
	}
	for _, group := range groups {
		keys = append(keys, claimKey{group: group})
	}
	slices.SortFunc(keys, func(a, b claimKey) int { return strings.Compare(a.name(), b.name()) })
	return slices.CompactFunc(keys, func(a, b claimKey) bool { return a.name() == b.name() })
}

// listClaims reads the claims of the namespace from the API server.
func (r *LeaseReconciler) listClaims(
	ctx context.Context, namespace string,
) ([]jumpstarterdevv1alpha1.ExporterClaim, error) {
	var claims jumpstarterdevv1alpha1.ExporterClaimList
	if err := r.APIReader.List(ctx, &claims, client.InNamespace(namespace)); err != nil {
		return nil, fmt.Errorf("listClaims: failed to list exporter claims: %w", err)
	}
	return claims.Items, nil
}

func claimsOf(
	claims []jumpstarterdevv1alpha1.ExporterClaim, lease *jumpstarterdevv1alpha1.Lease,
) []jumpstarterdevv1alpha1.ExporterClaim {
	var own []jumpstarterdevv1alpha1.ExporterClaim
	for i := range claims {
		if claims[i].Spec.LeaseRef.UID == lease.UID {
			own = append(own, claims[i])
		}
	}
	return own
}

// deleteClaim deletes exactly this claim object. A claim with the same name
// that was created later has another UID and stays.
func (r *LeaseReconciler) deleteClaim(ctx context.Context, claim *jumpstarterdevv1alpha1.ExporterClaim) error {
	err := r.Delete(ctx, claim, client.Preconditions{UID: &claim.UID})
	if err == nil || k8serrors.IsNotFound(err) || k8serrors.IsConflict(err) {
		return nil
	}
	return fmt.Errorf("deleteClaim: failed to delete exporter claim %s: %w", claim.Name, err)
}

// rollbackAttempt deletes the claims and removes the marks of a bind that this
// reconcile did not write to the lease status.
func (r *LeaseReconciler) rollbackAttempt(
	ctx context.Context, lease *jumpstarterdevv1alpha1.Lease, state *bindState,
) {
	r.rollbackClaims(ctx, state)
	r.rollbackMarks(ctx, lease, state)
}

// rollbackEnsured deletes the claims that this reconcile created for a bound
// lease, when a read from the API server shows that the lease ended or does
// not exist. The reconcile had an old copy of the lease then.
func (r *LeaseReconciler) rollbackEnsured(
	ctx context.Context, lease *jumpstarterdevv1alpha1.Lease, state *bindState,
) {
	if len(state.ensured) == 0 {
		return
	}
	logger := log.FromContext(ctx)
	holder, err := r.activeLease(ctx, lease.Namespace, jumpstarterdevv1alpha1.ClaimLeaseReference{
		Name: lease.Name, UID: lease.UID,
	})
	if err != nil {
		// Another lease replaces a claim only after it reads that this lease
		// ended, so a claim that stays here blocks nothing for long.
		logger.Error(err, "failed to read the lease of the claims that this reconcile created")
		return
	}
	if holder == nil {
		for _, claim := range state.ensured {
			if err := r.deleteClaim(ctx, claim); err != nil {
				logger.Error(err, "failed to delete a claim of a lease that ended")
			}
		}
	}
	state.ensured = nil
}

// rollbackClaims deletes the claims that this reconcile created.
func (r *LeaseReconciler) rollbackClaims(ctx context.Context, state *bindState) {
	for _, claim := range state.created {
		if err := r.deleteClaim(ctx, claim); err != nil {
			// The next reconcile of the lease starts a new attempt and
			// deletes what is left of this one.
			log.FromContext(ctx).Error(err, "failed to delete a claim of a bind attempt that did not complete")
		}
	}
	state.created = nil
}

// startAttempt starts a bind attempt: it increments status.bindAttempt, reads
// the claims from the API server and deletes those the lease owns from smaller
// attempts. It also removes the marks of smaller attempts. It returns the
// claims of the namespace that are left. A conflict on the status update means
// another reconcile changed the lease; the caller stops.
func (r *LeaseReconciler) startAttempt(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	state *bindState,
) ([]jumpstarterdevv1alpha1.ExporterClaim, error) {
	lease.Status.BindAttempt++
	if err := r.Status().Update(ctx, lease); err != nil {
		lease.Status.BindAttempt--
		return nil, fmt.Errorf("startAttempt: failed to record the bind attempt: %w", err)
	}
	state.attemptStarted = true

	claims, err := r.listClaims(ctx, lease.Namespace)
	if err != nil {
		return nil, err
	}
	if claims, err = r.deleteSmallerAttempts(ctx, lease, claims); err != nil {
		return nil, err
	}
	if err := r.removeLeaseMarks(ctx, lease, lease.Status.BindAttempt-1); err != nil {
		return nil, err
	}
	return claims, nil
}

// deleteSmallerAttempts deletes the claims of the lease that are from an
// attempt before status.bindAttempt and returns the other claims.
func (r *LeaseReconciler) deleteSmallerAttempts(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	claims []jumpstarterdevv1alpha1.ExporterClaim,
) ([]jumpstarterdevv1alpha1.ExporterClaim, error) {
	var left []jumpstarterdevv1alpha1.ExporterClaim
	for i := range claims {
		claim := &claims[i]
		if claim.Spec.LeaseRef.UID == lease.UID && claim.Spec.LeaseRef.Attempt < lease.Status.BindAttempt {
			if err := r.deleteClaim(ctx, claim); err != nil {
				return nil, err
			}
			continue
		}
		left = append(left, *claim)
	}
	return left, nil
}

// deleteLeaseClaims deletes all the claims of an ended lease and then removes
// its marks. status.ended is written before, and it never goes back to false.
func (r *LeaseReconciler) deleteLeaseClaims(ctx context.Context, lease *jumpstarterdevv1alpha1.Lease) error {
	claims, err := r.listClaims(ctx, lease.Namespace)
	if err != nil {
		return err
	}
	for _, claim := range claimsOf(claims, lease) {
		if err := r.deleteClaim(ctx, &claim); err != nil {
			return err
		}
	}
	return r.removeLeaseMarks(ctx, lease, lease.Status.BindAttempt)
}

func (r *LeaseReconciler) newClaim(
	lease *jumpstarterdevv1alpha1.Lease, key claimKey,
) (*jumpstarterdevv1alpha1.ExporterClaim, error) {
	claim := &jumpstarterdevv1alpha1.ExporterClaim{
		ObjectMeta: metav1.ObjectMeta{Name: key.name(), Namespace: lease.Namespace},
		Spec: jumpstarterdevv1alpha1.ExporterClaimSpec{
			Exporter:       key.exporter,
			ExclusionGroup: key.group,
			LeaseRef: jumpstarterdevv1alpha1.ClaimLeaseReference{
				Name:    lease.Name,
				UID:     lease.UID,
				Attempt: lease.Status.BindAttempt,
			},
		},
	}
	if err := controllerutil.SetControllerReference(lease, claim, r.Scheme); err != nil {
		return nil, fmt.Errorf("newClaim: failed to set the owner of exporter claim %s: %w", claim.Name, err)
	}
	return claim, nil
}

// claimHolder reads the lease of a claim from the API server. It returns nil
// when that lease ended or does not exist: the claim is then left over.
func (r *LeaseReconciler) claimHolder(
	ctx context.Context, claim *jumpstarterdevv1alpha1.ExporterClaim,
) (*jumpstarterdevv1alpha1.Lease, error) {
	holder, err := r.activeLease(ctx, claim.Namespace, claim.Spec.LeaseRef)
	if err != nil {
		return nil, fmt.Errorf("claimHolder: failed to get the lease of exporter claim %s: %w", claim.Name, err)
	}
	return holder, nil
}

// createClaim creates the claim of the lease for this key at the current
// attempt. It returns the claim when the lease holds it, with created set when
// this call created it. Otherwise holder is the active lease that holds the
// key, or nil when the holder could not be determined yet; the caller does not
// bind.
func (r *LeaseReconciler) createClaim(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	key claimKey,
) (claim *jumpstarterdevv1alpha1.ExporterClaim, created bool, holder *jumpstarterdevv1alpha1.Lease, err error) {
	// One retry: after a left-over claim is deleted, the create can still
	// lose against another lease.
	for range 2 {
		claim, err := r.newClaim(lease, key)
		if err != nil {
			return nil, false, nil, err
		}
		err = r.Create(ctx, claim)
		if err == nil {
			return claim, true, nil, nil
		}
		if !k8serrors.IsAlreadyExists(err) {
			return nil, false, nil, fmt.Errorf("createClaim: failed to create exporter claim for %s: %w", key, err)
		}

		var existing jumpstarterdevv1alpha1.ExporterClaim
		if err := r.APIReader.Get(ctx, client.ObjectKeyFromObject(claim), &existing); err != nil {
			if k8serrors.IsNotFound(err) {
				continue
			}
			return nil, false, nil, fmt.Errorf("createClaim: failed to get exporter claim for %s: %w", key, err)
		}

		if existing.Spec.LeaseRef.UID == lease.UID {
			switch {
			case existing.Spec.LeaseRef.Attempt == lease.Status.BindAttempt:
				return &existing, false, nil, nil
			case existing.Spec.LeaseRef.Attempt > lease.Status.BindAttempt:
				// A later attempt of this lease is under way; this
				// reconcile is the old one.
				return nil, false, nil, nil
			}
		} else {
			holder, err := r.claimHolder(ctx, &existing)
			if err != nil {
				return nil, false, nil, err
			}
			if holder != nil {
				return nil, false, holder, nil
			}
		}
		if err := r.deleteClaim(ctx, &existing); err != nil {
			return nil, false, nil, err
		}
	}
	return nil, false, nil, nil
}

// createBindClaims creates the claims for a new bind in sorted name order. It
// reports false when a claim is held by another lease; the claims created so
// far are then deleted.
func (r *LeaseReconciler) createBindClaims(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	state *bindState,
	keys []claimKey,
) (ok bool, blocked claimKey, err error) {
	for _, key := range keys {
		claim, created, _, err := r.createClaim(ctx, lease, key)
		if created {
			state.created = append(state.created, claim)
		}
		if err != nil {
			r.rollbackClaims(ctx, state)
			return false, key, err
		}
		if claim == nil {
			r.rollbackClaims(ctx, state)
			return false, key, nil
		}
	}
	return true, claimKey{}, nil
}

// boundClaimsOutcome is the result of ensureBoundClaims.
type boundClaimsOutcome int

const (
	// boundClaimsHeld: the lease has all its claims.
	boundClaimsHeld boundClaimsOutcome = iota
	// boundClaimsConflict: an older lease holds an exporter of this lease.
	boundClaimsConflict
	// boundClaimsRetry: a claim is not decided yet.
	boundClaimsRetry
)

func leaseOlder(a, b *jumpstarterdevv1alpha1.Lease) bool {
	if !a.CreationTimestamp.Equal(&b.CreationTimestamp) {
		return a.CreationTimestamp.Before(&b.CreationTimestamp)
	}
	return a.Name < b.Name
}

func leaseHoldsKey(lease *jumpstarterdevv1alpha1.Lease, key claimKey) bool {
	if key.group != "" {
		return slices.Contains(lease.Status.ExclusionGroups, key.group)
	}
	return slices.Contains(lease.HeldExporterNames(), key.exporter)
}

// leaseIsCurrent reports whether the API server has the lease as it is
// reconciled: it exists, did not end, and has the same resource version. A
// reconcile with an old copy (an old leader, or a cache that is behind) must
// not act as the holder of the exporters in that copy.
func (r *LeaseReconciler) leaseIsCurrent(ctx context.Context, lease *jumpstarterdevv1alpha1.Lease) (bool, error) {
	var current jumpstarterdevv1alpha1.Lease
	if err := r.APIReader.Get(ctx, client.ObjectKeyFromObject(lease), &current); err != nil {
		if k8serrors.IsNotFound(err) {
			return false, nil
		}
		return false, fmt.Errorf("leaseIsCurrent: failed to get lease %s: %w", lease.Name, err)
	}
	return current.UID == lease.UID && !current.Status.Ended &&
		current.ResourceVersion == lease.ResourceVersion, nil
}

// ensureBoundClaims creates the claims and the marks that a bound lease does
// not have: the lease was bound before the controller used claims, or by a
// controller of the previous version during an upgrade. When two leases hold
// one exporter, the older lease gets the claim; the newer lease gets Invalid
// with the reason ExporterConflict and then ends. The caller writes the status
// of the lease when the outcome is boundClaimsConflict.
//
// Before it creates a claim or decides a conflict, it reads the lease from the
// API server. With an old copy of a lease that ended meanwhile, it would take
// the lease that now holds the exporter for the newer of two holders. The
// claims it creates are recorded in state, when there is one.
func (r *LeaseReconciler) ensureBoundClaims(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	claims []jumpstarterdevv1alpha1.ExporterClaim,
	state *bindState,
) (boundClaimsOutcome, error) {
	owned := map[string]bool{}
	for _, claim := range claimsOf(claims, lease) {
		owned[claim.Name] = true
	}

	outcome := boundClaimsHeld
	verified := false
	for _, key := range claimKeys(lease.HeldExporterNames(), lease.Status.ExclusionGroups) {
		if owned[key.name()] {
			continue
		}
		if !verified {
			current, err := r.leaseIsCurrent(ctx, lease)
			if err != nil {
				return boundClaimsRetry, err
			}
			if !current {
				return boundClaimsRetry, nil
			}
			verified = true
		}
		claim, created, holder, err := r.createClaim(ctx, lease, key)
		if created && state != nil {
			state.ensured = append(state.ensured, claim)
		}
		if err != nil {
			return boundClaimsRetry, err
		}
		if claim != nil {
			continue
		}
		switch {
		case holder == nil || !leaseHoldsKey(holder, key):
			// The holder is in a bind attempt that has not completed.
			outcome = boundClaimsRetry
		case leaseOlder(holder, lease):
			setExporterConflict(lease, holder, key)
			return boundClaimsConflict, nil
		default:
			// This lease is the older one. The holder ends and deletes its
			// claim; the next reconcile of this lease then creates it.
			setExporterConflict(holder, lease, key)
			if err := r.Status().Update(ctx, holder); err != nil && !k8serrors.IsConflict(err) {
				return boundClaimsRetry, fmt.Errorf(
					"ensureBoundClaims: failed to update the status of lease %s: %w", holder.Name, err)
			}
			outcome = boundClaimsRetry
		}
	}
	if outcome == boundClaimsHeld {
		if err := r.ensureBoundMarks(ctx, lease, verified); err != nil {
			return boundClaimsRetry, err
		}
	}
	return outcome, nil
}

func setExporterConflict(lease, older *jumpstarterdevv1alpha1.Lease, key claimKey) {
	lease.SetStatusInvalid(jumpstarterdevv1alpha1.LeaseReasonExporterConflict,
		"The older lease %s also holds %s", older.Name, key)
	lease.SetStatusReady(false, jumpstarterdevv1alpha1.LeaseReasonExporterConflict,
		"The older lease %s also holds %s", older.Name, key)
}

// backfillClaims creates the claims of the bound leases of a namespace, oldest
// first. The reconciler does this once for each namespace before it binds a
// lease there, so that after an upgrade no new lease gets the exporter of a
// lease that was bound without a claim.
func (r *LeaseReconciler) backfillClaims(ctx context.Context, namespace string) error {
	r.backfillMu.Lock()
	defer r.backfillMu.Unlock()
	if r.backfilled[namespace] {
		return nil
	}

	activeLeases, err := r.ListActiveLeases(ctx, namespace)
	if err != nil {
		return fmt.Errorf("backfillClaims: failed to list active leases: %w", err)
	}
	claims, err := r.listClaims(ctx, namespace)
	if err != nil {
		return err
	}

	leases := activeLeases.Items
	slices.SortFunc(leases, func(a, b jumpstarterdevv1alpha1.Lease) int {
		if leaseOlder(&a, &b) {
			return -1
		}
		return 1
	})
	for i := range leases {
		lease := &leases[i]
		if lease.Status.Ended || !lease.IsBound() {
			continue
		}
		outcome, err := r.ensureBoundClaims(ctx, lease, claims, nil)
		if err != nil {
			return err
		}
		if outcome == boundClaimsConflict {
			if err := r.Status().Update(ctx, lease); err != nil {
				return fmt.Errorf("backfillClaims: failed to update the status of lease %s: %w", lease.Name, err)
			}
		}
	}

	if r.backfilled == nil {
		r.backfilled = map[string]bool{}
	}
	r.backfilled[namespace] = true
	return nil
}
