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
	"fmt"
	"slices"
	"strconv"
	"strings"
	"sync"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	jmpmetrics "github.com/jumpstarter-dev/jumpstarter/controller/internal/metrics"
	corev1 "k8s.io/api/core/v1"
	k8serrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/builder"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/event"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/predicate"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
)

// LeaseReconciler reconciles a Lease object
type LeaseReconciler struct {
	client.Client
	// APIReader reads from the API server instead of the cache. See
	// ListActiveLeases.
	APIReader client.Reader
	Scheme    *runtime.Scheme

	// StartedAt is when this controller process started. A bound member is
	// not taken for lost because its exporter is offline until the offline
	// time limit has passed since then (offlineGrace). The zero value is the
	// time the process loaded this package.
	StartedAt time.Time

	// backfilled has the namespaces whose bound leases got their claims
	// (backfillClaims).
	backfillMu sync.Mutex
	backfilled map[string]bool
}

// ApprovedExporter represents an exporter that has been approved for leasing,
// along with its associated policy and any existing lease.
type ApprovedExporter struct {
	// Exporter is the approved exporter
	Exporter jumpstarterdevv1alpha1.Exporter
	// ExistingLease is a pointer to any existing lease for this exporter, or nil if none exists
	ExistingLease *jumpstarterdevv1alpha1.Lease
	// Policy represents the access policy that approved this exporter
	Policy jumpstarterdevv1alpha1.Policy
}

// +kubebuilder:rbac:groups=jumpstarter.dev,resources=leases,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=leases/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=leases/finalizers,verbs=update
// Reconcile is part of the main kubernetes reconciliation loop which aims to
// move the current state of the cluster closer to the desired state.
// TODO(user): Modify the Reconcile function to compare the state specified by
// the Lease object against the actual cluster state, and then
// perform operations to make the cluster state reflect the state specified by
// the user.
//
// For more details, check Reconcile and its Result here:
// - https://pkg.go.dev/sigs.k8s.io/controller-runtime@v0.18.4/pkg/reconcile
func (r *LeaseReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)
	ctx = ctrl.LoggerInto(ctx, logger)

	var lease jumpstarterdevv1alpha1.Lease
	if err := r.Get(ctx, req.NamespacedName, &lease); err != nil {
		if k8serrors.IsNotFound(err) {
			// A lease that is deleted before it ends leaves its marks. The
			// garbage collector deletes its claims.
			return ctrl.Result{}, r.removeDeletedLeaseMarks(ctx, req.NamespacedName)
		}
		return ctrl.Result{}, fmt.Errorf("Reconcile: unable to get lease: %w", err)
	}

	leaseLogValues := []any{"lease_id", lease.Name, "client", lease.Spec.ClientRef.Name}
	if lease.Spec.ExporterRef != nil {
		leaseLogValues = append(leaseLogValues, "exporter", lease.Spec.ExporterRef.Name)
	}
	for k, v := range lease.Spec.Context {
		leaseLogValues = append(leaseLogValues, k, v)
	}
	logger = logger.WithValues(leaseLogValues...)
	ctx = ctrl.LoggerInto(ctx, logger)

	var result ctrl.Result
	priorBound := lease.IsBound()
	priorUnsatisfiable := meta.IsStatusConditionTrue(
		lease.Status.Conditions,
		string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable),
	)

	// The claims and the marks of a bind count only once the status update
	// below has written the bind. On every other way out they are removed,
	// except when it is not known whether that update was applied.
	state := &bindState{}
	keep := false
	defer func() {
		if !keep {
			cleanupCtx := context.WithoutCancel(ctx)
			r.rollbackAttempt(cleanupCtx, &lease, state)
			r.rollbackEnsured(cleanupCtx, &lease, state)
		}
	}()

	if err := r.reconcileStatusMembers(ctx, &result, &lease, state); err != nil {
		return RequeueConflict(logger, result, err)
	}

	if err := r.reconcileStatusBeginEndTimes(ctx, &lease); err != nil {
		return result, err
	}

	if err := r.reconcileStatusEnded(ctx, &result, &lease); err != nil {
		return result, err
	}

	// Recompute the effective shared-with set before persisting status so the
	// derived Status.SharedWith is saved by the single Status().Update below.
	if err := r.reconcileSharedWithPolicies(ctx, &lease); err != nil {
		return result, err
	}

	if err := r.Status().Update(ctx, &lease); err != nil {
		// Only a rejection says that the bind was not written. After any
		// other error (a timeout, for example) the API server can have
		// applied the update: the claims and the marks then belong to a
		// bound lease and must stay. If the update was not applied, the next
		// reconcile starts a new attempt, which removes them.
		keep = !k8serrors.IsConflict(err) && !k8serrors.IsInvalid(err) && !k8serrors.IsNotFound(err)
		return RequeueConflict(logger, result, err)
	}
	keep = true

	// status.ended is written and never goes back to false: now the claims
	// can go. The ended label is set only after, so a failure here is retried.
	if lease.Status.Ended {
		if err := r.deleteLeaseClaims(ctx, &lease); err != nil {
			return result, err
		}
	}

	// Record acquisition only after status is persisted so a failed Update
	// cannot double-count on requeue (success or failure).
	recordLeaseAcquisitionTransition(ctx, &lease, priorBound, priorUnsatisfiable)

	if lease.Labels == nil {
		lease.Labels = make(map[string]string)
	}
	if lease.Status.Ended {
		lease.Labels[string(jumpstarterdevv1alpha1.LeaseLabelEnded)] = jumpstarterdevv1alpha1.LeaseLabelEndedValue
	}

	if err := r.reconcileOwnerReferences(ctx, &lease); err != nil {
		return result, err
	}

	if err := r.Update(ctx, &lease); err != nil {
		return RequeueConflict(logger, result, fmt.Errorf("Reconcile: failed to update lease metadata: %w", err))
	}

	return result, nil
}

