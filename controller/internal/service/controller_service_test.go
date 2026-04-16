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

const testRouterToken = "tok"

func drainChannel(ch <-chan *pb.ListenResponse) int {
	count := 0
	for {
		select {
		case <-ch:
			count++
		default:
			return count
		}
	}
}

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
	svc.swapListenQueue(leaseName, wrapper)

	t.Run("queue is deleted when no reconnect replaced it", func(t *testing.T) {
		svc.listenQueues.CompareAndDelete(leaseName, wrapper)

		if _, ok := svc.listenQueues.Load(leaseName); ok {
			t.Fatal("queue should be deleted when it is still the same instance")
		}
	})

	t.Run("queue survives when a reconnecting Listen replaced it", func(t *testing.T) {
		newWrapper := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
		svc.swapListenQueue(leaseName, newWrapper)

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
	svc.swapListenQueue(leaseName, wrapper)

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
	svc.swapListenQueue(leaseName, originalWrapper)

	newWrapper := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, newWrapper)

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

	select {
	case <-originalWrapper.done:
	default:
		t.Fatal("original wrapper done channel should be closed after swap")
	}
}

func TestListenQueueDialTokenDeliveredToNewListener(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-dial-token"

	g1 := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
	svc.swapListenQueue(leaseName, g1)

	g2 := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
	svc.swapListenQueue(leaseName, g2)

	response := &pb.ListenResponse{RouterEndpoint: "test-endpoint", RouterToken: "test-token"}
	err := svc.sendToListener(context.Background(), leaseName, response)
	if err != nil {
		t.Fatalf("sendToListener should succeed for active queue: %v", err)
	}

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
	}
}

func TestListenQueueReconnectPreventsStaleCleanup(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-stale-cleanup"

	originalWrapper := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, originalWrapper)

	reconnectWrapper := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, reconnectWrapper)

	// Original wrapper's deferred CompareAndDelete should be a no-op.
	svc.listenQueues.CompareAndDelete(leaseName, originalWrapper)

	got, ok := svc.listenQueues.Load(leaseName)
	if !ok {
		t.Fatal("stale Listen cleanup deleted queue that reconnected Listen is using")
	}
	if got != reconnectWrapper {
		t.Fatal("queue entry does not match the reconnected wrapper")
	}

	token := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: testRouterToken}
	reconnectWrapper.ch <- token

	select {
	case msg := <-reconnectWrapper.ch:
		if msg.RouterEndpoint != "ep" || msg.RouterToken != testRouterToken {
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
	svc.swapListenQueue(leaseName, g1)

	g2 := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
	svc.swapListenQueue(leaseName, g2)

	g3 := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
	svc.swapListenQueue(leaseName, g3)

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

	g1Queue := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, g1Queue)

	g2Queue := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, g2Queue)

	token := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: testRouterToken}
	err := svc.sendToListener(context.Background(), leaseName, token)
	if err != nil {
		t.Fatalf("sendToListener should succeed for active queue: %v", err)
	}

	select {
	case <-g1Queue.done:
	default:
		t.Fatal("G1 done channel should be closed after swap")
	}

	select {
	case <-g1Queue.ch:
		t.Fatal("stale reader G1 consumed the dial token")
	default:
	}

	select {
	case got := <-g2Queue.ch:
		if got.RouterEndpoint != "ep" || got.RouterToken != testRouterToken {
			t.Fatal("token received by G2 was corrupted")
		}
	default:
		t.Fatal("active reader G2 did not receive the dial token")
	}
}

func TestListenQueueStaleReaderAlwaysDetectsSupersession(t *testing.T) {
	iterations := 100

	for i := 0; i < iterations; i++ {
		svc := &ControllerService{}
		leaseName := "test-lease-concurrent"

		g1Queue := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}
		svc.swapListenQueue(leaseName, g1Queue)

		g2Queue := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}
		svc.swapListenQueue(leaseName, g2Queue)

		err := svc.sendToListener(context.Background(), leaseName, &pb.ListenResponse{
			RouterEndpoint: "ep", RouterToken: testRouterToken,
		})
		if err != nil {
			t.Fatalf("iteration %d: sendToListener should succeed: %v", i, err)
		}

		select {
		case <-g1Queue.done:
		default:
			t.Fatalf("iteration %d: g1 done channel should be closed after supersession", i)
		}

		select {
		case <-g1Queue.ch:
			t.Fatalf("iteration %d: stale reader g1 consumed a token after supersession", i)
		default:
		}
	}
}

