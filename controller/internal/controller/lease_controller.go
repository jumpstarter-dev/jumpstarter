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
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/selection"
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
	log := log.FromContext(ctx)

	var lease jumpstarterdevv1alpha1.Lease
	if err := r.Get(ctx, req.NamespacedName, &lease); err != nil {
		log.Error(err, "unable to fetch Lease")
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	// lease already ended, ignoring
	if lease.Status.Ended {
		return ctrl.Result{}, nil
	}

	// force release lease
	if lease.Spec.Release {
		log.Info("lease released early", "lease", lease.Name)
		now := time.Now()

		if lease.Labels == nil {
			lease.Labels = make(map[string]string)
		}
		lease.Labels[string(jumpstarterdevv1alpha1.LeaseLabelEnded)] = jumpstarterdevv1alpha1.LeaseLabelEndedValue

		if err := r.Update(ctx, &lease); err != nil {
			log.Error(err, "unable to update Lease labels")
			return ctrl.Result{}, err
		}

		lease.Status.Ended = true
		lease.Status.EndTime = &metav1.Time{
			Time: now,
		}
		meta.SetStatusCondition(&lease.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.LeaseConditionTypeReady),
			Status:             metav1.ConditionFalse,
			ObservedGeneration: lease.Generation,
			LastTransitionTime: metav1.Time{
				Time: now,
			},
			Reason: "Released",
		})

		if err := r.Status().Update(ctx, &lease); err != nil {
			log.Error(err, "unable to update Lease status")
			return ctrl.Result{}, err
		}

		return ctrl.Result{}, nil
	}

	// 1. newly created lease
	if lease.Status.BeginTime == nil || lease.Status.EndTime == nil || lease.Status.ExporterRef == nil {
		return r.ReconcileNewLease(ctx, lease)
	} else {
		// 2. expired lease
		if time.Now().After(lease.Status.EndTime.Time) {
			log.Info("lease expired", "lease", lease.Name)
			if lease.Labels == nil {
				lease.Labels = make(map[string]string)
			}
			lease.Labels[string(jumpstarterdevv1alpha1.LeaseLabelEnded)] = jumpstarterdevv1alpha1.LeaseLabelEndedValue

			if err := r.Update(ctx, &lease); err != nil {
				log.Error(err, "unable to update Lease labels")
				return ctrl.Result{}, err
			}

			lease.Status.Ended = true
			meta.SetStatusCondition(&lease.Status.Conditions, metav1.Condition{
				Type:               string(jumpstarterdevv1alpha1.LeaseConditionTypeReady),
				Status:             metav1.ConditionFalse,
				ObservedGeneration: lease.Generation,
				LastTransitionTime: metav1.Time{
					Time: time.Now(),
				},
				Reason: "Expired",
			})

			if err := r.Status().Update(ctx, &lease); err != nil {
				log.Error(err, "unable to update Lease status")
				return ctrl.Result{}, err
			}

			return ctrl.Result{}, nil
		} else {
			// 3. acquired lease
			// Requeue acquire lease on EndTime
			return ctrl.Result{
				RequeueAfter: time.Until(lease.Status.EndTime.Time),
			}, nil
		}
	}
}

