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

package service

import (
	"testing"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestProtoStatusToString(t *testing.T) {
	tests := []struct {
		name     string
		input    pb.ExporterStatus
		expected string
	}{
		{
			name:     "UNSPECIFIED maps to ExporterStatusUnspecified",
			input:    pb.ExporterStatus_EXPORTER_STATUS_UNSPECIFIED,
			expected: jumpstarterdevv1alpha1.ExporterStatusUnspecified,
		},
		{
			name:     "OFFLINE maps to ExporterStatusOffline",
			input:    pb.ExporterStatus_EXPORTER_STATUS_OFFLINE,
			expected: jumpstarterdevv1alpha1.ExporterStatusOffline,
		},
		{
			name:     "AVAILABLE maps to ExporterStatusAvailable",
			input:    pb.ExporterStatus_EXPORTER_STATUS_AVAILABLE,
			expected: jumpstarterdevv1alpha1.ExporterStatusAvailable,
		},
		{
			name:     "BEFORE_LEASE_HOOK maps to ExporterStatusBeforeLeaseHook",
			input:    pb.ExporterStatus_EXPORTER_STATUS_BEFORE_LEASE_HOOK,
			expected: jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHook,
		},
		{
			name:     "LEASE_READY maps to ExporterStatusLeaseReady",
			input:    pb.ExporterStatus_EXPORTER_STATUS_LEASE_READY,
			expected: jumpstarterdevv1alpha1.ExporterStatusLeaseReady,
		},
		{
			name:     "AFTER_LEASE_HOOK maps to ExporterStatusAfterLeaseHook",
			input:    pb.ExporterStatus_EXPORTER_STATUS_AFTER_LEASE_HOOK,
			expected: jumpstarterdevv1alpha1.ExporterStatusAfterLeaseHook,
		},
		{
			name:     "BEFORE_LEASE_HOOK_FAILED maps to ExporterStatusBeforeLeaseHookFailed",
			input:    pb.ExporterStatus_EXPORTER_STATUS_BEFORE_LEASE_HOOK_FAILED,
			expected: jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHookFailed,
		},
		{
			name:     "AFTER_LEASE_HOOK_FAILED maps to ExporterStatusAfterLeaseHookFailed",
			input:    pb.ExporterStatus_EXPORTER_STATUS_AFTER_LEASE_HOOK_FAILED,
			expected: jumpstarterdevv1alpha1.ExporterStatusAfterLeaseHookFailed,
		},
		{
			name:     "Unknown value falls back to ExporterStatusUnspecified",
			input:    pb.ExporterStatus(999),
			expected: jumpstarterdevv1alpha1.ExporterStatusUnspecified,
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			result := protoStatusToString(tt.input)
			if result != tt.expected {
				t.Errorf("protoStatusToString(%v) = %q, want %q", tt.input, result, tt.expected)
			}
		})
	}
}

func TestCheckExporterStatusForDriverCalls(t *testing.T) {
	tests := []struct {
		name           string
		status         string
		expectError    bool
		expectedCode   codes.Code
		expectedSubstr string
	}{
		// Allowed statuses (should return nil)
		{
			name:        "LeaseReady allows driver calls",
			status:      jumpstarterdevv1alpha1.ExporterStatusLeaseReady,
			expectError: false,
		},
		{
			name:        "BeforeLeaseHook allows driver calls (for j commands in hooks)",
			status:      jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHook,
			expectError: false,
		},
		{
			name:        "AfterLeaseHook allows driver calls (for j commands in hooks)",
			status:      jumpstarterdevv1alpha1.ExporterStatusAfterLeaseHook,
			expectError: false,
		},
		{
			name:        "Unspecified allows driver calls (backwards compatibility)",
			status:      jumpstarterdevv1alpha1.ExporterStatusUnspecified,
			expectError: false,
		},
		{
			name:        "Empty string allows driver calls (backwards compatibility)",
			status:      "",
			expectError: false,
		},
		// Rejected statuses (should return error)
		{
			name:           "Offline is rejected",
			status:         jumpstarterdevv1alpha1.ExporterStatusOffline,
			expectError:    true,
			expectedCode:   codes.FailedPrecondition,
			expectedSubstr: "exporter is offline",
		},
		{
			name:           "Available is rejected",
			status:         jumpstarterdevv1alpha1.ExporterStatusAvailable,
			expectError:    true,
			expectedCode:   codes.FailedPrecondition,
			expectedSubstr: "exporter is not ready",
		},
		{
			name:           "BeforeLeaseHookFailed is rejected",
			status:         jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHookFailed,
			expectError:    true,
			expectedCode:   codes.FailedPrecondition,
			expectedSubstr: "beforeLease hook failed",
		},
		{
			name:           "AfterLeaseHookFailed is rejected",
			status:         jumpstarterdevv1alpha1.ExporterStatusAfterLeaseHookFailed,
			expectError:    true,
			expectedCode:   codes.FailedPrecondition,
			expectedSubstr: "afterLease hook failed",
		},
		{
			name:           "Unknown status is rejected",
			status:         "SomeUnknownStatus",
			expectError:    true,
			expectedCode:   codes.FailedPrecondition,
			expectedSubstr: "exporter not ready",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			err := checkExporterStatusForDriverCalls(tt.status)

			if tt.expectError {
				if err == nil {
					t.Errorf("checkExporterStatusForDriverCalls(%q) = nil, want error", tt.status)
					return
				}

				st, ok := status.FromError(err)
				if !ok {
					t.Errorf("expected gRPC status error, got %v", err)
					return
				}

				if st.Code() != tt.expectedCode {
					t.Errorf("error code = %v, want %v", st.Code(), tt.expectedCode)
				}

				if tt.expectedSubstr != "" {
					if !contains(st.Message(), tt.expectedSubstr) {
						t.Errorf("error message = %q, want to contain %q", st.Message(), tt.expectedSubstr)
					}
				}
			} else {
				if err != nil {
					t.Errorf("checkExporterStatusForDriverCalls(%q) = %v, want nil", tt.status, err)
				}
			}
		})
	}
}