// reconcileOwnerReferences gives a scalar lease its exporter as controller
// owner. A member-form lease has no owner reference to an exporter: the
// garbage collector deletes an object when all its owners are gone, and such a
// lease stays when its exporters are deleted (the exporter watch then makes
// their members Lost).
func (r *LeaseReconciler) reconcileOwnerReferences(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
) error {
	if lease.IsMemberForm() {
		// An earlier build of this controller made the exporters of the
		// bound members and the client owners of the lease.
		lease.OwnerReferences = slices.DeleteFunc(lease.OwnerReferences, func(ref metav1.OwnerReference) bool {
			return strings.HasPrefix(ref.APIVersion, jumpstarterdevv1alpha1.GroupVersion.Group+"/") &&
				(ref.Kind == "Exporter" || ref.Kind == "Client")
		})
		return nil
	}

	if lease.Status.ExporterRef == nil {
		return nil
	}
	var exporter jumpstarterdevv1alpha1.Exporter
	if err := r.Get(ctx, types.NamespacedName{
		Namespace: lease.Namespace,
		Name:      lease.Status.ExporterRef.Name,
	}, &exporter); err != nil {
		return err
	}
	if err := controllerutil.SetControllerReference(&exporter, lease, r.Scheme); err != nil {
		return fmt.Errorf("Reconcile: failed to update lease controller reference: %w", err)
	}
	return nil
}

// also manages EndTime and LeaseConditionTypeReady
// nolint:unparam
func (r *LeaseReconciler) reconcileStatusEnded(
	ctx context.Context,
	result *ctrl.Result,
	lease *jumpstarterdevv1alpha1.Lease,
) error {

	now := time.Now()
	if !lease.Status.Ended {
		// if lease has status condition unsatisfiable or invalid, we mark it as ended to avoid reprocessing
		if meta.IsStatusConditionTrue(lease.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable)) ||
			meta.IsStatusConditionTrue(lease.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypeInvalid)) {
			lease.Status.Ended = true
			lease.Status.EndTime = &metav1.Time{Time: now}
			return nil
		} else if lease.Spec.Release {
			lease.Release(ctx)
			return nil
		} else if lease.Status.BeginTime != nil {
			var expiration time.Time
			if lease.Spec.EndTime != nil {
				// expires at Spec.EndTime when specified
				expiration = lease.Spec.EndTime.Time
			} else if lease.Spec.BeginTime != nil && lease.Spec.Duration != nil {
				// expires at Spec.BeginTime + Spec.Duration - scheduled lease
				expiration = lease.Spec.BeginTime.Add(lease.Spec.Duration.Duration)
			} else if lease.Spec.Duration != nil {
				// expires at actual BeginTime + Spec.Duration - immediate lease
				expiration = lease.Status.BeginTime.Add(lease.Spec.Duration.Duration)
			}

			if expiration.Before(now) {
				lease.Expire(ctx)
				return nil
			}
			// A sooner requeue stays, for example that of a bound lease
			// that waits for a claim.
			requeueWithin(result, expiration.Sub(now))
			return nil
		}

	}
	return nil
}

