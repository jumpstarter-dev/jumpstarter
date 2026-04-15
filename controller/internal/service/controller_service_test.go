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

func TestListenQueueCompareAndDeleteOnStreamError(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-stream-error"

	wrapper := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
	svc.listenQueues.Store(leaseName, wrapper)

	t.Run("queue is deleted when no reconnect replaced it", func(t *testing.T) {
		svc.listenQueues.CompareAndDelete(leaseName, wrapper)

		if _, ok := svc.listenQueues.Load(leaseName); ok {
			t.Fatal("queue should be deleted when it is still the same instance")
		}
	})

	t.Run("queue survives when a reconnecting Listen replaced it", func(t *testing.T) {
		newWrapper := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
		svc.listenQueues.Store(leaseName, newWrapper)

		svc.listenQueues.CompareAndDelete(leaseName, wrapper)

		got, ok := svc.listenQueues.Load(leaseName)
		if !ok {
			t.Fatal("queue was deleted even though a new Listen replaced it")
		}
		if got != newWrapper {
			t.Fatal("queue was replaced with something unexpected")
		}
	})
}

func TestListenQueueCompareAndDeleteOnCleanShutdown(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-shutdown"

	wrapper := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
	svc.listenQueues.Store(leaseName, wrapper)

	svc.listenQueues.CompareAndDelete(leaseName, wrapper)

	if _, ok := svc.listenQueues.Load(leaseName); ok {
		t.Fatal("queue should be removed on clean shutdown")
	}
}

func TestListenQueueReconnectCreatesNewChannel(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-reconnect"

	originalWrapper := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.listenQueues.Store(leaseName, originalWrapper)

	newWrapper := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	old, loaded := svc.listenQueues.Swap(leaseName, newWrapper)
	if !loaded {
		t.Fatal("Swap should have found the existing entry")
	}
	close(old.(*listenQueue).done)

	v, ok := svc.listenQueues.Load(leaseName)
	if !ok {
		t.Fatal("queue entry should still exist")
	}
	current := v.(*listenQueue)
	if current.ch == originalWrapper.ch {
		t.Fatal("reconnecting Listen must use a new channel, not the old one")
	}
	if current != newWrapper {
		t.Fatal("queue entry should be the new wrapper")
	}
}

func TestListenQueueDialTokenDeliveredToNewListener(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-dial-token"

	g1 := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
	svc.listenQueues.Store(leaseName, g1)

	g2 := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
	old, _ := svc.listenQueues.Swap(leaseName, g2)
	close(old.(*listenQueue).done)

	// Dial loads the current queue and sends a token.
	v, ok := svc.listenQueues.Load(leaseName)
	if !ok {
		t.Fatal("queue entry should exist")
	}
	v.(*listenQueue).ch <- &pb.ListenResponse{RouterEndpoint: "test-endpoint", RouterToken: "test-token"}

	// Token must be on G2's channel, not G1's.
	select {
	case got := <-g2.ch:
		if got.RouterEndpoint != "test-endpoint" || got.RouterToken != "test-token" {
			t.Fatal("dial token was corrupted")
		}
	default:
		t.Fatal("dial token was not delivered to the new listener")
	}

	select {
	case <-g1.ch:
		t.Fatal("dial token was delivered to the old listener")
	default:
		// expected: G1 has nothing
	}
}

func TestListenQueueReconnectPreventsStaleCleanup(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-stale-cleanup"

	originalWrapper := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.listenQueues.Store(leaseName, originalWrapper)

	reconnectWrapper := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	old, _ := svc.listenQueues.Swap(leaseName, reconnectWrapper)
	close(old.(*listenQueue).done)

	// Original wrapper's deferred CompareAndDelete should be a no-op.
	svc.listenQueues.CompareAndDelete(leaseName, originalWrapper)

	got, ok := svc.listenQueues.Load(leaseName)
	if !ok {
		t.Fatal("stale Listen cleanup deleted queue that reconnected Listen is using")
	}
	if got != reconnectWrapper {
		t.Fatal("queue entry does not match the reconnected wrapper")
	}

	token := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: "tok"}
	reconnectWrapper.ch <- token

	select {
	case msg := <-reconnectWrapper.ch:
		if msg.RouterEndpoint != "ep" || msg.RouterToken != "tok" {
			t.Fatal("token was corrupted after stale cleanup attempt")
		}
	default:
		t.Fatal("token was lost after stale cleanup attempt")
	}
}

