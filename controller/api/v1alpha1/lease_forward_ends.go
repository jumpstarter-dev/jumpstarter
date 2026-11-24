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
	"encoding/json"
	"fmt"

	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
)

// The state of each end of a forward (JEP-0016), and the conversions of
// forwards and ports to and from the protocol.
//
// A forward has one state in status.forwards, which the lease controller
// calculates from the reports of its endpoint exporters (ReportForward). The
// reports themselves are kept on the lease, in the annotation
// LeaseForwardEndsAnnotation: the controller service writes them, and the
// write makes the lease controller reconcile. They are on the lease, and not
// in the memory of the controller, so that a restart of the controller loses
// nothing: the sequence numbers stay, and a forward does not go back to
// Pending.

// LeaseForwardEndsAnnotation has the state of the ends of the forwards of a
// lease, as the JSON of a ForwardEnds. Only the controller writes it.
const LeaseForwardEndsAnnotation = "jumpstarter.dev/forward-ends"

// ForwardEndState is the state that an exporter reported for its end of a
// forward. No value means that the end sent no report.
type ForwardEndState string

const (
	// ForwardEndStateReady: the listener is open, or the forward is recorded.
	ForwardEndStateReady ForwardEndState = "Ready"
	// ForwardEndStateConnected: a minimum of one connection is open.
	ForwardEndStateConnected ForwardEndState = "Connected"
	// ForwardEndStateFailed: the setup failed.
	ForwardEndStateFailed ForwardEndState = "Failed"
)

// ForwardEnd is the record of one end of a forward: the last report that the
// controller accepted, and the time of the last setup instruction.
type ForwardEnd struct {
	// State is the state of the last accepted report.
	State ForwardEndState `json:"state,omitempty"`
	// Message is the cause that a Failed report gave.
	Message string `json:"message,omitempty"`
	// Sequence is the sequence number of the last accepted report. A report
	// with a number that is not larger is ignored.
	Sequence uint64 `json:"sequence,omitempty"`
	// ReportTime is when the controller accepted the last report, in Unix
	// milliseconds.
	ReportTime int64 `json:"reportTime,omitempty"`
	// SetupTime is when the controller sent the last setup instruction to
	// the exporter of this end, in Unix milliseconds.
	SetupTime int64 `json:"setupTime,omitempty"`
}

// AwaitsReport reports whether the end got a setup instruction and sent no
// report after it.
func (e ForwardEnd) AwaitsReport() bool {
	return e.SetupTime != 0 && e.ReportTime < e.SetupTime
}

// ForwardEnds has the record of each end of the forwards of a lease, by
// ForwardEndKey.
type ForwardEnds map[string]ForwardEnd

// ForwardEndKey is the key of the end of a forward on a member. Forward names
// and role names are DNS labels: they contain no "/".
func ForwardEndKey(forward, member string) string {
	return forward + "/" + member
}

// ForwardEnds returns the records of the ends of the forwards of the lease.
// It never returns nil. An annotation that cannot be read counts as empty:
// the exporters report again after the next setup instruction.
func (l *Lease) ForwardEnds() ForwardEnds {
	ends := ForwardEnds{}
	raw := l.Annotations[LeaseForwardEndsAnnotation]
	if raw == "" {
		return ends
	}
	if err := json.Unmarshal([]byte(raw), &ends); err != nil || ends == nil {
		return ForwardEnds{}
	}
	return ends
}

// SetForwardEnds writes the records of the ends of the forwards to the lease
// metadata. The caller writes the lease.
func (l *Lease) SetForwardEnds(ends ForwardEnds) error {
	if len(ends) == 0 {
		delete(l.Annotations, LeaseForwardEndsAnnotation)
		return nil
	}
	raw, err := json.Marshal(ends)
	if err != nil {
		return fmt.Errorf("SetForwardEnds: %w", err)
	}
	if l.Annotations == nil {
		l.Annotations = map[string]string{}
	}
	l.Annotations[LeaseForwardEndsAnnotation] = string(raw)
	return nil
}

// ForwardEndStateFromProto converts the state of a ReportForward request. ok
// is false for an unspecified or unknown state.
func ForwardEndStateFromProto(state pb.ForwardEndState) (ForwardEndState, bool) {
	switch state {
	case pb.ForwardEndState_FORWARD_END_STATE_READY:
		return ForwardEndStateReady, true
	case pb.ForwardEndState_FORWARD_END_STATE_CONNECTED:
		return ForwardEndStateConnected, true
	case pb.ForwardEndState_FORWARD_END_STATE_FAILED:
		return ForwardEndStateFailed, true
	}
	return "", false
}

var portDirectionToProto = map[PortDirection]pb.PortDirection{
	PortDirectionProvides: pb.PortDirection_PORT_DIRECTION_PROVIDES,
	PortDirectionRequires: pb.PortDirection_PORT_DIRECTION_REQUIRES,
	PortDirectionConnects: pb.PortDirection_PORT_DIRECTION_CONNECTS,
}

