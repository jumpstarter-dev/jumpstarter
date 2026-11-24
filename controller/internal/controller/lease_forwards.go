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

package controller

import (
	"context"
	"fmt"
	"slices"
	"strings"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	k8serrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
)

// The forwards of a member-form lease (JEP-0016): the validation of a forward
// against the ports that the bound exporters report, and the state of a
// forward from the reports of its ends.
//
// PlanForwards is the validation. It is a function of the lease and of the
// exporters only, so that the lease controller (which writes status.forwards)
// and the controller service (which sends the setup instructions and answers
// DialPeer) get the same result.

// DefaultForwardReportTimeout is the time that the exporter of an endpoint
// has for its report after a setup instruction. After it, the forward is
// Failed until that exporter reports.
const DefaultForwardReportTimeout = 60 * time.Second

// ForwardPlanEndpoint is a bound endpoint of a forward, with the direction
// that its exporter reports for the port.
type ForwardPlanEndpoint struct {
	// Member is the role name of the member.
	Member string
	// Port is the name of the port.
	Port string
	// Exporter is the exporter of the member.
	Exporter string
	// Direction is the direction of the port.
	Direction jumpstarterdevv1alpha1.PortDirection
}

// ForwardPlan is the result of the validation of one forward.
type ForwardPlan struct {
	// Name of the forward.
	Name string
	// State is empty for a forward that passed the validation: its state then
	// comes from the reports of its ends. It is Disabled or Failed for a
	// forward that gets no setup.
	State jumpstarterdevv1alpha1.LeaseForwardState
	// Message gives the Omitted role of a Disabled forward or the cause of a
	// failure.
	Message string
	// MemberLost is set for a forward that is Failed because a member that it
	// cannot operate without is Lost. Its other endpoints get a teardown.
	MemberLost bool
	// Endpoints are the endpoints on Bound members, in spec order. A forward
	// that failed can have a part of them only.
	Endpoints []ForwardPlanEndpoint
	// Initiator is the role name of the member that opens a path. It is empty
	// for a forward that is not a path.
	Initiator string
}

// Active reports whether the forward passed the validation: its endpoint
// exporters get a setup instruction.
func (p *ForwardPlan) Active() bool {
	return p.State == ""
}

// IsPath reports whether the forward connects two connects ports.
func (p *ForwardPlan) IsPath() bool {
	return p.Initiator != ""
}

// Endpoint returns the bound endpoint of the forward on this member, or nil.
func (p *ForwardPlan) Endpoint(member string) *ForwardPlanEndpoint {
	for i := range p.Endpoints {
		if p.Endpoints[i].Member == member {
			return &p.Endpoints[i]
		}
	}
	return nil
}

// Provider returns the provides endpoint of the forward, or nil.
func (p *ForwardPlan) Provider() *ForwardPlanEndpoint {
	for i := range p.Endpoints {
		if p.Endpoints[i].Direction == jumpstarterdevv1alpha1.PortDirectionProvides {
			return &p.Endpoints[i]
		}
	}
	return nil
}

// Peers returns the other ends of the forward for the endpoint on this
// member: the provides endpoint for a requires port, each bound requires
// endpoint for a provides port, and the other end for a connects port.
func (p *ForwardPlan) Peers(member string) []ForwardPlanEndpoint {
	local := p.Endpoint(member)
	if local == nil {
		return nil
	}
	var peers []ForwardPlanEndpoint
	for i := range p.Endpoints {
		other := &p.Endpoints[i]
		if other.Member == member {
			continue
		}
		switch local.Direction {
		case jumpstarterdevv1alpha1.PortDirectionRequires:
			if other.Direction != jumpstarterdevv1alpha1.PortDirectionProvides {
				continue
			}
		case jumpstarterdevv1alpha1.PortDirectionProvides:
			if other.Direction != jumpstarterdevv1alpha1.PortDirectionRequires {
				continue
			}
		}
		peers = append(peers, *other)
	}
	return peers
}

