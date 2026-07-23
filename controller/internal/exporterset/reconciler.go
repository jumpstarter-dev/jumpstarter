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

// Package exporterset implements the ExporterSet controller — the scaling
// engine for virtual exporter pools (JEP-0014).
//
// Scaling rules, evaluated in priority order each reconcile:
//  1. Floor: replicas < minReplicas → scale up
//  2. Warm buffer: available < minAvailableReplicas → scale up
//  3. Demand: pending Leases match our labels, no capacity → scale up
//  4. Excess: available > min for longer than cooldown → disable one
//
// Creates at most 1 exporter per reconcile to avoid cache-race duplicates.
// Graceful scale-down: disable → drain leases → delete on next cycle.
//
// Why not HPA/KEDA: scaling depends on Exporter CRD fields (leaseRef,
// enabled, online) and Lease state (pending condition) — not expressible
// as standard metrics. The disable-drain-delete protocol has no HPA analog.
package exporterset

import (
	"context"
	"encoding/json"
	"fmt"
	"maps"
	"sync"
	"time"

	corev1 "k8s.io/api/core/v1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
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

const (
	annotationSurplusSince = "exporterset.jumpstarter.dev/surplus-since"

	// Bridges the grandparent lookup (ExporterSet -> Exporter -> Pod).
	labelExporterSetName = "exporterset.jumpstarter.dev/name"

	defaultScaleDownCooldown = 5 * time.Minute
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

	lastScaleDownMu sync.Mutex
	// Guards against disabling multiple exporters within the same cooldown
	// when the informer cache is stale.
	LastScaleDownAction map[types.NamespacedName]time.Time
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
		if client.IgnoreNotFound(err) == nil {
			// ExporterSet deleted — clean up in-memory state.
			r.lastScaleDownMu.Lock()
			if r.LastScaleDownAction != nil {
				delete(r.LastScaleDownAction, req.NamespacedName)
			}
			r.lastScaleDownMu.Unlock()
			return ctrl.Result{}, nil
		}
		return ctrl.Result{}, fmt.Errorf("Reconcile: unable to get ExporterSet: %w", err)
	}

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
				Message:            fmt.Sprintf("VirtualTargetClass %q not found", exporterSet.Spec.VirtualTargetClassName),
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

	// Snapshot condition state before changes (for event emission).
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

	ownedExporters, err := r.listOwnedExporters(ctx, &exporterSet)
	if err != nil {
		return ctrl.Result{}, err
	}

	// Clean up drained (disabled+unleased) exporters before computing state.
	if deleted, err := r.cleanupDisabledExporters(ctx, &exporterSet, ownedExporters); err != nil {
		return ctrl.Result{}, err
	} else if deleted {
		// Update status and return; the next scale-down step fires on RequeueAfter.
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

	state := computePoolState(ownedExporters)

	mergedParameters, err := deepMergeParameters(vtc.Spec.Parameters, exporterSet.Spec.Parameters)
	if err != nil {
		return ctrl.Result{}, fmt.Errorf("unable to merge parameters: %w", err)
	}

	var result ctrl.Result

	// Scale-up logic. Uses potentiallyAvailable (replicas - leased) to avoid
	// cache-race over-creation. One exporter per reconcile; Owns watch
	// triggers subsequent cycles.
	scaled, err := r.reconcileScaleUp(ctx, &exporterSet, &vtc, mergedParameters, state)
	if err != nil {
		return ctrl.Result{}, err
	}
	if !scaled {
		result, err = r.reconcileScaleDown(ctx, &exporterSet, ownedExporters, state)
		if err != nil {
			return ctrl.Result{}, err
		}
	}

	// Update status counts, conditions, and emit events.
	if err := r.reconcileStatusCounts(ctx, &exporterSet); err != nil {
		return ctrl.Result{}, err
	}
	r.reconcileConditions(&exporterSet)
	if err := r.Status().Update(ctx, &exporterSet); err != nil {
		return requeueConflict(logger, err)
	}
	r.emitConditionEvents(&exporterSet, prevAvailable, prevProgressing, prevDegraded, prevScalingLimited)

	return result, nil
}

type poolState struct {
	replicas  int32
	ready     int32
	available int32
	leased    int32
}

func computePoolState(exporters []jumpstarterdevv1alpha1.Exporter) poolState {
	var s poolState
	for i := range exporters {
		exp := &exporters[i]
		s.replicas++

		isOnline := meta.IsStatusConditionTrue(
			exp.Status.Conditions,
			string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
		)
		isEnabled := exp.IsEnabled()
		isLeased := exp.Status.LeaseRef != nil

		if isOnline {
			s.ready++
		}
		if isLeased {
			s.leased++
		}
		if isOnline && isEnabled && !isLeased {
			s.available++
		}
	}
	return s
}

// scaleUp creates Exporter + Pod pairs (ExporterSet → Exporter → Pod).
func (r *ExporterSetReconciler) scaleUp(
	ctx context.Context,
	es *virtualtargetv1alpha1.ExporterSet,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
	mergedParameters map[string]interface{},
	count int32,
) error {
	logger := log.FromContext(ctx)

	for i := int32(0); i < count; i++ {
		exporter := &jumpstarterdevv1alpha1.Exporter{
			ObjectMeta: metav1.ObjectMeta{
				GenerateName: es.Name + "-",
				Namespace:    es.Namespace,
				Labels:       maps.Clone(es.Spec.Template.Metadata.Labels),
				Annotations:  maps.Clone(es.Spec.Template.Metadata.Annotations),
			},
			Spec: jumpstarterdevv1alpha1.ExporterSpec{
				Enabled: boolPtr(true),
			},
		}

		if err := ctrl.SetControllerReference(es, exporter, r.Scheme); err != nil {
			return fmt.Errorf("unable to set owner reference on Exporter: %w", err)
		}

		if err := r.Create(ctx, exporter); err != nil {
			return fmt.Errorf("unable to create Exporter: %w", err)
		}

		logger.Info("created Exporter", "exporter", exporter.Name)

		pod, err := r.Provisioner.RenderPod(ctx, es, vtc, mergedParameters)
		if err != nil {
			return fmt.Errorf("unable to render Pod for Exporter %s: %w", exporter.Name, err)
		}

		if pod.Labels == nil {
			pod.Labels = make(map[string]string)
		}
		pod.Labels[labelExporterSetName] = es.Name

		// Exporter owns Pod; the label bridges the grandparent lookup.
		if err := ctrl.SetControllerReference(exporter, pod, r.Scheme); err != nil {
			return fmt.Errorf("unable to set owner reference on Pod: %w", err)
		}

		if err := r.Create(ctx, pod); err != nil {
			if delErr := r.Delete(ctx, exporter); delErr != nil {
				logger.Error(delErr, "failed to roll back Exporter after Pod creation failure", "exporter", exporter.Name)
			}
			return fmt.Errorf("unable to create Pod for Exporter %s: %w", exporter.Name, err)
		}

		logger.Info("created Pod for Exporter", "exporter", exporter.Name, "pod", pod.Name)

		if r.Recorder != nil {
			r.Recorder.Eventf(es, corev1.EventTypeNormal, "ScaleUp",
				"Created Exporter %s with Pod %s", exporter.Name, pod.Name)
		}
	}

	return nil
}

// cleanupDisabledExporters deletes disabled, unleased exporters. Returns true if any were deleted.
func (r *ExporterSetReconciler) cleanupDisabledExporters(
	ctx context.Context,
	es *virtualtargetv1alpha1.ExporterSet,
	exporters []jumpstarterdevv1alpha1.Exporter,
) (bool, error) {
	logger := log.FromContext(ctx)
	deleted := false

	for i := range exporters {
		exp := &exporters[i]

		if exp.IsEnabled() || exp.Status.LeaseRef != nil {
			continue
		}

		if err := r.Provisioner.Cleanup(ctx, es, exp); err != nil {
			return deleted, fmt.Errorf("unable to cleanup Exporter %s: %w", exp.Name, err)
		}

		if err := r.Delete(ctx, exp); err != nil && !apierrors.IsNotFound(err) {
			return deleted, fmt.Errorf("unable to delete Exporter %s: %w", exp.Name, err)
		}

		logger.Info("deleted disabled Exporter", "exporter", exp.Name)

		if r.Recorder != nil {
			r.Recorder.Eventf(es, corev1.EventTypeNormal, "ScaleDown",
				"Deleted Exporter %s", exp.Name)
		}
		deleted = true
	}

	return deleted, nil
}

// reconcileScaleUp evaluates the three scale-up rules in priority order and
// creates one Exporter if any rule fires. Returns true if a scale-up was
// attempted (caller should skip scale-down for this cycle).
func (r *ExporterSetReconciler) reconcileScaleUp(
	ctx context.Context,
	es *virtualtargetv1alpha1.ExporterSet,
	vtc *virtualtargetv1alpha1.VirtualTargetClass,
	params map[string]interface{},
	state poolState,
) (scaled bool, err error) {
	logger := log.FromContext(ctx)
	potentiallyAvailable := state.replicas - state.leased

	// 1. Floor: never drop below minReplicas.
	if state.replicas < es.Spec.MinReplicas && potentiallyAvailable < es.Spec.MinReplicas {
		if maxScaleUp(es, state.replicas) > 0 {
			return true, r.scaleUp(ctx, es, vtc, params, 1)
		}
		return false, nil
	}

	// 2. Warm buffer: keep minAvailableReplicas ready-and-unleased instances.
	if state.available < es.Spec.MinAvailableReplicas &&
		potentiallyAvailable < es.Spec.MinAvailableReplicas &&
		!atMaxReplicas(es, state.replicas) {
		if maxScaleUp(es, state.replicas) > 0 {
			return true, r.scaleUp(ctx, es, vtc, params, 1)
		}
		return false, nil
	}

	// 3. Demand: pending Leases exist and no capacity is available.
	if !atMaxReplicas(es, state.replicas) {
		pending, err := r.countPendingLeases(ctx, es)
		if err != nil {
			logger.Error(err, "failed to count pending leases")
			return false, nil
		}
		if pending > 0 && state.available == 0 && maxScaleUp(es, state.replicas) > 0 {
			return true, r.scaleUp(ctx, es, vtc, params, 1)
		}
	}

	return false, nil
}

// reconcileScaleDown disables one idle exporter per cooldown period.
func (r *ExporterSetReconciler) reconcileScaleDown(
	ctx context.Context,
	es *virtualtargetv1alpha1.ExporterSet,
	ownedExporters []jumpstarterdevv1alpha1.Exporter,
	state poolState,
) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	// Scale down when available > minAvailableReplicas AND replicas > minReplicas.
	surplus := state.available - es.Spec.MinAvailableReplicas
	excessOverMin := state.replicas - es.Spec.MinReplicas

	if excessOverMin <= 0 || surplus <= 0 {
		r.clearSurplusAnnotation(ctx, es)
		return ctrl.Result{}, nil
	}

	cooldown := defaultScaleDownCooldown
	if es.Spec.ScaleDownCooldown != nil {
		cooldown = es.Spec.ScaleDownCooldown.Duration
	}

	// In-memory guard against stale-cache double-disables.
	key := types.NamespacedName{Name: es.Name, Namespace: es.Namespace}
	r.lastScaleDownMu.Lock()
	if r.LastScaleDownAction == nil {
		r.LastScaleDownAction = make(map[types.NamespacedName]time.Time)
	}
	lastAction := r.LastScaleDownAction[key]
	r.lastScaleDownMu.Unlock()
	if !lastAction.IsZero() {
		if remaining := cooldown - time.Since(lastAction); remaining > 0 {
			return ctrl.Result{RequeueAfter: remaining}, nil
		}
	}

	surplusSince, hasAnnotation := es.Annotations[annotationSurplusSince]
	if !hasAnnotation {
		r.setSurplusAnnotation(ctx, es)
		return ctrl.Result{RequeueAfter: cooldown}, nil
	}

	surplusTime, err := time.Parse(time.RFC3339, surplusSince)
	if err != nil {
		r.setSurplusAnnotation(ctx, es)
		return ctrl.Result{RequeueAfter: cooldown}, nil
	}

	elapsed := time.Since(surplusTime)
	if elapsed < cooldown {
		return ctrl.Result{RequeueAfter: cooldown - elapsed}, nil
	}

	// Cooldown elapsed — pick one exporter to disable (prefer online first).
	for _, wantOnline := range []bool{true, false} {
		for i := range ownedExporters {
			exp := &ownedExporters[i]

			if !exp.IsEnabled() || exp.Status.LeaseRef != nil {
				continue
			}

			isOnline := meta.IsStatusConditionTrue(
				exp.Status.Conditions,
				string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
			)
			if isOnline != wantOnline {
				continue
			}

			exp.Spec.Enabled = boolPtr(false)
			if err := r.Update(ctx, exp); err != nil {
				return ctrl.Result{}, fmt.Errorf("unable to disable Exporter %s: %w", exp.Name, err)
			}

			logger.Info("disabled Exporter for scale-down", "exporter", exp.Name)

			if r.Recorder != nil {
				r.Recorder.Eventf(es, corev1.EventTypeNormal, "ScaleDown",
					"Disabled Exporter %s for removal", exp.Name)
			}

			r.lastScaleDownMu.Lock()
			if r.LastScaleDownAction == nil {
				r.LastScaleDownAction = make(map[types.NamespacedName]time.Time)
			}
			r.LastScaleDownAction[key] = time.Now()
			r.lastScaleDownMu.Unlock()

			r.setSurplusAnnotation(ctx, es)
			return ctrl.Result{RequeueAfter: cooldown}, nil
		}
	}

	return ctrl.Result{}, nil
}

