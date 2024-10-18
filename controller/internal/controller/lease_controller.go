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
	"slices"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
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

	var lease jumpstarterdevv1alpha1.Lease
	if err := r.Get(ctx, req.NamespacedName, &lease); err != nil {
		if !apierrors.IsNotFound(err) {
			logger.Error(err, "Reconcile: unable to get lease", "lease", req.NamespacedName)
		}
		return ctrl.Result{}, client.IgnoreNotFound(err)
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
		return result, err
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
			logger.Error(err, "Reconcile: failed to update lease controller reference", "lease", lease)
			return result, err
		}
	}

	if err := r.Update(ctx, &lease); err != nil {
		logger.Error(err, "Reconcile: failed to update lease metadata", "lease", lease)
		return result, err
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
	logger := log.FromContext(ctx)

	now := time.Now()
	if !lease.Status.Ended {
		if lease.Spec.Release {
			logger.Info("reconcileStatusEndTime: force releasing lease", "lease", lease)
			meta.SetStatusCondition(&lease.Status.Conditions, metav1.Condition{
				Type:               string(jumpstarterdevv1alpha1.LeaseConditionTypeReady),
				Status:             metav1.ConditionFalse,
				ObservedGeneration: lease.Generation,
				LastTransitionTime: metav1.Time{
					Time: now,
				},
				Reason: "Released",
			})
			lease.Status.Ended = true
			lease.Status.EndTime = &metav1.Time{
				Time: now,
			}
			return nil
		} else if lease.Status.BeginTime != nil {
			expiration := lease.Status.BeginTime.Add(lease.Spec.Duration.Duration)
			if expiration.Before(now) {
				logger.Info("reconcileStatusEndTime: lease expired", "lease", lease)
				meta.SetStatusCondition(&lease.Status.Conditions, metav1.Condition{
					Type:               string(jumpstarterdevv1alpha1.LeaseConditionTypeReady),
					Status:             metav1.ConditionFalse,
					ObservedGeneration: lease.Generation,
					LastTransitionTime: metav1.Time{
						Time: time.Now(),
					},
					Reason: "Expired",
				})
				lease.Status.Ended = true
				lease.Status.EndTime = &metav1.Time{
					Time: now,
				}
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
		logger.Info("reconcileStatusBeginTime: updating begin time", "lease", lease)
		meta.SetStatusCondition(&lease.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.LeaseConditionTypeReady),
			Status:             metav1.ConditionTrue,
			ObservedGeneration: lease.Generation,
			LastTransitionTime: metav1.Time{
				Time: now,
			},
			Reason: "Ready",
		})
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

	if lease.Status.ExporterRef == nil {
		logger.Info("reconcileStatusExporterRef: looking for matching exporter", "lease", lease)

		selector, err := metav1.LabelSelectorAsSelector(&lease.Spec.Selector)
		if err != nil {
			logger.Error(err, "reconcileStatusExporterRef: failed to create selector from label selector", "lease", lease)
			return err
		}

		// List all Exporter matching selector
		var matchingExporters jumpstarterdevv1alpha1.ExporterList
		if err := r.List(
			ctx,
			&matchingExporters,
			client.InNamespace(lease.Namespace),
			client.MatchingLabelsSelector{Selector: selector},
		); err != nil {
			logger.Error(err, "reconcileStatusExporterRef: failed to list exporters matching selector", "lease", lease)
			return err
		}

		// Filter out offline exporters
		onlineExporters := slices.DeleteFunc(
			matchingExporters.Items,
			func(exporter jumpstarterdevv1alpha1.Exporter) bool {
				return !(true &&
					meta.IsStatusConditionTrue(
						exporter.Status.Conditions,
						string(jumpstarterdevv1alpha1.ExporterConditionTypeRegistered),
					) &&
					meta.IsStatusConditionTrue(
						exporter.Status.Conditions,
						string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
					))
			},
		)

		// No matching exporter online, lease unsatisfiable
		if len(onlineExporters) == 0 {
			meta.SetStatusCondition(&lease.Status.Conditions, metav1.Condition{
				Type:               string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable),
				Status:             metav1.ConditionTrue,
				ObservedGeneration: lease.Generation,
				LastTransitionTime: metav1.Time{
					Time: time.Now(),
				},
				Reason: "NoExporter",
			})
			return nil
		}

		var leases jumpstarterdevv1alpha1.LeaseList
		if err := r.List(
			ctx,
			&leases,
			client.InNamespace(lease.Namespace),
			MatchingActiveLeases(),
		); err != nil {
			logger.Error(err, "reconcileStatusExporterRef: failed to list active leases", "lease", lease)
			return err
		}

		availableExporters := slices.DeleteFunc(onlineExporters, func(exporter jumpstarterdevv1alpha1.Exporter) bool {
			for _, existingLease := range leases.Items {
				// if the lease is referencing the current exporter
				if existingLease.Status.ExporterRef != nil && existingLease.Status.ExporterRef.Name == exporter.Name {
					return true
				}
			}
			return false
		})

		if len(availableExporters) == 0 {
			meta.SetStatusCondition(&lease.Status.Conditions, metav1.Condition{
				Type:               string(jumpstarterdevv1alpha1.LeaseConditionTypePending),
				Status:             metav1.ConditionTrue,
				ObservedGeneration: lease.Generation,
				LastTransitionTime: metav1.Time{
					Time: time.Now(),
				},
				Reason: "NotAvailable",
			})
			result.RequeueAfter = time.Second
			return nil
		} else {
			lease.Status.ExporterRef = &corev1.LocalObjectReference{
				Name: availableExporters[0].Name,
			}
			return nil
		}
	}

	return nil
}

// SetupWithManager sets up the controller with the Manager.
func (r *LeaseReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&jumpstarterdevv1alpha1.Lease{}).
		Complete(r)
}
