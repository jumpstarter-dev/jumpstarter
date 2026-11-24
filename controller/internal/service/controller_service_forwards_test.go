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
	"sync/atomic"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/config"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/interceptor"
)

// The controller service side of the forwards of a lease (JEP-0016).

const forwardLeaseName = "ivi-4fj2k"

func forwardEndpoints(name string, endpoints ...string) jumpstarterdevv1alpha1.LeaseForward {
	f := jumpstarterdevv1alpha1.LeaseForward{Name: name}
	for i := 0; i+1 < len(endpoints); i += 2 {
		f.Endpoints = append(f.Endpoints, jumpstarterdevv1alpha1.ForwardEndpoint{Member: endpoints[i], Port: endpoints[i+1]})
	}
	return f
}

// forwardLease is memberLease (headunit on hu-1, phone on phone-1, camera
// Omitted) with these forwards, each in the given state.
func forwardLease(state jumpstarterdevv1alpha1.LeaseForwardState, forwards ...jumpstarterdevv1alpha1.LeaseForward) *jumpstarterdevv1alpha1.Lease {
	lease := memberLease()
	lease.Spec.Forwards = forwards
	for _, f := range forwards {
		lease.Status.Forwards = append(lease.Status.Forwards, jumpstarterdevv1alpha1.LeaseForwardStatus{Name: f.Name, State: state})
	}
	return lease
}

// btForward connects headunit.rootcanal (provides) and phone.controller
// (requires).
func btForward() jumpstarterdevv1alpha1.LeaseForward {
	return forwardEndpoints("bt", "phone", "controller", "headunit", "rootcanal")
}

// uartForward is a path between the connects ports headunit.uart and
// phone.uart. Its initiator is headunit.
func uartForward() jumpstarterdevv1alpha1.LeaseForward {
	return forwardEndpoints("uart", "phone", "uart", "headunit", "uart")
}

func setExporterPorts(t *testing.T, c client.Client, name string, ports ...jumpstarterdevv1alpha1.DevicePort) {
	t.Helper()
	var exporter jumpstarterdevv1alpha1.Exporter
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: memberNamespace, Name: name}, &exporter); err != nil {
		t.Fatalf("failed to get exporter %s: %v", name, err)
	}
	exporter.Status.Devices = []jumpstarterdevv1alpha1.Device{{Uuid: name}, {Uuid: name + "-child", Ports: ports}}
	if err := c.Status().Update(context.Background(), &exporter); err != nil {
		t.Fatalf("failed to set the ports of exporter %s: %v", name, err)
	}
}

func setExporterStatus(t *testing.T, c client.Client, name, value string) {
	t.Helper()
	var exporter jumpstarterdevv1alpha1.Exporter
	if err := c.Get(context.Background(), types.NamespacedName{Namespace: memberNamespace, Name: name}, &exporter); err != nil {
		t.Fatalf("failed to get exporter %s: %v", name, err)
	}
	exporter.Status.ExporterStatusValue = value
	if err := c.Status().Update(context.Background(), &exporter); err != nil {
		t.Fatalf("failed to set the status of exporter %s: %v", name, err)
	}
}

// forwardService is memberService with the ports of the reference topology:
// hu-1 has rootcanal (provides) and uart (connects), phone-1 has controller
// (requires) and uart (connects). spare has a port and no lease.
func forwardService(t *testing.T, leases ...*jumpstarterdevv1alpha1.Lease) (*ControllerService, client.WithWatch) {
	t.Helper()
	svc, c := memberService(t, nil, leases...)
	svc.ForwardSetupPollInterval = 5 * time.Millisecond
	dir := func(name string, direction jumpstarterdevv1alpha1.PortDirection) jumpstarterdevv1alpha1.DevicePort {
		return jumpstarterdevv1alpha1.DevicePort{Name: name, Direction: direction}
	}
	setExporterPorts(t, c, "hu-1", dir("rootcanal", jumpstarterdevv1alpha1.PortDirectionProvides),
		dir("uart", jumpstarterdevv1alpha1.PortDirectionConnects))
	setExporterPorts(t, c, "phone-1", dir("controller", jumpstarterdevv1alpha1.PortDirectionRequires),
		dir("uart", jumpstarterdevv1alpha1.PortDirectionConnects))
	setExporterPorts(t, c, "spare", dir("controller", jumpstarterdevv1alpha1.PortDirectionRequires))
	return svc, c
}

// forwardEnd returns the status of an end of a forward of the lease. It
// returns an empty status for an end that the lease does not have.
func forwardEnd(t *testing.T, c client.Client, forward, member string) *jumpstarterdevv1alpha1.LeaseForwardEndStatus {
	t.Helper()
	if end := getLease(t, c, forwardLeaseName).ForwardEnd(forward, member); end != nil {
		return end
	}
	return &jumpstarterdevv1alpha1.LeaseForwardEndStatus{}
}

// forwardEndCount returns the number of ends in the status of the lease.
func forwardEndCount(t *testing.T, c client.Client) int {
	t.Helper()
	count := 0
	for _, forwardStatus := range getLease(t, c, forwardLeaseName).Status.Forwards {
		count += len(forwardStatus.Ends)
	}
	return count
}

func reportRequest(forward string, state pb.ForwardEndState, sequence uint64, message string) *pb.ReportForwardRequest {
	req := &pb.ReportForwardRequest{LeaseUid: "lease-uid", ForwardName: forward, State: state, Sequence: sequence}
	if message != "" {
		req.Message = &message
	}
	return req
}

