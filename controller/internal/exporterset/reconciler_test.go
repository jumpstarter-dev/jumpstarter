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
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/exporterset/provisioners/qemu"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"
)

func newScheme(t *testing.T) *runtime.Scheme {
	t.Helper()
	scheme := runtime.NewScheme()
	if err := virtualtargetv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatalf("AddToScheme virtualtarget: %v", err)
	}
	if err := jumpstarterdevv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatalf("AddToScheme jumpstarter: %v", err)
	}
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatalf("AddToScheme corev1: %v", err)
	}
	return scheme
}

func TestReconcile_virtualTargetClassNotFound(t *testing.T) {
	scheme := newScheme(t)
	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "demo-set", Namespace: "default"},
		Spec: virtualtargetv1alpha1.ExporterSetSpec{
			VirtualTargetClassName: "missing-class",
		},
	}
	c := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(exporterSet).
		WithStatusSubresource(&virtualtargetv1alpha1.ExporterSet{}).
		Build()
	r := &ExporterSetReconciler{
		Client:      c,
		Scheme:      scheme,
		Provisioner: qemu.New(),
	}

	result, err := r.Reconcile(context.Background(), reconcile.Request{
		NamespacedName: types.NamespacedName{Name: "demo-set", Namespace: "default"},
	})
	if err != nil {
		t.Fatalf("Reconcile() error = %v, want nil", err)
	}
	if result.RequeueAfter != 0 {
		t.Fatalf("Reconcile() result = %#v, want no requeue", result)
	}

	var updated virtualtargetv1alpha1.ExporterSet
	if err := c.Get(context.Background(), types.NamespacedName{Name: "demo-set", Namespace: "default"}, &updated); err != nil {
		t.Fatalf("Get() error = %v", err)
	}
	cond := meta.FindStatusCondition(updated.Status.Conditions, "Available")
	if cond == nil {
		t.Fatal("Expected Available condition to be set")
	}
	if cond.Status != metav1.ConditionFalse {
		t.Errorf("Available status = %v, want False", cond.Status)
	}
	if cond.Reason != "VirtualTargetClassNotFound" {
		t.Errorf("Available reason = %q, want VirtualTargetClassNotFound", cond.Reason)
	}
}

func TestReconcile_provisionerMismatch(t *testing.T) {
	scheme := newScheme(t)
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		ObjectMeta: metav1.ObjectMeta{Name: "other-class", Namespace: "default"},
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{
			Provisioner: "other.jumpstarter.dev",
		},
	}
	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "demo-set", Namespace: "default"},
		Spec: virtualtargetv1alpha1.ExporterSetSpec{
			VirtualTargetClassName: "other-class",
		},
	}
	c := fake.NewClientBuilder().WithScheme(scheme).WithObjects(vtc, exporterSet).Build()
	r := &ExporterSetReconciler{
		Client:      c,
		Scheme:      scheme,
		Provisioner: qemu.New(),
	}

	result, err := r.Reconcile(context.Background(), reconcile.Request{
		NamespacedName: types.NamespacedName{Name: "demo-set", Namespace: "default"},
	})
	if err != nil {
		t.Fatalf("Reconcile() error = %v, want nil", err)
	}
	if result.RequeueAfter != 0 {
		t.Fatalf("Reconcile() result = %#v, want no requeue", result)
	}
}

func TestFindExporterSetsForVTC_filtersByClassName(t *testing.T) {
	scheme := newScheme(t)
	vtc := &virtualtargetv1alpha1.VirtualTargetClass{
		ObjectMeta: metav1.ObjectMeta{Name: "qemu-class", Namespace: "default"},
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{
			Provisioner: qemu.ProvisionerName,
		},
	}
	matching := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "matching", Namespace: "default"},
		Spec: virtualtargetv1alpha1.ExporterSetSpec{
			VirtualTargetClassName: "qemu-class",
		},
	}
	other := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "other", Namespace: "default"},
		Spec: virtualtargetv1alpha1.ExporterSetSpec{
			VirtualTargetClassName: "other-class",
		},
	}
	c := fake.NewClientBuilder().WithScheme(scheme).WithObjects(vtc, matching, other).Build()
	r := &ExporterSetReconciler{
		Client:      c,
		Scheme:      scheme,
		Provisioner: qemu.New(),
	}

	requests := r.findExporterSetsForVTC(context.Background(), vtc)
	if len(requests) != 1 {
		t.Fatalf("got %d requests, want 1: %#v", len(requests), requests)
	}
	if requests[0].Name != "matching" || requests[0].Namespace != "default" {
		t.Errorf("request = %#v, want matching/default", requests[0])
	}
}