func TestListenQueueConcurrentSwapSupersedes(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-concurrent-swap"

	g1 := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
	svc.listenQueues.Store(leaseName, g1)

	// G2 swaps in, superseding G1.
	g2 := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
	old2, _ := svc.listenQueues.Swap(leaseName, g2)
	close(old2.(*listenQueue).done)

	// G3 swaps in, superseding G2.
	g3 := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
	old3, _ := svc.listenQueues.Swap(leaseName, g3)
	close(old3.(*listenQueue).done)

	// G1 and G2 should both have their done channels closed.
	select {
	case <-g1.done:
	default:
		t.Fatal("G1 done channel should be closed")
	}
	select {
	case <-g2.done:
	default:
		t.Fatal("G2 done channel should be closed")
	}

	// G3 should still be active.
	select {
	case <-g3.done:
		t.Fatal("G3 done channel should not be closed")
	default:
	}

	// G1 and G2 deferred CompareAndDelete are no-ops.
	svc.listenQueues.CompareAndDelete(leaseName, g1)
	svc.listenQueues.CompareAndDelete(leaseName, g2)

	got, ok := svc.listenQueues.Load(leaseName)
	if !ok {
		t.Fatal("queue was deleted by stale CompareAndDelete")
	}
	if got != g3 {
		t.Fatal("queue entry does not match G3")
	}
}

func TestListenQueueStaleReaderConsumesDialToken(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-stale-reader"

	// G1 starts listening: creates its own queue and stores it.
	g1Queue := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.listenQueues.Store(leaseName, g1Queue)

	// G2 reconnects: creates a NEW queue with its own channel and swaps it in.
	g2Queue := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	old, loaded := svc.listenQueues.Swap(leaseName, g2Queue)
	if !loaded {
		t.Fatal("Swap should have found the existing G1 entry")
	}
	// Signal the old goroutine to stop.
	oldQueue := old.(*listenQueue)
	close(oldQueue.done)

	// Simulate Dial: loads the current queue and sends a token.
	v, ok := svc.listenQueues.Load(leaseName)
	if !ok {
		t.Fatal("queue entry should exist for lease")
	}
	currentQueue := v.(*listenQueue)
	token := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: "tok"}
	currentQueue.ch <- token

	// G1 should NOT receive the token (its done channel is closed).
	select {
	case <-g1Queue.done:
		// G1 detected supersession -- correct behavior.
	case <-g1Queue.ch:
		t.Fatal("stale reader G1 consumed the dial token")
	}

	// G2 MUST receive the token.
	select {
	case got := <-g2Queue.ch:
		if got.RouterEndpoint != "ep" || got.RouterToken != "tok" {
			t.Fatal("token received by G2 was corrupted")
		}
	default:
		t.Fatal("active reader G2 did not receive the dial token")
	}
}

func TestListenQueueConcurrentReadersAreNonDeterministic(t *testing.T) {
	staleWins := 0
	iterations := 100

	for i := 0; i < iterations; i++ {
		svc := &ControllerService{}
		leaseName := "test-lease-concurrent"

		g1Queue := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}
		svc.listenQueues.Store(leaseName, g1Queue)

		g2Queue := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}
		old, _ := svc.listenQueues.Swap(leaseName, g2Queue)
		close(old.(*listenQueue).done)

		v, _ := svc.listenQueues.Load(leaseName)
		currentQueue := v.(*listenQueue)
		currentQueue.ch <- &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: "tok"}

		// G1's done is closed, so it should always detect supersession.
		// If the token ends up on G1's channel, that is a stale win.
		select {
		case <-g1Queue.done:
			// correct: G1 sees done
		case <-g1Queue.ch:
			staleWins++
		}
	}

	if staleWins > 0 {
		t.Fatalf("stale reader won %d out of %d iterations, expected 0", staleWins, iterations)
	}
}

func TestListenQueueSupersessionSignaling(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-supersession"

	g1Queue := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.listenQueues.Store(leaseName, g1Queue)

	g2Queue := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	old, loaded := svc.listenQueues.Swap(leaseName, g2Queue)
	if !loaded {
		t.Fatal("Swap should return the old entry")
	}
	close(old.(*listenQueue).done)

	// Verify G1's done channel is closed.
	select {
	case <-g1Queue.done:
		// expected
	default:
		t.Fatal("G1 done channel was not closed after supersession")
	}

	// Verify G2's done channel is still open.
	select {
	case <-g2Queue.done:
		t.Fatal("G2 done channel should not be closed")
	default:
		// expected
	}

	// CompareAndDelete by G1 should be a no-op (G2 is current).
	svc.listenQueues.CompareAndDelete(leaseName, g1Queue)
	v, ok := svc.listenQueues.Load(leaseName)
	if !ok {
		t.Fatal("G1 cleanup deleted the queue that G2 owns")
	}
	if v != g2Queue {
		t.Fatal("queue entry does not match G2's queue")
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
