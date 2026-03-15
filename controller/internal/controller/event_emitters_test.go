package controller

import (
	"testing"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/client-go/tools/record"
)

func TestClientEmitEventfNilRecorderDoesNotPanic(t *testing.T) {
	reconciler := &ClientReconciler{}
	client := &jumpstarterdevv1alpha1.Client{}

	assertNotPanics(t, func() {
		reconciler.emitEventf(client, corev1.EventTypeNormal, "CredentialCreated", "secret=%s", "test-secret")
	})
}

func TestExporterEmitEventfNilRecorderDoesNotPanic(t *testing.T) {
	reconciler := &ExporterReconciler{}
	exporter := &jumpstarterdevv1alpha1.Exporter{}

	assertNotPanics(t, func() {
		reconciler.emitEventf(exporter, corev1.EventTypeNormal, "ExporterOnline", "exporter=%s", "test-exporter")
	})
}

func TestLeaseEmitEventfNilRecorderDoesNotPanic(t *testing.T) {
	reconciler := &LeaseReconciler{}
	lease := &jumpstarterdevv1alpha1.Lease{}

	assertNotPanics(t, func() {
		reconciler.emitEventf(lease, corev1.EventTypeNormal, "LeaseReady", "lease=%s", "test-lease")
	})
}

func TestExporterEmitEventfWritesEvent(t *testing.T) {
	recorder := record.NewFakeRecorder(1)
	reconciler := &ExporterReconciler{Recorder: recorder}
	exporter := &jumpstarterdevv1alpha1.Exporter{}

	reconciler.emitEventf(exporter, corev1.EventTypeNormal, "ExporterOnline", "exporter=%s", "test-exporter")

	select {
	case event := <-recorder.Events:
		if event == "" {
			t.Fatal("expected non-empty event payload")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("expected event to be emitted")
	}
}

func TestLeaseConditionReason(t *testing.T) {
	lease := &jumpstarterdevv1alpha1.Lease{
		Status: jumpstarterdevv1alpha1.LeaseStatus{
			Conditions: []metav1.Condition{
				{
					Type:   string(jumpstarterdevv1alpha1.LeaseConditionTypePending),
					Status: metav1.ConditionTrue,
					Reason: "Offline",
				},
			},
		},
	}

	got := leaseConditionReason(lease, jumpstarterdevv1alpha1.LeaseConditionTypePending)
	if got != "Offline" {
		t.Fatalf("expected reason %q, got %q", "Offline", got)
	}

	// False-status condition should not be treated as active.
	lease.Status.Conditions[0].Status = metav1.ConditionFalse
	if got := leaseConditionReason(lease, jumpstarterdevv1alpha1.LeaseConditionTypePending); got != "" {
		t.Fatalf("expected empty reason for false condition, got %q", got)
	}
}

func assertNotPanics(t *testing.T, fn func()) {
	t.Helper()
	defer func() {
		if r := recover(); r != nil {
			t.Fatalf("unexpected panic: %v", r)
		}
	}()
	fn()
}
