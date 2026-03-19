package jumpstarter

import (
	"testing"
	"time"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/client-go/tools/record"
)

func TestJumpstarterEmitEventfNilRecorderDoesNotPanic(t *testing.T) {
	reconciler := &JumpstarterReconciler{}
	js := &operatorv1alpha1.Jumpstarter{}

	assertNotPanics(t, func() {
		reconciler.emitEventf(js, corev1.EventTypeNormal, "DeploymentReady", "name=%s", "jumpstarter")
	})
}

func TestJumpstarterEmitEventfWritesEvent(t *testing.T) {
	recorder := record.NewFakeRecorder(1)
	reconciler := &JumpstarterReconciler{Recorder: recorder}
	js := &operatorv1alpha1.Jumpstarter{}

	reconciler.emitEventf(js, corev1.EventTypeNormal, "DeploymentReady", "name=%s", "jumpstarter")

	select {
	case event := <-recorder.Events:
		if event == "" {
			t.Fatal("expected non-empty event payload")
		}
	case <-time.After(2 * time.Second):
		t.Fatal("expected event to be emitted")
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