var portAttachmentToProto = map[PortAttachment]pb.PortAttachment{
	PortAttachmentSocket:   pb.PortAttachment_PORT_ATTACHMENT_SOCKET,
	PortAttachmentWired:    pb.PortAttachment_PORT_ATTACHMENT_WIRED,
	PortAttachmentWireless: pb.PortAttachment_PORT_ATTACHMENT_WIRELESS,
}

var portTransportToProto = map[PortTransport]pb.PortTransport{
	PortTransportStream:   pb.PortTransport_PORT_TRANSPORT_STREAM,
	PortTransportDatagram: pb.PortTransport_PORT_TRANSPORT_DATAGRAM,
	PortTransportFrame:    pb.PortTransport_PORT_TRANSPORT_FRAME,
}

// ToProtobuf converts the direction of a port.
func (d PortDirection) ToProtobuf() pb.PortDirection {
	return portDirectionToProto[d]
}

// DevicePortFromProtobuf converts a port of a driver report. An attachment
// and a transport that the report does not specify get their defaults: socket
// and stream. It returns an error for a port without a direction and for an
// enum value that this controller does not know.
func DevicePortFromProtobuf(p *pb.PortReport) (*DevicePort, error) {
	port := &DevicePort{Name: p.GetName(), Protocol: p.GetProtocol()}
	for direction, value := range portDirectionToProto {
		if value == p.GetDirection() {
			port.Direction = direction
		}
	}
	if port.Direction == "" {
		if p.GetDirection() == pb.PortDirection_PORT_DIRECTION_UNSPECIFIED {
			return nil, fmt.Errorf("port %q has no direction", p.GetName())
		}
		return nil, fmt.Errorf("port %q has the unknown direction %d", p.GetName(), p.GetDirection())
	}

	port.Attachment = PortAttachmentSocket
	if p.GetAttachment() != pb.PortAttachment_PORT_ATTACHMENT_UNSPECIFIED {
		port.Attachment = ""
		for attachment, value := range portAttachmentToProto {
			if value == p.GetAttachment() {
				port.Attachment = attachment
			}
		}
		if port.Attachment == "" {
			return nil, fmt.Errorf("port %q has the unknown attachment %d", p.GetName(), p.GetAttachment())
		}
	}

	port.Transport = PortTransportStream
	if p.GetTransport() != pb.PortTransport_PORT_TRANSPORT_UNSPECIFIED {
		port.Transport = ""
		for transport, value := range portTransportToProto {
			if value == p.GetTransport() {
				port.Transport = transport
			}
		}
		if port.Transport == "" {
			return nil, fmt.Errorf("port %q has the unknown transport %d", p.GetName(), p.GetTransport())
		}
	}
	return port, nil
}

// ToProtobuf converts the port to a port report, with the defaults of the
// attachment and the transport.
func (p *DevicePort) ToProtobuf() *pb.PortReport {
	report := &pb.PortReport{
		Name:       p.Name,
		Direction:  portDirectionToProto[p.Direction],
		Attachment: portAttachmentToProto[p.EffectiveAttachment()],
		Transport:  portTransportToProto[p.EffectiveTransport()],
	}
	if p.Protocol != "" {
		report.Protocol = new(p.Protocol)
	}
	return report
}

// LeaseForwardFromProtobuf converts a forward of a lease request.
func LeaseForwardFromProtobuf(f *cpb.LeaseForward) LeaseForward {
	forward := LeaseForward{Name: f.GetName()}
	for _, endpoint := range f.GetEndpoints() {
		forward.Endpoints = append(forward.Endpoints, ForwardEndpoint{
			Member: endpoint.GetMemberName(),
			Port:   endpoint.GetPortName(),
		})
	}
	return forward
}

// ToProtobuf converts the forward.
func (f *LeaseForward) ToProtobuf() *cpb.LeaseForward {
	forward := &cpb.LeaseForward{Name: f.Name}
	for i := range f.Endpoints {
		forward.Endpoints = append(forward.Endpoints, &cpb.ForwardEndpoint{
			MemberName: f.Endpoints[i].Member,
			PortName:   f.Endpoints[i].Port,
		})
	}
	return forward
}

var leaseForwardStateToProto = map[LeaseForwardState]cpb.LeaseForwardState{
	LeaseForwardStatePending:   cpb.LeaseForwardState_LEASE_FORWARD_STATE_PENDING,
	LeaseForwardStateDisabled:  cpb.LeaseForwardState_LEASE_FORWARD_STATE_DISABLED,
	LeaseForwardStateListening: cpb.LeaseForwardState_LEASE_FORWARD_STATE_LISTENING,
	LeaseForwardStateConnected: cpb.LeaseForwardState_LEASE_FORWARD_STATE_CONNECTED,
	LeaseForwardStateFailed:    cpb.LeaseForwardState_LEASE_FORWARD_STATE_FAILED,
}

// ToProtobuf converts the status of a forward.
func (s *LeaseForwardStatus) ToProtobuf() *cpb.LeaseForwardStatus {
	forwardStatus := &cpb.LeaseForwardStatus{
		Name:  s.Name,
		State: leaseForwardStateToProto[s.State],
	}
	if s.Message != "" {
		forwardStatus.Message = new(s.Message)
	}
	return forwardStatus
}