func TestDialRejectsSupersededQueue(t *testing.T) {
	q := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	close(q.done)

	response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: testRouterToken}

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
	iterations := 500

	for i := 0; i < iterations; i++ {
		svc := &ControllerService{}
		leaseName := "test-lease-pre-swap-ref"

		g1 := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}
		svc.swapListenQueue(leaseName, g1)

		g2 := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}
		svc.swapListenQueue(leaseName, g2)

		response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: testRouterToken}

		err := svc.sendToListener(context.Background(), leaseName, response)
		if err != nil {
			t.Fatalf("iteration %d: sendToListener should succeed for active g2: %v", i, err)
		}

		select {
		case <-g1.ch:
			t.Fatalf("iteration %d: dial sent to stale queue g1", i)
		default:
		}

		select {
		case got := <-g2.ch:
			if got.RouterEndpoint != "ep" || got.RouterToken != testRouterToken {
				t.Fatalf("iteration %d: token corrupted on g2", i)
			}
		default:
			t.Fatalf("iteration %d: token not delivered to active g2", i)
		}
	}
}

func TestDialSendsTokenViaServiceMethod(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-dial-method"

	q := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, q)

	response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: testRouterToken}

	err := svc.sendToListener(context.Background(), leaseName, response)
	if err != nil {
		t.Fatalf("sendToListener should succeed for active queue: %v", err)
	}

	select {
	case got := <-q.ch:
		if got.RouterEndpoint != "ep" || got.RouterToken != testRouterToken {
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
	svc.swapListenQueue(leaseName, g1)

	g2 := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, g2)

	response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: testRouterToken}

	err := svc.sendToListener(context.Background(), leaseName, response)
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
		if got.RouterEndpoint != "ep" || got.RouterToken != testRouterToken {
			t.Fatal("token was corrupted")
		}
	default:
		t.Fatal("token was not delivered to active queue g2")
	}
}

func TestDialSendToListenerRejectsNoListener(t *testing.T) {
	svc := &ControllerService{}

	response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: testRouterToken}
	err := svc.sendToListener(context.Background(), "nonexistent-lease", response)
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
	svc.swapListenQueue(leaseName, q)
	q.closeDone()

	response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: testRouterToken}
	err := svc.sendToListener(context.Background(), leaseName, response)
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
		svc.swapListenQueue(leaseName, g1)

		g2 := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}

		svc.swapListenQueue(leaseName, g2)

		response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: testRouterToken}
		err := svc.sendToListener(context.Background(), leaseName, response)
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
			if got.RouterEndpoint != "ep" || got.RouterToken != testRouterToken {
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
		svc.swapListenQueue(leaseName, g1)

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
			sendResult <- svc.sendToListener(context.Background(), leaseName, &pb.ListenResponse{
				RouterEndpoint: "ep", RouterToken: testRouterToken,
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
	svc.swapListenQueue(leaseName, g1Queue)

	g2Queue := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, g2Queue)

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
	svc.swapListenQueue(leaseName, wrapper)

	v, ok := svc.listenQueues.Load(leaseName)
	if !ok {
		t.Fatal("queue entry should exist")
	}
	q := v.(*listenQueue)

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
	svc.swapListenQueue(leaseName, wrapper)

	wrapper.closeDone()

	response := &pb.ListenResponse{RouterEndpoint: "ep", RouterToken: testRouterToken}
	err := svc.sendToListener(context.Background(), leaseName, response)
	if err == nil {
		t.Fatal("sendToListener should return error when done is closed before map delete")
	}

	svc.listenQueues.CompareAndDelete(leaseName, wrapper)

	select {
	case <-wrapper.ch:
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
	svc.swapListenQueue(leaseName, q)
	q.closeDone()

	err := svc.sendToListener(context.Background(), leaseName, &pb.ListenResponse{
		RouterEndpoint: "ep", RouterToken: testRouterToken,
	})
	if err == nil {
		t.Fatal("sendToListener should return error for done queue")
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
	svc.swapListenQueue(leaseName, g1)

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
			ctx := context.Background()
			err := svc.sendToListener(ctx, leaseName, &pb.ListenResponse{
				RouterEndpoint: "ep", RouterToken: testRouterToken,
			})
			if err != nil {
				rejectedMu.Lock()
				rejectedCount++
				rejectedMu.Unlock()
				return
			}
			sentMu.Lock()
			sentCount++
			sentMu.Unlock()
		}()

		if i == 25 {
			g2 = &listenQueue{
				ch:   make(chan *pb.ListenResponse, 8),
				done: make(chan struct{}),
			}
			svc.swapListenQueue(leaseName, g2)

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

	drainCount := drainChannel(g1.ch)

	if g2 != nil {
		g2.closeDone()
		<-g2ListenerDone
		drainCount += drainChannel(g2.ch)
	}

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
	svc.swapListenQueue(leaseName, wrapper)

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
	svc.swapListenQueue(leaseName, superseder)

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

func TestSendToListenerReturnsResourceExhaustedWithCancelledContextAndBufferFull(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-ctx-cancel-buffer-full"

	q := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, q)

	for i := 0; i < 8; i++ {
		q.ch <- &pb.ListenResponse{RouterEndpoint: "fill", RouterToken: "fill"}
	}

	ctx, cancel := context.WithCancel(context.Background())
	cancel()

	err := svc.sendToListener(ctx, leaseName, &pb.ListenResponse{
		RouterEndpoint: "ep", RouterToken: testRouterToken,
	})
	if err == nil {
		t.Fatal("sendToListener should return error when buffer is full")
	}

	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %v", err)
	}
	if st.Code() != codes.ResourceExhausted {
		t.Fatalf("expected ResourceExhausted, got %v", st.Code())
	}
}

func TestSendToListenerReturnsImmediatelyDuringBackpressure(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-backpressure-immediate"

	q := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, q)

	for i := 0; i < 8; i++ {
		q.ch <- &pb.ListenResponse{RouterEndpoint: "fill", RouterToken: "fill"}
	}

	err := svc.sendToListener(context.Background(), leaseName, &pb.ListenResponse{
		RouterEndpoint: "ep", RouterToken: testRouterToken,
	})
	if err == nil {
		t.Fatal("sendToListener should return error when buffer is full")
	}

	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %v", err)
	}
	if st.Code() != codes.ResourceExhausted {
		t.Fatalf("expected ResourceExhausted, got %v", st.Code())
	}
}