func (r *ExporterSetReconciler) setSurplusAnnotation(ctx context.Context, es *virtualtargetv1alpha1.ExporterSet) {
	if es.Annotations == nil {
		es.Annotations = make(map[string]string)
	}
	es.Annotations[annotationSurplusSince] = time.Now().UTC().Format(time.RFC3339)
	if err := r.Update(ctx, es); err != nil && !apierrors.IsConflict(err) {
		log.FromContext(ctx).Error(err, "failed to set surplus-since annotation")
	}
}

func (r *ExporterSetReconciler) clearSurplusAnnotation(ctx context.Context, es *virtualtargetv1alpha1.ExporterSet) {
	if _, ok := es.Annotations[annotationSurplusSince]; !ok {
		return
	}
	delete(es.Annotations, annotationSurplusSince)
	if err := r.Update(ctx, es); err != nil && !apierrors.IsConflict(err) {
		log.FromContext(ctx).Error(err, "failed to clear surplus-since annotation")
	}
}

func (r *ExporterSetReconciler) listOwnedExporters(
	ctx context.Context,
	es *virtualtargetv1alpha1.ExporterSet,
) ([]jumpstarterdevv1alpha1.Exporter, error) {
	selector, err := metav1.LabelSelectorAsSelector(&es.Spec.Selector)
	if err != nil {
		return nil, fmt.Errorf("invalid label selector: %w", err)
	}

	var exporterList jumpstarterdevv1alpha1.ExporterList
	if err := r.List(ctx, &exporterList,
		client.InNamespace(es.Namespace),
		client.MatchingLabelsSelector{Selector: selector},
	); err != nil {
		return nil, fmt.Errorf("unable to list Exporters: %w", err)
	}

	owned := make([]jumpstarterdevv1alpha1.Exporter, 0, len(exporterList.Items))
	for i := range exporterList.Items {
		if isOwnedBy(&exporterList.Items[i], es.UID) {
			owned = append(owned, exporterList.Items[i])
		}
	}
	return owned, nil
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

// SetupWithManager sets up the controller with the Manager.
func (r *ExporterSetReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&virtualtargetv1alpha1.ExporterSet{}).
		Owns(&jumpstarterdevv1alpha1.Exporter{}).
		Watches(
			&corev1.Pod{},
			handler.EnqueueRequestsFromMapFunc(r.findExporterSetForPod),
		).
		Watches(
			&virtualtargetv1alpha1.VirtualTargetClass{},
			handler.EnqueueRequestsFromMapFunc(r.findExporterSetsForVTC),
		).
		Watches(
			&jumpstarterdevv1alpha1.Lease{},
			handler.EnqueueRequestsFromMapFunc(r.findExporterSetsForLease),
		).
		Named("exporterset").
		Complete(r)
}