func TestFindExporterSetsForVTC_ignoresNonVTC(t *testing.T) {
	scheme := newScheme(t)
	r := &ExporterSetReconciler{
		Client:      fake.NewClientBuilder().WithScheme(scheme).Build(),
		Scheme:      scheme,
		Provisioner: qemu.New(),
	}

	requests := r.findExporterSetsForVTC(context.Background(), &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{Name: "pod", Namespace: "default"},
	})
	if requests != nil {
		t.Errorf("got %#v, want nil", requests)
	}
}

// --- Status reconciliation tests ---

const testExporterSetUID = types.UID("es-uid-1234")

func makeExporterSet() *virtualtargetv1alpha1.ExporterSet {
	return &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{
			Name:       "demo-set",
			Namespace:  "default",
			UID:        testExporterSetUID,
			Generation: 1,
		},
		Spec: virtualtargetv1alpha1.ExporterSetSpec{
			MinReplicas:            2,
			MaxReplicas:            4,
			MinAvailableReplicas:   1,
			VirtualTargetClassName: "qemu-class",
			Selector: metav1.LabelSelector{
				MatchLabels: map[string]string{"exporterset": "demo-set"},
			},
		},
	}
}

func makeVTC() *virtualtargetv1alpha1.VirtualTargetClass {
	return &virtualtargetv1alpha1.VirtualTargetClass{
		ObjectMeta: metav1.ObjectMeta{Name: "qemu-class", Namespace: "default"},
		Spec: virtualtargetv1alpha1.VirtualTargetClassSpec{
			Provisioner: qemu.ProvisionerName,
		},
	}
}

func boolPtr(b bool) *bool { return &b }

func makeExporter(name string, online bool, leased bool, enabled bool) *jumpstarterdevv1alpha1.Exporter {
	exp := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: "default",
			Labels:    map[string]string{"exporterset": "demo-set"},
			OwnerReferences: []metav1.OwnerReference{{
				APIVersion: "virtualtarget.jumpstarter.dev/v1alpha1",
				Kind:       "ExporterSet",
				Name:       "demo-set",
				UID:        testExporterSetUID,
				Controller: boolPtr(true),
			}},
		},
		Spec: jumpstarterdevv1alpha1.ExporterSpec{
			Enabled: boolPtr(enabled),
		},
	}

	onlineStatus := metav1.ConditionFalse
	if online {
		onlineStatus = metav1.ConditionTrue
	}
	exp.Status.Conditions = []metav1.Condition{{
		Type:   string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
		Status: onlineStatus,
	}}

	if leased {
		exp.Status.LeaseRef = &corev1.LocalObjectReference{Name: name + "-lease"}
	}

	return exp
}

func makePod(name string, phase corev1.PodPhase) *corev1.Pod {
	return &corev1.Pod{
		ObjectMeta: metav1.ObjectMeta{
			Name:      name,
			Namespace: "default",
			Labels:    map[string]string{"exporterset": "demo-set"},
			OwnerReferences: []metav1.OwnerReference{{
				APIVersion: "virtualtarget.jumpstarter.dev/v1alpha1",
				Kind:       "ExporterSet",
				Name:       "demo-set",
				UID:        testExporterSetUID,
				Controller: boolPtr(true),
			}},
		},
		Status: corev1.PodStatus{Phase: phase},
	}
}

