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

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	k8serrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// Exporter.status.claimRef is the mark of the lease that gets or holds the
// exporter. Admission rejects a change of spec.exclusionGroup while the mark is
// set, so the group that an assignment used stays the group of the exporter
// from the mark to the end of the lease. The functions in this file are the
// mark part of the bind procedure:
//
//   - A bind attempt marks each exporter it selected, in sorted name order,
//     before it creates a claim. Each mark is a conditional update of an
//     exporter that was read from the API server: the API server puts the mark
//     and a group change in a sequence.
//   - The attempt fails when the exporter has the mark of another active lease
//     or of a later attempt of this lease, or when its exclusion group is not
//     the group of the assignment.
//   - A mark of a lease that ended or does not exist is left over; it is
//     replaced.
//   - A reconcile removes only marks with the UID of its lease and with its own
//     attempt or a smaller one.

// markUpdateRetries is how often a mark is tried again after the exporter
// changed between the read and the conditional update. The exporter service
// writes the heartbeat to the same object.
const markUpdateRetries = 5

// activeLease reads the lease of a claim or of a mark from the API server. It
// returns nil when that lease ended, does not exist, or has another UID.
func (r *LeaseReconciler) activeLease(
	ctx context.Context, namespace string, ref jumpstarterdevv1alpha1.ClaimLeaseReference,
) (*jumpstarterdevv1alpha1.Lease, error) {
	var holder jumpstarterdevv1alpha1.Lease
	if err := r.APIReader.Get(ctx, types.NamespacedName{Namespace: namespace, Name: ref.Name}, &holder); err != nil {
		if k8serrors.IsNotFound(err) {
			return nil, nil
		}
		return nil, fmt.Errorf("activeLease: failed to get lease %s: %w", ref.Name, err)
	}
	if holder.UID != ref.UID || holder.Status.Ended {
		return nil, nil
	}
	return &holder, nil
}

// markExporter writes the mark of this attempt of the lease on one exporter.
// It reports false with the cause when the attempt cannot have the exporter.
func (r *LeaseReconciler) markExporter(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	name, group string,
) (ok bool, cause string, err error) {
	mark := jumpstarterdevv1alpha1.ClaimLeaseReference{
		Name:    lease.Name,
		UID:     lease.UID,
		Attempt: lease.Status.BindAttempt,
	}
	for range markUpdateRetries {
		var exporter jumpstarterdevv1alpha1.Exporter
		if err := r.APIReader.Get(ctx, types.NamespacedName{Namespace: lease.Namespace, Name: name}, &exporter); err != nil {
			if k8serrors.IsNotFound(err) {
				return false, fmt.Sprintf("Exporter %s does not exist any more", name), nil
			}
			return false, "", fmt.Errorf("markExporter: failed to get exporter %s: %w", name, err)
		}
		// The assignment used the group that the cache had. The group can only
		// change while the exporter has no mark, so this read decides.
		if exporter.Spec.ExclusionGroup != group {
			return false, fmt.Sprintf("The exclusion group of exporter %s changed", name), nil
		}
		if ref := exporter.Status.ClaimRef; ref != nil {
			if ref.UID == lease.UID {
				if ref.Attempt > mark.Attempt {
					// A later attempt of this lease is under way; this
					// reconcile is the old one.
					return false, fmt.Sprintf("A later bind attempt has exporter %s", name), nil
				}
				if *ref == mark {
					return true, "", nil
				}
			} else {
				holder, err := r.activeLease(ctx, lease.Namespace, *ref)
				if err != nil {
					return false, "", err
				}
				if holder != nil {
					return false, fmt.Sprintf("Lease %s gets exporter %s", holder.Name, name), nil
				}
			}
		}

		exporter.Status.ClaimRef = &mark
		err := r.Status().Update(ctx, &exporter)
		switch {
		case err == nil:
			return true, "", nil
		case k8serrors.IsConflict(err):
			// The exporter changed after the read. Read it again: its group
			// or its mark can be different now.
			continue
		case k8serrors.IsNotFound(err):
			return false, fmt.Sprintf("Exporter %s does not exist any more", name), nil
		default:
			return false, "", fmt.Errorf("markExporter: failed to mark exporter %s: %w", name, err)
		}
	}
	return false, fmt.Sprintf("Exporter %s changed while the lease marked it", name), nil
}