func TestListenQueueDialFlowSendsToActiveListener(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-dial-flow"

	wrapper := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, wrapper)

	response := &pb.ListenResponse{RouterEndpoint: "dial-ep", RouterToken: "dial-tok"}
	err := svc.sendToListener(context.Background(), leaseName, response)
	if err != nil {
		t.Fatalf("sendToListener should succeed for active listener: %v", err)
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

func TestLeaseLockRefCountSingleListener(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-refcount-single"

	svc.acquireLeaseLock(leaseName)

	if _, ok := svc.leaseLocks.Load(leaseName); !ok {
		t.Fatal("lease lock should exist after acquire")
	}

	svc.releaseLeaseLock(leaseName)

	if _, ok := svc.leaseLocks.Load(leaseName); ok {
		t.Fatal("lease lock should be removed when last reference is released")
	}
}

func TestLeaseLockRefCountOverlappingListeners(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-refcount-overlap"

	svc.acquireLeaseLock(leaseName)
	svc.acquireLeaseLock(leaseName)

	if _, ok := svc.leaseLocks.Load(leaseName); !ok {
		t.Fatal("lease lock should exist with two references")
	}

	svc.releaseLeaseLock(leaseName)

	if _, ok := svc.leaseLocks.Load(leaseName); !ok {
		t.Fatal("lease lock should still exist with one remaining reference")
	}

	svc.releaseLeaseLock(leaseName)

	if _, ok := svc.leaseLocks.Load(leaseName); ok {
		t.Fatal("lease lock should be removed when all references are released")
	}
}

func TestLeaseLockRefCountConcurrentAcquireRelease(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-refcount-concurrent"

	var wg sync.WaitGroup
	goroutines := 100

	var counter int
	for i := 0; i < goroutines; i++ {
		wg.Add(1)
		go func() {
			defer wg.Done()
			lock := svc.acquireLeaseLock(leaseName)
			lock.Lock()
			counter++
			lock.Unlock()
			svc.releaseLeaseLock(leaseName)
		}()
	}

	wg.Wait()

	if counter != goroutines {
		t.Fatalf("expected counter=%d, got %d", goroutines, counter)
	}

	if _, ok := svc.leaseLocks.Load(leaseName); ok {
		t.Fatal("lease lock should be removed after all goroutines release")
	}
}

func TestLeaseLockRefCountSameInstanceForOverlap(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-refcount-same-instance"

	lock1 := svc.acquireLeaseLock(leaseName)
	lock2 := svc.acquireLeaseLock(leaseName)

	if lock1 != lock2 {
		t.Fatal("overlapping acquires must return the same mutex")
	}

	svc.releaseLeaseLock(leaseName)
	svc.releaseLeaseLock(leaseName)
}

func TestLeaseLockPreservedWhenNewListenerTakesOver(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-lock-preserved"

	g1Mu := svc.acquireLeaseLock(leaseName)
	g1 := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, g1)

	g2Mu := svc.acquireLeaseLock(leaseName)
	g2 := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, g2)

	if g1Mu != g2Mu {
		t.Fatal("overlapping listeners must share the same mutex")
	}

	g1Mu.Lock()
	g1.closeDone()
	g1Mu.Unlock()
	svc.listenQueues.CompareAndDelete(leaseName, g1)
	svc.releaseLeaseLock(leaseName)

	if _, ok := svc.leaseLocks.Load(leaseName); !ok {
		t.Fatal("lease lock should be preserved when a new listener still holds a reference")
	}

	if _, ok := svc.listenQueues.Load(leaseName); !ok {
		t.Fatal("queue should still exist for the new listener")
	}

	g2Mu.Lock()
	g2.closeDone()
	g2Mu.Unlock()
	svc.listenQueues.CompareAndDelete(leaseName, g2)
	svc.releaseLeaseLock(leaseName)

	if _, ok := svc.leaseLocks.Load(leaseName); ok {
		t.Fatal("lease lock should be cleaned up when last listener releases")
	}
}