func reconcileAndGet(t *testing.T, objs ...client.Object) virtualtargetv1alpha1.ExporterSet {
	t.Helper()
	scheme := newScheme(t)
	c := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(objs...).
		WithStatusSubresource(&virtualtargetv1alpha1.ExporterSet{}).
		Build()
	r := &ExporterSetReconciler{
		Client:      c,
		Scheme:      scheme,
		Provisioner: qemu.New(),
	}

	_, err := r.Reconcile(context.Background(), reconcile.Request{
		NamespacedName: types.NamespacedName{Name: "demo-set", Namespace: "default"},
	})
	if err != nil {
		t.Fatalf("Reconcile() error = %v", err)
	}

	var updated virtualtargetv1alpha1.ExporterSet
	if err := c.Get(context.Background(),
		types.NamespacedName{Name: "demo-set", Namespace: "default"}, &updated); err != nil {
		t.Fatalf("Get() error = %v", err)
	}
	return updated
}

func TestReconcile_statusCounts_allOnlineNotLeased(t *testing.T) {
	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
		makeExporter("exp-1", true, false, true),
		makeExporter("exp-2", true, false, true),
		makePod("pod-1", corev1.PodRunning),
		makePod("pod-2", corev1.PodRunning),
	)

	assertInt32(t, "Replicas", es.Status.Replicas, 2)
	assertInt32(t, "ReadyReplicas", es.Status.ReadyReplicas, 2)
	assertInt32(t, "AvailableReplicas", es.Status.AvailableReplicas, 2)
	assertInt32(t, "UnavailableReplicas", es.Status.UnavailableReplicas, 0)
	assertInt32(t, "LeasedReplicas", es.Status.LeasedReplicas, 0)
	assertInt32(t, "ExportersActive", es.Status.ExportersActive, 0)
	assertInt32(t, "ExportersIdle", es.Status.ExportersIdle, 2)
	assertInt32(t, "ExportersDisabled", es.Status.ExportersDisabled, 0)
	assertInt32(t, "ExportersOffline", es.Status.ExportersOffline, 0)
	assertInt32(t, "PodsRunning", es.Status.PodsRunning, 2)
	assertInt32(t, "PodsPending", es.Status.PodsPending, 0)
}

func TestReconcile_statusCounts_mixedStates(t *testing.T) {
	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
		makeExporter("exp-1", true, true, true),
		makeExporter("exp-2", true, false, true),
		makeExporter("exp-3", false, false, true),
		makeExporter("exp-4", true, false, false),
		makePod("pod-1", corev1.PodRunning),
		makePod("pod-2", corev1.PodRunning),
		makePod("pod-3", corev1.PodFailed),
		makePod("pod-4", corev1.PodPending),
	)

	assertInt32(t, "Replicas", es.Status.Replicas, 4)
	assertInt32(t, "ReadyReplicas", es.Status.ReadyReplicas, 3)
	assertInt32(t, "AvailableReplicas", es.Status.AvailableReplicas, 1)
	assertInt32(t, "UnavailableReplicas", es.Status.UnavailableReplicas, 1)
	assertInt32(t, "LeasedReplicas", es.Status.LeasedReplicas, 1)
	assertInt32(t, "ExportersActive", es.Status.ExportersActive, 1)
	assertInt32(t, "ExportersIdle", es.Status.ExportersIdle, 1)
	assertInt32(t, "ExportersDisabled", es.Status.ExportersDisabled, 1)
	assertInt32(t, "ExportersOffline", es.Status.ExportersOffline, 1)
	assertInt32(t, "PodsRunning", es.Status.PodsRunning, 2)
	assertInt32(t, "PodsFailed", es.Status.PodsFailed, 1)
	assertInt32(t, "PodsPending", es.Status.PodsPending, 1)
}