func TestRegisterPorts(t *testing.T) {
	registering := func(name string) context.Context {
		ctx := metadata.NewIncomingContext(context.Background(),
			metadata.Pairs("jumpstarter-kind", "Exporter", "authorization", "Bearer exporter-token"))
		return context.WithValue(ctx, callerKey{}, caller{resource: "Exporter", name: name})
	}
	devices := func(t *testing.T, c client.Client) []jumpstarterdevv1alpha1.Device {
		t.Helper()
		var exporter jumpstarterdevv1alpha1.Exporter
		if err := c.Get(context.Background(), types.NamespacedName{Namespace: memberNamespace, Name: "spare"}, &exporter); err != nil {
			t.Fatalf("failed to get exporter: %v", err)
		}
		return exporter.Status.Devices
	}
	protocol := "hci-h4"

	t.Run("stores the declared ports with direction, attachment and transport", func(t *testing.T) {
		svc, c := memberService(t, nil)
		_, err := svc.Register(registering("spare"), &pb.RegisterRequest{Reports: []*pb.DriverInstanceReport{
			{Uuid: "root"},
			{Uuid: "child", ParentUuid: new("root"), Ports: []*pb.PortReport{
				{Name: "rootcanal", Direction: pb.PortDirection_PORT_DIRECTION_PROVIDES, Protocol: &protocol},
				{Name: "can", Direction: pb.PortDirection_PORT_DIRECTION_REQUIRES,
					Attachment: pb.PortAttachment_PORT_ATTACHMENT_WIRED, Transport: pb.PortTransport_PORT_TRANSPORT_FRAME},
				{Name: "someip-sd", Direction: pb.PortDirection_PORT_DIRECTION_CONNECTS,
					Attachment: pb.PortAttachment_PORT_ATTACHMENT_WIRELESS, Transport: pb.PortTransport_PORT_TRANSPORT_DATAGRAM},
			}},
		}})
		if err != nil {
			t.Fatalf("expected the registration to succeed, got %v", err)
		}
		got := devices(t, c)
		if len(got) != 2 || len(got[0].Ports) != 0 {
			t.Fatalf("expected two devices, the first without ports, got %+v", got)
		}
		want := []jumpstarterdevv1alpha1.DevicePort{
			// No attachment and no transport: the defaults.
			{Name: "rootcanal", Direction: jumpstarterdevv1alpha1.PortDirectionProvides, Protocol: "hci-h4",
				Attachment: jumpstarterdevv1alpha1.PortAttachmentSocket, Transport: jumpstarterdevv1alpha1.PortTransportStream},
			{Name: "can", Direction: jumpstarterdevv1alpha1.PortDirectionRequires,
				Attachment: jumpstarterdevv1alpha1.PortAttachmentWired, Transport: jumpstarterdevv1alpha1.PortTransportFrame},
			{Name: "someip-sd", Direction: jumpstarterdevv1alpha1.PortDirectionConnects,
				Attachment: jumpstarterdevv1alpha1.PortAttachmentWireless, Transport: jumpstarterdevv1alpha1.PortTransportDatagram},
		}
		if len(got[1].Ports) != len(want) {
			t.Fatalf("expected %d ports, got %+v", len(want), got[1].Ports)
		}
		for i := range want {
			if got[1].Ports[i] != want[i] {
				t.Fatalf("port %d: expected %+v, got %+v", i, want[i], got[1].Ports[i])
			}
		}
	})

	t.Run("an exporter that reports no ports registers as before", func(t *testing.T) {
		svc, c := memberService(t, nil)
		_, err := svc.Register(registering("spare"), &pb.RegisterRequest{Reports: []*pb.DriverInstanceReport{
			{Uuid: "root", Labels: map[string]string{"a": "b"}},
		}})
		if err != nil {
			t.Fatalf("expected the registration to succeed, got %v", err)
		}
		got := devices(t, c)
		if len(got) != 1 || got[0].Uuid != "root" || got[0].Ports != nil || got[0].Labels["a"] != "b" {
			t.Fatalf("expected one device without ports, got %+v", got)
		}
	})

	port := func(name string, direction pb.PortDirection) *pb.PortReport {
		return &pb.PortReport{Name: name, Direction: direction}
	}
	long := "a123456789012345678901234567890123456789012345678901234567890123"
	rejected := []struct {
		name    string
		reports []*pb.DriverInstanceReport
		message string
	}{
		{name: "a port without a direction",
			reports: []*pb.DriverInstanceReport{{Uuid: "a", Ports: []*pb.PortReport{port("rootcanal", 0)}}},
			message: `port "rootcanal" has no direction`},
		{name: "a duplicate name in one report",
			reports: []*pb.DriverInstanceReport{{Uuid: "a", Ports: []*pb.PortReport{
				port("uart", pb.PortDirection_PORT_DIRECTION_CONNECTS), port("uart", pb.PortDirection_PORT_DIRECTION_PROVIDES)}}},
			message: `duplicate port name "uart"`},
		{name: "a duplicate name across the reports of the exporter",
			reports: []*pb.DriverInstanceReport{
				{Uuid: "a", Ports: []*pb.PortReport{port("uart", pb.PortDirection_PORT_DIRECTION_CONNECTS)}},
				{Uuid: "b", Ports: []*pb.PortReport{port("uart", pb.PortDirection_PORT_DIRECTION_CONNECTS)}}},
			message: `duplicate port name "uart"`},
		{name: "a name that is not a DNS label",
			reports: []*pb.DriverInstanceReport{{Uuid: "a", Ports: []*pb.PortReport{port("Root_Canal", pb.PortDirection_PORT_DIRECTION_PROVIDES)}}},
			message: `invalid port name "Root_Canal"`},
		{name: "an empty name",
			reports: []*pb.DriverInstanceReport{{Uuid: "a", Ports: []*pb.PortReport{port("", pb.PortDirection_PORT_DIRECTION_PROVIDES)}}},
			message: "invalid port name"},
		{name: "a name with more than 63 characters",
			reports: []*pb.DriverInstanceReport{{Uuid: "a", Ports: []*pb.PortReport{port(long, pb.PortDirection_PORT_DIRECTION_PROVIDES)}}},
			message: "invalid port name"},
		{name: "a direction that the controller does not know",
			reports: []*pb.DriverInstanceReport{{Uuid: "a", Ports: []*pb.PortReport{port("x", 42)}}},
			message: "unknown direction"},
	}
	for _, tt := range rejected {
		t.Run("rejects "+tt.name, func(t *testing.T) {
			svc, c := memberService(t, nil)
			before := devices(t, c)
			_, err := svc.Register(registering("spare"), &pb.RegisterRequest{
				Labels:  map[string]string{"jumpstarter.dev/new": "label"},
				Reports: tt.reports,
			})
			requireCode(t, err, codes.InvalidArgument, tt.message)
			// Nothing was written: not the status, and not the labels.
			if after := devices(t, c); len(after) != len(before) {
				t.Fatalf("a rejected registration changed the devices: %+v", after)
			}
			var exporter jumpstarterdevv1alpha1.Exporter
			if err := c.Get(context.Background(), types.NamespacedName{Namespace: memberNamespace, Name: "spare"}, &exporter); err != nil {
				t.Fatalf("failed to get exporter: %v", err)
			}
			if _, ok := exporter.Labels["jumpstarter.dev/new"]; ok {
				t.Fatalf("a rejected registration changed the labels")
			}
		})
	}
}