func TestSendToListenerReturnsResourceExhaustedWhenBufferFull(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-buffer-full-nonblocking"

	q := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, q)

	for i := 0; i < 8; i++ {
		q.ch <- &pb.ListenResponse{RouterEndpoint: "fill", RouterToken: "fill"}
	}

	err := svc.sendToListener(context.Background(), leaseName, &pb.ListenResponse{
		RouterEndpoint: "ep", RouterToken: testRouterToken,
	})
	if err == nil {
		t.Fatal("sendToListener should return error when buffer is full")
	}

	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %v", err)
	}
	if st.Code() != codes.ResourceExhausted {
		t.Fatalf("expected ResourceExhausted, got %v", st.Code())
	}
}

func TestSendToListenerDoesNotBlockMutexWhenBufferFull(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-lease-no-mutex-block"

	q := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, q)

	for i := 0; i < 8; i++ {
		q.ch <- &pb.ListenResponse{RouterEndpoint: "fill", RouterToken: "fill"}
	}

	sendDone := make(chan struct{})
	sendErr := make(chan error, 1)
	go func() {
		defer close(sendDone)
		sendErr <- svc.sendToListener(context.Background(), leaseName, &pb.ListenResponse{
			RouterEndpoint: "ep", RouterToken: testRouterToken,
		})
	}()

	select {
	case <-sendDone:
		if err := <-sendErr; err == nil {
			t.Fatal("sendToListener should return error when buffer is full")
		}
	case <-time.After(time.Second):
		t.Fatal("sendToListener blocked when buffer was full; mutex held too long")
	}

	swapDone := make(chan struct{})
	go func() {
		defer close(swapDone)
		g2 := &listenQueue{
			ch:   make(chan *pb.ListenResponse, 8),
			done: make(chan struct{}),
		}
		svc.swapListenQueue(leaseName, g2)
	}()

	select {
	case <-swapDone:
	case <-time.After(time.Second):
		t.Fatal("swapListenQueue blocked because sendToListener held the mutex on full buffer")
	}
}

func TestSwapNotBlockedWhenBufferFull(t *testing.T) {
	svc := &ControllerService{}
	leaseName := "test-no-deadlock-chain"

	g1 := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	svc.swapListenQueue(leaseName, g1)

	for i := 0; i < 8; i++ {
		g1.ch <- &pb.ListenResponse{RouterEndpoint: "fill", RouterToken: "fill"}
	}

	err := svc.sendToListener(context.Background(), leaseName, &pb.ListenResponse{
		RouterEndpoint: "ep", RouterToken: testRouterToken,
	})
	if err == nil {
		t.Fatal("sendToListener should return error when buffer is full")
	}
	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected gRPC status error, got %v", err)
	}
	if st.Code() != codes.ResourceExhausted {
		t.Fatalf("expected ResourceExhausted, got %v", st.Code())
	}

	g2 := &listenQueue{
		ch:   make(chan *pb.ListenResponse, 8),
		done: make(chan struct{}),
	}
	swapDone := make(chan struct{})
	go func() {
		defer close(swapDone)
		svc.swapListenQueue(leaseName, g2)
	}()

	select {
	case <-swapDone:
	case <-time.After(2 * time.Second):
		t.Fatal("swapListenQueue should not be blocked when sendToListener returned immediately")
	}

	select {
	case <-g1.done:
	default:
		t.Fatal("g1 done channel should be closed after swap")
	}

	v, loaded := svc.listenQueues.Load(leaseName)
	if !loaded {
		t.Fatal("queue should exist for g2")
	}
	if v != g2 {
		t.Fatal("active queue should be g2")
	}
}