func TestReconcile_conditionAvailable_meetsMinReplicas(t *testing.T) {
	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
		makeExporter("exp-1", true, false, true),
		makeExporter("exp-2", true, false, true),
	)

	cond := meta.FindStatusCondition(es.Status.Conditions, "Available")
	if cond == nil {
		t.Fatal("Expected Available condition")
	}
	if cond.Status != metav1.ConditionTrue {
		t.Errorf("Available = %v, want True", cond.Status)
	}
	if cond.Reason != "MinimumReplicasAvailable" {
		t.Errorf("Reason = %q, want MinimumReplicasAvailable", cond.Reason)
	}
}

func TestReconcile_conditionAvailable_belowMinReplicas(t *testing.T) {
	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
		makeExporter("exp-1", true, false, true),
	)

	cond := meta.FindStatusCondition(es.Status.Conditions, "Available")
	if cond == nil {
		t.Fatal("Expected Available condition")
	}
	if cond.Status != metav1.ConditionFalse {
		t.Errorf("Available = %v, want False", cond.Status)
	}
	if cond.Reason != "MinimumReplicasUnavailable" {
		t.Errorf("Reason = %q, want MinimumReplicasUnavailable", cond.Reason)
	}
}

func TestReconcile_conditionProgressing_podsPending(t *testing.T) {
	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
		makeExporter("exp-1", true, false, true),
		makePod("pod-1", corev1.PodRunning),
		makePod("pod-2", corev1.PodPending),
	)

	cond := meta.FindStatusCondition(es.Status.Conditions, "Progressing")
	if cond == nil {
		t.Fatal("Expected Progressing condition")
	}
	if cond.Status != metav1.ConditionTrue {
		t.Errorf("Progressing = %v, want True", cond.Status)
	}
	if cond.Reason != "PodsStarting" {
		t.Errorf("Reason = %q, want PodsStarting", cond.Reason)
	}
}

func TestReconcile_conditionProgressing_allReady(t *testing.T) {
	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
		makeExporter("exp-1", true, false, true),
		makeExporter("exp-2", true, false, true),
	)

	cond := meta.FindStatusCondition(es.Status.Conditions, "Progressing")
	if cond == nil {
		t.Fatal("Expected Progressing condition")
	}
	if cond.Status != metav1.ConditionFalse {
		t.Errorf("Progressing = %v, want False", cond.Status)
	}
}

func TestReconcile_conditionDegraded_failedPods(t *testing.T) {
	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
		makeExporter("exp-1", true, false, true),
		makeExporter("exp-2", false, false, true),
		makePod("pod-1", corev1.PodRunning),
		makePod("pod-2", corev1.PodFailed),
	)

	cond := meta.FindStatusCondition(es.Status.Conditions, "Degraded")
	if cond == nil {
		t.Fatal("Expected Degraded condition")
	}
	if cond.Status != metav1.ConditionTrue {
		t.Errorf("Degraded = %v, want True", cond.Status)
	}
	if cond.Reason != "PodsFailing" {
		t.Errorf("Reason = %q, want PodsFailing", cond.Reason)
	}
}

func TestReconcile_conditionDegraded_healthy(t *testing.T) {
	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
		makeExporter("exp-1", true, false, true),
		makeExporter("exp-2", true, false, true),
		makePod("pod-1", corev1.PodRunning),
		makePod("pod-2", corev1.PodRunning),
	)

	cond := meta.FindStatusCondition(es.Status.Conditions, "Degraded")
	if cond == nil {
		t.Fatal("Expected Degraded condition")
	}
	if cond.Status != metav1.ConditionFalse {
		t.Errorf("Degraded = %v, want False", cond.Status)
	}
}

func TestReconcile_conditionScalingLimited_atMax(t *testing.T) {
	esObj := makeExporterSet()
	esObj.Spec.MaxReplicas = 2
	esObj.Spec.MinAvailableReplicas = 2

	es := reconcileAndGet(t,
		esObj,
		makeVTC(),
		makeExporter("exp-1", true, true, true),
		makeExporter("exp-2", true, true, true),
	)

	cond := meta.FindStatusCondition(es.Status.Conditions, "ScalingLimited")
	if cond == nil {
		t.Fatal("Expected ScalingLimited condition")
	}
	if cond.Status != metav1.ConditionTrue {
		t.Errorf("ScalingLimited = %v, want True", cond.Status)
	}
	if cond.Reason != "AtMaxReplicas" {
		t.Errorf("Reason = %q, want AtMaxReplicas", cond.Reason)
	}
}

