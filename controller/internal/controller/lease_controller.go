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
	"strings"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// LeaseReconciler reconciles a Lease object
type LeaseReconciler struct {
	client.Client
	Scheme *runtime.Scheme
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
		return ctrl.Result{}, client.IgnoreNotFound(
			fmt.Errorf("Reconcile: unable to get lease: %w", err),
		)
	}

	var result ctrl.Result
	if err := r.reconcileStatusExporterRef(ctx, &result, &lease); err != nil {
		return result, err
	}

	if err := r.reconcileStatusBeginTime(ctx, &lease); err != nil {
		return result, err
	}

	if err := r.reconcileStatusEnded(ctx, &result, &lease); err != nil {
		return result, err
	}

	if err := r.Status().Update(ctx, &lease); err != nil {
		return RequeueConflict(logger, result, err)
	}

	if lease.Labels == nil {
		lease.Labels = make(map[string]string)
	}
	if lease.Status.Ended {
		lease.Labels[string(jumpstarterdevv1alpha1.LeaseLabelEnded)] = jumpstarterdevv1alpha1.LeaseLabelEndedValue
	}

	if lease.Status.ExporterRef != nil {
		var exporter jumpstarterdevv1alpha1.Exporter
		if err := r.Get(ctx, types.NamespacedName{
			Namespace: lease.Namespace,
			Name:      lease.Status.ExporterRef.Name,
		}, &exporter); err != nil {
			return result, err
		}
		if err := controllerutil.SetControllerReference(&exporter, &lease, r.Scheme); err != nil {
			return result, fmt.Errorf("Reconcile: failed to update lease controller reference: %w", err)
		}
	}

	if err := r.Update(ctx, &lease); err != nil {
		return RequeueConflict(logger, result, fmt.Errorf("Reconcile: failed to update lease metadata: %w", err))
	}

	return result, nil
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
			expiration := lease.Status.BeginTime.Add(lease.Spec.Duration.Duration)
			if expiration.Before(now) {
				lease.Expire(ctx)
				return nil
			} else {
				result.RequeueAfter = expiration.Sub(now)
				return nil
			}
		}

	}
	return nil
}

// nolint:unparam
func (r *LeaseReconciler) reconcileStatusBeginTime(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
) error {
	logger := log.FromContext(ctx)

	now := time.Now()
	if lease.Status.BeginTime == nil && lease.Status.ExporterRef != nil {
		logger.Info("Updating begin time for lease", "lease", lease.Name, "exporter", lease.GetExporterName(), "client", lease.GetClientName())
		lease.SetStatusReady(true, "Ready", "An exporter has been acquired for the client")
		lease.Status.BeginTime = &metav1.Time{
			Time: now,
		}
	}

	return nil
}

// Also manages LeaseConditionTypeUnsatisfiable and LeaseConditionTypePending
func (r *LeaseReconciler) reconcileStatusExporterRef(
	ctx context.Context,
	result *ctrl.Result,
	lease *jumpstarterdevv1alpha1.Lease,
) error {
	logger := log.FromContext(ctx)

	// Do not attempt to reconcile if the lease is already ended/invalid/etc
	if lease.Status.Ended {
		return nil
	}

	if lease.Status.ExporterRef == nil {
		logger.Info("Looking for a matching exporter for lease", "lease", lease.Name, "client", lease.GetClientName(), "selector", lease.Spec.Selector)

		selector, err := lease.GetExporterSelector()
		if err != nil {
			return fmt.Errorf("reconcileStatusExporterRef: failed to get exporter selector: %w", err)
		} else if selector.Empty() {
			lease.SetStatusInvalid("InvalidSelector", "The selector for the lease is empty, a selector is required")
			return nil
		}

		// List all Exporter matching selector
		matchingExporters, err := r.ListMatchingExporters(ctx, lease, selector)
		if err != nil {
			return fmt.Errorf("reconcileStatusExporterRef: failed to list matching exporters: %w", err)
		}

		approvedExporters, err := r.attachMatchingPolicies(ctx, lease, matchingExporters.Items)
		if err != nil {
			return fmt.Errorf("reconcileStatusExporterRef: failed to handle policy approval: %w", err)
		}

		if len(approvedExporters) == 0 {
			lease.SetStatusUnsatisfiable(
				"NoAccess",
				"While there are %d exporters matching the selector, none of them are approved by any policy for your client",
				len(matchingExporters.Items),
			)
			return nil
		}

		onlineApprovedExporters := filterOutOfflineExporters(approvedExporters)
		if len(onlineApprovedExporters) == 0 {
			lease.SetStatusPending(
				"Offline",
				"While there are %d available exporters (i.e. %s), none of them are online",
				len(approvedExporters),
				approvedExporters[0].Exporter.Name,
			)
			result.RequeueAfter = time.Second
			return nil
		}

		// Filter out exporters that are already leased
		activeLeases, err := r.ListActiveLeases(ctx, lease.Namespace)
		if err != nil {
			return fmt.Errorf("reconcileStatusExporterRef: failed to list active leases: %w", err)
		}

		onlineApprovedExporters = attachExistingLeases(onlineApprovedExporters, activeLeases.Items)
		orderedExporters := orderApprovedExporters(onlineApprovedExporters)

		if len(orderedExporters) > 0 && orderedExporters[0].Policy.SpotAccess {
			lease.SetStatusUnsatisfiable("SpotAccess",
				"The only possible exporters are under spot access (i.e. %s), but spot access is still not implemented",
				orderedExporters[0].Exporter.Name)
			return nil
		}

		availableExporters := filterOutLeasedExporters(onlineApprovedExporters)
		if len(availableExporters) == 0 {
			lease.SetStatusPending("NotAvailable",
				"There are %d approved exporters, (i.e. %s) but all of them are already leased",
				len(onlineApprovedExporters),
				onlineApprovedExporters[0].Exporter.Name,
			)
			result.RequeueAfter = time.Second
			return nil
		}

		// TODO: here there's room for improvement, i.e. we could have multiple
		// clients trying to lease the same exporters, we should look at priorities
		// and spot access to decide which client gets the exporter, this probably means
		// that we will need to construct a lease scheduler with the view of all leases
		// and exporters in the system, and (maybe) a priority queue for the leases.

		// For now, we just select the best available exporter without considering other
		// ongoing lease requests

		selected := availableExporters[0]

		if selected.ExistingLease != nil {
			// TODO: Implement eviction of spot access leases
			lease.SetStatusPending("NotAvailable",
				"Exporter %s is already leased by another client under spot access, but spot access eviction still not implemented",
				selected.Exporter.Name)
			result.RequeueAfter = time.Second
			return nil
		}

		lease.Status.Priority = selected.Policy.Priority
		lease.Status.SpotAccess = selected.Policy.SpotAccess
		lease.Status.ExporterRef = &corev1.LocalObjectReference{
			Name: selected.Exporter.Name,
		}
		return nil
	}

	return nil
}

