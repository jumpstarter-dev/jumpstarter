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
	"context"
	"strings"
	"sync"
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
					if !strings.Contains(st.Message(), tt.expectedSubstr) {
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

func TestListenQueueStaleReaderAlwaysDetectsSupersession(t *testing.T) {
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

func TestDialRejectsSupersededQueue(t *testing.T) {
	q := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	close(q.done)

	response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: "tok"}

	rejected := false
	select {
	case <-q.done:
		rejected = true
	default:
	}
	if !rejected {
		select {
		case <-q.done:
			rejected = true
		case q.ch <- response:
		}
	}

	if !rejected {
		t.Fatal("dial must reject send to a queue whose done channel is closed")
	}

	select {
	case <-q.ch:
		t.Fatal("token should not have been buffered in a superseded queue")
	default:
	}
}

func TestDialWithPreSwapReferenceNeverSendsToStaleQueue(t *testing.T) {
	staleSends := 0
	iterations := 500

	for i := 0; i < iterations; i++ {
		svc := &ControllerService{}
		leaseName := "test-lease-pre-swap-ref"

		g1 := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}
		svc.listenQueues.Store(leaseName, g1)

		v, _ := svc.listenQueues.Load(leaseName)
		preSwapRef := v.(*listenQueue)

		g2 := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}
		old, _ := svc.listenQueues.Swap(leaseName, g2)
		old.(*listenQueue).closeDone()

		response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: "tok"}

		sent := false
		select {
		case <-preSwapRef.done:
		default:
			select {
			case <-preSwapRef.done:
			case preSwapRef.ch <- response:
				sent = true
			}
		}

		if sent {
			staleSends++
			<-preSwapRef.ch
		}
	}

	if staleSends > 0 {
		t.Fatalf("dial sent to stale queue %d out of %d iterations", staleSends, iterations)
	}
}

func TestDialSendsTokenViaServiceMethod(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-dial-method"

	q := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.listenQueues.Store(leaseName, q)

	response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: "tok"}

	err := svc.sendToListener(leaseName, response)
	if err != nil {
		t.Fatalf("sendToListener should succeed for active queue: %v", err)
	}

	select {
	case got := <-q.ch:
		if got.RouterEndpoint != "ep" || got.RouterToken != "tok" {
			t.Fatal("token was corrupted")
		}
	default:
		t.Fatal("token was not delivered")
	}
}

func TestDialSendToListenerRejectsSupersededQueue(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-dial-method-superseded"

	g1 := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.listenQueues.Store(leaseName, g1)

	g2 := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	old, _ := svc.listenQueues.Swap(leaseName, g2)
	old.(*listenQueue).closeDone()

	response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: "tok"}

	err := svc.sendToListener(leaseName, response)
	if err != nil {
		t.Fatalf("sendToListener should succeed for the new active queue: %v", err)
	}

	select {
	case <-g1.ch:
		t.Fatal("token was delivered to superseded queue g1")
	default:
	}

	select {
	case got := <-g2.ch:
		if got.RouterEndpoint != "ep" || got.RouterToken != "tok" {
			t.Fatal("token was corrupted")
		}
	default:
		t.Fatal("token was not delivered to active queue g2")
	}
}

func TestDialSendToListenerRejectsNoListener(t *testing.T) {
	svc := &ControllerService{}

	response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: "tok"}
	err := svc.sendToListener("nonexistent-lease", response)
	if err == nil {
		t.Fatal("sendToListener should return error when no listener exists")
	}
}

func TestDialSendToListenerRejectsDoneQueue(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-done-queue"

	q := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	q.closeDone()
	svc.listenQueues.Store(leaseName, q)

	response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: "tok"}
	err := svc.sendToListener(leaseName, response)
	if err == nil {
		t.Fatal("sendToListener should return error for done queue")
	}

	select {
	case <-q.ch:
		t.Fatal("token should not be buffered in a done queue")
	default:
	}
}