// nolint:unparam
func (r *LeaseReconciler) reconcileStatusBeginEndTimes(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
) error {
	if lease.Status.BeginTime == nil && lease.IsBound() {
		logger := log.FromContext(ctx)
		logger.Info("Updating begin time for lease", "lease", lease.Name, "exporter", lease.GetExporterName(), "client", lease.GetClientName())
		now := time.Now()
		lease.Status.BeginTime = &metav1.Time{Time: now}
		// A member-form lease is Ready only after the beforeLease hooks of
		// its members (reconcileMemberConditions).
		if !lease.IsMemberForm() {
			lease.SetStatusReady(true, "Ready", "An exporter has been acquired for the client")
		}
	}

	return nil
}

// leaseAcquisitionTransitionResult returns the metric result for a persisted
// status transition, or ("", false) when no acquisition should be recorded.
// Recording is based on the pre-reconcile persisted snapshot so requeues after
// a successful Status().Update do not double-count.
func leaseAcquisitionTransitionResult(
	priorExporterRef *corev1.LocalObjectReference,
	priorUnsatisfiable bool,
	lease *jumpstarterdevv1alpha1.Lease,
) (string, bool) {
	return leaseAcquisitionResult(priorExporterRef != nil, priorUnsatisfiable, lease)
}

// leaseAcquisitionResult is leaseAcquisitionTransitionResult for both lease
// forms: a member-form lease is acquired when its members bind.
func leaseAcquisitionResult(
	priorBound bool,
	priorUnsatisfiable bool,
	lease *jumpstarterdevv1alpha1.Lease,
) (string, bool) {
	if lease == nil {
		return "", false
	}
	if !priorBound && lease.IsBound() {
		return jmpmetrics.ResultSuccess, true
	}
	nowUnsatisfiable := meta.IsStatusConditionTrue(
		lease.Status.Conditions,
		string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable),
	)
	if !priorUnsatisfiable && nowUnsatisfiable {
		return jmpmetrics.ResultFailure, true
	}
	return "", false
}

func recordLeaseAcquisitionTransition(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	priorBound bool,
	priorUnsatisfiable bool,
) {
	result, ok := leaseAcquisitionResult(priorBound, priorUnsatisfiable, lease)
	if !ok {
		return
	}
	recordLeaseAcquisition(ctx, lease, result)
}

func recordLeaseAcquisition(ctx context.Context, lease *jumpstarterdevv1alpha1.Lease, result string) {
	exemplars := map[string]string{}
	if lease != nil {
		exemplars["lease_id"] = lease.Name
		if lease.Spec.ClientRef.Name != "" {
			exemplars["client"] = lease.Spec.ClientRef.Name
		}
		if lease.IsMemberForm() {
			// The number of members is recorded with the acquisition; it is
			// not a metric label.
			members := len(lease.BoundExporterNames())
			exemplars["lease_members"] = strconv.Itoa(members)
			log.FromContext(ctx).Info("Lease acquisition recorded",
				"result", result, "lease_members", members)
		}
	}
	jmpmetrics.Default.RecordAcquisition(ctx, result, exemplars)
}

