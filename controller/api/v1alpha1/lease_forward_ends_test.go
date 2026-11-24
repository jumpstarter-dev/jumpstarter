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

package v1alpha1

import (
	"strings"
	"testing"
	"time"

	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/protobuf/types/known/durationpb"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

func TestLeaseForwardEnds(t *testing.T) {
	lease := &Lease{}
	if lease.ForwardEnd("bt", "peer") != nil {
		t.Fatalf("expected no end for a lease without status")
	}
	var none *LeaseForwardEndStatus
	if none.Reported() || none.AwaitsReport() {
		t.Fatalf("no end has no report and waits for none")
	}

	// The first end of a forward adds its status too. An end is Pending
	// until its exporter sends a report.
	end := lease.EnsureForwardEnd("bt", "peer")
	if len(lease.Status.Forwards) != 1 || lease.Status.Forwards[0].State != LeaseForwardStatePending ||
		end.Member != "peer" || end.State != LeaseForwardEndStatePending || end.Reported() || end.AwaitsReport() {
		t.Fatalf("unexpected first end: %+v in %+v", end, lease.Status.Forwards)
	}
	lease.EnsureForwardEnd("bt", "headunit")
	lease.EnsureForwardEnd("can", "peer")
	if lease.EnsureForwardEnd("bt", "peer") != lease.ForwardEnd("bt", "peer") ||
		len(lease.Status.Forwards) != 2 || len(lease.Status.Forwards[0].Ends) != 2 {
		t.Fatalf("expected one end for each forward and member, got %+v", lease.Status.Forwards)
	}

	// The times have the precision of the API: one second.
	t0 := time.Date(2026, 11, 24, 10, 0, 0, 0, time.UTC)
	at := func(seconds int, millis int) time.Time {
		return t0.Add(time.Duration(seconds)*time.Second + time.Duration(millis)*time.Millisecond)
	}
	end = lease.ForwardEnd("bt", "peer")
	end.RecordSetup(at(0, 700))
	if !end.SetupTime.Time.Equal(t0) || !end.AwaitsReport() || end.Reported() {
		t.Fatalf("expected an end that waits for its report, got %+v", end)
	}

	// The first report is accepted with each sequence number. A report in
	// the second of the setup answers it.
	if changed, accepted := end.RecordReport(LeaseForwardEndStateReady, "", 100, at(0, 900)); !changed || !accepted {
		t.Fatalf("expected the first report to be accepted")
	}
	if end.AwaitsReport() || !end.Reported() || end.Sequence != 100 || !end.ReportTime.Time.Equal(t0) {
		t.Fatalf("unexpected end after the first report: %+v", end)
	}

	// A number that is not larger is ignored, and so is the same report
	// again when no setup waits for it.
	for _, sequence := range []uint64{99, 100} {
		if changed, accepted := end.RecordReport(LeaseForwardEndStateFailed, "old", sequence, at(5, 0)); changed || accepted {
			t.Fatalf("sequence %d: expected the report to be ignored", sequence)
		}
	}
	if changed, _ := end.RecordReport(LeaseForwardEndStateReady, "", 100, at(5, 0)); changed {
		t.Fatalf("expected the same report again to change nothing")
	}
	if end.State != LeaseForwardEndStateReady || !end.ReportTime.Time.Equal(t0) {
		t.Fatalf("an ignored report changed the end: %+v", end)
	}

	// A setup in the second of the last report is after that report, and a
	// report in that second answers it.
	end.RecordSetup(at(0, 950))
	if !end.SetupTime.Time.Equal(at(1, 0)) || !end.AwaitsReport() {
		t.Fatalf("expected the setup after the report, got %+v", end)
	}
	if changed, accepted := end.RecordReport(LeaseForwardEndStateFailed, "old", 99, at(0, 960)); changed || accepted {
		t.Fatalf("expected an old report to be ignored while the end waits")
	}
	if changed, accepted := end.RecordReport(LeaseForwardEndStateReady, "", 100, at(0, 970)); !changed || accepted {
		t.Fatalf("expected the current state again to answer the setup and to be no new state")
	}
	if end.AwaitsReport() || end.Sequence != 100 || !end.ReportTime.Time.Equal(at(1, 0)) {
		t.Fatalf("unexpected end after the answer to the setup: %+v", end)
	}

	// A larger number replaces the state and the message.
	if _, accepted := end.RecordReport(LeaseForwardEndStateFailed, "address already in use", 101, at(9, 0)); !accepted {
		t.Fatalf("expected a larger number to be accepted")
	}
	if _, accepted := end.RecordReport(LeaseForwardEndStateConnected, "", 102, at(9, 0)); !accepted || end.Message != "" {
		t.Fatalf("expected the message of the old state to go, got %+v", end)
	}

	if !lease.ClearForwardEnds() || lease.ClearForwardEnds() ||
		len(lease.Status.Forwards) != 2 || lease.ForwardEnd("bt", "peer") != nil {
		t.Fatalf("expected the ends to go and the forwards to stay, got %+v", lease.Status.Forwards)
	}
}

func TestLeaseForwardEndStateFromProto(t *testing.T) {
	for state, want := range map[pb.ForwardEndState]LeaseForwardEndState{
		pb.ForwardEndState_FORWARD_END_STATE_READY:     LeaseForwardEndStateReady,
		pb.ForwardEndState_FORWARD_END_STATE_CONNECTED: LeaseForwardEndStateConnected,
		pb.ForwardEndState_FORWARD_END_STATE_FAILED:    LeaseForwardEndStateFailed,
	} {
		if got, ok := LeaseForwardEndStateFromProto(state); !ok || got != want {
			t.Fatalf("%s: got %q, %t", state, got, ok)
		}
	}
	for _, state := range []pb.ForwardEndState{pb.ForwardEndState_FORWARD_END_STATE_UNSPECIFIED, 42} {
		if _, ok := LeaseForwardEndStateFromProto(state); ok {
			t.Fatalf("%d: expected no state", state)
		}
	}
}

func TestDevicePortProtobuf(t *testing.T) {
	protocol := "hci-h4"
	port, err := DevicePortFromProtobuf(&pb.PortReport{
		Name: "rootcanal", Direction: pb.PortDirection_PORT_DIRECTION_PROVIDES, Protocol: &protocol,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	// No attachment and no transport: the defaults of the JEP.
	want := DevicePort{Name: "rootcanal", Direction: PortDirectionProvides, Protocol: "hci-h4",
		Attachment: PortAttachmentSocket, Transport: PortTransportStream}
	if *port != want {
		t.Fatalf("expected %+v, got %+v", want, *port)
	}
	if !port.IsForwardable() {
		t.Fatalf("a socket port with the stream transport is forwardable")
	}

	directions := map[pb.PortDirection]PortDirection{
		pb.PortDirection_PORT_DIRECTION_PROVIDES: PortDirectionProvides,
		pb.PortDirection_PORT_DIRECTION_REQUIRES: PortDirectionRequires,
		pb.PortDirection_PORT_DIRECTION_CONNECTS: PortDirectionConnects,
	}
	attachments := map[pb.PortAttachment]PortAttachment{
		pb.PortAttachment_PORT_ATTACHMENT_SOCKET:   PortAttachmentSocket,
		pb.PortAttachment_PORT_ATTACHMENT_WIRED:    PortAttachmentWired,
		pb.PortAttachment_PORT_ATTACHMENT_WIRELESS: PortAttachmentWireless,
	}
	transports := map[pb.PortTransport]PortTransport{
		pb.PortTransport_PORT_TRANSPORT_STREAM:   PortTransportStream,
		pb.PortTransport_PORT_TRANSPORT_DATAGRAM: PortTransportDatagram,
		pb.PortTransport_PORT_TRANSPORT_FRAME:    PortTransportFrame,
	}
	for direction, wantDirection := range directions {
		for attachment, wantAttachment := range attachments {
			for transport, wantTransport := range transports {
				report := &pb.PortReport{Name: "p", Direction: direction, Attachment: attachment, Transport: transport}
				port, err := DevicePortFromProtobuf(report)
				if err != nil {
					t.Fatalf("%v: unexpected error: %v", report, err)
				}
				if port.Direction != wantDirection || port.Attachment != wantAttachment || port.Transport != wantTransport {
					t.Fatalf("%v: got %+v", report, port)
				}
				back := port.ToProtobuf()
				if back.Direction != direction || back.Attachment != attachment || back.Transport != transport ||
					back.Protocol != nil || back.Name != "p" {
					t.Fatalf("%v: converted back to %v", report, back)
				}
				if direction.String() != port.Direction.ToProtobuf().String() {
					t.Fatalf("%v: direction converted back to %v", report, port.Direction.ToProtobuf())
				}
			}
		}
	}

	// A port of an older exporter status has no attachment and no transport.
	old := DevicePort{Name: "p", Direction: PortDirectionRequires}
	if back := old.ToProtobuf(); back.Attachment != pb.PortAttachment_PORT_ATTACHMENT_SOCKET ||
		back.Transport != pb.PortTransport_PORT_TRANSPORT_STREAM {
		t.Fatalf("expected the defaults, got %v", back)
	}

	for name, report := range map[string]*pb.PortReport{
		"has no direction":   {Name: "p"},
		"unknown direction":  {Name: "p", Direction: 9},
		"unknown attachment": {Name: "p", Direction: pb.PortDirection_PORT_DIRECTION_PROVIDES, Attachment: 9},
		"unknown transport":  {Name: "p", Direction: pb.PortDirection_PORT_DIRECTION_PROVIDES, Transport: 9},
	} {
		if _, err := DevicePortFromProtobuf(report); err == nil || !strings.Contains(err.Error(), name) {
			t.Fatalf("expected an error with %q, got %v", name, err)
		}
	}
}

func TestLeaseForwardsProtobuf(t *testing.T) {
	selector := "device-type=bt-peer"
	lease, err := LeaseFromProtobuf(&cpb.Lease{
		Duration: durationpb.New(time.Hour),
		Members: []*cpb.LeaseMember{
			{Name: "peer", Selection: &cpb.LeaseMember_Selector{Selector: selector}},
			{Name: "headunit", Selection: &cpb.LeaseMember_ExporterName{ExporterName: "hu-1"}},
		},
		Forwards: []*cpb.LeaseForward{{Name: "bt", Endpoints: []*cpb.ForwardEndpoint{
			{MemberName: "headunit", PortName: "rootcanal"}, {MemberName: "peer", PortName: "controller"},
		}}},
	}, types.NamespacedName{Namespace: "lab", Name: "lease"}, corev1.LocalObjectReference{Name: "client"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(lease.Spec.Forwards) != 1 || lease.Spec.Forwards[0].Name != "bt" ||
		lease.Spec.Forwards[0].Endpoints[0] != (ForwardEndpoint{Member: "headunit", Port: "rootcanal"}) ||
		lease.Spec.Forwards[0].Endpoints[1] != (ForwardEndpoint{Member: "peer", Port: "controller"}) {
		t.Fatalf("unexpected forwards: %+v", lease.Spec.Forwards)
	}

	lease.Status.Forwards = []LeaseForwardStatus{{Name: "bt", State: LeaseForwardStateFailed, Message: "Role `peer` is Lost"}}
	out := lease.ToProtobuf()
	if len(out.Forwards) != 1 || out.Forwards[0].Name != "bt" || len(out.Forwards[0].Endpoints) != 2 ||
		out.Forwards[0].Endpoints[1].MemberName != "peer" || out.Forwards[0].Endpoints[1].PortName != "controller" {
		t.Fatalf("unexpected forwards: %v", out.Forwards)
	}
	if len(out.ForwardStatuses) != 1 || out.ForwardStatuses[0].State != cpb.LeaseForwardState_LEASE_FORWARD_STATE_FAILED ||
		out.ForwardStatuses[0].GetMessage() != "Role `peer` is Lost" {
		t.Fatalf("unexpected forward statuses: %v", out.ForwardStatuses)
	}

	for state, want := range map[LeaseForwardState]cpb.LeaseForwardState{
		LeaseForwardStatePending:   cpb.LeaseForwardState_LEASE_FORWARD_STATE_PENDING,
		LeaseForwardStateDisabled:  cpb.LeaseForwardState_LEASE_FORWARD_STATE_DISABLED,
		LeaseForwardStateListening: cpb.LeaseForwardState_LEASE_FORWARD_STATE_LISTENING,
		LeaseForwardStateConnected: cpb.LeaseForwardState_LEASE_FORWARD_STATE_CONNECTED,
		LeaseForwardStateFailed:    cpb.LeaseForwardState_LEASE_FORWARD_STATE_FAILED,
	} {
		forwardStatus := LeaseForwardStatus{Name: "f", State: state}
		if got := forwardStatus.ToProtobuf(); got.State != want || got.Message != nil {
			t.Fatalf("%s: got %v", state, got)
		}
	}

	// The ends of a forward: the member, the state and the message. The
	// sequence number and the times are for the controller only.
	setup := metav1.NewTime(time.Date(2026, 11, 24, 10, 0, 0, 0, time.UTC))
	lease.Status.Forwards[0].Ends = []LeaseForwardEndStatus{
		{Member: "headunit", State: LeaseForwardEndStateReady, Sequence: 7, SetupTime: &setup, ReportTime: &setup},
		{Member: "peer", State: LeaseForwardEndStateFailed, Message: "address already in use", Sequence: 9},
	}
	ends := lease.ToProtobuf().ForwardStatuses[0].Ends
	if len(ends) != 2 || ends[0].MemberName != "headunit" || ends[0].Message != nil ||
		ends[0].State != cpb.LeaseForwardEndState_LEASE_FORWARD_END_STATE_READY ||
		ends[1].MemberName != "peer" || ends[1].GetMessage() != "address already in use" ||
		ends[1].State != cpb.LeaseForwardEndState_LEASE_FORWARD_END_STATE_FAILED {
		t.Fatalf("unexpected ends: %v", ends)
	}
	for state, want := range map[LeaseForwardEndState]cpb.LeaseForwardEndState{
		LeaseForwardEndStatePending:   cpb.LeaseForwardEndState_LEASE_FORWARD_END_STATE_PENDING,
		LeaseForwardEndStateReady:     cpb.LeaseForwardEndState_LEASE_FORWARD_END_STATE_READY,
		LeaseForwardEndStateConnected: cpb.LeaseForwardEndState_LEASE_FORWARD_END_STATE_CONNECTED,
		LeaseForwardEndStateFailed:    cpb.LeaseForwardEndState_LEASE_FORWARD_END_STATE_FAILED,
	} {
		end := LeaseForwardEndStatus{Member: "m", State: state}
		if got := end.ToProtobuf(); got.State != want || got.Message != nil || got.MemberName != "m" {
			t.Fatalf("%s: got %v", state, got)
		}
	}

	// A lease without forwards, and a scalar lease, have none.
	scalar := &Lease{ObjectMeta: metav1.ObjectMeta{Name: "scalar", Namespace: "lab"}}
	if out := scalar.ToProtobuf(); len(out.Forwards) != 0 || len(out.ForwardStatuses) != 0 {
		t.Fatalf("expected no forwards for a scalar lease, got %v", out)
	}

	template := &LeaseTemplate{
		ObjectMeta: metav1.ObjectMeta{Name: "headunit-peer", Namespace: "lab"},
		Spec:       LeaseTemplateSpec{Members: lease.Spec.Members, Forwards: lease.Spec.Forwards},
	}
	if out := template.ToProtobuf(); len(out.Forwards) != 1 || out.Forwards[0].Name != "bt" || len(out.Forwards[0].Endpoints) != 2 {
		t.Fatalf("unexpected forwards of the template: %v", out.Forwards)
	}
}

func TestExporterPortsProtobuf(t *testing.T) {
	exporter := &Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: "hu-1", Namespace: "lab"},
		Status: ExporterStatus{Devices: []Device{
			{Uuid: "root"},
			{Uuid: "a", Ports: []DevicePort{{Name: "rootcanal", Direction: PortDirectionProvides, Protocol: "hci-h4"}}},
			{Uuid: "b", Ports: []DevicePort{{Name: "uart", Direction: PortDirectionConnects}}},
		}},
	}
	out := exporter.ToProtobuf()
	if len(out.Ports) != 2 || out.Ports[0].Name != "rootcanal" || out.Ports[0].GetProtocol() != "hci-h4" ||
		out.Ports[1].Name != "uart" || out.Ports[1].Direction != pb.PortDirection_PORT_DIRECTION_CONNECTS {
		t.Fatalf("unexpected ports: %v", out.Ports)
	}
	if none := (&Exporter{}).ToProtobuf(); none.Ports != nil {
		t.Fatalf("an exporter that reports no ports has no ports field, got %v", none.Ports)
	}
}