func TestReportForward(t *testing.T) {
	ready := pb.ForwardEndState_FORWARD_END_STATE_READY
	connected := pb.ForwardEndState_FORWARD_END_STATE_CONNECTED
	failed := pb.ForwardEndState_FORWARD_END_STATE_FAILED
	phone := asExporter(context.Background(), "phone-1")
	headunit := asExporter(context.Background(), "hu-1")

	t.Run("records the state of the end of the caller only", func(t *testing.T) {
		svc, c := forwardService(t, forwardLease(jumpstarterdevv1alpha1.LeaseForwardStatePending, btForward()))
		before := time.Now().Truncate(time.Second)
		if _, err := svc.ReportForward(phone, reportRequest("bt", ready, 100, "")); err != nil {
			t.Fatalf("expected the report to succeed, got %v", err)
		}
		end := forwardEnd(t, c, "bt", "phone")
		if end.State != jumpstarterdevv1alpha1.LeaseForwardEndStateReady || end.Sequence != 100 ||
			end.ReportTime == nil || end.ReportTime.Time.Before(before) {
			t.Fatalf("unexpected record of the end: %+v", end)
		}
		if other := forwardEnd(t, c, "bt", "headunit"); *other != (jumpstarterdevv1alpha1.LeaseForwardEndStatus{}) {
			t.Fatalf("the report changed the end of a different member: %+v", other)
		}

		if _, err := svc.ReportForward(headunit, reportRequest("bt", failed, 7, "connection refused")); err != nil {
			t.Fatalf("expected the report to succeed, got %v", err)
		}
		other := forwardEnd(t, c, "bt", "headunit")
		if other.State != jumpstarterdevv1alpha1.LeaseForwardEndStateFailed || other.Message != "connection refused" {
			t.Fatalf("unexpected record of the end: %+v", other)
		}
		if end := forwardEnd(t, c, "bt", "phone"); end.State != jumpstarterdevv1alpha1.LeaseForwardEndStateReady {
			t.Fatalf("the report of headunit changed the end of phone: %+v", end)
		}
	})

	t.Run("ignores a report with a sequence number that is not larger", func(t *testing.T) {
		svc, c := forwardService(t, forwardLease(jumpstarterdevv1alpha1.LeaseForwardStatePending, btForward()))
		for _, req := range []*pb.ReportForwardRequest{
			reportRequest("bt", ready, 10, ""),
			reportRequest("bt", failed, 12, "address already in use"),
			// An old READY after the newer FAILED, and the same number with
			// a different state.
			reportRequest("bt", ready, 11, ""),
			reportRequest("bt", ready, 12, ""),
		} {
			if _, err := svc.ReportForward(phone, req); err != nil {
				t.Fatalf("expected the report %d to succeed, got %v", req.Sequence, err)
			}
		}
		end := forwardEnd(t, c, "bt", "phone")
		if end.State != jumpstarterdevv1alpha1.LeaseForwardEndStateFailed || end.Sequence != 12 || end.Message != "address already in use" {
			t.Fatalf("an old report replaced a newer one: %+v", end)
		}

		// A larger number is a new state.
		if _, err := svc.ReportForward(phone, reportRequest("bt", connected, 13, "")); err != nil {
			t.Fatalf("expected the report to succeed, got %v", err)
		}
		end = forwardEnd(t, c, "bt", "phone")
		if end.State != jumpstarterdevv1alpha1.LeaseForwardEndStateConnected || end.Sequence != 13 || end.Message != "" {
			t.Fatalf("expected the newer report, got %+v", end)
		}
	})

	t.Run("accepts the first report of an end with any number", func(t *testing.T) {
		svc, c := forwardService(t, forwardLease(jumpstarterdevv1alpha1.LeaseForwardStatePending, btForward()))
		if _, err := svc.ReportForward(phone, reportRequest("bt", ready, 0, "")); err != nil {
			t.Fatalf("expected the report to succeed, got %v", err)
		}
		if end := forwardEnd(t, c, "bt", "phone"); end.State != jumpstarterdevv1alpha1.LeaseForwardEndStateReady {
			t.Fatalf("expected the first report to be accepted, got %+v", end)
		}
	})

	t.Run("the current state again answers a repeated setup", func(t *testing.T) {
		lease := forwardLease(jumpstarterdevv1alpha1.LeaseForwardStateListening, btForward())
		end := lease.EnsureForwardEnd("bt", "phone")
		end.RecordReport(jumpstarterdevv1alpha1.LeaseForwardEndStateReady, "", 50, time.Now().Add(-time.Hour))
		end.RecordSetup(time.Now().Add(-time.Second))
		svc, c := forwardService(t, lease)
		if !forwardEnd(t, c, "bt", "phone").AwaitsReport() {
			t.Fatalf("expected the end to wait for a report")
		}
		if _, err := svc.ReportForward(phone, reportRequest("bt", ready, 50, "")); err != nil {
			t.Fatalf("expected the report to succeed, got %v", err)
		}
		end = forwardEnd(t, c, "bt", "phone")
		if end.AwaitsReport() || end.Sequence != 50 || end.State != jumpstarterdevv1alpha1.LeaseForwardEndStateReady {
			t.Fatalf("expected the report to answer the setup, got %+v", end)
		}
	})

	t.Run("a report that races a different write of the lease status loses nothing", func(t *testing.T) {
		// The lease controller, or the report of a different end, writes the
		// status after ReportForward read the lease. The write of the report
		// has the old resource version: it fails with a conflict, and the
		// report is applied again to the new lease.
		var raced atomic.Bool
		funcs := interceptor.Funcs{SubResourceUpdate: func(
			ctx context.Context, c client.Client, subResource string, obj client.Object, opts ...client.SubResourceUpdateOption,
		) error {
			if _, isLease := obj.(*jumpstarterdevv1alpha1.Lease); isLease && raced.CompareAndSwap(false, true) {
				var current jumpstarterdevv1alpha1.Lease
				if err := c.Get(ctx, client.ObjectKeyFromObject(obj), &current); err != nil {
					return err
				}
				current.EnsureForwardEnd("bt", "headunit").RecordReport(
					jumpstarterdevv1alpha1.LeaseForwardEndStateFailed, "connection refused", 7, time.Now())
				current.SetForwardStatus("bt", jumpstarterdevv1alpha1.LeaseForwardStateFailed, "The setup failed")
				if err := c.Status().Update(ctx, &current); err != nil {
					return err
				}
			}
			return c.SubResource(subResource).Update(ctx, obj, opts...)
		}}
		svc, c := memberService(t, &funcs, forwardLease(jumpstarterdevv1alpha1.LeaseForwardStatePending, btForward()))
		if _, err := svc.ReportForward(phone, reportRequest("bt", ready, 100, "")); err != nil {
			t.Fatalf("expected the report to succeed, got %v", err)
		}
		if !raced.Load() {
			t.Fatalf("the report did not write the lease status")
		}
		if end := forwardEnd(t, c, "bt", "phone"); end.State != jumpstarterdevv1alpha1.LeaseForwardEndStateReady || end.Sequence != 100 {
			t.Fatalf("the report was lost: %+v", end)
		}
		if other := forwardEnd(t, c, "bt", "headunit"); other.State != jumpstarterdevv1alpha1.LeaseForwardEndStateFailed ||
			other.Sequence != 7 || other.Message != "connection refused" {
			t.Fatalf("the report replaced the write that it raced: %+v", other)
		}
		if forwardStatus := getLease(t, c, forwardLeaseName).ForwardStatus("bt"); forwardStatus.State != jumpstarterdevv1alpha1.LeaseForwardStateFailed {
			t.Fatalf("the report replaced the state of the forward: %+v", forwardStatus)
		}
	})

	t.Run("writes the ends to the status and not to the metadata", func(t *testing.T) {
		lease := forwardLease(jumpstarterdevv1alpha1.LeaseForwardStatePending, btForward())
		svc, c := forwardService(t, lease)
		if _, err := svc.ReportForward(phone, reportRequest("bt", ready, 100, "")); err != nil {
			t.Fatalf("expected the report to succeed, got %v", err)
		}
		updated := getLease(t, c, forwardLeaseName)
		if _, ok := updated.Annotations[jumpstarterdevv1alpha1.LegacyLeaseForwardEndsAnnotation]; ok {
			t.Fatalf("the report wrote the annotation of an earlier build")
		}
		if len(updated.Status.Forwards) != 1 || len(updated.Status.Forwards[0].Ends) != 1 ||
			updated.Status.Forwards[0].Ends[0].Member != "phone" {
			t.Fatalf("expected the end in status.forwards, got %+v", updated.Status.Forwards)
		}
	})

	t.Run("rejects a sequence number that the lease status cannot keep", func(t *testing.T) {
		svc, c := forwardService(t, forwardLease(jumpstarterdevv1alpha1.LeaseForwardStatePending, btForward()))
		_, err := svc.ReportForward(phone, reportRequest("bt", ready, 1<<63, ""))
		requireCode(t, err, codes.InvalidArgument)
		if count := forwardEndCount(t, c); count != 0 {
			t.Fatalf("a rejected report changed the lease: %d ends", count)
		}
	})

	denied := []struct {
		name   string
		ctx    context.Context
		req    *pb.ReportForwardRequest
		mutate func(*jumpstarterdevv1alpha1.Lease)
	}{
		{name: "an exporter that is not a member of the lease", ctx: asExporter(context.Background(), "spare"),
			req: reportRequest("bt", ready, 1, "")},
		{name: "an exporter with a different lease", ctx: asExporter(context.Background(), "rpi-1"),
			req: reportRequest("bt", ready, 1, "")},
		{name: "a member that is not an endpoint of the forward", ctx: phone, req: reportRequest("side", ready, 1, ""),
			mutate: func(l *jumpstarterdevv1alpha1.Lease) {
				// side connects headunit and camera: phone is not an end.
				l.Spec.Forwards = append(l.Spec.Forwards, forwardEndpoints("side", "headunit", "rootcanal", "camera", "x"))
			}},
		{name: "a forward that the lease does not have", ctx: phone, req: reportRequest("nothing", ready, 1, "")},
		{name: "a different lease UID", ctx: phone,
			req: &pb.ReportForwardRequest{LeaseUid: "other-uid", ForwardName: "bt", State: ready, Sequence: 1}},
		{name: "the exporter of a Lost member", ctx: phone, req: reportRequest("bt", ready, 1, ""),
			mutate: func(l *jumpstarterdevv1alpha1.Lease) {
				l.MarkMemberLost("phone", jumpstarterdevv1alpha1.LeaseMemberReasonOffline, "")
			}},
		{name: "a lease that ended", ctx: phone, req: reportRequest("bt", ready, 1, ""),
			mutate: func(l *jumpstarterdevv1alpha1.Lease) { l.Status.Ended = true }},
	}
	for _, tt := range denied {
		t.Run("denies "+tt.name, func(t *testing.T) {
			lease := forwardLease(jumpstarterdevv1alpha1.LeaseForwardStatePending, btForward())
			if tt.mutate != nil {
				tt.mutate(lease)
			}
			svc, c := forwardService(t, lease, scalarLease("rpi-1"))
			_, err := svc.ReportForward(tt.ctx, tt.req)
			requireCode(t, err, codes.PermissionDenied)
			if count := forwardEndCount(t, c); count != 0 {
				t.Fatalf("a denied report changed the lease: %d ends", count)
			}
		})
	}

	t.Run("rejects a client", func(t *testing.T) {
		svc, _ := forwardService(t, forwardLease(jumpstarterdevv1alpha1.LeaseForwardStatePending, btForward()))
		if _, err := svc.ReportForward(asClient(context.Background(), "owner"), reportRequest("bt", ready, 1, "")); err == nil {
			t.Fatalf("expected an error for a client")
		}
	})

	t.Run("rejects a request without a state", func(t *testing.T) {
		svc, _ := forwardService(t, forwardLease(jumpstarterdevv1alpha1.LeaseForwardStatePending, btForward()))
		_, err := svc.ReportForward(phone, reportRequest("bt", 0, 1, ""))
		requireCode(t, err, codes.InvalidArgument, "invalid forward end state")
		_, err = svc.ReportForward(phone, &pb.ReportForwardRequest{ForwardName: "bt", State: ready})
		requireCode(t, err, codes.InvalidArgument, "lease_uid is required")
		_, err = svc.ReportForward(phone, &pb.ReportForwardRequest{LeaseUid: "lease-uid", State: ready})
		requireCode(t, err, codes.InvalidArgument, "forward_name is required")
	})
}