// DialTarget returns the endpoint that gets the connect instruction when the
// member opens a connection on the forward: the provides endpoint for a
// requires endpoint, and the other end for the initiator of a path. It
// returns nil for a member that cannot open a connection.
func (p *ForwardPlan) DialTarget(member string) *ForwardPlanEndpoint {
	local := p.Endpoint(member)
	if local == nil || !p.Active() {
		return nil
	}
	switch local.Direction {
	case jumpstarterdevv1alpha1.PortDirectionRequires:
		return p.Provider()
	case jumpstarterdevv1alpha1.PortDirectionConnects:
		if p.Initiator != member {
			return nil
		}
		for i := range p.Endpoints {
			if p.Endpoints[i].Member != member {
				return &p.Endpoints[i]
			}
		}
	}
	return nil
}

func endpointName(member, port string) string {
	return "`" + member + "." + port + "`"
}

// PlanForwards validates the forwards of a bound member-form lease, in spec
// order. exporters has the exporters of the Bound members by name; an
// exporter that it does not have reports no ports.
//
// The steps are those of the JEP (Forward validation):
//
//  1. The endpoints of Omitted members are ignored. The endpoints of Lost
//     members are ignored too: a Lost member keeps no exporter to ask.
//  2. Each other port exists on its bound exporter.
//  3. A forward that cannot operate without the ignored endpoints is Disabled
//     when the members are Omitted, and Failed when a member is Lost.
//  4. One port is provides and all the others are requires, or the forward
//     has two endpoints and the two are connects.
//  5. Each port is a socket port with the stream transport.
//  6. The protocols that the ports declare are equal.
//  7. No requires port and no connects port is in two forwards.
func PlanForwards(
	lease *jumpstarterdevv1alpha1.Lease,
	exporters map[string]*jumpstarterdevv1alpha1.Exporter,
) []ForwardPlan {
	plans := make([]ForwardPlan, 0, len(lease.Spec.Forwards))
	for i := range lease.Spec.Forwards {
		plans = append(plans, planForward(lease, &lease.Spec.Forwards[i], exporters))
	}

	// Step 7. A provides port can be in many forwards; a requires port has one
	// listener and a connects port has one path.
	users := map[string][]string{}
	for i := range plans {
		if plans[i].State == jumpstarterdevv1alpha1.LeaseForwardStateDisabled {
			continue
		}
		for _, endpoint := range plans[i].Endpoints {
			if endpoint.Direction == jumpstarterdevv1alpha1.PortDirectionProvides {
				continue
			}
			key := endpoint.Member + "." + endpoint.Port
			users[key] = append(users[key], plans[i].Name)
		}
	}
	for i := range plans {
		if !plans[i].Active() {
			continue
		}
		for _, endpoint := range plans[i].Endpoints {
			key := endpoint.Member + "." + endpoint.Port
			if len(users[key]) > 1 {
				plans[i].State = jumpstarterdevv1alpha1.LeaseForwardStateFailed
				plans[i].Message = fmt.Sprintf("The %s port %s is in more than one forward: %s",
					endpoint.Direction, endpointName(endpoint.Member, endpoint.Port), quoteRoles(users[key]))
				plans[i].Initiator = ""
				break
			}
		}
	}
	return plans
}