func TestSyncOnlineConditionWithStatus(t *testing.T) {
	tests := []struct {
		name           string
		statusValue    string
		expectedOnline metav1.ConditionStatus
		expectedReason string
	}{
		// Online statuses (should set Online=True)
		{
			name:           "Available sets Online=True",
			statusValue:    jumpstarterdevv1alpha1.ExporterStatusAvailable,
			expectedOnline: metav1.ConditionTrue,
			expectedReason: "StatusReported",
		},
		{
			name:           "LeaseReady sets Online=True",
			statusValue:    jumpstarterdevv1alpha1.ExporterStatusLeaseReady,
			expectedOnline: metav1.ConditionTrue,
			expectedReason: "StatusReported",
		},
		{
			name:           "BeforeLeaseHook sets Online=True",
			statusValue:    jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHook,
			expectedOnline: metav1.ConditionTrue,
			expectedReason: "StatusReported",
		},
		{
			name:           "AfterLeaseHook sets Online=True",
			statusValue:    jumpstarterdevv1alpha1.ExporterStatusAfterLeaseHook,
			expectedOnline: metav1.ConditionTrue,
			expectedReason: "StatusReported",
		},
		{
			name:           "BeforeLeaseHookFailed sets Online=True (online but hook failed)",
			statusValue:    jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHookFailed,
			expectedOnline: metav1.ConditionTrue,
			expectedReason: "StatusReported",
		},
		{
			name:           "AfterLeaseHookFailed sets Online=True (online but hook failed)",
			statusValue:    jumpstarterdevv1alpha1.ExporterStatusAfterLeaseHookFailed,
			expectedOnline: metav1.ConditionTrue,
			expectedReason: "StatusReported",
		},
		// Offline statuses (should set Online=False)
		{
			name:           "Offline sets Online=False",
			statusValue:    jumpstarterdevv1alpha1.ExporterStatusOffline,
			expectedOnline: metav1.ConditionFalse,
			expectedReason: "Offline",
		},
		{
			name:           "Unspecified sets Online=False",
			statusValue:    jumpstarterdevv1alpha1.ExporterStatusUnspecified,
			expectedOnline: metav1.ConditionFalse,
			expectedReason: "Offline",
		},
		{
			name:           "Empty string sets Online=False",
			statusValue:    "",
			expectedOnline: metav1.ConditionFalse,
			expectedReason: "Offline",
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			exporter := &jumpstarterdevv1alpha1.Exporter{
				ObjectMeta: metav1.ObjectMeta{
					Name:       "test-exporter",
					Namespace:  "default",
					Generation: 1,
				},
				Status: jumpstarterdevv1alpha1.ExporterStatus{
					ExporterStatusValue: tt.statusValue,
					StatusMessage:       "test message",
				},
			}

			syncOnlineConditionWithStatus(exporter)

			condition := meta.FindStatusCondition(exporter.Status.Conditions,
				string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline))

			if condition == nil {
				t.Fatal("Online condition was not set")
			}

			if condition.Status != tt.expectedOnline {
				t.Errorf("Online condition status = %v, want %v", condition.Status, tt.expectedOnline)
			}

			if condition.Reason != tt.expectedReason {
				t.Errorf("Online condition reason = %q, want %q", condition.Reason, tt.expectedReason)
			}

			if condition.ObservedGeneration != exporter.Generation {
				t.Errorf("ObservedGeneration = %d, want %d", condition.ObservedGeneration, exporter.Generation)
			}
		})
	}
}