// attachMatchingPolicies attaches the matching policies to the list of online exporters
// if the exporter matches the policy and the client matches the policy's client selector
// the exporter is approved for leasing
func (r *LeaseReconciler) attachMatchingPolicies(ctx context.Context, lease *jumpstarterdevv1alpha1.Lease, onlineExporters []jumpstarterdevv1alpha1.Exporter) ([]ApprovedExporter, []string, error) {
	var approvedExporters []ApprovedExporter

	var policies jumpstarterdevv1alpha1.ExporterAccessPolicyList
	if err := r.List(ctx, &policies,
		client.InNamespace(lease.Namespace),
	); err != nil {
		return nil, nil, fmt.Errorf("attachMatchingPolicies: failed to list exporter access policies: %w", err)
	}

	// If there are no policies, we just approve all online exporters
	if len(policies.Items) == 0 {
		for _, exporter := range onlineExporters {
			approvedExporters = append(approvedExporters, ApprovedExporter{
				Exporter: exporter,
				Policy: jumpstarterdevv1alpha1.Policy{
					Priority:   0,
					SpotAccess: false,
				},
			})
		}
		return approvedExporters, nil, nil
	}
	// If policies exist: get the client to obtain the metadata necessary for policy matching
	var jclient jumpstarterdevv1alpha1.Client
	if err := r.Get(ctx, types.NamespacedName{
		Namespace: lease.Namespace,
		Name:      lease.Spec.ClientRef.Name,
	}, &jclient); err != nil {
		return nil, nil, fmt.Errorf("attachMatchingPolicies: failed to get client: %w", err)
	}

	seenDescriptions := make(map[string]bool)
	var unmatchedDescriptions []string

	for _, exporter := range onlineExporters {
		for _, policy := range policies.Items {
			exporterSelector, err := metav1.LabelSelectorAsSelector(&policy.Spec.ExporterSelector)
			if err != nil {
				return nil, nil, fmt.Errorf("attachMatchingPolicies: failed to convert exporter selector: %w", err)
			}
			if exporterSelector.Matches(labels.Set(exporter.Labels)) {
				for _, p := range policy.Spec.Policies {
					clientMatched := false
					for _, from := range p.From {
						clientSelector, err := metav1.LabelSelectorAsSelector(&from.ClientSelector)
						if err != nil {
							return nil, nil, fmt.Errorf("attachMatchingPolicies: failed to convert client selector: %w", err)
						}
						if clientSelector.Matches(labels.Set(jclient.Labels)) {
							clientMatched = true
							if p.MaximumDuration != nil {
								// Calculate requested duration (may be from explicit Duration or computed from times)
								requestedDuration := time.Duration(0)
								if lease.Spec.Duration != nil {
									requestedDuration = lease.Spec.Duration.Duration
								} else if lease.Spec.BeginTime != nil && lease.Spec.EndTime != nil {
									requestedDuration = lease.Spec.EndTime.Sub(lease.Spec.BeginTime.Time)
								}
								if requestedDuration > p.MaximumDuration.Duration {
									// TODO: we probably should keep this on the list of approved exporters
									// but mark as excessive duration so we can report it on the status
									// of lease if no other options exist
									continue
								}
							}
							approvedExporters = append(approvedExporters, ApprovedExporter{
								Exporter: exporter,
								Policy:   p,
							})
						}
					}
					if !clientMatched && p.Description != "" && !seenDescriptions[p.Description] {
						seenDescriptions[p.Description] = true
						unmatchedDescriptions = append(unmatchedDescriptions, p.Description)
					}
				}
			}
		}
	}
	return approvedExporters, unmatchedDescriptions, nil
}