func TestDialSendToListenerSerializesWithSwap(t *testing.T) {
	// Verify that swapListenQueue followed by sendToListener always delivers
	// to the new queue (or returns an error), never to the superseded queue.
	// This tests the scenario where the swap completes before the send.
	iterations := 500

	for i := 0; i < iterations; i++ {
		svc := &ControllerService{}
		leaseName := "test-lease-serialized"

		g1 := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}
		svc.listenQueues.Store(leaseName, g1)

		g2 := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}

		svc.swapListenQueue(leaseName, g2)

		response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: "tok"}
		err := svc.sendToListener(leaseName, response)
		if err != nil {
			t.Fatalf("iteration %d: sendToListener should succeed for active g2: %v", i, err)
		}

		select {
		case <-g1.ch:
			t.Fatalf("iteration %d: token delivered to superseded g1", i)
		default:
		}

		select {
		case got := <-g2.ch:
			if got.RouterEndpoint != "ep" || got.RouterToken != "tok" {
				t.Fatalf("iteration %d: token corrupted on g2", i)
			}
		default:
			t.Fatalf("iteration %d: token not delivered to active g2", i)
		}
	}
}

func TestDialSendToListenerConcurrentWithSwapNeverLandsOnSuperseded(t *testing.T) {
	// Race swapListenQueue against sendToListener using goroutines.
	// The per-lease mutex guarantees that the Load+send in sendToListener
	// is atomic with respect to the Swap+closeDone in swapListenQueue.
	// When sendToListener acquires the lock first, it sends to g1 (which
	// is still current -- a valid send). When swapListenQueue acquires
	// first, sendToListener sees g2 as the current queue.
	//
	// The invariant: if sendToListener returns nil, the done channel of the
	// queue it sent to was NOT closed at the time of the send (guaranteed by
	// the lock preventing concurrent swap+closeDone).
	iterations := 500
	sentToG1 := 0
	sentToG2 := 0
	rejected := 0

	for i := 0; i < iterations; i++ {
		svc := &ControllerService{}
		leaseName := "test-lease-concurrent-serial"

		g1 := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}
		svc.listenQueues.Store(leaseName, g1)

		g2 := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}

		swapDone := make(chan struct{})
		sendResult := make(chan error, 1)

		go func() {
			defer close(swapDone)
			svc.swapListenQueue(leaseName, g2)
		}()
		go func() {
			sendResult <- svc.sendToListener(leaseName, &pb.ListenResponse{
				RouterEndpoint: "ep", RouterToken: "tok",
			})
		}()

		<-swapDone
		sendErr := <-sendResult

		if sendErr != nil {
			rejected++
			continue
		}

		onG1 := false
		select {
		case <-g1.ch:
			onG1 = true
			sentToG1++
		default:
		}
		onG2 := false
		select {
		case <-g2.ch:
			onG2 = true
			sentToG2++
		default:
		}

		if !onG1 && !onG2 {
			t.Fatalf("iteration %d: send succeeded but token is lost", i)
		}
		if onG1 && onG2 {
			t.Fatalf("iteration %d: token duplicated across queues", i)
		}
	}

	if sentToG1+sentToG2+rejected != iterations {
		t.Fatalf("accounting error: g1=%d g2=%d rejected=%d total=%d",
			sentToG1, sentToG2, rejected, sentToG1+sentToG2+rejected)
	}
}

