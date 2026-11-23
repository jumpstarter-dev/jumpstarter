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

// Accessors that read a lease in its scalar form (spec.selector or
// spec.exporterRef, status.exporterRef) and in its member form (spec.members,
// status.members) alike. Code that only needs "the exporters of this lease"
// uses these instead of status.exporterRef.

// IsMemberForm reports whether the lease has spec.members. Such a lease never
// has status.exporterRef.
func (l *Lease) IsMemberForm() bool {
	return len(l.Spec.Members) > 0
}

// MemberNames returns the role names of the lease, in spec order.
func (l *Lease) MemberNames() []string {
	names := make([]string, 0, len(l.Spec.Members))
	for i := range l.Spec.Members {
		names = append(names, l.Spec.Members[i].Name)
	}
	return names
}

// MemberStatus returns the status of the member with this role name, or nil.
func (l *Lease) MemberStatus(name string) *LeaseMemberStatus {
	for i := range l.Status.Members {
		if l.Status.Members[i].Name == name {
			return &l.Status.Members[i]
		}
	}
	return nil
}

// MemberStatusForExporter returns the status of the member that has this
// exporter in the given states (all states when none is given), or nil.
func (l *Lease) MemberStatusForExporter(exporterName string, states ...LeaseMemberState) *LeaseMemberStatus {
	for i := range l.Status.Members {
		m := &l.Status.Members[i]
		if m.ExporterRef == nil || m.ExporterRef.Name != exporterName {
			continue
		}
		if len(states) == 0 {
			return m
		}
		for _, s := range states {
			if m.State == s {
				return m
			}
		}
	}
	return nil
}

// BoundExporterNames returns the exporters that the lease gives access to:
// status.exporterRef of a scalar lease, or the exporter of each Bound member.
func (l *Lease) BoundExporterNames() []string {
	if !l.IsMemberForm() {
		if l.Status.ExporterRef == nil {
			return nil
		}
		return []string{l.Status.ExporterRef.Name}
	}
	var names []string
	for i := range l.Status.Members {
		m := &l.Status.Members[i]
		if m.State == LeaseMemberStateBound && m.ExporterRef != nil {
			names = append(names, m.ExporterRef.Name)
		}
	}
	return names
}

// HeldExporterNames returns the exporters that the lease holds a claim for:
// the bound exporters and the exporters of Lost members, which keep their
// claim until the lease ends.
func (l *Lease) HeldExporterNames() []string {
	if !l.IsMemberForm() {
		return l.BoundExporterNames()
	}
	var names []string
	for i := range l.Status.Members {
		m := &l.Status.Members[i]
		if (m.State == LeaseMemberStateBound || m.State == LeaseMemberStateLost) && m.ExporterRef != nil {
			names = append(names, m.ExporterRef.Name)
		}
	}
	return names
}

// HasBoundExporter reports whether the lease gives access to this exporter:
// it is status.exporterRef of a scalar lease or the exporter of a Bound member.
func (l *Lease) HasBoundExporter(exporterName string) bool {
	if !l.IsMemberForm() {
		return l.Status.ExporterRef != nil && l.Status.ExporterRef.Name == exporterName
	}
	return l.MemberStatusForExporter(exporterName, LeaseMemberStateBound) != nil
}

// IsBound reports whether the bind of the lease happened: a scalar lease has
// status.exporterRef; a member-form lease has a member that is Bound or Lost.
func (l *Lease) IsBound() bool {
	if !l.IsMemberForm() {
		return l.Status.ExporterRef != nil
	}
	for i := range l.Status.Members {
		s := l.Status.Members[i].State
		if s == LeaseMemberStateBound || s == LeaseMemberStateLost {
			return true
		}
	}
	return false
}

// HasLostMember reports whether a member of the lease is Lost.
func (l *Lease) HasLostMember() bool {
	for i := range l.Status.Members {
		if l.Status.Members[i].State == LeaseMemberStateLost {
			return true
		}
	}
	return false
}

// MarkMemberLost sets the Bound member with this role name to Lost with a
// reason and a message. Lost is final: the member keeps its exporterRef, and
// the lease keeps its claim until it ends. It reports whether the status
// changed; the caller writes the status. The lease controller then sets the
// Degraded and Ready conditions and takes the lease away from the exporter.
func (l *Lease) MarkMemberLost(name, reason, message string) bool {
	m := l.MemberStatus(name)
	if m == nil || m.State != LeaseMemberStateBound {
		return false
	}
	m.State = LeaseMemberStateLost
	m.Reason = reason
	m.Message = message
	return true
}
