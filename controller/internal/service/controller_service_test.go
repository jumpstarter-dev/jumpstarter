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

func TestListenQueuesCleanupOnContextCancellation(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-ctx-cancel"

	svc.listenQueues.Store(leaseName, make(chan *pb.ListenResponse, 8))

	if _, ok := svc.listenQueues.Load(leaseName); !ok {
		t.Fatal("listenQueues entry should exist before Listen returns")
	}

	svc.cleanupListenQueue(leaseName)

	if _, ok := svc.listenQueues.Load(leaseName); ok {
		t.Error("listenQueues entry should be removed after listener context cancellation")
	}
}

func TestListenQueuesCleanupOnStreamSendFailure(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-send-fail"

	svc.listenQueues.Store(leaseName, make(chan *pb.ListenResponse, 8))

	if _, ok := svc.listenQueues.Load(leaseName); !ok {
		t.Fatal("listenQueues entry should exist before Listen returns")
	}

	svc.cleanupListenQueue(leaseName)

	if _, ok := svc.listenQueues.Load(leaseName); ok {
		t.Error("listenQueues entry should be removed after stream send failure")
	}
}

func TestListenQueuesNewListenerAfterCleanup(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-reconnect"

	svc.listenQueues.Store(leaseName, make(chan *pb.ListenResponse, 8))
	svc.cleanupListenQueue(leaseName)

	if _, ok := svc.listenQueues.Load(leaseName); ok {
		t.Fatal("listenQueues entry should be removed after first listener cleanup")
	}

	newQueue := make(chan *pb.ListenResponse, 8)
	svc.listenQueues.Store(leaseName, newQueue)

	loaded, ok := svc.listenQueues.Load(leaseName)
	if !ok {
		t.Fatal("listenQueues entry should exist after new listener connects")
	}

	testMsg := &pb.ListenResponse{
		RouterEndpoint: "test-endpoint",
		RouterToken:    "test-token",
	}
	loaded.(chan *pb.ListenResponse) <- testMsg

	received := <-loaded.(chan *pb.ListenResponse)
	if received.RouterEndpoint != "test-endpoint" {
		t.Errorf("received wrong endpoint: got %q, want %q", received.RouterEndpoint, "test-endpoint")
	}
	if received.RouterToken != "test-token" {
		t.Errorf("received wrong token: got %q, want %q", received.RouterToken, "test-token")
	}
}

func TestDialDoesNotPanicAfterListenerCleanup(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-dial-after-cleanup"

	svc.listenQueues.Store(leaseName, make(chan *pb.ListenResponse, 8))
	svc.cleanupListenQueue(leaseName)

	queue, loaded := svc.listenQueues.LoadOrStore(leaseName, make(chan *pb.ListenResponse, 8))
	if loaded {
		t.Error("LoadOrStore should have stored a new entry, not loaded an existing one")
	}

	msg := &pb.ListenResponse{
		RouterEndpoint: "new-endpoint",
		RouterToken:    "new-token",
	}

	select {
	case queue.(chan *pb.ListenResponse) <- msg:
	default:
		t.Error("should be able to send to newly created queue")
	}
}

func TestDialMessageDeliveredToNewListenerAfterCleanup(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-dial-new-listener"

	oldQueue := make(chan *pb.ListenResponse, 8)
	svc.listenQueues.Store(leaseName, oldQueue)
	svc.cleanupListenQueue(leaseName)

	newQueue, _ := svc.listenQueues.LoadOrStore(leaseName, make(chan *pb.ListenResponse, 8))

	msg := &pb.ListenResponse{
		RouterEndpoint: "reconnected-endpoint",
		RouterToken:    "reconnected-token",
	}
	newQueue.(chan *pb.ListenResponse) <- msg

	received := <-newQueue.(chan *pb.ListenResponse)
	if received.RouterEndpoint != "reconnected-endpoint" {
		t.Errorf("new listener received wrong endpoint: got %q, want %q",
			received.RouterEndpoint, "reconnected-endpoint")
	}
	if received.RouterToken != "reconnected-token" {
		t.Errorf("new listener received wrong token: got %q, want %q",
			received.RouterToken, "reconnected-token")
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
