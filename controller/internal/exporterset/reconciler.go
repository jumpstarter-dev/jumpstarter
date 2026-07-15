/*
Copyright 2026 The Jumpstarter Authors

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

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/tools/record"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/handler"
	"sigs.k8s.io/controller-runtime/pkg/log"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
)

// ExporterSetReconciler reconciles an ExporterSet object.
// It watches ExporterSets, Exporters, and Leases to maintain a warm pool
// of virtual exporter instances with configurable autoscaling.
// Provisioner-specific logic (Pod rendering, cleanup) is delegated to the
// Provisioner interface implementation.
type ExporterSetReconciler struct {
	client.Client
	Scheme      *runtime.Scheme
	Recorder    record.EventRecorder
	Provisioner Provisioner
}

// +kubebuilder:rbac:groups=virtualtarget.jumpstarter.dev,resources=exportersets,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=virtualtarget.jumpstarter.dev,resources=exportersets/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=virtualtarget.jumpstarter.dev,resources=exportersets/finalizers,verbs=update
// +kubebuilder:rbac:groups=virtualtarget.jumpstarter.dev,resources=exportersets/scale,verbs=get;update;patch
// +kubebuilder:rbac:groups=virtualtarget.jumpstarter.dev,resources=virtualtargetclasses,verbs=get;list;watch
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporters,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=leases,verbs=get;list;watch
// +kubebuilder:rbac:groups=core,resources=pods,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=events,verbs=create;patch
// +kubebuilder:rbac:groups="",resources=secrets,verbs=get;list;watch
// +kubebuilder:rbac:groups="",resources=configmaps,verbs=get;list;watch

// Reconcile is the main reconciliation loop for ExporterSet resources.
// It resolves the referenced VirtualTargetClass, counts owned instances,
// and scales the pool to maintain the desired number of available replicas.
func (r *ExporterSetReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	var exporterSet virtualtargetv1alpha1.ExporterSet
	if err := r.Get(ctx, req.NamespacedName, &exporterSet); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(
			fmt.Errorf("Reconcile: unable to get ExporterSet: %w", err),
		)
	}

	// Resolve the VirtualTargetClass
	var vtc virtualtargetv1alpha1.VirtualTargetClass
	vtcKey := client.ObjectKey{
		Namespace: exporterSet.Namespace,
		Name:      exporterSet.Spec.VirtualTargetClassName,
	}
	if err := r.Get(ctx, vtcKey, &vtc); err != nil {
		if apierrors.IsNotFound(err) {
			logger.Info("VirtualTargetClass not found",
				"virtualTargetClassName", exporterSet.Spec.VirtualTargetClassName)
			// TODO: set a condition on the ExporterSet indicating the class is missing
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, fmt.Errorf("unable to get VirtualTargetClass %q: %w",
			exporterSet.Spec.VirtualTargetClassName, err)
	}

	// Only reconcile ExporterSets whose class matches our provisioner
	if vtc.Spec.Provisioner != r.Provisioner.Name() {
		return ctrl.Result{}, nil
	}

	logger.Info("reconciling ExporterSet",
		"name", exporterSet.Name,
		"provisioner", vtc.Spec.Provisioner,
		"minReplicas", exporterSet.Spec.MinReplicas,
		"maxReplicas", exporterSet.Spec.MaxReplicas,
		"minAvailableReplicas", exporterSet.Spec.MinAvailableReplicas,
	)

	// TODO: Phase 2 implementation
	// 1. Count owned Exporter CRs: replicas, readyReplicas, leasedReplicas, availableReplicas
	// 2. Deep-merge parameters from VirtualTargetClass + ExporterSet overrides
	// 3. Scale up if availableReplicas < minAvailableReplicas and replicas < maxReplicas
	//    - Use r.Provisioner.RenderPod() to create Pods
	//    - Set OwnerReferences on the Pod via ctrl.SetControllerReference
	//      before creation so that ExporterSet deletion cascades to Pods
	// 4. Scale up on demand if pending leases match selector
	// 5. Scale down if availableReplicas > minAvailableReplicas after cooldown
	//    - Use r.Provisioner.Cleanup() before deleting
	// 6. Update ExporterSet.status

	return ctrl.Result{}, nil
}

// SetupWithManager sets up the controller with the Manager.
func (r *ExporterSetReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&virtualtargetv1alpha1.ExporterSet{}).
		Owns(&jumpstarterdevv1alpha1.Exporter{}).
		Owns(&corev1.Pod{}).
		Watches(
			&virtualtargetv1alpha1.VirtualTargetClass{},
			handler.EnqueueRequestsFromMapFunc(r.findExporterSetsForVTC),
		).
		Named("exporterset").
		Complete(r)
}

func (r *ExporterSetReconciler) findExporterSetsForVTC(
	ctx context.Context,
	obj client.Object,
) []reconcile.Request {
	vtc, ok := obj.(*virtualtargetv1alpha1.VirtualTargetClass)
	if !ok {
		return nil
	}

	var exporterSetList virtualtargetv1alpha1.ExporterSetList
	if err := r.List(ctx, &exporterSetList, client.InNamespace(vtc.Namespace)); err != nil {
		return nil
	}

	requests := make([]reconcile.Request, 0, len(exporterSetList.Items))
	for _, exporterSet := range exporterSetList.Items {
		if exporterSet.Spec.VirtualTargetClassName != vtc.Name {
			continue
		}
		requests = append(requests, reconcile.Request{
			NamespacedName: types.NamespacedName{
				Name:      exporterSet.Name,
				Namespace: exporterSet.Namespace,
			},
		})
	}

	return requests
}
