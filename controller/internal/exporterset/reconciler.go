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
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
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

			prevAvailable := meta.IsStatusConditionTrue(
				exporterSet.Status.Conditions,
				string(virtualtargetv1alpha1.ExporterSetConditionAvailable),
			)
			prevDegraded := meta.IsStatusConditionTrue(
				exporterSet.Status.Conditions,
				string(virtualtargetv1alpha1.ExporterSetConditionDegraded),
			)
			prevProgressing := meta.IsStatusConditionTrue(
				exporterSet.Status.Conditions,
				string(virtualtargetv1alpha1.ExporterSetConditionProgressing),
			)
			prevScalingLimited := meta.IsStatusConditionTrue(
				exporterSet.Status.Conditions,
				string(virtualtargetv1alpha1.ExporterSetConditionScalingLimited),
			)

			if countErr := r.reconcileStatusCounts(ctx, &exporterSet); countErr != nil {
				return ctrl.Result{}, countErr
			}
			r.reconcileConditions(&exporterSet)

			meta.SetStatusCondition(&exporterSet.Status.Conditions, metav1.Condition{
				Type:               string(virtualtargetv1alpha1.ExporterSetConditionAvailable),
				Status:             metav1.ConditionFalse,
				ObservedGeneration: exporterSet.Generation,
				Reason:             "VirtualTargetClassNotFound",
				Message: fmt.Sprintf("VirtualTargetClass %q not found",
					exporterSet.Spec.VirtualTargetClassName),
			})

			if updateErr := r.Status().Update(ctx, &exporterSet); updateErr != nil {
				return requeueConflict(logger, updateErr)
			}
			r.emitConditionEvents(&exporterSet, prevAvailable, prevProgressing, prevDegraded, prevScalingLimited)
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

	prevAvailable := meta.IsStatusConditionTrue(
		exporterSet.Status.Conditions,
		string(virtualtargetv1alpha1.ExporterSetConditionAvailable),
	)
	prevDegraded := meta.IsStatusConditionTrue(
		exporterSet.Status.Conditions,
		string(virtualtargetv1alpha1.ExporterSetConditionDegraded),
	)
	prevProgressing := meta.IsStatusConditionTrue(
		exporterSet.Status.Conditions,
		string(virtualtargetv1alpha1.ExporterSetConditionProgressing),
	)
	prevScalingLimited := meta.IsStatusConditionTrue(
		exporterSet.Status.Conditions,
		string(virtualtargetv1alpha1.ExporterSetConditionScalingLimited),
	)

	if err := r.reconcileStatusCounts(ctx, &exporterSet); err != nil {
		return ctrl.Result{}, err
	}

	r.reconcileConditions(&exporterSet)

	if err := r.Status().Update(ctx, &exporterSet); err != nil {
		return requeueConflict(logger, err)
	}

	r.emitConditionEvents(&exporterSet, prevAvailable, prevProgressing, prevDegraded, prevScalingLimited)

	return ctrl.Result{}, nil
}