func planForward(
	lease *jumpstarterdevv1alpha1.Lease,
	forward *jumpstarterdevv1alpha1.LeaseForward,
	exporters map[string]*jumpstarterdevv1alpha1.Exporter,
) ForwardPlan {
	plan := ForwardPlan{Name: forward.Name}
	fail := func(format string, a ...any) ForwardPlan {
		plan.State = jumpstarterdevv1alpha1.LeaseForwardStateFailed
		plan.Message = fmt.Sprintf(format, a...)
		return plan
	}

	// Steps 1 and 2.
	var omitted, lost []string
	var ports []*jumpstarterdevv1alpha1.DevicePort
	var missing []string
	for i := range forward.Endpoints {
		endpoint := &forward.Endpoints[i]
		member := lease.MemberStatus(endpoint.Member)
		switch {
		case member == nil || member.State == jumpstarterdevv1alpha1.LeaseMemberStateOmitted ||
			member.State == jumpstarterdevv1alpha1.LeaseMemberStatePending:
			omitted = append(omitted, endpoint.Member)
			continue
		case member.State == jumpstarterdevv1alpha1.LeaseMemberStateLost || member.ExporterRef == nil:
			lost = append(lost, endpoint.Member)
			continue
		}
		var port *jumpstarterdevv1alpha1.DevicePort
		if exporter := exporters[member.ExporterRef.Name]; exporter != nil {
			port = exporter.Port(endpoint.Port)
		}
		if port == nil {
			missing = append(missing, fmt.Sprintf("exporter %s of role `%s` reports no port `%s`",
				member.ExporterRef.Name, endpoint.Member, endpoint.Port))
			continue
		}
		ports = append(ports, port)
		plan.Endpoints = append(plan.Endpoints, ForwardPlanEndpoint{
			Member:    endpoint.Member,
			Port:      endpoint.Port,
			Exporter:  member.ExporterRef.Name,
			Direction: port.Direction,
		})
	}
	if len(missing) > 0 {
		return fail("Port not found: %s", strings.Join(missing, "; "))
	}

	var provides, requires, connects []string
	for _, endpoint := range plan.Endpoints {
		name := endpointName(endpoint.Member, endpoint.Port)
		switch endpoint.Direction {
		case jumpstarterdevv1alpha1.PortDirectionProvides:
			provides = append(provides, name)
		case jumpstarterdevv1alpha1.PortDirectionRequires:
			requires = append(requires, name)
		case jumpstarterdevv1alpha1.PortDirectionConnects:
			connects = append(connects, name)
		}
	}

	// Step 3.
	if len(omitted)+len(lost) > 0 {
		inoperable := len(plan.Endpoints) == 0 ||
			(len(provides)+len(requires) > 0 && (len(provides) == 0 || len(requires) == 0)) ||
			(len(forward.Endpoints) == 2 && len(connects) == 1)
		if inoperable {
			// A forward that was Disabled at the bind stays Disabled when one
			// more of its members is lost later.
			prior := lease.ForwardStatus(forward.Name)
			wasDisabled := prior != nil && prior.State == jumpstarterdevv1alpha1.LeaseForwardStateDisabled
			if len(lost) > 0 && (len(omitted) == 0 || !wasDisabled) {
				plan.MemberLost = true
				return fail("%s Lost, and the forward cannot operate without it", rolesAre(lost))
			}
			plan.State = jumpstarterdevv1alpha1.LeaseForwardStateDisabled
			plan.Message = fmt.Sprintf("%s Omitted, and the forward cannot operate without it", rolesAre(omitted))
			return plan
		}
	}

	// Step 4.
	switch {
	case len(connects) > 0:
		if len(provides)+len(requires) > 0 {
			return fail("A `connects` port connects only to one other `connects` port: %s with %s",
				strings.Join(connects, ", "), strings.Join(append(provides, requires...), ", "))
		}
		if len(forward.Endpoints) != 2 || len(connects) != 2 {
			return fail("A path has two `connects` ports, and this forward has %d endpoints", len(forward.Endpoints))
		}
	case len(provides) == 0:
		return fail("No endpoint is a `provides` port: %s are `requires` ports", strings.Join(requires, ", "))
	case len(provides) > 1:
		return fail("More than one endpoint is a `provides` port: %s", strings.Join(provides, ", "))
	case len(requires) == 0:
		return fail("No endpoint is a `requires` port")
	}

	// Step 5.
	for i, port := range ports {
		name := endpointName(plan.Endpoints[i].Member, plan.Endpoints[i].Port)
		if port.EffectiveAttachment() != jumpstarterdevv1alpha1.PortAttachmentSocket {
			return fail("Port %s has the attachment `%s`: a forward accepts only `socket` ports",
				name, port.EffectiveAttachment())
		}
		if port.EffectiveTransport() != jumpstarterdevv1alpha1.PortTransportStream {
			return fail("Port %s has the transport `%s`: a forward accepts only `stream` ports",
				name, port.EffectiveTransport())
		}
	}

	// Step 6.
	var protocols, declared []string
	for i, port := range ports {
		if port.Protocol == "" {
			continue
		}
		if !slices.Contains(protocols, port.Protocol) {
			protocols = append(protocols, port.Protocol)
		}
		declared = append(declared, fmt.Sprintf("%s (`%s`)",
			endpointName(plan.Endpoints[i].Member, plan.Endpoints[i].Port), port.Protocol))
	}
	if len(protocols) > 1 {
		return fail("The ports declare different protocols: %s", strings.Join(declared, ", "))
	}

	if len(connects) == 2 {
		plan.Initiator = forward.PathInitiator()
	}
	return plan
}

