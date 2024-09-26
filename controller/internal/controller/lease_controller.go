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
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
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

	// 1. newly created lease
	if lease.Status.BeginTime == nil || lease.Status.EndTime == nil || lease.Status.ExporterRef == nil {
		selector, err := metav1.LabelSelectorAsSelector(&lease.Spec.Selector)
		if err != nil {
			log.Error(err, "Error creating selector for label selector")
			return ctrl.Result{}, err
		}

		// List all exporters matching selector
		var exporters jumpstarterdevv1alpha1.ExporterList
		err = r.List(ctx, &exporters, client.InNamespace(req.Namespace), client.MatchingLabelsSelector{Selector: selector})
		if err != nil {
			log.Error(err, "Error listing exporters")
			return ctrl.Result{}, err
		}

		// TODO: use field selector once KEP-4358 is stabilized
		// Reference: https://github.com/kubernetes/kubernetes/pull/122717
		var leases jumpstarterdevv1alpha1.LeaseList
		err = r.List(
			ctx,
			&leases,
			client.InNamespace(req.Namespace),
		)
		if err != nil {
			log.Error(err, "Error listing leases")
			return ctrl.Result{}, err
		}

		// Find available exporter
		for _, exporter := range exporters.Items {
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
				Conditions: []metav1.Condition{{
					Type:               "Ready",
					Status:             metav1.ConditionTrue,
					ObservedGeneration: lease.Generation,
					LastTransitionTime: metav1.Time{
						Time: beginTime,
					},
					Reason: "acquired",
				}},
			}

			if err := r.Status().Update(ctx, &lease); err != nil {
				log.Error(err, "unable to update Lease status")
				return ctrl.Result{}, client.IgnoreNotFound(err)
			}

			exporter.Status.LeaseRef = &corev1.LocalObjectReference{
				Name: lease.Name,
			}

			if err := r.Status().Update(ctx, &exporter); err != nil {
				log.Error(err, "unable to update Exporter status")
				return ctrl.Result{}, client.IgnoreNotFound(err)
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
			Conditions: []metav1.Condition{{
				Type:               "Ready",
				Status:             metav1.ConditionFalse,
				ObservedGeneration: lease.Generation,
				LastTransitionTime: metav1.Time{
					Time: time.Now(),
				},
				Reason: "pending",
			}},
		}

		if err := r.Status().Update(ctx, &lease); err != nil {
			log.Error(err, "unable to update Lease status")
			return ctrl.Result{}, client.IgnoreNotFound(err)
		}

		// No exporter available
		// Try again later
		return ctrl.Result{
			RequeueAfter: time.Second,
		}, nil
	} else {
		// 2. expired lease
		if !lease.Status.Ended &&
			// lease expired
			(time.Now().After(lease.Status.EndTime.Time) ||
				// lease force released
				lease.Spec.Release) {

			// Attempt to clear the exporter first before setting lease to ended
			var exporter jumpstarterdevv1alpha1.Exporter
			if err := r.Get(ctx, types.NamespacedName{
				Namespace: req.Namespace,
				Name:      lease.Status.ExporterRef.Name,
			}, &exporter); err != nil {
				log.Error(err, "unable to get Exporter")
				return ctrl.Result{}, err
			}

			// only if the lease is this one, otherwise we leave it untouched
			// i.e. this iteration loop already ran, but then we failed to update the
			// lease as ended
			if exporter.Status.LeaseRef.Name == lease.Name {
				exporter.Status.LeaseRef = nil

				if err := r.Status().Update(ctx, &exporter); err != nil {
					log.Error(err, "unable to update Exporter status")
					return ctrl.Result{}, err
				}
			}

			lease.Status.Ended = true

			// If lease has been released early, set EndTime to now
			if lease.Spec.Release {
				log.Info("lease released early", "lease", lease.Name)
				endTime := time.Now()
				lease.Status.EndTime = &metav1.Time{
					Time: endTime,
				}
				lease.Status.Conditions = []metav1.Condition{{
					Type:               "Ready",
					Status:             metav1.ConditionFalse,
					ObservedGeneration: lease.Generation,
					LastTransitionTime: metav1.Time{
						Time: time.Now(),
					},
					Reason: "released",
				}}
			} else {
				log.Info("lease expired", "lease", lease.Name)
				lease.Status.Conditions = []metav1.Condition{{
					Type:               "Ready",
					Status:             metav1.ConditionFalse,
					ObservedGeneration: lease.Generation,
					LastTransitionTime: metav1.Time{
						Time: time.Now(),
					},
					Reason: "expired",
				}}
			}

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

// SetupWithManager sets up the controller with the Manager.
func (r *LeaseReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&jumpstarterdevv1alpha1.Lease{}).
		Complete(r)
}