func (r *ExporterSetReconciler) reconcileStatusCounts(
	ctx context.Context,
	es *virtualtargetv1alpha1.ExporterSet,
) error {
	selector, err := metav1.LabelSelectorAsSelector(&es.Spec.Selector)
	if err != nil {
		return fmt.Errorf("invalid label selector: %w", err)
	}

	es.Status.Selector = selector.String()

	var exporterList jumpstarterdevv1alpha1.ExporterList
	if err := r.List(ctx, &exporterList,
		client.InNamespace(es.Namespace),
		client.MatchingLabelsSelector{Selector: selector},
	); err != nil {
		return fmt.Errorf("unable to list Exporters: %w", err)
	}

	ownedExporters := filterOwnedExporters(exporterList.Items, es.UID)

	var replicas, ready, available, leased int32
	var active, idle, disabled, offline int32

	for i := range ownedExporters {
		exp := &ownedExporters[i]
		replicas++

		isOnline := meta.IsStatusConditionTrue(
			exp.Status.Conditions,
			string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
		)
		isEnabled := exp.IsEnabled()
		isLeased := exp.Status.LeaseRef != nil

		if isOnline {
			ready++
		}
		if isLeased {
			leased++
		}
		if isOnline && isEnabled && !isLeased {
			available++
		}

		if !isEnabled {
			disabled++
		} else if isLeased && isOnline {
			active++
		} else if isOnline {
			idle++
		} else {
			offline++
		}
	}

	es.Status.Replicas = replicas
	es.Status.ReadyReplicas = ready
	es.Status.AvailableReplicas = available
	es.Status.UnavailableReplicas = offline
	es.Status.LeasedReplicas = leased
	es.Status.ExportersActive = active
	es.Status.ExportersIdle = idle
	es.Status.ExportersDisabled = disabled
	es.Status.ExportersOffline = offline

	var podList corev1.PodList
	if err := r.List(ctx, &podList,
		client.InNamespace(es.Namespace),
		client.MatchingLabelsSelector{Selector: selector},
	); err != nil {
		return fmt.Errorf("unable to list Pods: %w", err)
	}

	var pending, running, failed, unknown int32

	for i := range podList.Items {
		if !isOwnedBy(&podList.Items[i], es.UID) {
			continue
		}
		switch podList.Items[i].Status.Phase {
		case corev1.PodPending:
			pending++
		case corev1.PodRunning:
			running++
		case corev1.PodFailed:
			failed++
		default:
			unknown++
		}
	}

	es.Status.PodsPending = pending
	es.Status.PodsRunning = running
	es.Status.PodsFailed = failed
	es.Status.PodsUnknown = unknown

	return nil
}

func (r *ExporterSetReconciler) reconcileConditions(es *virtualtargetv1alpha1.ExporterSet) {
	r.reconcileConditionAvailable(es)
	r.reconcileConditionProgressing(es)
	r.reconcileConditionDegraded(es)
	r.reconcileConditionScalingLimited(es)
}

func (r *ExporterSetReconciler) reconcileConditionAvailable(es *virtualtargetv1alpha1.ExporterSet) {
	condType := string(virtualtargetv1alpha1.ExporterSetConditionAvailable)

	if es.Spec.MinReplicas == 0 && es.Status.Replicas == 0 {
		meta.SetStatusCondition(&es.Status.Conditions, metav1.Condition{
			Type:               condType,
			Status:             metav1.ConditionTrue,
			ObservedGeneration: es.Generation,
			Reason:             "NoReplicasRequired",
			Message:            "No minimum replicas configured",
		})
		return
	}

	if es.Status.ReadyReplicas >= es.Spec.MinReplicas {
		meta.SetStatusCondition(&es.Status.Conditions, metav1.Condition{
			Type:               condType,
			Status:             metav1.ConditionTrue,
			ObservedGeneration: es.Generation,
			Reason:             "MinimumReplicasAvailable",
			Message: fmt.Sprintf("%d/%d replicas ready",
				es.Status.ReadyReplicas, es.Spec.MinReplicas),
		})
		return
	}

	meta.SetStatusCondition(&es.Status.Conditions, metav1.Condition{
		Type:               condType,
		Status:             metav1.ConditionFalse,
		ObservedGeneration: es.Generation,
		Reason:             "MinimumReplicasUnavailable",
		Message: fmt.Sprintf("%d/%d replicas ready, need %d",
			es.Status.ReadyReplicas, es.Status.Replicas, es.Spec.MinReplicas),
	})
}