func (r *LeaseReconciler) reconcileSharedWithPolicies(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
) error {
	// Status.SharedWith is derived state: the effective, policy-filtered access
	// set. It is recomputed on every reconcile so the controller never mutates the
	// owner-controlled Spec.SharedWith.
	if len(lease.Spec.SharedWith) == 0 || lease.Status.Ended {
		lease.Status.SharedWith = nil
		return nil
	}

	if !lease.IsBound() {
		if lease.IsMemberForm() {
			// The list is empty before the bind. This function runs again
			// before the status update that binds the members, so that update
			// writes the list that the policies permit.
			lease.Status.SharedWith = nil
			return nil
		}
		// Scalar lease: without an assigned exporter, exporter-scoped policies
		// cannot be evaluated yet; grant the desired set for now. It is
		// filtered once an exporter is bound.
		lease.Status.SharedWith = slices.Clone(lease.Spec.SharedWith)
		return nil
	}

	var policies jumpstarterdevv1alpha1.ExporterAccessPolicyList
	if err := r.List(ctx, &policies, client.InNamespace(lease.Namespace)); err != nil {
		return fmt.Errorf("reconcileSharedWithPolicies: failed to list policies: %w", err)
	}

	// No policies configured means sharing is unrestricted.
	if len(policies.Items) == 0 {
		lease.Status.SharedWith = slices.Clone(lease.Spec.SharedWith)
		return nil
	}

	// A shared client must be allowed to use every exporter that the lease
	// gives access to: the exporter of a scalar lease, or each bound member.
	var exporters []jumpstarterdevv1alpha1.Exporter
	for _, name := range lease.BoundExporterNames() {
		var exporter jumpstarterdevv1alpha1.Exporter
		if err := r.Get(ctx, types.NamespacedName{
			Namespace: lease.Namespace,
			Name:      name,
		}, &exporter); err != nil {
			return fmt.Errorf("reconcileSharedWithPolicies: failed to get exporter: %w", err)
		}
		exporters = append(exporters, exporter)
	}

	logger := log.FromContext(ctx)
	var allowed []string
	for _, clientName := range lease.Spec.SharedWith {
		var jclient jumpstarterdevv1alpha1.Client
		if err := r.Get(ctx, types.NamespacedName{
			Namespace: lease.Namespace,
			Name:      clientName,
		}, &jclient); err != nil {
			if k8serrors.IsNotFound(err) {
				logger.Info("excluding shared client from effective access: not found", "client", clientName)
				continue
			}
			return fmt.Errorf("reconcileSharedWithPolicies: failed to get shared client %s: %w", clientName, err)
		}
		allowedByPolicy := true
		for i := range exporters {
			exporterAllowed, err := jumpstarterdevv1alpha1.ClientAllowedByPolicy(policies.Items, &exporters[i], &jclient)
			if err != nil {
				// A malformed policy selector must not silently exclude the shared client;
				// return the error so the reconcile is retried and the misconfiguration surfaces.
				return fmt.Errorf("reconcileSharedWithPolicies: failed to evaluate access policy for client %s: %w", clientName, err)
			}
			if !exporterAllowed {
				allowedByPolicy = false
				break
			}
		}
		if allowedByPolicy {
			allowed = append(allowed, clientName)
		} else {
			logger.Info("excluding shared client from effective access: denied by policy", "client", clientName)
		}
	}
	lease.Status.SharedWith = allowed
	return nil
}

// ListMatchingExporters returns a list of exporters that match the selector of the lease
func (r *LeaseReconciler) ListMatchingExporters(ctx context.Context, lease *jumpstarterdevv1alpha1.Lease,
	selector labels.Selector) (*jumpstarterdevv1alpha1.ExporterList, error) {

	var matchingExporters jumpstarterdevv1alpha1.ExporterList
	if err := r.List(
		ctx,
		&matchingExporters,
		client.InNamespace(lease.Namespace),
		client.MatchingLabelsSelector{Selector: selector},
	); err != nil {
		return nil, fmt.Errorf("ListMatchingExporters: failed to list exporters matching selector: %w", err)
	}
	return &matchingExporters, nil
}

// ListActiveLeases returns the active leases in the namespace, read from the
// API server. Exporter selection treats every exporter these leases hold as
// taken, and the cache sees a status write only some time after it succeeds: a
// lease reconciled in that window would be assigned the exporter the previous
// reconcile has just assigned to another lease. Both writes would succeed, as
// they go to different Lease objects.
//
// A fresh read is enough while assignments are made one at a time, by a single
// controller replica with a single worker. It does not fence a former leader
// that keeps reconciling after losing leadership.
func (r *LeaseReconciler) ListActiveLeases(ctx context.Context, namespace string) (*jumpstarterdevv1alpha1.LeaseList, error) {
	var activeLeases jumpstarterdevv1alpha1.LeaseList
	if err := r.APIReader.List(
		ctx,
		&activeLeases,
		client.InNamespace(namespace),
		MatchingActiveLeases(),
	); err != nil {
		return nil, err
	}
	return &activeLeases, nil
}