func rolesNoun(roles []string) string {
	if len(roles) == 1 {
		return "Role " + quoteRoles(roles)
	}
	return "Roles " + quoteRoles(roles)
}

func rolesAre(roles []string) string {
	if len(roles) == 1 {
		return rolesNoun(roles) + " is"
	}
	return rolesNoun(roles) + " are"
}

// ForwardExporters reads the exporters of the Bound members that are an
// endpoint of a forward of the lease, by name, for PlanForwards. An exporter
// that does not exist is not in the result.
func ForwardExporters(
	ctx context.Context,
	reader client.Reader,
	lease *jumpstarterdevv1alpha1.Lease,
) (map[string]*jumpstarterdevv1alpha1.Exporter, error) {
	exporters := map[string]*jumpstarterdevv1alpha1.Exporter{}
	for i := range lease.Spec.Forwards {
		for _, endpoint := range lease.Spec.Forwards[i].Endpoints {
			member := lease.MemberStatus(endpoint.Member)
			if member == nil || member.State != jumpstarterdevv1alpha1.LeaseMemberStateBound || member.ExporterRef == nil {
				continue
			}
			name := member.ExporterRef.Name
			if _, ok := exporters[name]; ok {
				continue
			}
			var exporter jumpstarterdevv1alpha1.Exporter
			err := reader.Get(ctx, types.NamespacedName{Namespace: lease.Namespace, Name: name}, &exporter)
			if k8serrors.IsNotFound(err) {
				continue
			}
			if err != nil {
				return nil, fmt.Errorf("ForwardExporters: failed to get exporter %s: %w", name, err)
			}
			exporters[name] = &exporter
		}
	}
	return exporters, nil
}

// forwardEnds returns the ends of a forward for status.forwards[].ends[], in
// the order of the endpoints: the end that the status has for each endpoint
// on a Bound member, and a Pending end for each endpoint of a forward that
// passed the validation and has none. The end of a member that is not Bound
// goes: its exporter has no forward, and the member does not become Bound
// again. Only the controller service changes an end that exists (the reports
// and the setup times); the lease controller keeps it as it read it.
func forwardEnds(
	lease *jumpstarterdevv1alpha1.Lease,
	forward *jumpstarterdevv1alpha1.LeaseForward,
	plan *ForwardPlan,
) []jumpstarterdevv1alpha1.LeaseForwardEndStatus {
	var ends []jumpstarterdevv1alpha1.LeaseForwardEndStatus
	for i := range forward.Endpoints {
		name := forward.Endpoints[i].Member
		member := lease.MemberStatus(name)
		if member == nil || member.State != jumpstarterdevv1alpha1.LeaseMemberStateBound {
			continue
		}
		switch end := lease.ForwardEnd(forward.Name, name); {
		case end != nil:
			ends = append(ends, *end.DeepCopy())
		case plan.Active() && plan.Endpoint(name) != nil:
			ends = append(ends, jumpstarterdevv1alpha1.LeaseForwardEndStatus{
				Member: name, State: jumpstarterdevv1alpha1.LeaseForwardEndStatePending,
			})
		}
	}
	return ends
}