func TestDialPeer(t *testing.T) {
	listening := jumpstarterdevv1alpha1.LeaseForwardStateListening
	phone := asExporter(context.Background(), "phone-1")
	headunit := asExporter(context.Background(), "hu-1")
	request := func(forward string) *pb.DialPeerRequest {
		return &pb.DialPeerRequest{LeaseUid: "lease-uid", ForwardName: forward}
	}
	receive := func(t *testing.T, queue *listenQueue) *pb.ForwardConnect {
		t.Helper()
		select {
		case msg := <-queue.ch:
			if msg.RouterEndpoint != "" || msg.RouterToken != "" {
				t.Fatalf("a forward instruction must not be a client connection too: %v", msg)
			}
			if msg.GetForwardConnect() == nil {
				t.Fatalf("expected a connect instruction, got %v", msg)
			}
			return msg.GetForwardConnect()
		default:
			t.Fatalf("the peer exporter got no connect instruction")
			return nil
		}
	}

	t.Run("gives the requires end and the provides exporter the same token for a new stream", func(t *testing.T) {
		svc, _ := forwardService(t, forwardLease(listening, btForward()))
		svc.Router = config.Router{
			"default": {Endpoint: "router.example.com:443"},
			"edge":    {Endpoint: "edge.example.com:443", Labels: map[string]string{"zone": "edge"}},
		}
		// The router is selected with the labels of the caller.
		var caller jumpstarterdevv1alpha1.Exporter
		if err := svc.Client.Get(context.Background(), types.NamespacedName{Namespace: memberNamespace, Name: "phone-1"}, &caller); err != nil {
			t.Fatal(err)
		}
		caller.Labels = map[string]string{"zone": "edge"}
		if err := svc.Client.Update(context.Background(), &caller); err != nil {
			t.Fatal(err)
		}
		provider := listenFor(svc, forwardLeaseName, "hu-1")
		own := listenFor(svc, forwardLeaseName, "phone-1")

		before := time.Now()
		first, err := svc.DialPeer(phone, request("bt"))
		if err != nil {
			t.Fatalf("expected DialPeer to succeed, got %v", err)
		}
		if first.RouterEndpoint != "edge.example.com:443" {
			t.Fatalf("expected the router of the labels of the caller, got %s", first.RouterEndpoint)
		}
		connect := receive(t, provider)
		if connect.RouterToken != first.RouterToken || connect.ConnectionId != first.ConnectionId ||
			connect.RouterEndpoint != first.RouterEndpoint {
			t.Fatalf("the provides exporter got a different stream: %v, %v", connect, first)
		}
		if connect.LeaseUid != "lease-uid" || connect.ForwardName != "bt" || connect.PeerMemberName != "phone" {
			t.Fatalf("unexpected connect instruction: %v", connect)
		}
		if len(own.ch) != 0 {
			t.Fatalf("the caller got an instruction on its own Listen stream")
		}

		// The token is a router token for the stream, and it expires after 60 seconds.
		claims := jwt.RegisteredClaims{}
		if _, err := jwt.ParseWithClaims(first.RouterToken, &claims, func(*jwt.Token) (any, error) {
			return []byte("test-router-key"), nil
		}, jwt.WithAudience("https://jumpstarter.dev/router"), jwt.WithIssuer("https://jumpstarter.dev/stream")); err != nil {
			t.Fatalf("the token is not a valid router token: %v", err)
		}
		if claims.Subject != first.ConnectionId || first.ConnectionId == "" {
			t.Fatalf("expected the stream ID %q as subject, got %q", first.ConnectionId, claims.Subject)
		}
		lifetime := claims.ExpiresAt.Sub(before)
		if lifetime < 55*time.Second || lifetime > 65*time.Second {
			t.Fatalf("expected the token to expire after 60 seconds, got %s", lifetime)
		}

		// Two connections on one forward get different streams.
		second, err := svc.DialPeer(phone, request("bt"))
		if err != nil {
			t.Fatalf("expected the second DialPeer to succeed, got %v", err)
		}
		if second.ConnectionId == first.ConnectionId || second.RouterToken == first.RouterToken {
			t.Fatalf("two connections got the same stream")
		}
		if again := receive(t, provider); again.ConnectionId != second.ConnectionId {
			t.Fatalf("the provides exporter got the stream %s for the connection %s", again.ConnectionId, second.ConnectionId)
		}
	})

	t.Run("works on a forward that is Connected", func(t *testing.T) {
		svc, _ := forwardService(t, forwardLease(jumpstarterdevv1alpha1.LeaseForwardStateConnected, btForward()))
		listenFor(svc, forwardLeaseName, "hu-1")
		if _, err := svc.DialPeer(phone, request("bt")); err != nil {
			t.Fatalf("expected DialPeer to succeed, got %v", err)
		}
	})

	t.Run("only the initiator opens a path", func(t *testing.T) {
		svc, _ := forwardService(t, forwardLease(listening, uartForward()))
		other := listenFor(svc, forwardLeaseName, "phone-1")
		listenFor(svc, forwardLeaseName, "hu-1")

		resp, err := svc.DialPeer(headunit, request("uart"))
		if err != nil {
			t.Fatalf("expected DialPeer of the initiator to succeed, got %v", err)
		}
		connect := receive(t, other)
		if connect.ConnectionId != resp.ConnectionId || connect.PeerMemberName != "headunit" {
			t.Fatalf("unexpected connect instruction: %v", connect)
		}

		_, err = svc.DialPeer(phone, request("uart"))
		requireCode(t, err, codes.PermissionDenied, "initiator of a path")
	})

	rejected := []struct {
		name    string
		ctx     context.Context
		forward string
		state   jumpstarterdevv1alpha1.LeaseForwardState
		mutate  func(*jumpstarterdevv1alpha1.Lease)
		code    codes.Code
	}{
		{name: "the provides end", ctx: headunit, code: codes.PermissionDenied},
		{name: "an exporter that is not a member", ctx: asExporter(context.Background(), "spare"), code: codes.PermissionDenied},
		{name: "a forward that the lease does not have", ctx: phone, forward: "nothing", code: codes.PermissionDenied},
		{name: "the exporter of a member that is not Bound", ctx: phone, code: codes.PermissionDenied,
			mutate: func(l *jumpstarterdevv1alpha1.Lease) {
				l.MarkMemberLost("phone", jumpstarterdevv1alpha1.LeaseMemberReasonOffline, "")
			}},
		{name: "a lease that ended", ctx: phone, code: codes.PermissionDenied,
			mutate: func(l *jumpstarterdevv1alpha1.Lease) { l.Status.Ended = true }},
		{name: "a forward that is Failed", ctx: phone, state: jumpstarterdevv1alpha1.LeaseForwardStateFailed,
			code: codes.FailedPrecondition},
		{name: "a forward that is Disabled", ctx: phone, state: jumpstarterdevv1alpha1.LeaseForwardStateDisabled,
			code: codes.FailedPrecondition},
		{name: "a forward that is Pending", ctx: phone, state: jumpstarterdevv1alpha1.LeaseForwardStatePending,
			code: codes.FailedPrecondition},
		{name: "a forward without a status", ctx: phone, code: codes.FailedPrecondition,
			mutate: func(l *jumpstarterdevv1alpha1.Lease) { l.Status.Forwards = nil }},
	}
	for _, tt := range rejected {
		t.Run("rejects "+tt.name, func(t *testing.T) {
			state := tt.state
			if state == "" {
				state = listening
			}
			lease := forwardLease(state, btForward())
			if tt.mutate != nil {
				tt.mutate(lease)
			}
			svc, _ := forwardService(t, lease)
			provider := listenFor(svc, forwardLeaseName, "hu-1")
			own := listenFor(svc, forwardLeaseName, "phone-1")
			name := tt.forward
			if name == "" {
				name = "bt"
			}
			_, err := svc.DialPeer(tt.ctx, request(name))
			requireCode(t, err, tt.code)
			if len(provider.ch) != 0 || len(own.ch) != 0 {
				t.Fatalf("a rejected DialPeer sent an instruction")
			}
		})
	}

	t.Run("rejects a different lease UID", func(t *testing.T) {
		svc, _ := forwardService(t, forwardLease(listening, btForward()))
		listenFor(svc, forwardLeaseName, "hu-1")
		_, err := svc.DialPeer(phone, &pb.DialPeerRequest{LeaseUid: "other-uid", ForwardName: "bt"})
		requireCode(t, err, codes.PermissionDenied)
	})

	t.Run("fails with UNAVAILABLE when the provides exporter has no Listen stream", func(t *testing.T) {
		svc, _ := forwardService(t, forwardLease(listening, btForward()))
		listenFor(svc, forwardLeaseName, "phone-1")
		_, err := svc.DialPeer(phone, request("bt"))
		requireCode(t, err, codes.Unavailable)
	})

	t.Run("fails with RESOURCE_EXHAUSTED when the Listen queue of the provides exporter is full", func(t *testing.T) {
		svc, _ := forwardService(t, forwardLease(listening, btForward()))
		provider := listenFor(svc, forwardLeaseName, "hu-1")
		for range cap(provider.ch) {
			if _, err := svc.DialPeer(phone, request("bt")); err != nil {
				t.Fatalf("expected DialPeer to succeed, got %v", err)
			}
		}
		_, err := svc.DialPeer(phone, request("bt"))
		requireCode(t, err, codes.ResourceExhausted)
	})

	t.Run("fails when the port of the peer is gone", func(t *testing.T) {
		svc, c := forwardService(t, forwardLease(listening, btForward()))
		listenFor(svc, forwardLeaseName, "hu-1")
		setExporterPorts(t, c, "hu-1")
		_, err := svc.DialPeer(phone, request("bt"))
		requireCode(t, err, codes.FailedPrecondition, "reports no port `rootcanal`")
	})
}