// orderAvailableExporters orders the exporters in the following order
// 1. Not being leased
// 2. Not accessible under spot access
// 3. Highest priority
// 4. Alphabetically by exporter name

func orderApprovedExporters(exporters []ApprovedExporter) []ApprovedExporter {
	// Order by lease status, priority, spot access, and name

	cmpFunc := func(a, b ApprovedExporter) int {
		// If one of the exporters has an existing lease, we want to prioritize the one that doesn't
		if a.ExistingLease != nil && b.ExistingLease == nil {
			return 1
		} else if a.ExistingLease == nil && b.ExistingLease != nil {
			return -1
		}

		// We want spot access policies to be later on the returned array
		if a.Policy.SpotAccess != b.Policy.SpotAccess {
			if a.Policy.SpotAccess {
				return 1
			}
			return -1
		}

		// We want the highest priority to be first
		if a.Policy.Priority != b.Policy.Priority {
			return b.Policy.Priority - a.Policy.Priority
		}

		// If the priority is the same, we want to sort by exporter name
		return strings.Compare(a.Exporter.Name, b.Exporter.Name)
	}

	slices.SortFunc(exporters, cmpFunc)

	return exporters
}

// filterOutLeasedExporters filters out the exporters that are already leased
func filterOutLeasedExporters(exporters []ApprovedExporter) []ApprovedExporter {
	// Exclude exporter that are already leased and non-takeable
	return slices.DeleteFunc(slices.Clone(exporters), func(ae ApprovedExporter) bool {
		existingLease := ae.ExistingLease
		if existingLease == nil {
			return false
		}

		weHaveNonSpotAccess := !ae.Policy.SpotAccess

		// There is an existing lease, but, if it's spot access we can take it
		if weHaveNonSpotAccess && ae.ExistingLease.Status.SpotAccess {
			return false
		}

		// ok, there is an existing lease, and it's not spot access, we can't take it
		return true
	})

}

// filterOutNotReadyExporters filters out exporters that are not in a ready state
// to accept new leases. Only exporters with Available status (or unset status for
// backwards compatibility with old exporters) are considered ready.
//
// An exporter that records its lease hooks is also not ready while its record
// says the previous lease still needs its afterLease hook, so the next lease
// never gets a board that still owes cleanup. This does not depend on the
// status it reported: status reports are asynchronous, and one sent before the
// record changed can arrive after it. An exporter that does not record its
// hooks (for example one rolled back to an older version) is not held by a
// record it left behind.
func filterOutNotReadyExporters(approvedExporters []ApprovedExporter) []ApprovedExporter {
	return slices.DeleteFunc(
		slices.Clone(approvedExporters),
		func(approvedExporter ApprovedExporter) bool {
			exporterStatus := approvedExporter.Exporter.Status
			if exporterStatus.RecordsLeaseHooks && exporterStatus.LeaseHooks.OwesAfterLease() {
				return true
			}
			// Allow Available or unset (backwards compat with old exporters that don't report status)
			return exporterStatus.ExporterStatusValue != jumpstarterdevv1alpha1.ExporterStatusAvailable &&
				exporterStatus.ExporterStatusValue != jumpstarterdevv1alpha1.ExporterStatusUnspecified &&
				exporterStatus.ExporterStatusValue != ""
		},
	)
}

// filterOutDisabledExporters removes exporters that have spec.enabled set to false,
// unless allowDisabled is true. Exporters with nil Enabled (backward compatibility)
// or Enabled=true are always kept.
func filterOutDisabledExporters(exporters []jumpstarterdevv1alpha1.Exporter, allowDisabled bool) []jumpstarterdevv1alpha1.Exporter {
	if allowDisabled {
		return slices.Clone(exporters)
	}
	return slices.DeleteFunc(
		slices.Clone(exporters),
		func(exporter jumpstarterdevv1alpha1.Exporter) bool {
			return !exporter.IsEnabled()
		},
	)
}

