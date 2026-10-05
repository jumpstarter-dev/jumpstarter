/*
Copyright 2026. The Jumpstarter Authors.

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
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	jmpmetrics "github.com/jumpstarter-dev/jumpstarter/controller/internal/metrics"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestLeaseAcquisitionTransitionResult_SuccessOnce(t *testing.T) {
	lease := &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{Name: "lease-1"},
		Status: jumpstarterdevv1alpha1.LeaseStatus{
			ExporterRef: &corev1.LocalObjectReference{Name: "exporter-a"},
		},
	}

	result, ok := leaseAcquisitionTransitionResult(nil, false, lease)
	if !ok || result != jmpmetrics.ResultSuccess {
		t.Fatalf("first transition: got (%q, %v), want (%q, true)", result, ok, jmpmetrics.ResultSuccess)
	}

	// Reconcile retry after status persisted: priorExporterRef is set.
	result, ok = leaseAcquisitionTransitionResult(lease.Status.ExporterRef, false, lease)
	if ok {
		t.Fatalf("second observation after persist must not record, got %q", result)
	}
}

func TestLeaseAcquisitionTransitionResult_FailureOnce(t *testing.T) {
	lease := &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{Name: "lease-2"},
	}
	lease.SetStatusUnsatisfiable("NoAccess", "no exporters approved")

	result, ok := leaseAcquisitionTransitionResult(nil, false, lease)
	if !ok || result != jmpmetrics.ResultFailure {
		t.Fatalf("first unsatisfiable transition: got (%q, %v), want (%q, true)", result, ok, jmpmetrics.ResultFailure)
	}

	// Reconcile retry after Unsatisfiable was persisted.
	result, ok = leaseAcquisitionTransitionResult(nil, true, lease)
	if ok {
		t.Fatalf("second unsatisfiable observation after persist must not record, got %q", result)
	}
}

func TestLeaseAcquisitionTransitionResult_NoTransition(t *testing.T) {
	lease := &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{Name: "lease-3"},
	}

	result, ok := leaseAcquisitionTransitionResult(nil, false, lease)
	if ok {
		t.Fatalf("pending lease with no exporter/unsatisfiable must not record, got %q", result)
	}
}