// forwardStateFromEnds calculates the state of a forward that passed the
// validation from the status of its ends. It returns the time until the
// report time limit of an end that got a setup and sent no report after it,
// or zero when no end waits.
func forwardStateFromEnds(
	plan *ForwardPlan,
	ends []jumpstarterdevv1alpha1.LeaseForwardEndStatus,
	now time.Time,
	timeout time.Duration,
) (jumpstarterdevv1alpha1.LeaseForwardState, string, time.Duration) {
	var failed, silent, waiting []string
	var wait time.Duration
	connected := false
	for _, endpoint := range plan.Endpoints {
		var end jumpstarterdevv1alpha1.LeaseForwardEndStatus
		for i := range ends {
			if ends[i].Member == endpoint.Member {
				end = ends[i]
			}
		}
		if end.AwaitsReport() {
			remaining := end.SetupTime.Add(timeout).Sub(now)
			if remaining <= 0 {
				silent = append(silent, endpoint.Member)
				continue
			}
			if wait == 0 || remaining < wait {
				wait = remaining
			}
		}
		switch end.State {
		case jumpstarterdevv1alpha1.LeaseForwardEndStateFailed:
			cause := end.Message
			if cause == "" {
				cause = "the setup failed"
			}
			failed = append(failed, fmt.Sprintf("role `%s`: %s", endpoint.Member, cause))
		case jumpstarterdevv1alpha1.LeaseForwardEndStateConnected:
			connected = true
		case jumpstarterdevv1alpha1.LeaseForwardEndStateReady:
		default:
			// Pending, or no end: the exporter sent no report.
			waiting = append(waiting, endpoint.Member)
		}
	}
	switch {
	case len(failed) > 0:
		return jumpstarterdevv1alpha1.LeaseForwardStateFailed,
			"The setup failed on " + strings.Join(failed, "; "), wait
	case len(silent) > 0:
		return jumpstarterdevv1alpha1.LeaseForwardStateFailed,
			fmt.Sprintf("%s sent no report in %s after the setup instruction", rolesNoun(silent), timeout), wait
	case len(waiting) > 0:
		return jumpstarterdevv1alpha1.LeaseForwardStatePending,
			"Waiting for the setup of roles: " + quoteRoles(waiting), wait
	case connected:
		return jumpstarterdevv1alpha1.LeaseForwardStateConnected, "", wait
	}
	return jumpstarterdevv1alpha1.LeaseForwardStateListening, "", wait
}

// setPendingForwards gives a lease that is not bound its forward statuses:
// each forward waits for the bind.
func setPendingForwards(lease *jumpstarterdevv1alpha1.Lease) {
	if !lease.HasForwards() {
		return
	}
	statuses := make([]jumpstarterdevv1alpha1.LeaseForwardStatus, 0, len(lease.Spec.Forwards))
	for i := range lease.Spec.Forwards {
		statuses = append(statuses, jumpstarterdevv1alpha1.LeaseForwardStatus{
			Name:    lease.Spec.Forwards[i].Name,
			State:   jumpstarterdevv1alpha1.LeaseForwardStatePending,
			Message: "Waiting for the members of the lease",
		})
	}
	lease.Status.Forwards = statuses
	lease.SetStatusCondition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady, false,
		jumpstarterdevv1alpha1.LeaseReasonForwardsPending, "Waiting for the members of the lease")
}

// memberHooksComplete reports whether an earlier reconcile found the
// beforeLease hook of each bound member complete: the lease is Ready, or
// only a Failed forward keeps it from Ready. A lease that waits for a Pending
// forward has the same Ready reason as one that waits for a hook
// (BeforeLeaseHook), so each reconcile looks at its exporters again.
func memberHooksComplete(lease *jumpstarterdevv1alpha1.Lease) bool {
	ready := meta.FindStatusCondition(lease.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypeReady))
	if ready == nil {
		return false
	}
	if ready.Status == "True" {
		return true
	}
	return lease.HasForwards() && ready.Reason == jumpstarterdevv1alpha1.LeaseReasonForwardFailed
}

func (r *LeaseReconciler) forwardReportTimeout() time.Duration {
	if r.ForwardReportTimeout > 0 {
		return r.ForwardReportTimeout
	}
	return DefaultForwardReportTimeout
}