func TestReconcile_conditionScalingLimited_withinLimits(t *testing.T) {
	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
		makeExporter("exp-1", true, false, true),
		makeExporter("exp-2", true, false, true),
	)

	cond := meta.FindStatusCondition(es.Status.Conditions, "ScalingLimited")
	if cond == nil {
		t.Fatal("Expected ScalingLimited condition")
	}
	if cond.Status != metav1.ConditionFalse {
		t.Errorf("ScalingLimited = %v, want False", cond.Status)
	}
}

func TestReconcile_selectorField_populated(t *testing.T) {
	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
	)

	if es.Status.Selector != "exporterset=demo-set" {
		t.Errorf("Selector = %q, want %q", es.Status.Selector, "exporterset=demo-set")
	}
}

func TestReconcile_ignoresUnownedExporters(t *testing.T) {
	unowned := makeExporter("unowned", true, false, true)
	unowned.OwnerReferences = nil

	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
		unowned,
	)

	assertInt32(t, "Replicas", es.Status.Replicas, 0)
}

func TestReconcile_ignoresUnownedPods(t *testing.T) {
	unowned := makePod("unowned-pod", corev1.PodRunning)
	unowned.OwnerReferences = nil

	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
		unowned,
	)

	assertInt32(t, "PodsRunning", es.Status.PodsRunning, 0)
}

func TestReconcile_idempotent(t *testing.T) {
	scheme := newScheme(t)
	objs := []client.Object{
		makeExporterSet(),
		makeVTC(),
		makeExporter("exp-1", true, false, true),
		makeExporter("exp-2", true, true, true),
		makePod("pod-1", corev1.PodRunning),
		makePod("pod-2", corev1.PodRunning),
	}
	c := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(objs...).
		WithStatusSubresource(&virtualtargetv1alpha1.ExporterSet{}).
		Build()
	r := &ExporterSetReconciler{
		Client:      c,
		Scheme:      scheme,
		Provisioner: qemu.New(),
	}
	req := reconcile.Request{
		NamespacedName: types.NamespacedName{Name: "demo-set", Namespace: "default"},
	}

	if _, err := r.Reconcile(context.Background(), req); err != nil {
		t.Fatalf("first Reconcile() error = %v", err)
	}
	if _, err := r.Reconcile(context.Background(), req); err != nil {
		t.Fatalf("second Reconcile() error = %v", err)
	}

	var es virtualtargetv1alpha1.ExporterSet
	if err := c.Get(context.Background(),
		types.NamespacedName{Name: "demo-set", Namespace: "default"}, &es); err != nil {
		t.Fatalf("Get() error = %v", err)
	}

	assertInt32(t, "Replicas", es.Status.Replicas, 2)
	assertInt32(t, "ReadyReplicas", es.Status.ReadyReplicas, 2)
	assertInt32(t, "AvailableReplicas", es.Status.AvailableReplicas, 1)
	assertInt32(t, "LeasedReplicas", es.Status.LeasedReplicas, 1)
}

func TestReconcile_offlineLeasedExporter_notCountedAsActive(t *testing.T) {
	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
		makeExporter("exp-online-idle", true, false, true),
		makeExporter("exp-online-leased", true, true, true),
		makeExporter("exp-offline-leased", false, true, true),
	)

	assertInt32(t, "ExportersActive", es.Status.ExportersActive, 1)
	assertInt32(t, "ExportersOffline", es.Status.ExportersOffline, 1)
	assertInt32(t, "ExportersIdle", es.Status.ExportersIdle, 1)
	assertInt32(t, "LeasedReplicas", es.Status.LeasedReplicas, 2)
	assertInt32(t, "UnavailableReplicas", es.Status.UnavailableReplicas, 1)
}