func (r *ExporterSetReconciler) reconcileConditionProgressing(es *virtualtargetv1alpha1.ExporterSet) {
	condType := string(virtualtargetv1alpha1.ExporterSetConditionProgressing)

	if es.Status.PodsPending > 0 {
		meta.SetStatusCondition(&es.Status.Conditions, metav1.Condition{
			Type:               condType,
			Status:             metav1.ConditionTrue,
			ObservedGeneration: es.Generation,
			Reason:             "PodsStarting",
			Message:            fmt.Sprintf("%d pod(s) pending", es.Status.PodsPending),
		})
		return
	}

	if es.Status.UnavailableReplicas > 0 {
		meta.SetStatusCondition(&es.Status.Conditions, metav1.Condition{
			Type:               condType,
			Status:             metav1.ConditionTrue,
			ObservedGeneration: es.Generation,
			Reason:             "ReplicasNotReady",
			Message: fmt.Sprintf("%d replica(s) not yet ready",
				es.Status.UnavailableReplicas),
		})
		return
	}

	meta.SetStatusCondition(&es.Status.Conditions, metav1.Condition{
		Type:               condType,
		Status:             metav1.ConditionFalse,
		ObservedGeneration: es.Generation,
		Reason:             "AllReplicasReady",
		Message:            "All replicas are ready",
	})
}

func (r *ExporterSetReconciler) reconcileConditionDegraded(es *virtualtargetv1alpha1.ExporterSet) {
	condType := string(virtualtargetv1alpha1.ExporterSetConditionDegraded)

	if es.Status.PodsFailed > 0 {
		meta.SetStatusCondition(&es.Status.Conditions, metav1.Condition{
			Type:               condType,
			Status:             metav1.ConditionTrue,
			ObservedGeneration: es.Generation,
			Reason:             "PodsFailing",
			Message:            fmt.Sprintf("%d pod(s) in Failed state", es.Status.PodsFailed),
		})
		return
	}

	if es.Status.UnavailableReplicas > 0 && es.Status.PodsPending == 0 {
		meta.SetStatusCondition(&es.Status.Conditions, metav1.Condition{
			Type:               condType,
			Status:             metav1.ConditionTrue,
			ObservedGeneration: es.Generation,
			Reason:             "ExportersOffline",
			Message: fmt.Sprintf("%d exporter(s) not ready with no pending pods",
				es.Status.UnavailableReplicas),
		})
		return
	}

	meta.SetStatusCondition(&es.Status.Conditions, metav1.Condition{
		Type:               condType,
		Status:             metav1.ConditionFalse,
		ObservedGeneration: es.Generation,
		Reason:             "AllReplicasHealthy",
		Message:            "No degraded replicas",
	})
}

func (r *ExporterSetReconciler) reconcileConditionScalingLimited(es *virtualtargetv1alpha1.ExporterSet) {
	condType := string(virtualtargetv1alpha1.ExporterSetConditionScalingLimited)

	atMax := es.Spec.MaxReplicas > 0 && es.Status.Replicas >= es.Spec.MaxReplicas
	needMore := es.Status.AvailableReplicas < es.Spec.MinAvailableReplicas

	if atMax && needMore {
		meta.SetStatusCondition(&es.Status.Conditions, metav1.Condition{
			Type:               condType,
			Status:             metav1.ConditionTrue,
			ObservedGeneration: es.Generation,
			Reason:             "AtMaxReplicas",
			Message: fmt.Sprintf("Cannot scale beyond %d replicas; %d available, need %d",
				es.Spec.MaxReplicas, es.Status.AvailableReplicas,
				es.Spec.MinAvailableReplicas),
		})
		return
	}

	meta.SetStatusCondition(&es.Status.Conditions, metav1.Condition{
		Type:               condType,
		Status:             metav1.ConditionFalse,
		ObservedGeneration: es.Generation,
		Reason:             "WithinLimits",
		Message:            "Scaling is not constrained",
	})
}