// filterOutOfflineExporters filters out the exporters that are not online
func filterOutOfflineExporters(approvedExporters []ApprovedExporter) []ApprovedExporter {
	onlineExporters := slices.DeleteFunc(
		slices.Clone(approvedExporters),
		func(approvedExporter ApprovedExporter) bool {
			return !meta.IsStatusConditionTrue(
				approvedExporter.Exporter.Status.Conditions,
				string(jumpstarterdevv1alpha1.ExporterConditionTypeRegistered),
			) || !meta.IsStatusConditionTrue(
				approvedExporter.Exporter.Status.Conditions,
				string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
			)
		},
	)
	return onlineExporters
}

// isLeaseEnded checks whether a lease object carries the ended label.
func isLeaseEnded(obj client.Object) bool {
	if v, ok := obj.GetLabels()[string(jumpstarterdevv1alpha1.LeaseLabelEnded)]; ok {
		return v == jumpstarterdevv1alpha1.LeaseLabelEndedValue
	}
	return false
}

// skipEndedPredicate returns a predicate that filters out leases carrying the
// ended label. Leases without the label are admitted so the reconciler can
// backfill it when Status.Ended is true but the label write was lost.
func skipEndedPredicate() predicate.Funcs {
	return predicate.Funcs{
		CreateFunc: func(e event.CreateEvent) bool {
			return !isLeaseEnded(e.Object)
		},
		UpdateFunc: func(e event.UpdateEvent) bool {
			return !isLeaseEnded(e.ObjectNew)
		},
		DeleteFunc: func(e event.DeleteEvent) bool {
			return true
		},
		GenericFunc: func(e event.GenericEvent) bool {
			return !isLeaseEnded(e.Object)
		},
	}
}

const maxPendingRequeue = 30 * time.Second

func pendingRequeueAfter(lease *jumpstarterdevv1alpha1.Lease) time.Duration {
	cond := meta.FindStatusCondition(
		lease.Status.Conditions,
		string(jumpstarterdevv1alpha1.LeaseConditionTypePending),
	)
	if cond == nil {
		return time.Second
	}
	elapsed := time.Since(cond.LastTransitionTime.Time)
	backoff := time.Second
	for backoff < maxPendingRequeue && backoff < elapsed {
		backoff *= 2
	}
	if backoff > maxPendingRequeue {
		backoff = maxPendingRequeue
	}
	return backoff
}

func exporterChangedForPendingLeases() predicate.Funcs {
	meaningful := func(oldExp, newExp *jumpstarterdevv1alpha1.Exporter) bool {
		if oldExp.Status.ExporterStatusValue != newExp.Status.ExporterStatusValue {
			return true
		}
		oldOnline := meta.IsStatusConditionTrue(oldExp.Status.Conditions,
			string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline))
		newOnline := meta.IsStatusConditionTrue(newExp.Status.Conditions,
			string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline))
		if oldOnline != newOnline {
			return true
		}
		// A bound member is lost when its exporter unregisters.
		if exporterOnline(oldExp) != exporterOnline(newExp) {
			return true
		}
		oldLeased := oldExp.Status.LeaseRef != nil
		newLeased := newExp.Status.LeaseRef != nil
		if oldLeased != newLeased {
			return true
		}
		// The lease of a mark removes the marks it must not have, and an
		// exporter without a mark can be assigned.
		if !claimRefEqual(oldExp.Status.ClaimRef, newExp.Status.ClaimRef) {
			return true
		}
		// Readiness also depends on whether the exporter records its lease hooks
		// and still owes an afterLease hook (filterOutNotReadyExporters).
		if oldExp.Status.RecordsLeaseHooks != newExp.Status.RecordsLeaseHooks ||
			oldExp.Status.LeaseHooks.OwesAfterLease() != newExp.Status.LeaseHooks.OwesAfterLease() {
			return true
		}
		if !labels.Equals(labels.Set(oldExp.Labels), labels.Set(newExp.Labels)) {
			return true
		}
		return false
	}
	return predicate.Funcs{
		CreateFunc: func(e event.CreateEvent) bool {
			return true
		},
		UpdateFunc: func(e event.UpdateEvent) bool {
			oldExp := e.ObjectOld.(*jumpstarterdevv1alpha1.Exporter)
			newExp := e.ObjectNew.(*jumpstarterdevv1alpha1.Exporter)
			return meaningful(oldExp, newExp)
		},
		// The member of a deleted exporter is lost.
		DeleteFunc: func(e event.DeleteEvent) bool {
			return true
		},
		GenericFunc: func(e event.GenericEvent) bool {
			return true
		},
	}
}