// reconcileForwards validates the forwards of a bound member-form lease and
// sets status.forwards, the ForwardsReady condition and, for a failed forward,
// the Degraded condition. The state of a forward comes from
// status.forwards[].ends[], which the controller service writes. The status
// update of the reconcile has the resource version that the lease was read
// with: a report that the service writes during the reconcile makes that
// update fail with a conflict, and the next reconcile reads the report. Thus
// the lease controller never replaces a report with an older one. membersReady says that the lease is Ready as far as
// its members are concerned (JEP-0015): the forwards then decide Ready. When
// it is false, the caller has set Ready already.
func (r *LeaseReconciler) reconcileForwards(
	ctx context.Context,
	result *ctrl.Result,
	lease *jumpstarterdevv1alpha1.Lease,
	membersReady bool,
) error {
	exporters, err := ForwardExporters(ctx, r.Client, lease)
	if err != nil {
		return err
	}
	plans := PlanForwards(lease, exporters)
	now := time.Now()

	statuses := make([]jumpstarterdevv1alpha1.LeaseForwardStatus, 0, len(plans))
	var failed, pending []string
	for i := range plans {
		plan := &plans[i]
		state, message := plan.State, plan.Message
		// PlanForwards gives one plan for each forward, in spec order.
		ends := forwardEnds(lease, &lease.Spec.Forwards[i], plan)
		if plan.Active() {
			var wait time.Duration
			state, message, wait = forwardStateFromEnds(plan, ends, now, r.forwardReportTimeout())
			// Look again when the report time limit of an end passes.
			requeueWithin(result, wait)
		}
		switch state {
		case jumpstarterdevv1alpha1.LeaseForwardStateFailed:
			failed = append(failed, fmt.Sprintf("`%s` (%s)", plan.Name, message))
		case jumpstarterdevv1alpha1.LeaseForwardStatePending:
			pending = append(pending, plan.Name)
		}
		statuses = append(statuses, jumpstarterdevv1alpha1.LeaseForwardStatus{
			Name: plan.Name, State: state, Message: message, Ends: ends,
		})
	}
	lease.Status.Forwards = statuses

	failedMessage := "Failed forwards: " + strings.Join(failed, ", ")
	pendingMessage := "Waiting for forwards: " + quoteRoles(pending)
	switch {
	case len(failed) > 0:
		lease.SetStatusCondition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady, false,
			jumpstarterdevv1alpha1.LeaseReasonForwardFailed, "%s", failedMessage)
	case len(pending) > 0:
		lease.SetStatusCondition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady, false,
			jumpstarterdevv1alpha1.LeaseReasonForwardsPending, "%s", pendingMessage)
	default:
		lease.SetStatusCondition(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady, true,
			jumpstarterdevv1alpha1.LeaseReasonForwardsReady, "Each forward that is not Disabled is ready")
	}

	// A failed forward is a second cause of Degraded, after a Lost member. A
	// forward can recover (a later report), a Lost member cannot: the
	// condition goes when its only cause was a forward.
	if !lease.HasLostMember() {
		degraded := meta.FindStatusCondition(lease.Status.Conditions,
			string(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded))
		switch {
		case len(failed) > 0:
			lease.SetStatusCondition(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded, true,
				jumpstarterdevv1alpha1.LeaseReasonForwardFailed, "%s", failedMessage)
		case degraded != nil && degraded.Reason == jumpstarterdevv1alpha1.LeaseReasonForwardFailed:
			meta.RemoveStatusCondition(&lease.Status.Conditions,
				string(jumpstarterdevv1alpha1.LeaseConditionTypeDegraded))
		}
	}

	if !membersReady {
		return nil
	}
	switch {
	case len(failed) > 0:
		lease.SetStatusReady(false, jumpstarterdevv1alpha1.LeaseReasonForwardFailed, "%s", failedMessage)
	case len(pending) > 0:
		// The reason is the one of a lease that waits for a beforeLease hook:
		// a client that does not know forwards (JEP-0015) waits for that
		// reason, and takes each other reason of a false Ready as the end of
		// the lease. The ForwardsReady condition has the reason
		// ForwardsPending.
		lease.SetStatusReady(false, jumpstarterdevv1alpha1.LeaseReasonBeforeLeaseHook, "%s", pendingMessage)
	default:
		lease.SetStatusReady(true, "Ready", "The exporters of all members are ready")
	}
	return nil
}