func TestListenQueueDoneClosedOnNormalExit(t *testing.T) {
	q := &listenQueue{
		ch:        make(chan *pb.ListenResponse, 8),
		done:      make(chan struct{}),
		closeOnce: sync.Once{},
	}

	q.closeDone()

	select {
	case <-q.done:
	default:
		t.Fatal("done channel should be closed after closeDone is called")
	}

	q.closeDone()

	select {
	case <-q.done:
	default:
		t.Fatal("done channel should remain closed after duplicate closeDone call")
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

func TestListenQueueDoneClosedBeforeMapDelete(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-defer-order"

	wrapper := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.listenQueues.Store(leaseName, wrapper)

	// Simulate a Dial that loaded the queue reference before Listen exits.
	v, ok := svc.listenQueues.Load(leaseName)
	if !ok {
		t.Fatal("queue entry should exist")
	}
	q := v.(*listenQueue)

	// Simulate Listen exit with correct defer order: closeDone first, then CompareAndDelete.
	// This is the order that prevents the TOCTOU race.
	q.closeDone()
	svc.listenQueues.CompareAndDelete(leaseName, wrapper)

	// The Dial that loaded q before cleanup must see done is closed.
	select {
	case <-q.done:
		// correct: Dial detects the listener exited
	default:
		t.Fatal("Dial did not detect listener exit via done channel")
	}

	// Map entry should be removed.
	if _, ok := svc.listenQueues.Load(leaseName); ok {
		t.Fatal("map entry should be removed after cleanup")
	}
}

func TestListenQueueDoneClosedBeforeMapDeleteWithConcurrentDial(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-defer-order-concurrent"

	wrapper := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.listenQueues.Store(leaseName, wrapper)

	// Simulate a Dial that loads the queue ref and then checks done.
	// With correct defer order (closeDone before CompareAndDelete),
	// done is closed before the map entry is removed, so Dial always
	// sees the closed done channel.
	v, _ := svc.listenQueues.Load(leaseName)
	q := v.(*listenQueue)

	response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: "tok"}

	// Close done (simulating closeDone() running first in defer chain).
	q.closeDone()

	// Dial's pre-check: done is already closed, so send is rejected.
	rejected := false
	select {
	case <-q.done:
		rejected = true
	default:
	}
	if !rejected {
		select {
		case <-q.done:
			rejected = true
		case q.ch <- response:
		}
	}

	if !rejected {
		t.Fatal("Dial must reject send when done is closed before map delete")
	}

	// Now map entry is removed (second defer).
	svc.listenQueues.CompareAndDelete(leaseName, wrapper)

	// No token should be buffered.
	select {
	case <-q.ch:
		t.Fatal("token should not be buffered in a queue whose done was closed first")
	default:
	}
}

func TestListenQueueDialReturnsUnavailableWhenNoListener(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "nonexistent-lease"

	_, ok := svc.listenQueues.Load(leaseName)
	if ok {
		t.Fatal("expected no entry for nonexistent lease")
	}
}

func TestListenQueueDialReturnsUnavailableWhenDoneClosed(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-done-closed"

	q := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	q.closeDone()
	svc.listenQueues.Store(leaseName, q)

	v, ok := svc.listenQueues.Load(leaseName)
	if !ok {
		t.Fatal("queue entry should exist")
	}
	loaded := v.(*listenQueue)

	select {
	case <-loaded.done:
	default:
		t.Fatal("dial pre-check should detect closed done channel")
	}
}

func TestListenQueueContextCancellationExitsListenLoop(t *testing.T) {
	wrapper := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}

	ctx, cancel := context.WithCancel(context.Background())
	exited := make(chan struct{})

	go func() {
		defer close(exited)
		for {
			select {
			case <-ctx.Done():
				return
			case <-wrapper.done:
				return
			case <-wrapper.ch:
			}
		}
	}()

	cancel()

	select {
	case <-exited:
	case <-time.After(time.Second):
		t.Fatal("listen loop did not exit after context cancellation")
	}
}

func TestListenQueueConcurrentDialDuringReconnection(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-concurrent-dial"

	g1 := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.listenQueues.Store(leaseName, g1)

	var deliveredCount int64
	var mu sync.Mutex

	g1ListenerDone := make(chan struct{})
	go func() {
		defer close(g1ListenerDone)
		for {
			select {
			case <-g1.done:
				return
			case <-g1.ch:
				mu.Lock()
				deliveredCount++
				mu.Unlock()
			}
		}
	}()

	dialAttempts := 50
	var dialWg sync.WaitGroup
	var rejectedCount int64
	var rejectedMu sync.Mutex
	var sentCount int64
	var sentMu sync.Mutex

	var g2 *listenQueue
	g2ListenerDone := make(chan struct{})

	for i := 0; i < dialAttempts; i++ {
		dialWg.Add(1)
		go func() {
			defer dialWg.Done()
			v, ok := svc.listenQueues.Load(leaseName)
			if !ok {
				rejectedMu.Lock()
				rejectedCount++
				rejectedMu.Unlock()
				return
			}
			q := v.(*listenQueue)
			select {
			case <-q.done:
				rejectedMu.Lock()
				rejectedCount++
				rejectedMu.Unlock()
				return
			default:
			}
			select {
			case <-q.done:
				rejectedMu.Lock()
				rejectedCount++
				rejectedMu.Unlock()
			case q.ch <- &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: "tok"}:
				sentMu.Lock()
				sentCount++
				sentMu.Unlock()
			}
		}()

		if i == 25 {
			g2 = &listenQueue{
				ch:   make(chan *pb.ListenResponse, 8),
				done: make(chan struct{}),
			}
			old, _ := svc.listenQueues.Swap(leaseName, g2)
			old.(*listenQueue).closeDone()

			localG2 := g2
			go func() {
				defer close(g2ListenerDone)
				for {
					select {
					case <-localG2.done:
						return
					case <-localG2.ch:
						mu.Lock()
						deliveredCount++
						mu.Unlock()
					}
				}
			}()
		}
	}

	dialWg.Wait()

	<-g1ListenerDone

	drainCount := 0
	for {
		select {
		case <-g1.ch:
			drainCount++
		default:
			goto drained
		}
	}