// markExporters marks the exporters that the assignment selected, by name with
// the exclusion group that the assignment used. It reports false with the
// cause when the attempt cannot have an exporter; the caller then removes the
// marks of the attempt.
func (r *LeaseReconciler) markExporters(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	state *bindState,
	selected map[string]string,
) (ok bool, cause string, err error) {
	names := make([]string, 0, len(selected))
	for name := range selected {
		names = append(names, name)
	}
	slices.Sort(names)
	for _, name := range names {
		// Recorded before the write: a write that returns an error can have
		// succeeded, and the removal checks the mark itself.
		state.marked = append(state.marked, name)
		ok, cause, err := r.markExporter(ctx, lease, name, selected[name])
		if err != nil || !ok {
			return false, cause, err
		}
	}
	return true, "", nil
}

// removeMark removes the mark of the lease from one exporter, when it is from
// this attempt or a smaller one. The mark of a later attempt and the mark of
// another lease stay.
func (r *LeaseReconciler) removeMark(
	ctx context.Context,
	namespace, name string,
	uid types.UID,
	attempt int64,
) error {
	for range markUpdateRetries {
		var exporter jumpstarterdevv1alpha1.Exporter
		if err := r.APIReader.Get(ctx, types.NamespacedName{Namespace: namespace, Name: name}, &exporter); err != nil {
			if k8serrors.IsNotFound(err) {
				return nil
			}
			return fmt.Errorf("removeMark: failed to get exporter %s: %w", name, err)
		}
		ref := exporter.Status.ClaimRef
		if ref == nil || ref.UID != uid || ref.Attempt > attempt {
			return nil
		}
		exporter.Status.ClaimRef = nil
		err := r.Status().Update(ctx, &exporter)
		switch {
		case err == nil, k8serrors.IsNotFound(err):
			return nil
		case k8serrors.IsConflict(err):
			continue
		default:
			return fmt.Errorf("removeMark: failed to remove the mark of exporter %s: %w", name, err)
		}
	}
	return fmt.Errorf("removeMark: exporter %s changed on each of %d tries", name, markUpdateRetries)
}

// rollbackMarks removes the marks that this reconcile wrote.
func (r *LeaseReconciler) rollbackMarks(
	ctx context.Context, lease *jumpstarterdevv1alpha1.Lease, state *bindState,
) {
	for _, name := range state.marked {
		if err := r.removeMark(ctx, lease.Namespace, name, lease.UID, lease.Status.BindAttempt); err != nil {
			// The next attempt of the lease, or its end, removes what is left.
			log.FromContext(ctx).Error(err, "failed to remove a mark of a bind attempt that did not complete")
		}
	}
	state.marked = nil
}

// leaseHasMarks reports whether the cache shows an exporter with a mark of the
// lease.
func (r *LeaseReconciler) leaseHasMarks(ctx context.Context, lease *jumpstarterdevv1alpha1.Lease) (bool, error) {
	var exporters jumpstarterdevv1alpha1.ExporterList
	if err := r.List(ctx, &exporters, client.InNamespace(lease.Namespace)); err != nil {
		return false, fmt.Errorf("leaseHasMarks: failed to list exporters: %w", err)
	}
	for i := range exporters.Items {
		if ref := exporters.Items[i].Status.ClaimRef; ref != nil && ref.UID == lease.UID {
			return true, nil
		}
	}
	return false, nil
}

// removeLeaseMarks removes the marks of the lease with an attempt up to
// maxAttempt from the exporters of the namespace. The exporters are read from
// the API server.
func (r *LeaseReconciler) removeLeaseMarks(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	maxAttempt int64,
) error {
	var exporters jumpstarterdevv1alpha1.ExporterList
	if err := r.APIReader.List(ctx, &exporters, client.InNamespace(lease.Namespace)); err != nil {
		return fmt.Errorf("removeLeaseMarks: failed to list exporters: %w", err)
	}
	for i := range exporters.Items {
		exporter := &exporters.Items[i]
		ref := exporter.Status.ClaimRef
		if ref == nil || ref.UID != lease.UID || ref.Attempt > maxAttempt {
			continue
		}
		if err := r.removeMark(ctx, lease.Namespace, exporter.Name, lease.UID, maxAttempt); err != nil {
			return err
		}
	}
	return nil
}