func TestReconcile_disabledExporters_dontInflateUnavailable(t *testing.T) {
	es := reconcileAndGet(t,
		makeExporterSet(),
		makeVTC(),
		makeExporter("exp-1", true, false, true),
		makeExporter("exp-2", true, false, true),
		makeExporter("exp-disabled-offline", false, false, false),
		makeExporter("exp-disabled-online", true, false, false),
	)

	assertInt32(t, "Replicas", es.Status.Replicas, 4)
	assertInt32(t, "ReadyReplicas", es.Status.ReadyReplicas, 3)
	assertInt32(t, "UnavailableReplicas", es.Status.UnavailableReplicas, 0)
	assertInt32(t, "ExportersDisabled", es.Status.ExportersDisabled, 2)

	cond := meta.FindStatusCondition(es.Status.Conditions, "Progressing")
	if cond == nil {
		t.Fatal("Expected Progressing condition")
	}
	if cond.Status != metav1.ConditionFalse {
		t.Errorf("Progressing = %v, want False (disabled exporters should not trigger)", cond.Status)
	}

	degraded := meta.FindStatusCondition(es.Status.Conditions, "Degraded")
	if degraded == nil {
		t.Fatal("Expected Degraded condition")
	}
	if degraded.Status != metav1.ConditionFalse {
		t.Errorf("Degraded = %v, want False (disabled exporters should not trigger)", degraded.Status)
	}
}

func TestReconcile_vtcNotFound_updatesAllConditionsAndCounters(t *testing.T) {
	scheme := newScheme(t)
	esObj := makeExporterSet()
	exp1 := makeExporter("exp-1", true, false, true)
	exp2 := makeExporter("exp-2", false, false, true)

	c := fake.NewClientBuilder().
		WithScheme(scheme).
		WithObjects(esObj, makeVTC(), exp1, exp2).
		WithStatusSubresource(&virtualtargetv1alpha1.ExporterSet{}).
		Build()
	r := &ExporterSetReconciler{
		Client:      c,
		Scheme:      scheme,
		Provisioner: qemu.New(),
	}
	req := reconcile.Request{
		NamespacedName: types.NamespacedName{Name: "demo-set", Namespace: "default"},
	}

	if _, err := r.Reconcile(context.Background(), req); err != nil {
		t.Fatalf("first Reconcile() error = %v", err)
	}

	// Delete VTC to trigger not-found path
	vtc := makeVTC()
	if err := c.Delete(context.Background(), vtc); err != nil {
		t.Fatalf("Delete VTC error = %v", err)
	}

	if _, err := r.Reconcile(context.Background(), req); err != nil {
		t.Fatalf("second Reconcile() error = %v", err)
	}

	var es virtualtargetv1alpha1.ExporterSet
	if err := c.Get(context.Background(),
		types.NamespacedName{Name: "demo-set", Namespace: "default"}, &es); err != nil {
		t.Fatalf("Get() error = %v", err)
	}

	// Available should be False with VTC reason
	avail := meta.FindStatusCondition(es.Status.Conditions, "Available")
	if avail == nil {
		t.Fatal("Expected Available condition")
	}
	if avail.Status != metav1.ConditionFalse {
		t.Errorf("Available = %v, want False", avail.Status)
	}
	if avail.Reason != "VirtualTargetClassNotFound" {
		t.Errorf("Available reason = %q, want VirtualTargetClassNotFound", avail.Reason)
	}

	// Counters should reflect current exporter state, not be stale
	assertInt32(t, "Replicas", es.Status.Replicas, 2)
	assertInt32(t, "ReadyReplicas", es.Status.ReadyReplicas, 1)
	assertInt32(t, "UnavailableReplicas", es.Status.UnavailableReplicas, 1)

	// Degraded should reflect current state
	degraded := meta.FindStatusCondition(es.Status.Conditions, "Degraded")
	if degraded == nil {
		t.Fatal("Expected Degraded condition")
	}
}

func assertInt32(t *testing.T, field string, got, want int32) {
	t.Helper()
	if got != want {
		t.Errorf("%s = %d, want %d", field, got, want)
	}
}