// findExporterSetForPod bridges the ExporterSet→Exporter→Pod grandchild gap via label.
func (r *ExporterSetReconciler) findExporterSetForPod(
	ctx context.Context,
	obj client.Object,
) []reconcile.Request {
	pod, ok := obj.(*corev1.Pod)
	if !ok {
		return nil
	}

	esName, ok := pod.Labels[labelExporterSetName]
	if !ok {
		return nil
	}

	return []reconcile.Request{{
		NamespacedName: types.NamespacedName{
			Name:      esName,
			Namespace: pod.Namespace,
		},
	}}
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

// findExporterSetsForLease enqueues ExporterSets whose template labels match the Lease's selector.
func (r *ExporterSetReconciler) findExporterSetsForLease(
	ctx context.Context,
	obj client.Object,
) []reconcile.Request {
	lease, ok := obj.(*jumpstarterdevv1alpha1.Lease)
	if !ok {
		return nil
	}

	if lease.Status.Ended {
		return nil
	}

	leaseSelector, err := metav1.LabelSelectorAsSelector(&lease.Spec.Selector)
	if err != nil {
		return nil
	}

	var exporterSetList virtualtargetv1alpha1.ExporterSetList
	if err := r.List(ctx, &exporterSetList, client.InNamespace(lease.Namespace)); err != nil {
		log.FromContext(ctx).Error(err, "unable to list ExporterSets for Lease",
			"namespace", lease.Namespace, "lease", lease.Name)
		return nil
	}

	var requests []reconcile.Request
	for _, es := range exporterSetList.Items {
		templateLabels := es.Spec.Template.Metadata.Labels
		if leaseSelector.Matches(labels.Set(templateLabels)) {
			requests = append(requests, reconcile.Request{
				NamespacedName: types.NamespacedName{
					Name:      es.Name,
					Namespace: es.Namespace,
				},
			})
		}
	}
	return requests
}

// countPendingLeases counts unassigned pending Leases that match this pool's template labels.
func (r *ExporterSetReconciler) countPendingLeases(
	ctx context.Context,
	es *virtualtargetv1alpha1.ExporterSet,
) (int32, error) {
	var leaseList jumpstarterdevv1alpha1.LeaseList
	if err := r.List(ctx, &leaseList, client.InNamespace(es.Namespace)); err != nil {
		return 0, fmt.Errorf("unable to list Leases: %w", err)
	}

	templateLabels := labels.Set(es.Spec.Template.Metadata.Labels)
	var count int32

	for i := range leaseList.Items {
		lease := &leaseList.Items[i]

		if lease.Status.Ended || lease.Status.ExporterRef != nil {
			continue
		}

		if !meta.IsStatusConditionTrue(lease.Status.Conditions,
			string(jumpstarterdevv1alpha1.LeaseConditionTypePending)) {
			continue
		}

		sel, err := metav1.LabelSelectorAsSelector(&lease.Spec.Selector)
		if err != nil {
			continue
		}
		if sel.Matches(templateLabels) {
			count++
		}
	}
	return count, nil
}

func isOwnedBy(obj client.Object, ownerUID types.UID) bool {
	for _, ref := range obj.GetOwnerReferences() {
		if ref.UID == ownerUID && ref.Controller != nil && *ref.Controller {
			return true
		}
	}
	return false
}

func boolPtr(b bool) *bool { return &b }

func atMaxReplicas(es *virtualtargetv1alpha1.ExporterSet, current int32) bool {
	return es.Spec.MaxReplicas > 0 && current >= es.Spec.MaxReplicas
}

func maxScaleUp(es *virtualtargetv1alpha1.ExporterSet, current int32) int32 {
	if es.Spec.MaxReplicas <= 0 {
		return int32(1<<31 - 1)
	}
	room := es.Spec.MaxReplicas - current
	if room < 0 {
		return 0
	}
	return room
}

func requeueConflict(logger interface{ Info(string, ...interface{}) }, err error) (ctrl.Result, error) {
	if apierrors.IsConflict(err) {
		logger.Info("conflict on status update, will retry")
	}
	return ctrl.Result{}, err
}

// deepMergeParameters merges VTC + ExporterSet params (maps recursive, scalars replace).
func deepMergeParameters(
	classParams *apiextensionsv1.JSON,
	setParams *apiextensionsv1.JSON,
) (map[string]interface{}, error) {
	base := make(map[string]interface{})
	override := make(map[string]interface{})

	if classParams != nil && classParams.Raw != nil {
		if err := json.Unmarshal(classParams.Raw, &base); err != nil {
			return nil, fmt.Errorf("unable to unmarshal class parameters: %w", err)
		}
	}

	if setParams != nil && setParams.Raw != nil {
		if err := json.Unmarshal(setParams.Raw, &override); err != nil {
			return nil, fmt.Errorf("unable to unmarshal set parameters: %w", err)
		}
	}

	return deepMerge(base, override), nil
}

func deepMerge(base, override map[string]interface{}) map[string]interface{} {
	result := make(map[string]interface{}, len(base)+len(override))
	for k, v := range base {
		result[k] = v
	}
	for k, v := range override {
		if baseMap, ok := result[k].(map[string]interface{}); ok {
			if overrideMap, ok := v.(map[string]interface{}); ok {
				result[k] = deepMerge(baseMap, overrideMap)
				continue
			}
		}
		result[k] = v
	}
	return result
}