// removeStrayMarks removes the marks of a bound lease from the exporters that
// it does not hold. An old leader can write such a mark after the bind. The
// cache finds them; a mark that the cache does not show yet goes with the
// reconcile that its write causes, or when the lease ends.
func (r *LeaseReconciler) removeStrayMarks(ctx context.Context, lease *jumpstarterdevv1alpha1.Lease) error {
	var exporters jumpstarterdevv1alpha1.ExporterList
	if err := r.List(ctx, &exporters, client.InNamespace(lease.Namespace)); err != nil {
		return fmt.Errorf("removeStrayMarks: failed to list exporters: %w", err)
	}
	held := lease.HeldExporterNames()
	for i := range exporters.Items {
		exporter := &exporters.Items[i]
		ref := exporter.Status.ClaimRef
		if ref == nil || ref.UID != lease.UID || slices.Contains(held, exporter.Name) {
			continue
		}
		if err := r.removeMark(ctx, lease.Namespace, exporter.Name, lease.UID, lease.Status.BindAttempt); err != nil {
			return err
		}
	}
	return nil
}

// removeDeletedLeaseMarks removes the marks that name a lease that does not
// exist any more. A lease that is deleted before it ends leaves them.
func (r *LeaseReconciler) removeDeletedLeaseMarks(ctx context.Context, key types.NamespacedName) error {
	var exporters jumpstarterdevv1alpha1.ExporterList
	if err := r.List(ctx, &exporters, client.InNamespace(key.Namespace)); err != nil {
		return fmt.Errorf("removeDeletedLeaseMarks: failed to list exporters: %w", err)
	}
	for i := range exporters.Items {
		exporter := &exporters.Items[i]
		ref := exporter.Status.ClaimRef
		if ref == nil || ref.Name != key.Name {
			continue
		}
		// A new lease can have the name of the deleted one.
		holder, err := r.activeLease(ctx, key.Namespace, *ref)
		if err != nil {
			return err
		}
		if holder != nil {
			continue
		}
		if err := r.removeMark(ctx, key.Namespace, exporter.Name, ref.UID, ref.Attempt); err != nil {
			return err
		}
	}
	return nil
}

// ensureBoundMarks writes the mark of a bound lease on the exporters that it
// holds and that do not have it: the lease was bound before the controller
// used marks. The lease has the claims of these exporters, so the mark of
// another lease is replaced. verified says that a read from the API server
// already showed the lease as it is reconciled.
func (r *LeaseReconciler) ensureBoundMarks(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	verified bool,
) error {
	mark := jumpstarterdevv1alpha1.ClaimLeaseReference{
		Name:    lease.Name,
		UID:     lease.UID,
		Attempt: lease.Status.BindAttempt,
	}
	for _, name := range lease.HeldExporterNames() {
		var cached jumpstarterdevv1alpha1.Exporter
		if err := r.Get(ctx, types.NamespacedName{Namespace: lease.Namespace, Name: name}, &cached); err != nil {
			if k8serrors.IsNotFound(err) {
				continue
			}
			return fmt.Errorf("ensureBoundMarks: failed to get exporter %s: %w", name, err)
		}
		if ref := cached.Status.ClaimRef; ref != nil && *ref == mark {
			continue
		}
		if !verified {
			current, err := r.leaseIsCurrent(ctx, lease)
			if err != nil {
				return err
			}
			if !current {
				return nil
			}
			verified = true
		}
		if err := r.writeBoundMark(ctx, lease.Namespace, name, mark); err != nil {
			return err
		}
	}
	return nil
}

func (r *LeaseReconciler) writeBoundMark(
	ctx context.Context,
	namespace, name string,
	mark jumpstarterdevv1alpha1.ClaimLeaseReference,
) error {
	for range markUpdateRetries {
		var exporter jumpstarterdevv1alpha1.Exporter
		if err := r.APIReader.Get(ctx, types.NamespacedName{Namespace: namespace, Name: name}, &exporter); err != nil {
			if k8serrors.IsNotFound(err) {
				return nil
			}
			return fmt.Errorf("writeBoundMark: failed to get exporter %s: %w", name, err)
		}
		if ref := exporter.Status.ClaimRef; ref != nil && *ref == mark {
			return nil
		}
		exporter.Status.ClaimRef = &mark
		err := r.Status().Update(ctx, &exporter)
		switch {
		case err == nil, k8serrors.IsNotFound(err):
			return nil
		case k8serrors.IsConflict(err):
			continue
		default:
			return fmt.Errorf("writeBoundMark: failed to mark exporter %s: %w", name, err)
		}
	}
	// The next reconcile of the lease tries again.
	log.FromContext(ctx).Info("exporter changed on each try to mark it", "exporter", name)
	return nil
}
