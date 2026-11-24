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

// Accessors for the forwards of a lease (spec.forwards, status.forwards) and
// for the ports that exporters report (status.devices[].ports). They only
// read and write the API objects: the forward validation and the forward
// state machine are in the controller.

// HasForwards reports whether the lease has spec.forwards.
func (l *Lease) HasForwards() bool {
	return len(l.Spec.Forwards) > 0
}

// Forward returns the forward of the lease with this name, or nil.
func (l *Lease) Forward(name string) *LeaseForward {
	for i := range l.Spec.Forwards {
		if l.Spec.Forwards[i].Name == name {
			return &l.Spec.Forwards[i]
		}
	}
	return nil
}

// ForwardNames returns the names of the forwards of the lease, in spec order.
func (l *Lease) ForwardNames() []string {
	names := make([]string, 0, len(l.Spec.Forwards))
	for i := range l.Spec.Forwards {
		names = append(names, l.Spec.Forwards[i].Name)
	}
	return names
}

// ForwardsOfMember returns the forwards that have an endpoint on the member
// with this role name, in spec order.
func (l *Lease) ForwardsOfMember(member string) []*LeaseForward {
	var forwards []*LeaseForward
	for i := range l.Spec.Forwards {
		if l.Spec.Forwards[i].Endpoint(member) != nil {
			forwards = append(forwards, &l.Spec.Forwards[i])
		}
	}
	return forwards
}

// ForwardEndpointForExporter returns the endpoint of the named forward that
// is on the Bound member of this exporter, with the status of that member.
// It returns nil, nil when the lease has no such forward, when no Bound
// member has this exporter, or when that member is not an endpoint of the
// forward.
func (l *Lease) ForwardEndpointForExporter(forwardName, exporterName string) (*ForwardEndpoint, *LeaseMemberStatus) {
	forward := l.Forward(forwardName)
	if forward == nil {
		return nil, nil
	}
	member := l.MemberStatusForExporter(exporterName, LeaseMemberStateBound)
	if member == nil {
		return nil, nil
	}
	endpoint := forward.Endpoint(member.Name)
	if endpoint == nil {
		return nil, nil
	}
	return endpoint, member
}

// ForwardStatus returns the status of the forward with this name, or nil.
func (l *Lease) ForwardStatus(name string) *LeaseForwardStatus {
	for i := range l.Status.Forwards {
		if l.Status.Forwards[i].Name == name {
			return &l.Status.Forwards[i]
		}
	}
	return nil
}

// SetForwardStatus sets the state and the message of the forward with this
// name, and adds the entry if status.forwards has none. It reports whether
// the status changed; the caller writes the status.
func (l *Lease) SetForwardStatus(name string, state LeaseForwardState, message string) bool {
	if s := l.ForwardStatus(name); s != nil {
		if s.State == state && s.Message == message {
			return false
		}
		s.State = state
		s.Message = message
		return true
	}
	l.Status.Forwards = append(l.Status.Forwards, LeaseForwardStatus{Name: name, State: state, Message: message})
	return true
}

// HasForwardInState reports whether a forward of the lease has one of these
// states in status.forwards.
func (l *Lease) HasForwardInState(states ...LeaseForwardState) bool {
	for i := range l.Status.Forwards {
		if l.Status.Forwards[i].State.In(states...) {
			return true
		}
	}
	return false
}

// In reports whether the state is one of these states.
func (s LeaseForwardState) In(states ...LeaseForwardState) bool {
	for _, state := range states {
		if s == state {
			return true
		}
	}
	return false
}

// IsOperational reports whether new connections can open on a forward in
// this state: it is Listening or Connected.
func (s LeaseForwardState) IsOperational() bool {
	return s == LeaseForwardStateListening || s == LeaseForwardStateConnected
}

// Endpoint returns the endpoint of the forward on the member with this role
// name, or nil. A forward has a maximum of one endpoint on each member.
func (f *LeaseForward) Endpoint(member string) *ForwardEndpoint {
	for i := range f.Endpoints {
		if f.Endpoints[i].Member == member {
			return &f.Endpoints[i]
		}
	}
	return nil
}

// Peers returns the endpoints of the forward that are not on the member with
// this role name, in spec order.
func (f *LeaseForward) Peers(member string) []ForwardEndpoint {
	peers := make([]ForwardEndpoint, 0, len(f.Endpoints))
	for i := range f.Endpoints {
		if f.Endpoints[i].Member != member {
			peers = append(peers, f.Endpoints[i])
		}
	}
	return peers
}

// PathInitiator returns the role name of the member that opens a path (a
// forward between two connects ports): the member name that is first in
// sorted order. Only that member calls DialPeer. It returns "" for a forward
// with no endpoints.
func (f *LeaseForward) PathInitiator() string {
	initiator := ""
	for i := range f.Endpoints {
		if initiator == "" || f.Endpoints[i].Member < initiator {
			initiator = f.Endpoints[i].Member
		}
	}
	return initiator
}

// EffectiveAttachment returns the attachment of the port, with the default:
// a port that reports no attachment is a socket port.
func (p *DevicePort) EffectiveAttachment() PortAttachment {
	if p.Attachment == "" {
		return PortAttachmentSocket
	}
	return p.Attachment
}

// EffectiveTransport returns the transport of the port, with the default: a
// port that reports no transport is a stream port.
func (p *DevicePort) EffectiveTransport() PortTransport {
	if p.Transport == "" {
		return PortTransportStream
	}
	return p.Transport
}

// IsForwardable reports whether a forward can use the port: it is a socket
// port with the stream transport.
func (p *DevicePort) IsForwardable() bool {
	return p.EffectiveAttachment() == PortAttachmentSocket && p.EffectiveTransport() == PortTransportStream
}

// Port returns the port with this name that a device of the exporter
// reports, or nil. A port name is unique across the devices of one exporter.
func (e *Exporter) Port(name string) *DevicePort {
	for i := range e.Status.Devices {
		for j := range e.Status.Devices[i].Ports {
			if e.Status.Devices[i].Ports[j].Name == name {
				return &e.Status.Devices[i].Ports[j]
			}
		}
	}
	return nil
}

// Ports returns all the ports that the devices of the exporter report, in
// device order.
func (e *Exporter) Ports() []DevicePort {
	var ports []DevicePort
	for i := range e.Status.Devices {
		ports = append(ports, e.Status.Devices[i].Ports...)
	}
	return ports
}