func (r *ExporterSetReconciler) emitConditionEvents(
	es *virtualtargetv1alpha1.ExporterSet,
	prevAvailable, prevProgressing, prevDegraded, prevScalingLimited bool,
) {
	if r.Recorder == nil {
		return
	}

	nowAvailable := meta.IsStatusConditionTrue(
		es.Status.Conditions,
		string(virtualtargetv1alpha1.ExporterSetConditionAvailable),
	)
	nowProgressing := meta.IsStatusConditionTrue(
		es.Status.Conditions,
		string(virtualtargetv1alpha1.ExporterSetConditionProgressing),
	)
	nowDegraded := meta.IsStatusConditionTrue(
		es.Status.Conditions,
		string(virtualtargetv1alpha1.ExporterSetConditionDegraded),
	)
	nowScalingLimited := meta.IsStatusConditionTrue(
		es.Status.Conditions,
		string(virtualtargetv1alpha1.ExporterSetConditionScalingLimited),
	)

	if !prevAvailable && nowAvailable {
		r.Recorder.Eventf(es, corev1.EventTypeNormal, "ExporterSetAvailable",
			"ExporterSet %s is available: %d ready replicas",
			es.Name, es.Status.ReadyReplicas)
	} else if prevAvailable && !nowAvailable {
		r.Recorder.Eventf(es, corev1.EventTypeWarning, "ExporterSetUnavailable",
			"ExporterSet %s is unavailable: %d/%d replicas ready",
			es.Name, es.Status.ReadyReplicas, es.Spec.MinReplicas)
	}

	if !prevProgressing && nowProgressing {
		r.Recorder.Eventf(es, corev1.EventTypeNormal, "ExporterSetProgressing",
			"ExporterSet %s is progressing: %d pending pods, %d unavailable replicas",
			es.Name, es.Status.PodsPending, es.Status.UnavailableReplicas)
	} else if prevProgressing && !nowProgressing {
		r.Recorder.Eventf(es, corev1.EventTypeNormal, "ExporterSetSettled",
			"ExporterSet %s finished progressing: all replicas ready", es.Name)
	}

	if !prevDegraded && nowDegraded {
		r.Recorder.Eventf(es, corev1.EventTypeWarning, "ExporterSetDegraded",
			"ExporterSet %s is degraded: %d failed pods, %d unavailable replicas",
			es.Name, es.Status.PodsFailed, es.Status.UnavailableReplicas)
	} else if prevDegraded && !nowDegraded {
		r.Recorder.Eventf(es, corev1.EventTypeNormal, "ExporterSetRecovered",
			"ExporterSet %s recovered: all replicas healthy", es.Name)
	}

	if !prevScalingLimited && nowScalingLimited {
		r.Recorder.Eventf(es, corev1.EventTypeWarning, "ScalingLimited",
			"ExporterSet %s scaling limited at %d replicas",
			es.Name, es.Spec.MaxReplicas)
	} else if prevScalingLimited && !nowScalingLimited {
		r.Recorder.Eventf(es, corev1.EventTypeNormal, "ScalingUnlimited",
			"ExporterSet %s scaling no longer limited", es.Name)
	}
}

func filterOwnedExporters(
	exporters []jumpstarterdevv1alpha1.Exporter,
	ownerUID types.UID,
) []jumpstarterdevv1alpha1.Exporter {
	owned := make([]jumpstarterdevv1alpha1.Exporter, 0, len(exporters))
	for i := range exporters {
		if isOwnedBy(&exporters[i], ownerUID) {
			owned = append(owned, exporters[i])
		}
	}
	return owned
}

func isOwnedBy(obj client.Object, ownerUID types.UID) bool {
	for _, ref := range obj.GetOwnerReferences() {
		if ref.UID == ownerUID && ref.Controller != nil && *ref.Controller {
			return true
		}
	}
	return false
}

func requeueConflict(logger interface{ Info(string, ...interface{}) }, err error) (ctrl.Result, error) {
	if apierrors.IsConflict(err) {
		logger.Info("conflict on status update, will retry")
	}
	return ctrl.Result{}, err
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
		log.FromContext(ctx).Error(err, "unable to list ExporterSets for VirtualTargetClass",
			"namespace", vtc.Namespace, "name", vtc.Name)
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
