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
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/runtime"
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

	// Ignore invalid lease
	if !lease.Spec.BeginTime.Before(&lease.Spec.EndTime) {
		log.Error(fmt.Errorf("BeginTime not before EndTime"), "invalid lease")
		return ctrl.Result{}, nil
	}

	// Ignore leases that are yet to begin
	// Requeue at BeginTime
	if lease.Spec.BeginTime.After(time.Now()) {
		return ctrl.Result{
			RequeueAfter: lease.Spec.BeginTime.Sub(time.Now()),
		}, nil
	}

	if !lease.Spec.EndTime.After(time.Now()) {
		// Update status for expired leases
		lease.Status.Ended = true
		lease.Status.Exporter = nil

		if err := r.Status().Update(ctx, &lease); err != nil {
			log.Error(err, "unable to update Lease status")
			return ctrl.Result{}, client.IgnoreNotFound(err)
		}

		return ctrl.Result{}, nil
	} else {
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

		for _, exporter := range exporters.Items {
			// Filter for Leases already using this exporter
			leaseSelector := fields.OneTermEqualSelector("status.exporter.uid", string(exporter.UID))

			// List all leases matching selector
			var leases jumpstarterdevv1alpha1.LeaseList
			err = r.List(ctx, &leases, client.InNamespace(req.Namespace), client.MatchingFieldsSelector{
				Selector: leaseSelector,
			})

			// Exporter available
			if len(leases.Items) == 0 {
				lease.Status.Ended = false
				lease.Status.Exporter = &corev1.ObjectReference{
					Kind:       exporter.Kind,
					Namespace:  exporter.Namespace,
					Name:       exporter.Name,
					UID:        exporter.UID,
					APIVersion: exporter.APIVersion,
				}

				if err := r.Status().Update(ctx, &lease); err != nil {
					log.Error(err, "unable to update Lease status")
					return ctrl.Result{}, client.IgnoreNotFound(err)
				}

				// Requeue at EndTime
				return ctrl.Result{
					RequeueAfter: lease.Spec.EndTime.Sub(time.Now()),
				}, nil
			}
		}

		// No exporter available
		// Try again later
		return ctrl.Result{
			RequeueAfter: time.Second,
		}, nil
	}
}

// SetupWithManager sets up the controller with the Manager.
func (r *LeaseReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&jumpstarterdevv1alpha1.Lease{}).
		Complete(r)
}