func (r *LeaseReconciler) ReconcileNewLease(
	ctx context.Context,
	lease jumpstarterdevv1alpha1.Lease,
) (ctrl.Result, error) {
	log := log.FromContext(ctx)

	selector, err := metav1.LabelSelectorAsSelector(&lease.Spec.Selector)
	if err != nil {
		log.Error(err, "Error creating selector for label selector")
		return ctrl.Result{}, err
	}

	// List all Exporter matching selector
	var matchedExporters jumpstarterdevv1alpha1.ExporterList
	if err := r.List(
		ctx,
		&matchedExporters,
		client.InNamespace(lease.Namespace),
		client.MatchingLabelsSelector{Selector: selector},
	); err != nil {
		log.Error(err, "Error listing exporters")
		return ctrl.Result{}, err
	}

	onlineExporters := slices.DeleteFunc(
		matchedExporters.Items,
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

	// No Exporter available, lease unsatisfiable
	if len(onlineExporters) == 0 {
		lease.Status = jumpstarterdevv1alpha1.LeaseStatus{
			BeginTime:   nil,
			EndTime:     nil,
			ExporterRef: nil,
			Ended:       true,
		}

		meta.SetStatusCondition(&lease.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable),
			Status:             metav1.ConditionTrue,
			ObservedGeneration: lease.Generation,
			LastTransitionTime: metav1.Time{
				Time: time.Now(),
			},
			Reason: "NoExporter",
		})

		if err := r.Status().Update(ctx, &lease); err != nil {
			log.Error(err, "unable to update Lease status")
			return ctrl.Result{}, client.IgnoreNotFound(err)
		}

		return ctrl.Result{}, nil
	}

	// TODO: use field selector once KEP-4358 is stabilized
	// Reference: https://github.com/kubernetes/kubernetes/pull/122717
	requirement, err := labels.NewRequirement(
		string(jumpstarterdevv1alpha1.LeaseLabelEnded),
		selection.DoesNotExist,
		[]string{},
	)
	if err != nil {
		log.Error(err, "Error creating leases selector")
		return ctrl.Result{}, err
	}

	var leases jumpstarterdevv1alpha1.LeaseList
	err = r.List(
		ctx,
		&leases,
		client.InNamespace(lease.Namespace),
		client.MatchingLabelsSelector{Selector: labels.Everything().Add(*requirement)},
	)
	if err != nil {
		log.Error(err, "Error listing leases")
		return ctrl.Result{}, err
	}

	// Find available exporter
	for _, exporter := range onlineExporters {
		taken := false
		for _, existingLease := range leases.Items {
			// if lease is active and is referencing an exporter
			if !existingLease.Status.Ended && existingLease.Status.ExporterRef != nil {
				// if lease is referencing this exporter
				if existingLease.Status.ExporterRef.Name == exporter.Name {
					taken = true
				}
			}
		}
		// Exporter taken by lease
		if taken {
			continue
		}

		beginTime := time.Now()

		lease.Status = jumpstarterdevv1alpha1.LeaseStatus{
			BeginTime: &metav1.Time{
				Time: beginTime,
			},
			EndTime: &metav1.Time{
				Time: beginTime.Add(lease.Spec.Duration.Duration),
			},
			ExporterRef: &corev1.LocalObjectReference{
				Name: exporter.Name,
			},
			Ended: false,
		}

		meta.SetStatusCondition(&lease.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.LeaseConditionTypeReady),
			Status:             metav1.ConditionTrue,
			ObservedGeneration: lease.Generation,
			LastTransitionTime: metav1.Time{
				Time: beginTime,
			},
			Reason: "Acquired",
		})

		if err := r.Status().Update(ctx, &lease); err != nil {
			log.Error(err, "unable to update Lease status")
			return ctrl.Result{}, client.IgnoreNotFound(err)
		}

		if err := controllerutil.SetControllerReference(&exporter, &lease, r.Scheme); err != nil {
			log.Error(err, "unable to update Lease owner reference")
			return ctrl.Result{}, err
		}

		if err := r.Update(ctx, &lease); err != nil {
			log.Error(err, "unable to update Lease")
			return ctrl.Result{}, err
		}

		// Requeue at EndTime
		return ctrl.Result{
			RequeueAfter: time.Until(lease.Status.EndTime.Time),
		}, nil
	}

	lease.Status = jumpstarterdevv1alpha1.LeaseStatus{
		BeginTime:   nil,
		EndTime:     nil,
		ExporterRef: nil,
		Ended:       false,
	}
	meta.SetStatusCondition(&lease.Status.Conditions, metav1.Condition{
		Type:               string(jumpstarterdevv1alpha1.LeaseConditionTypePending),
		Status:             metav1.ConditionTrue,
		ObservedGeneration: lease.Generation,
		LastTransitionTime: metav1.Time{
			Time: time.Now(),
		},
		Reason: "NotAvailable",
	})
	meta.SetStatusCondition(&lease.Status.Conditions, metav1.Condition{
		Type:               string(jumpstarterdevv1alpha1.LeaseConditionTypeReady),
		Status:             metav1.ConditionFalse,
		ObservedGeneration: lease.Generation,
		LastTransitionTime: metav1.Time{
			Time: time.Now(),
		},
		Reason: "Pending",
	})

	if err := r.Status().Update(ctx, &lease); err != nil {
		log.Error(err, "unable to update Lease status")
		return ctrl.Result{}, client.IgnoreNotFound(err)
	}

	// No exporter available
	// Try again later
	return ctrl.Result{
		RequeueAfter: time.Second,
	}, nil
}

// SetupWithManager sets up the controller with the Manager.
func (r *LeaseReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&jumpstarterdevv1alpha1.Lease{}).
		Complete(r)
}
