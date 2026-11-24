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

func TestForwardEndsAnnotation(t *testing.T) {
	lease := &Lease{}
	if ends := lease.ForwardEnds(); ends == nil || len(ends) != 0 {
		t.Fatalf("expected an empty map for a lease without the annotation, got %v", ends)
	}

	ends := ForwardEnds{
		ForwardEndKey("bt", "peer"):     {State: ForwardEndStateFailed, Message: "address already in use", Sequence: 1764000000123, ReportTime: 20, SetupTime: 10},
		ForwardEndKey("bt", "headunit"): {SetupTime: 10},
	}
	if err := lease.SetForwardEnds(ends); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if !strings.HasPrefix(lease.Annotations[LeaseForwardEndsAnnotation], `{"bt/headunit":`) {
		t.Fatalf("expected the records in the annotation, sorted by key, got %q", lease.Annotations[LeaseForwardEndsAnnotation])
	}
	got := lease.ForwardEnds()
	if len(got) != 2 || got["bt/peer"] != ends["bt/peer"] || got["bt/headunit"] != ends["bt/headunit"] {
		t.Fatalf("expected the records back, got %+v", got)
	}
	if !got["bt/headunit"].AwaitsReport() || got["bt/peer"].AwaitsReport() {
		t.Fatalf("an end waits for a report only after a setup that has no later report")
	}
	if (ForwardEnd{State: ForwardEndStateReady, ReportTime: 5}).AwaitsReport() {
		t.Fatalf("an end without a setup does not wait for a report")
	}

	// An annotation that cannot be read counts as empty.
	lease.Annotations[LeaseForwardEndsAnnotation] = "{not json"
	if ends := lease.ForwardEnds(); ends == nil || len(ends) != 0 {
		t.Fatalf("expected an empty map for an invalid annotation, got %v", ends)
	}
	lease.Annotations[LeaseForwardEndsAnnotation] = "null"
	if ends := lease.ForwardEnds(); ends == nil {
		t.Fatalf("expected a map for a null annotation")
	}

	// No records: no annotation.
	if err := lease.SetForwardEnds(ForwardEnds{}); err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if _, ok := lease.Annotations[LeaseForwardEndsAnnotation]; ok {
		t.Fatalf("expected the annotation to be removed")
	}
}

func TestForwardEndStateFromProto(t *testing.T) {
	for state, want := range map[pb.ForwardEndState]ForwardEndState{
		pb.ForwardEndState_FORWARD_END_STATE_READY:     ForwardEndStateReady,
		pb.ForwardEndState_FORWARD_END_STATE_CONNECTED: ForwardEndStateConnected,
		pb.ForwardEndState_FORWARD_END_STATE_FAILED:    ForwardEndStateFailed,
	} {
		if got, ok := ForwardEndStateFromProto(state); !ok || got != want {
			t.Fatalf("%s: expected %s, got %s, %t", state, want, got, ok)
		}
	}
	for _, state := range []pb.ForwardEndState{pb.ForwardEndState_FORWARD_END_STATE_UNSPECIFIED, 42} {
		if _, ok := ForwardEndStateFromProto(state); ok {
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