// attachMatchingPolicies attaches the matching policies to the list of online exporters
// if the exporter matches the policy and the client matches the policy's client selector
// the exporter is approved for leasing
func (r *LeaseReconciler) attachMatchingPolicies(ctx context.Context, lease *jumpstarterdevv1alpha1.Lease, onlineExporters []jumpstarterdevv1alpha1.Exporter) ([]ApprovedExporter, error) {
	var approvedExporters []ApprovedExporter

	var policies jumpstarterdevv1alpha1.ExporterAccessPolicyList
	if err := r.List(ctx, &policies,
		client.InNamespace(lease.Namespace),
	); err != nil {
		return nil, fmt.Errorf("reconcileStatusExporterRef: failed to list exporter access policies: %w", err)
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
		return approvedExporters, nil
	}
	// If policies exist: get the client to obtain the metadata necessary for policy matching
	var jclient jumpstarterdevv1alpha1.Client
	if err := r.Get(ctx, types.NamespacedName{
		Namespace: lease.Namespace,
		Name:      lease.Spec.ClientRef.Name,
	}, &jclient); err != nil {
		return nil, fmt.Errorf("reconcileStatusExporterRef: failed to get client: %w", err)
	}

	for _, exporter := range onlineExporters {
		for _, policy := range policies.Items {
			exporterSelector, err := metav1.LabelSelectorAsSelector(&policy.Spec.ExporterSelector)
			if err != nil {
				return nil, fmt.Errorf("reconcileStatusExporterRef: failed to convert exporter selector: %w", err)
			}
			if exporterSelector.Matches(labels.Set(exporter.Labels)) {
				for _, p := range policy.Spec.Policies {
					for _, from := range p.From {
						clientSelector, err := metav1.LabelSelectorAsSelector(&from.ClientSelector)
						if err != nil {
							return nil, fmt.Errorf("reconcileStatusExporterRef: failed to convert client selector: %w", err)
						}
						if clientSelector.Matches(labels.Set(jclient.Labels)) {
							if p.MaximumDuration != nil {
								if lease.Spec.Duration.Duration > p.MaximumDuration.Duration {
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
				}
			}
		}
	}
	return approvedExporters, nil
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

// ListActiveLeases returns a list of active leases in the namespace
func (r *LeaseReconciler) ListActiveLeases(ctx context.Context, namespace string) (*jumpstarterdevv1alpha1.LeaseList, error) {
	var activeLeases jumpstarterdevv1alpha1.LeaseList
	if err := r.List(
		ctx,
		&activeLeases,
		client.InNamespace(namespace),
		MatchingActiveLeases(),
	); err != nil {
		return nil, err
	}
	return &activeLeases, nil
}

// attachExistingLeases attaches the existing leases to the approved exporter list
// if the activeLeases slice contains a lease that references the exporter in the
// approved exporter list
func attachExistingLeases(exporters []ApprovedExporter, activeLeases []jumpstarterdevv1alpha1.Lease) []ApprovedExporter {
	for i, exporter := range exporters {
		for _, existingLease := range activeLeases {
			if existingLease.Status.ExporterRef != nil &&
				existingLease.Status.ExporterRef.Name == exporter.Exporter.Name {
				exporters[i].ExistingLease = &existingLease
			}
		}
	}
	return exporters
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
	return slices.DeleteFunc(exporters, func(ae ApprovedExporter) bool {
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

// filterOutOfflineExporters filters out the exporters that are not online
func filterOutOfflineExporters(approvedExporters []ApprovedExporter) []ApprovedExporter {
	onlineExporters := slices.DeleteFunc(
		approvedExporters,
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

// SetupWithManager sets up the controller with the Manager.
func (r *LeaseReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&jumpstarterdevv1alpha1.Lease{}).
		Complete(r)
}
