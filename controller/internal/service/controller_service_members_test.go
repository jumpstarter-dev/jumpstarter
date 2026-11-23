/*
Copyright 2026.

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
	"html/template"
	"strings"
	"sync"
	"testing"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/config"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apiserver/pkg/authentication/user"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/controller-runtime/pkg/client/interceptor"
)

const memberNamespace = "lab"

// callerKey carries the identity that callerAttributesGetter authenticates,
// so that one service can serve a client and several exporters in a test.
type callerKey struct{}

type caller struct{ resource, name string }

func asClient(ctx context.Context, name string) context.Context {
	return context.WithValue(ctx, callerKey{}, caller{resource: "Client", name: name})
}

func asExporter(ctx context.Context, name string) context.Context {
	ctx = metadata.NewIncomingContext(ctx, metadata.Pairs("jumpstarter-kind", "Exporter"))
	return context.WithValue(ctx, callerKey{}, caller{resource: "Exporter", name: name})
}

type callerAttributesGetter struct{}

func (callerAttributesGetter) ContextAttributes(ctx context.Context, u user.Info) (authorizer.Attributes, error) {
	c, _ := ctx.Value(callerKey{}).(caller)
	return authorizer.AttributesRecord{User: u, Namespace: memberNamespace, Resource: c.resource, Name: c.name}, nil
}

func boundMember(role, exporter string) jumpstarterdevv1alpha1.LeaseMemberStatus {
	return jumpstarterdevv1alpha1.LeaseMemberStatus{
		Name:        role,
		State:       jumpstarterdevv1alpha1.LeaseMemberStateBound,
		ExporterRef: &corev1.LocalObjectReference{Name: exporter},
	}
}

// memberLease is a member-form lease in the state that the lease controller
// gives it after the bind: the service only reads that state.
func memberLease() *jumpstarterdevv1alpha1.Lease {
	selector := func(deviceType string) *metav1.LabelSelector {
		return &metav1.LabelSelector{MatchLabels: map[string]string{"device-type": deviceType}}
	}
	return &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{Name: "ivi-4fj2k", Namespace: memberNamespace, UID: "lease-uid"},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			ClientRef:   corev1.LocalObjectReference{Name: "owner"},
			SharedWith:  []string{"shared", "denied"},
			TemplateRef: &jumpstarterdevv1alpha1.LeaseTemplateReference{Name: "ivi", Generation: 3},
			Members: []jumpstarterdevv1alpha1.LeaseMember{
				{Name: "headunit", Selector: selector("headunit")},
				{Name: "phone", Selector: selector("phone")},
				{Name: "camera", Selector: selector("camera"), Optional: true, Excluded: true},
			},
		},
		Status: jumpstarterdevv1alpha1.LeaseStatus{
			BeginTime: &metav1.Time{Time: time.Now()},
			// The controller lists a shared client only if the policy lets it
			// use each bound member.
			SharedWith: []string{"shared"},
			Members: []jumpstarterdevv1alpha1.LeaseMemberStatus{
				boundMember("headunit", "hu-1"),
				boundMember("phone", "phone-1"),
				{Name: "camera", State: jumpstarterdevv1alpha1.LeaseMemberStateOmitted, Reason: jumpstarterdevv1alpha1.LeaseMemberReasonExcluded},
			},
		},
	}
}

func scalarLease(exporter string) *jumpstarterdevv1alpha1.Lease {
	lease := &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{Name: "scalar", Namespace: memberNamespace, UID: "scalar-uid"},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			ClientRef: corev1.LocalObjectReference{Name: "owner"},
			Selector:  metav1.LabelSelector{MatchLabels: map[string]string{"board": "rpi4"}},
		},
	}
	if exporter != "" {
		lease.Status.ExporterRef = &corev1.LocalObjectReference{Name: exporter}
	}
	return lease
}

func readyExporter(name, leaseName string) *jumpstarterdevv1alpha1.Exporter {
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: memberNamespace, UID: types.UID(name + "-uid")},
		Status:     jumpstarterdevv1alpha1.ExporterStatus{ExporterStatusValue: jumpstarterdevv1alpha1.ExporterStatusLeaseReady},
	}
	if leaseName != "" {
		exporter.Status.LeaseRef = &corev1.LocalObjectReference{Name: leaseName}
	}
	return exporter
}

// memberService serves the clients owner, shared, denied and stranger, and
// the exporters hu-1, phone-1, rpi-1 and spare, together with the given leases.
func memberService(t *testing.T, funcs *interceptor.Funcs, leases ...*jumpstarterdevv1alpha1.Lease) (*ControllerService, client.WithWatch) {
	t.Helper()
	t.Setenv("ROUTER_KEY", "test-router-key")
	objs := []client.Object{
		readyExporter("hu-1", "ivi-4fj2k"), readyExporter("phone-1", "ivi-4fj2k"),
		readyExporter("rpi-1", "scalar"), readyExporter("spare", ""),
	}
	for _, name := range []string{"owner", "shared", "denied", "stranger"} {
		objs = append(objs, &jumpstarterdevv1alpha1.Client{ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: memberNamespace}})
	}
	for _, lease := range leases {
		objs = append(objs, lease)
	}
	builder := fake.NewClientBuilder().
		WithScheme(newReleaseLeaseTestScheme(t)).
		WithObjects(objs...).
		WithStatusSubresource(&jumpstarterdevv1alpha1.Lease{}, &jumpstarterdevv1alpha1.Exporter{})
	if funcs != nil {
		builder = builder.WithInterceptorFuncs(*funcs)
	}
	fakeClient := builder.Build()
	return &ControllerService{
		Client: fakeClient,
		Authn:  &passingAuthenticator{userName: "test-user"},
		Authz:  passingAuthorizer{},
		Attr:   callerAttributesGetter{},
		Router: config.Router{"default": {Endpoint: "router.example.com:443"}},
	}, fakeClient
}

// listenFor registers a listen queue for one exporter of a lease, as Listen does.
func listenFor(svc *ControllerService, leaseName, exporterName string) *listenQueue {
	queue := &listenQueue{ch: make(chan *pb.ListenResponse, 8), done: make(chan struct{})}
	svc.swapListenQueue(listenQueueKey(leaseName, exporterName), queue)
	return queue
}

func requireCode(t *testing.T, err error, code codes.Code, parts ...string) {
	t.Helper()
	st, ok := status.FromError(err)
	if err == nil || !ok {
		t.Fatalf("expected %v status error, got %v", code, err)
	}
	if st.Code() != code {
		t.Fatalf("expected %v, got %v: %s", code, st.Code(), st.Message())
	}
	for _, part := range parts {
		if !strings.Contains(st.Message(), part) {
			t.Fatalf("expected message to contain %q, got %q", part, st.Message())
		}
	}
}

func getLease(t *testing.T, c client.Client, name string) *jumpstarterdevv1alpha1.Lease {
	t.Helper()
	var lease jumpstarterdevv1alpha1.Lease
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: memberNamespace, Name: name}, &lease); err != nil {
		t.Fatalf("failed to get lease %s: %v", name, err)
	}
	return &lease
}

func dialRequest(leaseName, role string) *pb.DialRequest {
	req := &pb.DialRequest{LeaseName: leaseName}
	if role != "" {
		req.MemberName = &role
	}
	return req
}

func TestDialMemberForm(t *testing.T) {
	ctx := context.Background()

	t.Run("reaches the exporter of the named member", func(t *testing.T) {
		svc, _ := memberService(t, nil, memberLease())
		headunit := listenFor(svc, "ivi-4fj2k", "hu-1")
		phone := listenFor(svc, "ivi-4fj2k", "phone-1")

		resp, err := svc.Dial(asClient(ctx, "owner"), dialRequest("ivi-4fj2k", "phone"))
		if err != nil {
			t.Fatalf("expected Dial to succeed, got %v", err)
		}
		if resp.RouterEndpoint != "router.example.com:443" || resp.RouterToken == "" {
			t.Fatalf("unexpected response %v", resp)
		}
		if n := drainChannel(headunit.ch); n != 0 {
			t.Fatalf("the head unit got %d tokens of a phone dial", n)
		}
		select {
		case msg := <-phone.ch:
			if msg.RouterToken != resp.RouterToken {
				t.Fatal("the phone exporter got a different token than the client")
			}
		default:
			t.Fatal("the phone exporter got no token")
		}

		if _, err := svc.Dial(asClient(ctx, "owner"), dialRequest("ivi-4fj2k", "headunit")); err != nil {
			t.Fatalf("expected Dial to succeed, got %v", err)
		}
		if h, p := drainChannel(headunit.ch), drainChannel(phone.ch); h != 1 || p != 0 {
			t.Fatalf("expected the token at the head unit only, got headunit=%d phone=%d", h, p)
		}
	})

	t.Run("without member_name lists the roles", func(t *testing.T) {
		svc, _ := memberService(t, nil, memberLease())
		_, err := svc.Dial(asClient(ctx, "owner"), dialRequest("ivi-4fj2k", ""))
		requireCode(t, err, codes.InvalidArgument, "member_name is required", "headunit, phone, camera")
	})

	t.Run("rejects a role that the lease does not have", func(t *testing.T) {
		svc, _ := memberService(t, nil, memberLease())
		_, err := svc.Dial(asClient(ctx, "owner"), dialRequest("ivi-4fj2k", "ecu"))
		requireCode(t, err, codes.InvalidArgument, `no member "ecu"`, "headunit, phone, camera")
	})

	notBound := []struct {
		name   string
		role   string
		mutate func(*jumpstarterdevv1alpha1.Lease)
		want   []string
	}{
		{name: "an omitted member", role: "camera", want: []string{"camera", "state: Omitted", "reason: Excluded"}},
		{name: "a lost member", role: "phone", want: []string{"phone", "state: Lost", "reason: Offline"},
			mutate: func(l *jumpstarterdevv1alpha1.Lease) {
				l.MarkMemberLost("phone", jumpstarterdevv1alpha1.LeaseMemberReasonOffline, "exporter went offline")
			}},
		{name: "a pending member", role: "phone", want: []string{"phone", "state: Pending"},
			mutate: func(l *jumpstarterdevv1alpha1.Lease) {
				l.Status.Members[1] = jumpstarterdevv1alpha1.LeaseMemberStatus{Name: "phone", State: jumpstarterdevv1alpha1.LeaseMemberStatePending}
			}},
		{name: "a member of a lease that the controller did not reconcile yet", role: "phone", want: []string{"phone", "state: Pending"},
			mutate: func(l *jumpstarterdevv1alpha1.Lease) { l.Status.Members = nil }},
	}
	for _, tt := range notBound {
		t.Run("rejects "+tt.name, func(t *testing.T) {
			lease := memberLease()
			if tt.mutate != nil {
				tt.mutate(lease)
			}
			svc, _ := memberService(t, nil, lease)
			queue := listenFor(svc, "ivi-4fj2k", "phone-1")
			_, err := svc.Dial(asClient(ctx, "owner"), dialRequest("ivi-4fj2k", tt.role))
			requireCode(t, err, codes.FailedPrecondition, tt.want...)
			if n := drainChannel(queue.ch); n != 0 {
				t.Fatalf("expected no token, got %d", n)
			}
		})
	}

	t.Run("a shared client reaches each member", func(t *testing.T) {
		svc, _ := memberService(t, nil, memberLease())
		headunit := listenFor(svc, "ivi-4fj2k", "hu-1")
		phone := listenFor(svc, "ivi-4fj2k", "phone-1")
		for _, role := range []string{"headunit", "phone"} {
			if _, err := svc.Dial(asClient(ctx, "shared"), dialRequest("ivi-4fj2k", role)); err != nil {
				t.Fatalf("expected the shared client to dial %s, got %v", role, err)
			}
		}
		if h, p := drainChannel(headunit.ch), drainChannel(phone.ch); h != 1 || p != 1 {
			t.Fatalf("expected one token for each member, got headunit=%d phone=%d", h, p)
		}
	})

	t.Run("a client that the policy keeps out of status.sharedWith is denied for each member", func(t *testing.T) {
		svc, _ := memberService(t, nil, memberLease())
		listenFor(svc, "ivi-4fj2k", "hu-1")
		listenFor(svc, "ivi-4fj2k", "phone-1")
		for _, role := range []string{"headunit", "phone", ""} {
			_, err := svc.Dial(asClient(ctx, "denied"), dialRequest("ivi-4fj2k", role))
			requireCode(t, err, codes.PermissionDenied)
		}
	})

	t.Run("a client without access is denied before the roles are shown", func(t *testing.T) {
		svc, _ := memberService(t, nil, memberLease())
		_, err := svc.Dial(asClient(ctx, "stranger"), dialRequest("ivi-4fj2k", ""))
		requireCode(t, err, codes.PermissionDenied)
		if strings.Contains(status.Convert(err).Message(), "headunit") {
			t.Fatalf("the error shows the roles to a client without access: %v", err)
		}
	})

	t.Run("reports an exporter that does not listen", func(t *testing.T) {
		svc, _ := memberService(t, nil, memberLease())
		listenFor(svc, "ivi-4fj2k", "hu-1")
		_, err := svc.Dial(asClient(ctx, "owner"), dialRequest("ivi-4fj2k", "phone"))
		requireCode(t, err, codes.Unavailable)
	})
}

func TestDialScalarLease(t *testing.T) {
	ctx := context.Background()

	t.Run("connects without member_name as before", func(t *testing.T) {
		svc, _ := memberService(t, nil, scalarLease("rpi-1"))
		queue := listenFor(svc, "scalar", "rpi-1")
		resp, err := svc.Dial(asClient(ctx, "owner"), dialRequest("scalar", ""))
		if err != nil {
			t.Fatalf("expected Dial to succeed, got %v", err)
		}
		select {
		case msg := <-queue.ch:
			if msg.RouterToken != resp.RouterToken {
				t.Fatal("the exporter got a different token than the client")
			}
		default:
			t.Fatal("the exporter got no token")
		}
	})

	t.Run("rejects member_name", func(t *testing.T) {
		svc, _ := memberService(t, nil, scalarLease("rpi-1"))
		queue := listenFor(svc, "scalar", "rpi-1")
		_, err := svc.Dial(asClient(ctx, "owner"), dialRequest("scalar", "phone"))
		requireCode(t, err, codes.InvalidArgument, "has no members", "phone")
		if n := drainChannel(queue.ch); n != 0 {
			t.Fatalf("expected no token, got %d", n)
		}
	})

	t.Run("a lease without an exporter is not active", func(t *testing.T) {
		svc, _ := memberService(t, nil, scalarLease(""))
		_, err := svc.Dial(asClient(ctx, "owner"), dialRequest("scalar", ""))
		if err == nil || err.Error() != "lease not active" {
			t.Fatalf("expected the lease not active error, got %v", err)
		}
	})
}

// recordingListenStream implements pb.ControllerService_ListenServer and
// passes on what Listen sends.
type recordingListenStream struct {
	pb.ControllerService_ListenServer
	ctx  context.Context
	sent chan *pb.ListenResponse
}

func (s *recordingListenStream) Context() context.Context { return s.ctx }

func (s *recordingListenStream) Send(r *pb.ListenResponse) error {
	s.sent <- r
	return nil
}

func waitForListener(t *testing.T, svc *ControllerService, leaseName, exporterName string) {
	t.Helper()
	deadline := time.Now().Add(5 * time.Second)
	for time.Now().Before(deadline) {
		if _, ok := svc.listenQueues.Load(listenQueueKey(leaseName, exporterName)); ok {
			return
		}
		time.Sleep(5 * time.Millisecond)
	}
	t.Fatalf("exporter %s did not start to listen on lease %s", exporterName, leaseName)
}

func TestListenMemberForm(t *testing.T) {
	t.Run("two exporters of one lease listen at the same time", func(t *testing.T) {
		svc, _ := memberService(t, nil, memberLease())

		ctx, cancel := context.WithCancel(context.Background())
		streams := map[string]*recordingListenStream{}
		var wg sync.WaitGroup
		errs := make(chan error, 2)
		for _, exporter := range []string{"hu-1", "phone-1"} {
			stream := &recordingListenStream{ctx: asExporter(ctx, exporter), sent: make(chan *pb.ListenResponse, 8)}
			streams[exporter] = stream
			wg.Add(1)
			go func() {
				defer wg.Done()
				errs <- svc.Listen(&pb.ListenRequest{LeaseName: "ivi-4fj2k"}, stream)
			}()
		}
		waitForListener(t, svc, "ivi-4fj2k", "hu-1")
		waitForListener(t, svc, "ivi-4fj2k", "phone-1")

		for role, exporter := range map[string]string{"phone": "phone-1", "headunit": "hu-1"} {
			resp, err := svc.Dial(asClient(context.Background(), "owner"), dialRequest("ivi-4fj2k", role))
			if err != nil {
				t.Fatalf("expected Dial for %s to succeed, got %v", role, err)
			}
			select {
			case msg := <-streams[exporter].sent:
				if msg.RouterToken != resp.RouterToken {
					t.Fatalf("exporter %s got a different token than the client", exporter)
				}
			case <-time.After(5 * time.Second):
				t.Fatalf("exporter %s got no token for the dial of %s", exporter, role)
			}
			for other, stream := range streams {
				if other != exporter && len(stream.sent) != 0 {
					t.Fatalf("exporter %s got the token for %s", other, role)
				}
			}
		}

		cancel()
		wg.Wait()
		close(errs)
		for err := range errs {
			if err != nil {
				t.Fatalf("expected Listen to end without an error, got %v", err)
			}
		}
		for _, exporter := range []string{"hu-1", "phone-1"} {
			if _, ok := svc.listenQueues.Load(listenQueueKey("ivi-4fj2k", exporter)); ok {
				t.Fatalf("the queue of %s stays after its listener ended", exporter)
			}
		}
	})

	rejected := []struct {
		name     string
		exporter string
		mutate   func(*jumpstarterdevv1alpha1.Lease)
	}{
		{name: "an exporter that is not a member", exporter: "spare"},
		{name: "the exporter of a lost member", exporter: "phone-1", mutate: func(l *jumpstarterdevv1alpha1.Lease) {
			l.MarkMemberLost("phone", jumpstarterdevv1alpha1.LeaseMemberReasonOffline, "")
		}},
	}
	for _, tt := range rejected {
		t.Run("rejects "+tt.name, func(t *testing.T) {
			lease := memberLease()
			if tt.mutate != nil {
				tt.mutate(lease)
			}
			svc, _ := memberService(t, nil, lease)
			stream := &recordingListenStream{ctx: asExporter(context.Background(), tt.exporter), sent: make(chan *pb.ListenResponse, 1)}
			err := svc.Listen(&pb.ListenRequest{LeaseName: "ivi-4fj2k"}, stream)
			if err == nil || err.Error() != "permission denied" {
				t.Fatalf("expected permission denied, got %v", err)
			}
			if _, ok := svc.listenQueues.Load(listenQueueKey("ivi-4fj2k", tt.exporter)); ok {
				t.Fatal("a rejected listener left a queue")
			}
		})
	}

	t.Run("the exporter of a scalar lease listens as before", func(t *testing.T) {
		svc, _ := memberService(t, nil, scalarLease("rpi-1"))
		ctx, cancel := context.WithCancel(context.Background())
		stream := &recordingListenStream{ctx: asExporter(ctx, "rpi-1"), sent: make(chan *pb.ListenResponse, 1)}
		done := make(chan error, 1)
		go func() { done <- svc.Listen(&pb.ListenRequest{LeaseName: "scalar"}, stream) }()
		waitForListener(t, svc, "scalar", "rpi-1")

		resp, err := svc.Dial(asClient(context.Background(), "owner"), dialRequest("scalar", ""))
		if err != nil {
			t.Fatalf("expected Dial to succeed, got %v", err)
		}
		select {
		case msg := <-stream.sent:
			if msg.RouterToken != resp.RouterToken {
				t.Fatal("the exporter got a different token than the client")
			}
		case <-time.After(5 * time.Second):
			t.Fatal("the exporter got no token")
		}
		cancel()
		if err := <-done; err != nil {
			t.Fatalf("expected Listen to end without an error, got %v", err)
		}

		other := &recordingListenStream{ctx: asExporter(context.Background(), "spare"), sent: make(chan *pb.ListenResponse, 1)}
		if err := svc.Listen(&pb.ListenRequest{LeaseName: "scalar"}, other); err == nil || err.Error() != "permission denied" {
			t.Fatalf("expected permission denied, got %v", err)
		}
	})
}

func TestAssignedLeaseMemberForm(t *testing.T) {
	ctx := context.Background()
	tests := []struct {
		name     string
		exporter *jumpstarterdevv1alpha1.Exporter
		mutate   func(*jumpstarterdevv1alpha1.Lease)
		assigned bool
	}{
		{name: "the exporter of a bound member", exporter: readyExporter("phone-1", "ivi-4fj2k"), assigned: true},
		{name: "the exporter of a lost member", exporter: readyExporter("phone-1", "ivi-4fj2k"),
			mutate: func(l *jumpstarterdevv1alpha1.Lease) {
				l.MarkMemberLost("phone", jumpstarterdevv1alpha1.LeaseMemberReasonExporterReleased, "")
			}},
		{name: "an exporter that is not a member", exporter: readyExporter("spare", "ivi-4fj2k")},
		{name: "an ended lease", exporter: readyExporter("phone-1", "ivi-4fj2k"),
			mutate: func(l *jumpstarterdevv1alpha1.Lease) { l.Status.Ended = true }},
		{name: "an exporter without a lease", exporter: readyExporter("phone-1", "")},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			lease := memberLease()
			if tt.mutate != nil {
				tt.mutate(lease)
			}
			svc, _ := memberService(t, nil, lease)
			got, err := svc.assignedLease(ctx, tt.exporter)
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if (got != nil) != tt.assigned {
				t.Fatalf("expected assigned=%v, got %v", tt.assigned, got)
			}
		})
	}
}

func TestUpdateLeaseHooksMemberForm(t *testing.T) {
	running := func() *pb.UpdateLeaseHooksRequest {
		return &pb.UpdateLeaseHooksRequest{
			LeaseName: "ivi-4fj2k",
			LeaseUid:  "lease-uid",
			Hook: &pb.UpdateLeaseHooksRequest_BeforeLease{BeforeLease: &pb.LeaseHookState{
				Phase: pb.LeaseHookPhase_LEASE_HOOK_PHASE_RUNNING, Attempts: 1,
			}},
		}
	}

	t.Run("each bound member keeps its own hook record", func(t *testing.T) {
		svc, c := memberService(t, nil, memberLease())
		for _, exporter := range []string{"hu-1", "phone-1"} {
			hooks, err := svc.UpdateLeaseHooks(asExporter(context.Background(), exporter), running())
			if err != nil {
				t.Fatalf("expected the hook update of %s to succeed, got %v", exporter, err)
			}
			if hooks.LeaseName != "ivi-4fj2k" || hooks.ClientName != "owner" {
				t.Fatalf("unexpected hook record %v", hooks)
			}
			var stored jumpstarterdevv1alpha1.Exporter
			if err := c.Get(context.Background(), types.NamespacedName{Namespace: memberNamespace, Name: exporter}, &stored); err != nil {
				t.Fatalf("failed to get exporter: %v", err)
			}
			if !stored.Status.LeaseHooks.IsFor("ivi-4fj2k", "lease-uid") || stored.Status.LeaseHooks.BeforeLease == nil {
				t.Fatalf("expected a hook record on %s, got %+v", exporter, stored.Status.LeaseHooks)
			}
		}
	})

	t.Run("the exporter of a lost member cannot start setup", func(t *testing.T) {
		lease := memberLease()
		lease.MarkMemberLost("phone", jumpstarterdevv1alpha1.LeaseMemberReasonOffline, "")
		svc, _ := memberService(t, nil, lease)
		_, err := svc.UpdateLeaseHooks(asExporter(context.Background(), "phone-1"), running())
		requireCode(t, err, codes.FailedPrecondition, "not assigned")
	})
}

func TestReleaseMemberFormLeaseByExporter(t *testing.T) {
	ctx := context.Background()

	assertPhoneLost := func(t *testing.T, c client.Client) {
		t.Helper()
		lease := getLease(t, c, "ivi-4fj2k")
		phone := lease.MemberStatus("phone")
		if phone.State != jumpstarterdevv1alpha1.LeaseMemberStateLost ||
			phone.Reason != jumpstarterdevv1alpha1.LeaseMemberReasonExporterReleased ||
			!strings.Contains(phone.Message, "phone-1") {
			t.Fatalf("expected the phone member to be Lost with ExporterReleased, got %+v", phone)
		}
		if phone.ExporterRef == nil || phone.ExporterRef.Name != "phone-1" {
			t.Fatalf("a lost member keeps its exporter, got %+v", phone.ExporterRef)
		}
		if headunit := lease.MemberStatus("headunit"); headunit.State != jumpstarterdevv1alpha1.LeaseMemberStateBound ||
			headunit.ExporterRef.Name != "hu-1" || headunit.Reason != "" {
			t.Fatalf("expected the head unit member to stay Bound, got %+v", headunit)
		}
		if camera := lease.MemberStatus("camera"); camera.State != jumpstarterdevv1alpha1.LeaseMemberStateOmitted {
			t.Fatalf("expected the camera member to stay Omitted, got %+v", camera)
		}
		if lease.Spec.Release || lease.Status.Ended {
			t.Fatalf("a release by one exporter must not end the lease: release=%v ended=%v", lease.Spec.Release, lease.Status.Ended)
		}
		// The lease controller owns the conditions and the shared clients.
		if len(lease.Status.Conditions) != 0 || len(lease.Status.SharedWith) != 1 || lease.Status.BeginTime == nil {
			t.Fatalf("the release changed status fields of the lease controller: %+v", lease.Status)
		}
	}

	t.Run("ReleaseLease marks the member of the exporter lost", func(t *testing.T) {
		svc, c := memberService(t, nil, memberLease())
		buf, logCtx := withCapturedLog(t)

		if _, err := svc.ReleaseLease(asExporter(logCtx, "phone-1"), &pb.ReleaseLeaseRequest{Name: "ivi-4fj2k"}); err != nil {
			t.Fatalf("expected the release to succeed, got %v", err)
		}
		assertPhoneLost(t, c)
		for _, want := range []string{`"outcome": "member_lost"`, `"lease_member": "phone"`, `"exporter": "phone-1"`, `"path": "ReleaseLease"`} {
			if !strings.Contains(buf.String(), want) {
				t.Errorf("expected %q in log, got:\n%s", want, buf.String())
			}
		}

		// The other member still serves the lease, and the lost one does not.
		listenFor(svc, "ivi-4fj2k", "hu-1")
		listenFor(svc, "ivi-4fj2k", "phone-1")
		if _, err := svc.Dial(asClient(ctx, "owner"), dialRequest("ivi-4fj2k", "headunit")); err != nil {
			t.Fatalf("expected the head unit to stay reachable, got %v", err)
		}
		_, err := svc.Dial(asClient(ctx, "owner"), dialRequest("ivi-4fj2k", "phone"))
		requireCode(t, err, codes.FailedPrecondition, "state: Lost", "reason: ExporterReleased")
	})

	t.Run("a repeated release changes nothing", func(t *testing.T) {
		svc, c := memberService(t, nil, memberLease())
		for range 2 {
			if _, err := svc.ReleaseLease(asExporter(ctx, "phone-1"), &pb.ReleaseLeaseRequest{Name: "ivi-4fj2k"}); err != nil {
				t.Fatalf("expected the release to succeed, got %v", err)
			}
		}
		assertPhoneLost(t, c)
	})

	t.Run("the status report path marks the member lost", func(t *testing.T) {
		svc, c := memberService(t, nil, memberLease())
		_, err := svc.ReportStatus(asExporter(ctx, "phone-1"), &pb.ReportStatusRequest{
			Status:       pb.ExporterStatus_EXPORTER_STATUS_AVAILABLE,
			ReleaseLease: new(true),
		})
		if err != nil {
			t.Fatalf("expected the status report to succeed, got %v", err)
		}
		assertPhoneLost(t, c)
	})

	t.Run("retries when the lease changed under the write", func(t *testing.T) {
		conflicts := 2
		svc, c := memberService(t, &interceptor.Funcs{
			SubResourceUpdate: func(ctx context.Context, c client.Client, subResourceName string, obj client.Object, opts ...client.SubResourceUpdateOption) error {
				if _, ok := obj.(*jumpstarterdevv1alpha1.Lease); ok && conflicts > 0 {
					conflicts--
					// The lease controller wrote the lease in between.
					var current jumpstarterdevv1alpha1.Lease
					if err := c.Get(ctx, client.ObjectKeyFromObject(obj), &current); err != nil {
						return err
					}
					current.Status.BindAttempt++
					if err := c.Status().Update(ctx, &current); err != nil {
						return err
					}
					return apierrors.NewConflict(schema.GroupResource{Group: "jumpstarter.dev", Resource: "leases"}, obj.GetName(), nil)
				}
				return c.SubResource(subResourceName).Update(ctx, obj, opts...)
			},
		}, memberLease())

		if _, err := svc.ReleaseLease(asExporter(ctx, "phone-1"), &pb.ReleaseLeaseRequest{Name: "ivi-4fj2k"}); err != nil {
			t.Fatalf("expected the release to succeed after the conflicts, got %v", err)
		}
		if conflicts != 0 {
			t.Fatalf("expected 2 conflicts to be consumed, %d left", conflicts)
		}
		assertPhoneLost(t, c)
		// The write of the other writer is kept: the retry read the lease again.
		if attempt := getLease(t, c, "ivi-4fj2k").Status.BindAttempt; attempt != 2 {
			t.Fatalf("expected the concurrent writes to be kept (bindAttempt 2), got %d", attempt)
		}
	})

	t.Run("rejects an exporter that is not a member", func(t *testing.T) {
		svc, c := memberService(t, nil, memberLease())
		_, err := svc.ReleaseLease(asExporter(ctx, "spare"), &pb.ReleaseLeaseRequest{Name: "ivi-4fj2k"})
		requireCode(t, err, codes.FailedPrecondition, "not held by exporter spare")
		lease := getLease(t, c, "ivi-4fj2k")
		if lease.HasLostMember() || lease.Spec.Release {
			t.Fatalf("a rejected release changed the lease: %+v", lease.Status.Members)
		}
	})

	t.Run("an ended lease is left as it is", func(t *testing.T) {
		lease := memberLease()
		lease.Status.Ended = true
		svc, c := memberService(t, nil, lease)
		if _, err := svc.ReleaseLease(asExporter(ctx, "phone-1"), &pb.ReleaseLeaseRequest{Name: "ivi-4fj2k"}); err != nil {
			t.Fatalf("expected the release to succeed, got %v", err)
		}
		if getLease(t, c, "ivi-4fj2k").HasLostMember() {
			t.Fatal("a release of an ended lease marked a member lost")
		}
	})
}

func TestReleaseScalarLeaseByExporterEndsLease(t *testing.T) {
	ctx := context.Background()

	t.Run("ReleaseLease", func(t *testing.T) {
		svc, c := memberService(t, nil, scalarLease("rpi-1"))
		if _, err := svc.ReleaseLease(asExporter(ctx, "rpi-1"), &pb.ReleaseLeaseRequest{Name: "scalar"}); err != nil {
			t.Fatalf("expected the release to succeed, got %v", err)
		}
		if lease := getLease(t, c, "scalar"); !lease.Spec.Release || len(lease.Status.Members) != 0 {
			t.Fatalf("expected spec.release on the scalar lease, got %+v", lease)
		}
	})

	t.Run("status report", func(t *testing.T) {
		svc, c := memberService(t, nil, scalarLease("rpi-1"))
		_, err := svc.ReportStatus(asExporter(ctx, "rpi-1"), &pb.ReportStatusRequest{
			Status:       pb.ExporterStatus_EXPORTER_STATUS_AVAILABLE,
			ReleaseLease: new(true),
		})
		if err != nil {
			t.Fatalf("expected the status report to succeed, got %v", err)
		}
		if lease := getLease(t, c, "scalar"); !lease.Spec.Release {
			t.Fatal("expected spec.release on the scalar lease")
		}
	})
}

func TestReleaseMemberFormLeaseByClientEndsLease(t *testing.T) {
	ctx := context.Background()
	svc, c := memberService(t, nil, memberLease())

	_, err := svc.ReleaseLease(asClient(ctx, "shared"), &pb.ReleaseLeaseRequest{Name: "ivi-4fj2k"})
	requireCode(t, err, codes.PermissionDenied)

	if _, err := svc.ReleaseLease(asClient(ctx, "owner"), &pb.ReleaseLeaseRequest{Name: "ivi-4fj2k"}); err != nil {
		t.Fatalf("expected the release to succeed, got %v", err)
	}
	lease := getLease(t, c, "ivi-4fj2k")
	if !lease.Spec.Release || lease.HasLostMember() {
		t.Fatalf("expected spec.release and no lost member, got release=%v members=%+v", lease.Spec.Release, lease.Status.Members)
	}
}

func TestControllerGetLeaseMemberForm(t *testing.T) {
	ctx := context.Background()
	svc, _ := memberService(t, nil, memberLease(), scalarLease("rpi-1"))

	resp, err := svc.GetLease(asClient(ctx, "owner"), &pb.GetLeaseRequest{Name: "ivi-4fj2k"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.ExporterUuid != nil {
		t.Fatalf("expected no exporter_uuid for a member-form lease, got %q", *resp.ExporterUuid)
	}
	if len(resp.Selector.MatchLabels) != 0 || len(resp.Selector.MatchExpressions) != 0 {
		t.Fatalf("expected an empty selector, got %v", resp.Selector)
	}

	resp, err = svc.GetLease(asClient(ctx, "owner"), &pb.GetLeaseRequest{Name: "scalar"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if resp.GetExporterUuid() != "rpi-1-uid" {
		t.Fatalf("expected the exporter uuid of the scalar lease, got %q", resp.GetExporterUuid())
	}

	names, err := svc.ListLeases(asClient(ctx, "shared"), &pb.ListLeasesRequest{})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(names.Names) != 1 || names.Names[0] != "ivi-4fj2k" {
		t.Fatalf("expected the shared member-form lease, got %v", names.Names)
	}
}

func TestListenAndDialLogCorrelation(t *testing.T) {
	svc, _ := memberService(t, nil, memberLease())
	listenFor(svc, "ivi-4fj2k", "phone-1")
	buf, logCtx := withCapturedLog(t)

	if _, err := svc.Dial(asClient(logCtx, "owner"), dialRequest("ivi-4fj2k", "phone")); err != nil {
		t.Fatalf("expected Dial to succeed, got %v", err)
	}
	for _, want := range []string{`"lease_id": "ivi-4fj2k"`, `"lease_member": "phone"`, `"lease_template": "ivi"`} {
		if !strings.Contains(buf.String(), want) {
			t.Errorf("expected %q in log, got:\n%s", want, buf.String())
		}
	}
}

func TestDashboardShowsMemberExporters(t *testing.T) {
	tmpl, err := template.ParseFS(fs, "templates/*")
	if err != nil {
		t.Fatalf("failed to parse the dashboard templates: %v", err)
	}
	lease := memberLease()
	lease.MarkMemberLost("phone", jumpstarterdevv1alpha1.LeaseMemberReasonOffline, "")
	var out strings.Builder
	err = tmpl.ExecuteTemplate(&out, "index.html", map[string]any{
		"Exporters": []jumpstarterdevv1alpha1.Exporter{},
		"Clients":   []jumpstarterdevv1alpha1.Client{},
		"Leases":    []jumpstarterdevv1alpha1.Lease{*lease, *scalarLease("rpi-1"), *scalarLease("")},
	})
	if err != nil {
		t.Fatalf("failed to render the dashboard: %v", err)
	}
	for _, want := range []string{"headunit=hu-1", "phone=phone-1", "<td>rpi-1</td>"} {
		if !strings.Contains(out.String(), want) {
			t.Errorf("expected %q in the dashboard, got:\n%s", want, out.String())
		}
	}
}
