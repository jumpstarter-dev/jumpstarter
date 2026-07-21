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
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
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
	c := fake.NewClientBuilder().WithScheme(scheme).WithObjects(exporterSet).Build()
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