drained:

	if g2 != nil {
		g2.closeDone()
		<-g2ListenerDone
		for {
			select {
			case <-g2.ch:
				drainCount++
			default:
				goto g2drained
			}
		}
	}
g2drained:

	mu.Lock()
	delivered := deliveredCount
	mu.Unlock()
	rejectedMu.Lock()
	rejected := rejectedCount
	rejectedMu.Unlock()
	sentMu.Lock()
	sent := sentCount
	sentMu.Unlock()

	totalHandled := delivered + rejected + int64(drainCount)
	if totalHandled != int64(dialAttempts) {
		t.Fatalf("expected %d total outcomes, got %d delivered + %d rejected + %d drained = %d",
			dialAttempts, delivered, rejected, drainCount, totalHandled)
	}

	if sent != delivered+int64(drainCount) {
		t.Fatalf("sent count %d does not match delivered %d + drained %d",
			sent, delivered, drainCount)
	}

	select {
	case <-g1.done:
	default:
		t.Fatal("g1 done channel should be closed after reconnection")
	}
}

func TestListenQueueListenLoopDeliversTokensAndExitsOnDone(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-listen-loop"

	wrapper := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	old, loaded := svc.listenQueues.Swap(leaseName, wrapper)
	if loaded {
		old.(*listenQueue).closeDone()
	}

	delivered := make(chan *pb.ListenResponse, 8)
	loopExited := make(chan struct{})

	go func() {
		defer close(loopExited)
		defer svc.listenQueues.CompareAndDelete(leaseName, wrapper)
		defer wrapper.closeDone()
		for {
			select {
			case <-wrapper.done:
				return
			case msg := <-wrapper.ch:
				delivered <- msg
			}
		}
	}()

	wrapper.ch <- &pb.ListenResponse{RouterEndpoint: "ep1", RouterToken: "tok1"}
	wrapper.ch <- &pb.ListenResponse{RouterEndpoint: "ep2", RouterToken: "tok2"}

	for i := 0; i < 2; i++ {
		select {
		case msg := <-delivered:
			if msg.RouterEndpoint == "" || msg.RouterToken == "" {
				t.Fatal("received empty token")
			}
		case <-time.After(time.Second):
			t.Fatal("timed out waiting for token delivery")
		}
	}

	superseder := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	prev, _ := svc.listenQueues.Swap(leaseName, superseder)
	prev.(*listenQueue).closeDone()

	select {
	case <-loopExited:
	case <-time.After(time.Second):
		t.Fatal("listen loop did not exit after supersession")
	}

	v, ok := svc.listenQueues.Load(leaseName)
	if !ok {
		t.Fatal("queue entry should still exist for superseder")
	}
	if v != superseder {
		t.Fatal("queue entry should be the superseder")
	}
}

func TestListenQueueDialFlowSendsToActiveListener(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-dial-flow"

	wrapper := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.listenQueues.Swap(leaseName, wrapper)

	ctx := context.Background()
	response := &pb.ListenResponse{RouterEndpoint: "dial-ep", RouterToken: "dial-tok"}

	v, ok := svc.listenQueues.Load(leaseName)
	if !ok {
		t.Fatal("queue entry should exist")
	}
	q := v.(*listenQueue)
	select {
	case <-q.done:
		t.Fatal("done channel should not be closed for active listener")
	default:
	}
	select {
	case <-ctx.Done():
		t.Fatal("context should not be done")
	case <-q.done:
		t.Fatal("done channel should not be closed for active listener")
	case q.ch <- response:
	}

	select {
	case got := <-wrapper.ch:
		if got.RouterEndpoint != "dial-ep" || got.RouterToken != "dial-tok" {
			t.Fatal("received corrupted token")
		}
	default:
		t.Fatal("token was not delivered to the active listener")
	}
}