// listenAs opens Listen for an exporter and returns what it sends.
func listenAs(t *testing.T, ctx context.Context, svc *ControllerService, exporter string) (*recordingListenStream, <-chan error) {
	t.Helper()
	stream := &recordingListenStream{ctx: asExporter(ctx, exporter), sent: make(chan *pb.ListenResponse, 16)}
	done := make(chan error, 1)
	go func() { done <- svc.Listen(&pb.ListenRequest{LeaseName: forwardLeaseName}, stream) }()
	return stream, done
}

func nextInstruction(t *testing.T, stream *recordingListenStream) *pb.ListenResponse {
	t.Helper()
	select {
	case msg := <-stream.sent:
		if msg.RouterEndpoint != "" || msg.RouterToken != "" {
			t.Fatalf("a forward instruction must not be a client connection too: %v", msg)
		}
		return msg
	case <-time.After(5 * time.Second):
		t.Fatalf("the exporter got no instruction")
		return nil
	}
}

func expectNoInstruction(t *testing.T, stream *recordingListenStream) {
	t.Helper()
	select {
	case msg := <-stream.sent:
		t.Fatalf("unexpected instruction: %v", msg)
	case <-time.After(100 * time.Millisecond):
	}
}

func TestListenForwardSetup(t *testing.T) {
	pending := jumpstarterdevv1alpha1.LeaseForwardStatePending

	t.Run("sends each exporter its own setups, each time it opens Listen", func(t *testing.T) {
		svc, c := forwardService(t, forwardLease(pending, btForward(), uartForward()))
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()

		before := time.Now().Truncate(time.Second)
		phone, phoneDone := listenAs(t, ctx, svc, "phone-1")
		bt := nextInstruction(t, phone).GetForwardSetup()
		uart := nextInstruction(t, phone).GetForwardSetup()
		if bt == nil || uart == nil {
			t.Fatalf("expected two setup instructions")
		}
		if bt.LeaseUid != "lease-uid" || bt.ForwardName != "bt" || bt.MemberName != "phone" ||
			bt.Direction != pb.PortDirection_PORT_DIRECTION_REQUIRES || bt.LocalPortName != "controller" || bt.Initiator {
			t.Fatalf("unexpected setup of the requires end: %v", bt)
		}
		if len(bt.Peers) != 1 || bt.Peers[0].MemberName != "headunit" || bt.Peers[0].PortName != "rootcanal" {
			t.Fatalf("the requires end must have the provides endpoint as peer: %v", bt.Peers)
		}
		if uart.ForwardName != "uart" || uart.Direction != pb.PortDirection_PORT_DIRECTION_CONNECTS ||
			uart.LocalPortName != "uart" || uart.Initiator {
			t.Fatalf("unexpected setup of the second end of the path: %v", uart)
		}
		expectNoInstruction(t, phone)

		headunit, headunitDone := listenAs(t, ctx, svc, "hu-1")
		bt = nextInstruction(t, headunit).GetForwardSetup()
		uart = nextInstruction(t, headunit).GetForwardSetup()
		if bt == nil || bt.MemberName != "headunit" || bt.Direction != pb.PortDirection_PORT_DIRECTION_PROVIDES ||
			bt.LocalPortName != "rootcanal" || len(bt.Peers) != 1 || bt.Peers[0].MemberName != "phone" ||
			bt.Peers[0].PortName != "controller" {
			t.Fatalf("unexpected setup of the provides end: %v", bt)
		}
		if uart == nil || !uart.Initiator || len(uart.Peers) != 1 || uart.Peers[0].MemberName != "phone" {
			t.Fatalf("headunit is the initiator of the path: %v", uart)
		}
		expectNoInstruction(t, headunit)
		expectNoInstruction(t, phone)

		// The time of the setup is on the lease: the report time limit starts there.
		for _, end := range [][2]string{{"bt", "phone"}, {"uart", "phone"}, {"bt", "headunit"}, {"uart", "headunit"}} {
			record := forwardEnd(t, c, end[0], end[1])
			if record.SetupTime == nil || record.SetupTime.Time.Before(before) || !record.AwaitsReport() ||
				record.State != jumpstarterdevv1alpha1.LeaseForwardEndStatePending {
				t.Fatalf("expected the setup time of %v, got %+v", end, record)
			}
		}

		// The exporter reports, and opens Listen again: it gets the setups
		// again, and the controller waits for a new report.
		if _, err := svc.ReportForward(asExporter(context.Background(), "phone-1"),
			reportRequest("bt", pb.ForwardEndState_FORWARD_END_STATE_READY, 5, "")); err != nil {
			t.Fatalf("expected the report to succeed, got %v", err)
		}
		if forwardEnd(t, c, "bt", "phone").AwaitsReport() {
			t.Fatalf("the report must answer the setup")
		}
		again, againDone := listenAs(t, ctx, svc, "phone-1")
		if setup := nextInstruction(t, again).GetForwardSetup(); setup == nil || setup.ForwardName != "bt" {
			t.Fatalf("expected the setup of bt again, got %v", setup)
		}
		if setup := nextInstruction(t, again).GetForwardSetup(); setup == nil || setup.ForwardName != "uart" {
			t.Fatalf("expected the setup of uart again, got %v", setup)
		}
		record := forwardEnd(t, c, "bt", "phone")
		if !record.AwaitsReport() || record.State != jumpstarterdevv1alpha1.LeaseForwardEndStateReady || record.Sequence != 5 {
			t.Fatalf("a repeated setup keeps the last state and waits for a report, got %+v", record)
		}

		cancel()
		for _, done := range []<-chan error{phoneDone, headunitDone, againDone} {
			select {
			case err := <-done:
				if err != nil {
					t.Fatalf("expected Listen to end without an error, got %v", err)
				}
			case <-time.After(5 * time.Second):
				t.Fatalf("Listen did not end")
			}
		}
	})

	t.Run("sends the setups after the beforeLease hook", func(t *testing.T) {
		svc, c := forwardService(t, forwardLease(pending, btForward()))
		setExporterStatus(t, c, "phone-1", jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHook)
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()

		phone, _ := listenAs(t, ctx, svc, "phone-1")
		waitForListener(t, svc, forwardLeaseName, "phone-1")
		expectNoInstruction(t, phone)
		if forwardEnd(t, c, "bt", "phone").SetupTime != nil {
			t.Fatalf("the report time limit must not start before the setup")
		}

		setExporterStatus(t, c, "phone-1", jumpstarterdevv1alpha1.ExporterStatusLeaseReady)
		if setup := nextInstruction(t, phone).GetForwardSetup(); setup == nil || setup.ForwardName != "bt" {
			t.Fatalf("expected the setup after the hook, got %v", setup)
		}
		if forwardEnd(t, c, "bt", "phone").SetupTime == nil {
			t.Fatalf("expected the setup time on the lease")
		}
	})

	t.Run("sends no setup for a forward that failed the validation or is Disabled", func(t *testing.T) {
		svc, _ := forwardService(t, forwardLease(pending,
			// phone.nothing is not a port of phone-1.
			forwardEndpoints("broken", "headunit", "rootcanal", "phone", "nothing"),
			// camera is Omitted, and the path cannot operate without it.
			forwardEndpoints("cam", "phone", "uart", "camera", "uart"),
			btForward()))
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()

		phone, _ := listenAs(t, ctx, svc, "phone-1")
		if setup := nextInstruction(t, phone).GetForwardSetup(); setup == nil || setup.ForwardName != "bt" {
			t.Fatalf("expected only the setup of bt, got %v", setup)
		}
		expectNoInstruction(t, phone)
	})

	t.Run("sends no forward instruction to an exporter that reports no ports", func(t *testing.T) {
		svc, c := forwardService(t, forwardLease(pending, btForward()))
		setExporterPorts(t, c, "phone-1")
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()

		phone, _ := listenAs(t, ctx, svc, "phone-1")
		waitForListener(t, svc, forwardLeaseName, "phone-1")
		expectNoInstruction(t, phone)
		if count := forwardEndCount(t, c); count != 0 {
			t.Fatalf("an exporter without ports got a setup time: %d ends", count)
		}
		// It serves client connections as before.
		resp, err := svc.Dial(asClient(context.Background(), "owner"), dialRequest(forwardLeaseName, "phone"))
		if err != nil {
			t.Fatalf("expected Dial to succeed, got %v", err)
		}
		select {
		case msg := <-phone.sent:
			if msg.RouterToken != resp.RouterToken || msg.Forward != nil {
				t.Fatalf("expected the client connection, got %v", msg)
			}
		case <-time.After(5 * time.Second):
			t.Fatalf("the exporter got no client connection")
		}
	})

	t.Run("sends no forward instruction for a lease without forwards", func(t *testing.T) {
		svc, c := forwardService(t, memberLease())
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		phone, _ := listenAs(t, ctx, svc, "phone-1")
		waitForListener(t, svc, forwardLeaseName, "phone-1")
		expectNoInstruction(t, phone)
		if lease := getLease(t, c, forwardLeaseName); len(lease.Status.Forwards) != 0 {
			t.Fatalf("a lease without forwards got a forward status: %+v", lease.Status.Forwards)
		}
	})

	t.Run("delivers the connect instruction of DialPeer on the Listen stream of the peer", func(t *testing.T) {
		svc, _ := forwardService(t, forwardLease(jumpstarterdevv1alpha1.LeaseForwardStateListening, btForward()))
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		headunit, _ := listenAs(t, ctx, svc, "hu-1")
		if setup := nextInstruction(t, headunit).GetForwardSetup(); setup == nil {
			t.Fatalf("expected the setup first")
		}
		resp, err := svc.DialPeer(asExporter(context.Background(), "phone-1"),
			&pb.DialPeerRequest{LeaseUid: "lease-uid", ForwardName: "bt"})
		if err != nil {
			t.Fatalf("expected DialPeer to succeed, got %v", err)
		}
		connect := nextInstruction(t, headunit).GetForwardConnect()
		if connect == nil || connect.ConnectionId != resp.ConnectionId || connect.RouterToken != resp.RouterToken {
			t.Fatalf("expected the connect instruction of the connection, got %v", connect)
		}
	})

	t.Run("sends a teardown when a member that the forward cannot operate without is Lost", func(t *testing.T) {
		svc, c := forwardService(t, forwardLease(pending, btForward()))
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		headunit, _ := listenAs(t, ctx, svc, "hu-1")
		if setup := nextInstruction(t, headunit).GetForwardSetup(); setup == nil {
			t.Fatalf("expected the setup first")
		}

		lease := getLease(t, c, forwardLeaseName)
		lease.MarkMemberLost("phone", jumpstarterdevv1alpha1.LeaseMemberReasonOffline, "")
		if err := c.Status().Update(context.Background(), lease); err != nil {
			t.Fatal(err)
		}
		teardown := nextInstruction(t, headunit).GetForwardTeardown()
		if teardown == nil || teardown.ForwardName != "bt" || teardown.LeaseUid != "lease-uid" {
			t.Fatalf("expected the teardown of bt, got %v", teardown)
		}
		expectNoInstruction(t, headunit)
	})

	t.Run("sends a teardown when the lease ends", func(t *testing.T) {
		svc, c := forwardService(t, forwardLease(pending, btForward()))
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		headunit, _ := listenAs(t, ctx, svc, "hu-1")
		if setup := nextInstruction(t, headunit).GetForwardSetup(); setup == nil {
			t.Fatalf("expected the setup first")
		}
		lease := getLease(t, c, forwardLeaseName)
		lease.Status.Ended = true
		if err := c.Status().Update(context.Background(), lease); err != nil {
			t.Fatal(err)
		}
		if teardown := nextInstruction(t, headunit).GetForwardTeardown(); teardown == nil || teardown.ForwardName != "bt" {
			t.Fatalf("expected the teardown of bt, got %v", teardown)
		}
	})

	t.Run("sends the setup when a forward that failed the validation becomes valid", func(t *testing.T) {
		svc, c := forwardService(t, forwardLease(pending, btForward()))
		setExporterPorts(t, c, "hu-1", jumpstarterdevv1alpha1.DevicePort{
			Name: "other", Direction: jumpstarterdevv1alpha1.PortDirectionProvides})
		ctx, cancel := context.WithCancel(context.Background())
		defer cancel()
		phone, _ := listenAs(t, ctx, svc, "phone-1")
		waitForListener(t, svc, forwardLeaseName, "phone-1")
		expectNoInstruction(t, phone)

		// The lease controller sees the missing port, then the port.
		setForwardState := func(state jumpstarterdevv1alpha1.LeaseForwardState) {
			lease := getLease(t, c, forwardLeaseName)
			lease.SetForwardStatus("bt", state, "")
			if err := c.Status().Update(context.Background(), lease); err != nil {
				t.Fatal(err)
			}
		}
		setForwardState(jumpstarterdevv1alpha1.LeaseForwardStateFailed)
		expectNoInstruction(t, phone)
		setExporterPorts(t, c, "hu-1", jumpstarterdevv1alpha1.DevicePort{
			Name: "rootcanal", Direction: jumpstarterdevv1alpha1.PortDirectionProvides})
		setForwardState(pending)
		if setup := nextInstruction(t, phone).GetForwardSetup(); setup == nil || setup.ForwardName != "bt" {
			t.Fatalf("expected the setup of bt, got %v", setup)
		}
	})
}