func claimRefEqual(a, b *jumpstarterdevv1alpha1.ClaimLeaseReference) bool {
	if a == nil || b == nil {
		return a == b
	}
	return *a == *b
}

func (r *LeaseReconciler) mapExporterToLeases(ctx context.Context, obj client.Object) []reconcile.Request {
	exporter := obj.(*jumpstarterdevv1alpha1.Exporter)

	var leaseList jumpstarterdevv1alpha1.LeaseList
	if err := r.List(ctx, &leaseList, client.InNamespace(exporter.Namespace), MatchingActiveLeases()); err != nil {
		return nil
	}

	var requests []reconcile.Request
	for i := range leaseList.Items {
		lease := &leaseList.Items[i]
		marked := exporter.Status.ClaimRef != nil && exporter.Status.ClaimRef.UID == lease.UID
		if marked || leaseFollowsExporter(lease, exporter) {
			requests = append(requests, reconcile.Request{
				NamespacedName: types.NamespacedName{Name: lease.Name, Namespace: lease.Namespace},
			})
		}
	}
	return requests
}

// leaseFollowsExporter reports whether a change of the exporter can change the
// lease: a pending lease that can select the exporter, or a member-form lease
// that has it as a bound member (the member is lost when the exporter goes
// offline or is deleted, and Ready waits for its beforeLease hook).
func leaseFollowsExporter(lease *jumpstarterdevv1alpha1.Lease, exporter *jumpstarterdevv1alpha1.Exporter) bool {
	if lease.IsMemberForm() && lease.IsBound() {
		return !lease.Status.Ended && lease.HasBoundExporter(exporter.Name)
	}
	if !meta.IsStatusConditionTrue(lease.Status.Conditions,
		string(jumpstarterdevv1alpha1.LeaseConditionTypePending)) {
		return false
	}

	if lease.IsMemberForm() {
		for i := range lease.Spec.Members {
			member := &lease.Spec.Members[i]
			if member.Excluded {
				continue
			}
			if member.ExporterRef != nil {
				if member.ExporterRef.Name == exporter.Name {
					return true
				}
				continue
			}
			if member.Selector == nil {
				continue
			}
			selector, err := metav1.LabelSelectorAsSelector(member.Selector)
			if err == nil && selector.Matches(labels.Set(exporter.Labels)) {
				return true
			}
		}
		return false
	}

	if lease.Spec.ExporterRef != nil && lease.Spec.ExporterRef.Name == exporter.Name {
		return true
	}
	selector, err := metav1.LabelSelectorAsSelector(&lease.Spec.Selector)
	if err != nil {
		return false
	}
	return selector.Matches(labels.Set(exporter.Labels))
}

// SetupWithManager sets up the controller with the Manager.
func (r *LeaseReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&jumpstarterdevv1alpha1.Lease{}, builder.WithPredicates(skipEndedPredicate())).
		Watches(&jumpstarterdevv1alpha1.Exporter{},
			handler.EnqueueRequestsFromMapFunc(r.mapExporterToLeases),
			builder.WithPredicates(exporterChangedForPendingLeases())).
		// A claim has its lease as controller owner. The lease is reconciled
		// when one of its claims appears or goes: an old leader can create a
		// claim of a smaller attempt after the bind, and a bound lease
		// creates again a claim that was deleted. A reconcile that changes
		// no claim causes no further event, so this does not loop.
		Owns(&jumpstarterdevv1alpha1.ExporterClaim{}, builder.WithPredicates(predicate.Funcs{
			UpdateFunc:  func(event.UpdateEvent) bool { return false },
			GenericFunc: func(event.GenericEvent) bool { return false },
		})).
		Complete(r)
}