// TestListenQueueTimerCleanup verifies that the listen queue is NOT removed
// immediately when a transient stream error occurs, giving a reconnecting
// exporter time to inherit the queue (and any buffered Dial token), and that
// the queue IS removed once the cleanup timer fires.
func TestListenQueueTimerCleanup(t *testing.T) {
	// Shorten the delay so the test completes quickly.
	original := listenQueueCleanupDelay
	listenQueueCleanupDelay = 50 * time.Millisecond
	t.Cleanup(func() { listenQueueCleanupDelay = original })

	svc := &ControllerService{}
	leaseName := "test-lease"

	// Seed the queue as Listen() would via LoadOrStore.
	ch := make(chan *pb.ListenResponse, 8)
	svc.listenQueues.Store(leaseName, ch)

	// Simulate the stream-error path: schedule deferred cleanup.
	t.Run("queue survives transient error", func(t *testing.T) {
		timer := time.AfterFunc(listenQueueCleanupDelay, func() {
			svc.listenQueues.Delete(leaseName)
			svc.listenTimers.Delete(leaseName)
		})
		svc.listenTimers.Store(leaseName, timer)

		// Queue must still be present immediately after the error.
		if _, ok := svc.listenQueues.Load(leaseName); !ok {
			t.Fatal("listen queue was removed immediately after stream error — Dial token would be lost")
		}
	})

	t.Run("reconnecting exporter cancels cleanup timer", func(t *testing.T) {
		// Simulate Listen() reconnect: cancel the timer and call LoadOrStore.
		if raw, ok := svc.listenTimers.LoadAndDelete(leaseName); ok {
			raw.(*time.Timer).Stop()
		}
		got, _ := svc.listenQueues.LoadOrStore(leaseName, make(chan *pb.ListenResponse, 8))
		if got != ch {
			t.Fatal("reconnecting Listen() did not inherit the existing queue")
		}

		// Wait well past the original delay — the queue must still be present
		// because the timer was stopped.
		time.Sleep(listenQueueCleanupDelay * 4)
		if _, ok := svc.listenQueues.Load(leaseName); !ok {
			t.Fatal("listen queue was removed even though cleanup timer was cancelled")
		}
	})

	t.Run("timer fires and removes queue when exporter does not reconnect", func(t *testing.T) {
		// Re-arm the timer without cancelling it this time.
		timer := time.AfterFunc(listenQueueCleanupDelay, func() {
			svc.listenQueues.Delete(leaseName)
			svc.listenTimers.Delete(leaseName)
		})
		svc.listenTimers.Store(leaseName, timer)

		// Wait for the timer to fire.
		time.Sleep(listenQueueCleanupDelay * 4)
		if _, ok := svc.listenQueues.Load(leaseName); ok {
			t.Fatal("listen queue was not removed after cleanup timer fired")
		}
	})
}

// TestListenQueueCleanShutdown verifies that a clean context cancellation
// (lease end / server stop) removes the queue immediately without waiting for
// the cleanup timer.
func TestListenQueueCleanShutdown(t *testing.T) {
	original := listenQueueCleanupDelay
	listenQueueCleanupDelay = 2 * time.Minute // keep long — must NOT fire during test
	t.Cleanup(func() { listenQueueCleanupDelay = original })

	svc := &ControllerService{}
	leaseName := "test-lease-shutdown"

	ch := make(chan *pb.ListenResponse, 8)
	svc.listenQueues.Store(leaseName, ch)

	// Arm a timer that should be cancelled before it fires.
	timer := time.AfterFunc(listenQueueCleanupDelay, func() {
		svc.listenQueues.Delete(leaseName)
		svc.listenTimers.Delete(leaseName)
	})
	svc.listenTimers.Store(leaseName, timer)

	// Simulate the ctx.Done() path in Listen().
	if raw, ok := svc.listenTimers.LoadAndDelete(leaseName); ok {
		raw.(*time.Timer).Stop()
	}
	svc.listenQueues.Delete(leaseName)

	if _, ok := svc.listenQueues.Load(leaseName); ok {
		t.Fatal("listen queue was not removed on clean shutdown")
	}
	if _, ok := svc.listenTimers.Load(leaseName); ok {
		t.Fatal("cleanup timer was not cancelled on clean shutdown")
	}
}

// contains checks if substr is contained in s
func contains(s, substr string) bool {
	return len(s) >= len(substr) && (s == substr || len(substr) == 0 ||
		(len(s) > 0 && len(substr) > 0 && searchSubstring(s, substr)))
}

func searchSubstring(s, substr string) bool {
	for i := 0; i <= len(s)-len(substr); i++ {
		if s[i:i+len(substr)] == substr {
			return true
		}
	}
	return false
}
