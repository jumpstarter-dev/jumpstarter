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
	"fmt"
	"time"

	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
)

// The state of each end of a forward (JEP-0016), and the conversions of
// forwards and ports to and from the protocol.
//
// A forward has one state in status.forwards, which the lease controller
// calculates from the reports of its endpoint exporters (ReportForward). The
// reports themselves are kept in the lease status, in
// status.forwards[].ends[]: the controller service writes them, and the write
// makes the lease controller reconcile. They are on the lease, and not in the
// memory of the controller, so that a restart of the controller loses
// nothing: the sequence numbers stay, and a forward does not go back to
// Pending.
//
// The times of an end have the precision of the API: one second. The setup
// time and the report time are compared only with each other and with the
// report time limit, and the writers keep them in order (RecordSetup,
// RecordReport).

// LegacyLeaseForwardEndsAnnotation had the state of the ends of the forwards
// of a lease before status.forwards[].ends[] did. A lease that an earlier
// build created can have it. Nothing reads it, and the lease controller
// deletes it.
const LegacyLeaseForwardEndsAnnotation = "jumpstarter.dev/forward-ends"

// Reported reports whether the exporter of the end sent a report that the
// controller accepted. It is false for nil.
func (e *LeaseForwardEndStatus) Reported() bool {
	return e != nil && e.State != "" && e.State != LeaseForwardEndStatePending
}

// AwaitsReport reports whether the end got a setup instruction and sent no
// report after it. It is false for nil.
func (e *LeaseForwardEndStatus) AwaitsReport() bool {
	if e == nil || e.SetupTime == nil {
		return false
	}
	return e.ReportTime == nil || e.ReportTime.Before(e.SetupTime)
}

func forwardEndTime(t time.Time) *metav1.Time {
	return new(metav1.NewTime(t).Rfc3339Copy())
}

// RecordSetup records a setup instruction that is sent at this time. The
// setup time is always after the last report, so that the end waits for a
// new report: a report that the controller accepted in the same second is
// before this setup.
func (e *LeaseForwardEndStatus) RecordSetup(now time.Time) {
	setup := forwardEndTime(now)
	if e.ReportTime != nil && !e.ReportTime.Before(setup) {
		setup = forwardEndTime(e.ReportTime.Add(time.Second))
	}
	e.SetupTime = setup
}

// RecordReport applies a report of the exporter of the end that arrives at
// this time, and reports whether the end changed and whether the report is a
// new state (accepted).
//
// A report is accepted when the end has no report, or when its sequence
// number is larger than the last accepted number. The same state with the
// same number is the answer to a repeated setup instruction: it is not a new
// state, and it counts as the report that the setup waits for. Each other
// report changes nothing.
func (e *LeaseForwardEndStatus) RecordReport(
	state LeaseForwardEndState,
	message string,
	sequence uint64,
	now time.Time,
) (changed, accepted bool) {
	switch {
	case !e.Reported() || sequence > e.Sequence:
		e.State = state
		e.Message = message
		e.Sequence = sequence
		accepted = true
	case sequence == e.Sequence && e.State == state && e.AwaitsReport():
	default:
		return false, false
	}
	// The report is never before the setup that it answers.
	report := forwardEndTime(now)
	if e.SetupTime != nil && report.Before(e.SetupTime) {
		report = e.SetupTime.DeepCopy()
	}
	e.ReportTime = report
	return true, accepted
}

// End returns the status of the end of the forward on this member, or nil.
func (s *LeaseForwardStatus) End(member string) *LeaseForwardEndStatus {
	if s == nil {
		return nil
	}
	for i := range s.Ends {
		if s.Ends[i].Member == member {
			return &s.Ends[i]
		}
	}
	return nil
}

// ForwardEnd returns the status of the end of the named forward on this
// member, or nil when status.forwards has none.
func (l *Lease) ForwardEnd(forward, member string) *LeaseForwardEndStatus {
	return l.ForwardStatus(forward).End(member)
}

// EnsureForwardEnd returns the status of the end of the named forward on this
// member. It adds a Pending end, and a Pending forward status, if
// status.forwards has none. The caller writes the status.
func (l *Lease) EnsureForwardEnd(forward, member string) *LeaseForwardEndStatus {
	forwardStatus := l.ForwardStatus(forward)
	if forwardStatus == nil {
		l.Status.Forwards = append(l.Status.Forwards, LeaseForwardStatus{Name: forward, State: LeaseForwardStatePending})
		forwardStatus = &l.Status.Forwards[len(l.Status.Forwards)-1]
	}
	if end := forwardStatus.End(member); end != nil {
		return end
	}
	forwardStatus.Ends = append(forwardStatus.Ends, LeaseForwardEndStatus{Member: member, State: LeaseForwardEndStatePending})
	return &forwardStatus.Ends[len(forwardStatus.Ends)-1]
}

// ClearForwardEnds removes the ends of each forward from the status, and
// reports whether it removed one. The caller writes the status.
func (l *Lease) ClearForwardEnds() bool {
	cleared := false
	for i := range l.Status.Forwards {
		if len(l.Status.Forwards[i].Ends) > 0 {
			l.Status.Forwards[i].Ends = nil
			cleared = true
		}
	}
	return cleared
}

// LeaseForwardEndStateFromProto converts the state of a ReportForward
// request. ok is false for an unspecified or unknown state.
func LeaseForwardEndStateFromProto(state pb.ForwardEndState) (LeaseForwardEndState, bool) {
	switch state {
	case pb.ForwardEndState_FORWARD_END_STATE_READY:
		return LeaseForwardEndStateReady, true
	case pb.ForwardEndState_FORWARD_END_STATE_CONNECTED:
		return LeaseForwardEndStateConnected, true
	case pb.ForwardEndState_FORWARD_END_STATE_FAILED:
		return LeaseForwardEndStateFailed, true
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
	for i := range s.Ends {
		forwardStatus.Ends = append(forwardStatus.Ends, s.Ends[i].ToProtobuf())
	}
	return forwardStatus
}

var leaseForwardEndStateToProto = map[LeaseForwardEndState]cpb.LeaseForwardEndState{
	LeaseForwardEndStatePending:   cpb.LeaseForwardEndState_LEASE_FORWARD_END_STATE_PENDING,
	LeaseForwardEndStateReady:     cpb.LeaseForwardEndState_LEASE_FORWARD_END_STATE_READY,
	LeaseForwardEndStateConnected: cpb.LeaseForwardEndState_LEASE_FORWARD_END_STATE_CONNECTED,
	LeaseForwardEndStateFailed:    cpb.LeaseForwardEndState_LEASE_FORWARD_END_STATE_FAILED,
}

// ToProtobuf converts the status of an end of a forward.
func (e *LeaseForwardEndStatus) ToProtobuf() *cpb.LeaseForwardEndStatus {
	endStatus := &cpb.LeaseForwardEndStatus{
		MemberName: e.Member,
		State:      leaseForwardEndStateToProto[e.State],
	}
	if e.Message != "" {
		endStatus.Message = new(e.Message)
	}
	return endStatus
}
