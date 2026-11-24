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
	"strings"
	"testing"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	provides = jumpstarterdevv1alpha1.PortDirectionProvides
	requires = jumpstarterdevv1alpha1.PortDirectionRequires
	connects = jumpstarterdevv1alpha1.PortDirectionConnects
)

func devicePort(name string, direction jumpstarterdevv1alpha1.PortDirection) jumpstarterdevv1alpha1.DevicePort {
	return jumpstarterdevv1alpha1.DevicePort{Name: name, Direction: direction}
}

func portExporter(name string, ports ...jumpstarterdevv1alpha1.DevicePort) *jumpstarterdevv1alpha1.Exporter {
	return &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "default"},
		Status: jumpstarterdevv1alpha1.ExporterStatus{
			Devices: []jumpstarterdevv1alpha1.Device{{Uuid: name + "-root"}, {Uuid: name + "-child", Ports: ports}},
		},
	}
}

// planLease is a bound member-form lease: each role is Bound to the exporter
// with the name of the role, except the roles in states.
func planLease(
	roles []string,
	states map[string]jumpstarterdevv1alpha1.LeaseMemberState,
	forwards ...jumpstarterdevv1alpha1.LeaseForward,
) *jumpstarterdevv1alpha1.Lease {
	lease := &jumpstarterdevv1alpha1.Lease{ObjectMeta: metav1.ObjectMeta{Name: "plan", Namespace: "default"}}
	for _, role := range roles {
		lease.Spec.Members = append(lease.Spec.Members, jumpstarterdevv1alpha1.LeaseMember{Name: role})
		status := jumpstarterdevv1alpha1.LeaseMemberStatus{Name: role, State: jumpstarterdevv1alpha1.LeaseMemberStateBound}
		if state, ok := states[role]; ok {
			status.State = state
		}
		if status.State != jumpstarterdevv1alpha1.LeaseMemberStateOmitted {
			status.ExporterRef = &corev1.LocalObjectReference{Name: role}
		}
		lease.Status.Members = append(lease.Status.Members, status)
	}
	lease.Spec.Forwards = forwards
	return lease
}

func exporterMap(exporters ...*jumpstarterdevv1alpha1.Exporter) map[string]*jumpstarterdevv1alpha1.Exporter {
	m := map[string]*jumpstarterdevv1alpha1.Exporter{}
	for _, exporter := range exporters {
		m[exporter.Name] = exporter
	}
	return m
}

func TestPlanForwardsValidation(t *testing.T) {
	omitted := jumpstarterdevv1alpha1.LeaseMemberStateOmitted
	lost := jumpstarterdevv1alpha1.LeaseMemberStateLost
	failed := jumpstarterdevv1alpha1.LeaseForwardStateFailed
	disabled := jumpstarterdevv1alpha1.LeaseForwardStateDisabled
	const active = jumpstarterdevv1alpha1.LeaseForwardState("")

	wired := devicePort("can", provides)
	wired.Attachment = jumpstarterdevv1alpha1.PortAttachmentWired
	wireless := devicePort("radio", provides)
	wireless.Attachment = jumpstarterdevv1alpha1.PortAttachmentWireless
	datagram := devicePort("someip", provides)
	datagram.Transport = jumpstarterdevv1alpha1.PortTransportDatagram
	frame := devicePort("eth", provides)
	frame.Transport = jumpstarterdevv1alpha1.PortTransportFrame
	hci := devicePort("rootcanal", provides)
	hci.Protocol = "hci-h4"
	hciClient := devicePort("controller", requires)
	hciClient.Protocol = "hci-h4"
	otherClient := devicePort("netsim", requires)
	otherClient.Protocol = "netsim-packet"
	explicit := devicePort("explicit", provides)
	explicit.Attachment = jumpstarterdevv1alpha1.PortAttachmentSocket
	explicit.Transport = jumpstarterdevv1alpha1.PortTransportStream

	headunit := portExporter("headunit", hci, devicePort("console", provides), wired, wireless, datagram, frame,
		explicit, devicePort("uart", connects), devicePort("uart2", connects))
	peer := portExporter("peer", hciClient, otherClient, devicePort("console", requires),
		devicePort("uart", connects), devicePort("service", provides))
	logger := portExporter("logger", devicePort("console", requires), devicePort("uart", connects))
	old := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: "peer"},
		Status:     jumpstarterdevv1alpha1.ExporterStatus{Devices: []jumpstarterdevv1alpha1.Device{{Uuid: "root"}}},
	}
	all := exporterMap(headunit, peer, logger)
	roles := []string{"headunit", "peer", "logger"}

	tests := []struct {
		name      string
		states    map[string]jumpstarterdevv1alpha1.LeaseMemberState
		exporters map[string]*jumpstarterdevv1alpha1.Exporter
		forwards  []jumpstarterdevv1alpha1.LeaseForward
		want      []jumpstarterdevv1alpha1.LeaseForwardState
		messages  []string
		endpoints []int
		lost      []bool
	}{
		{
			name:     "a provides port and a requires port, in any sequence",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("bt", "peer", "controller", "headunit", "rootcanal")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{active}, endpoints: []int{2},
		},
		{
			name:     "ports with the explicit defaults",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("x", "headunit", "explicit", "peer", "console")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{active},
		},
		{
			name: "one provides port for many requires ports",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{
				forward("console", "headunit", "console", "peer", "console", "logger", "console")},
			want: []jumpstarterdevv1alpha1.LeaseForwardState{active}, endpoints: []int{3},
		},
		{
			name:     "a port that is not on the bound exporter",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("bt", "headunit", "rootcanal", "peer", "nothing")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages: []string{"exporter peer of role `peer` reports no port `nothing`"},
		},
		{
			name:      "an exporter that reports no ports",
			exporters: exporterMap(headunit, old),
			forwards:  []jumpstarterdevv1alpha1.LeaseForward{forward("bt", "headunit", "rootcanal", "peer", "controller")},
			want:      []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages:  []string{"reports no port `controller`"},
		},
		{
			name:     "two provides ports",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("x", "headunit", "rootcanal", "peer", "service")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages: []string{"More than one endpoint is a `provides` port"},
		},
		{
			name:     "all requires ports",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("x", "logger", "console", "peer", "console")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages: []string{"No endpoint is a `provides` port"},
		},
		{
			name:     "different protocols",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("x", "headunit", "rootcanal", "peer", "netsim")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages: []string{"different protocols: `headunit.rootcanal` (`hci-h4`), `peer.netsim` (`netsim-packet`)"},
		},
		{
			name:     "one protocol tag is absent",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("x", "headunit", "rootcanal", "peer", "console")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{active},
		},
		{
			name:     "a wired port",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("x", "headunit", "can", "peer", "console")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages: []string{"`headunit.can` has the attachment `wired`"},
		},
		{
			name:     "a wireless port",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("x", "headunit", "radio", "peer", "console")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages: []string{"the attachment `wireless`"},
		},
		{
			name:     "a datagram port",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("x", "headunit", "someip", "peer", "console")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages: []string{"the transport `datagram`"},
		},
		{
			name:     "a frame port",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("x", "headunit", "eth", "peer", "console")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages: []string{"the transport `frame`"},
		},
		{
			name:     "a path between two connects ports",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("uart", "peer", "uart", "headunit", "uart")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{active}, endpoints: []int{2},
		},
		{
			name:     "a connects port with a requires port",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("x", "headunit", "uart", "peer", "console")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages: []string{"A `connects` port connects only to one other `connects` port"},
		},
		{
			name: "more than two connects ports",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{
				forward("x", "headunit", "uart", "peer", "uart", "logger", "uart")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages: []string{"A path has two `connects` ports, and this forward has 3 endpoints"},
		},
		{
			name: "a requires port in two forwards fails the two",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{
				forward("a", "headunit", "rootcanal", "peer", "console"),
				forward("b", "headunit", "console", "peer", "console"),
			},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed, failed},
			messages: []string{"`peer.console` is in more than one forward: `a`, `b`", "`peer.console`"},
		},
		{
			name: "a connects port in two forwards fails the two",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{
				forward("a", "headunit", "uart", "peer", "uart"),
				forward("b", "headunit", "uart", "logger", "uart"),
			},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed, failed},
			messages: []string{"`headunit.uart` is in more than one forward", "`headunit.uart`"},
		},
		{
			name: "a provides port in two forwards",
			forwards: []jumpstarterdevv1alpha1.LeaseForward{
				forward("a", "headunit", "console", "peer", "console"),
				forward("b", "headunit", "console", "logger", "console"),
			},
			want: []jumpstarterdevv1alpha1.LeaseForwardState{active, active},
		},
		{
			name:   "the port check ignores a Disabled forward",
			states: map[string]jumpstarterdevv1alpha1.LeaseMemberState{"logger": omitted},
			forwards: []jumpstarterdevv1alpha1.LeaseForward{
				forward("a", "headunit", "console", "peer", "console"),
				forward("b", "logger", "console", "peer", "console"),
			},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{active, disabled},
			messages: []string{"", "Role `logger` is Omitted"},
		},
		{
			name:     "an Omitted member leaves no provides port",
			states:   map[string]jumpstarterdevv1alpha1.LeaseMemberState{"headunit": omitted},
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("bt", "headunit", "rootcanal", "peer", "controller")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{disabled},
			messages: []string{"Role `headunit` is Omitted"},
		},
		{
			name:     "an Omitted member leaves no requires port",
			states:   map[string]jumpstarterdevv1alpha1.LeaseMemberState{"peer": omitted},
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("bt", "headunit", "rootcanal", "peer", "controller")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{disabled},
			messages: []string{"Role `peer` is Omitted"},
		},
		{
			name:   "an Omitted member is one of many requires endpoints",
			states: map[string]jumpstarterdevv1alpha1.LeaseMemberState{"logger": omitted},
			forwards: []jumpstarterdevv1alpha1.LeaseForward{
				forward("console", "headunit", "console", "peer", "console", "logger", "console")},
			want: []jumpstarterdevv1alpha1.LeaseForwardState{active}, endpoints: []int{2},
		},
		{
			name:     "an Omitted member is one end of a path",
			states:   map[string]jumpstarterdevv1alpha1.LeaseMemberState{"peer": omitted},
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("uart", "headunit", "uart", "peer", "uart")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{disabled},
			messages: []string{"Role `peer` is Omitted"},
		},
		{
			name:     "a forward with no bound end",
			states:   map[string]jumpstarterdevv1alpha1.LeaseMemberState{"peer": omitted, "logger": omitted},
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("x", "logger", "console", "peer", "service")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{disabled},
			messages: []string{"Roles `logger`, `peer` are Omitted"},
		},
		{
			name:     "the port of the other endpoint of a forward with an Omitted member must exist",
			states:   map[string]jumpstarterdevv1alpha1.LeaseMemberState{"peer": omitted},
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("bt", "headunit", "nothing", "peer", "controller")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages: []string{"reports no port `nothing`"},
		},
		{
			name:     "a Lost member that the forward cannot operate without",
			states:   map[string]jumpstarterdevv1alpha1.LeaseMemberState{"headunit": lost},
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("bt", "headunit", "rootcanal", "peer", "controller")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages: []string{"Role `headunit` is Lost"}, lost: []bool{true},
		},
		{
			name:   "a Lost member that is one of many requires endpoints",
			states: map[string]jumpstarterdevv1alpha1.LeaseMemberState{"logger": lost},
			forwards: []jumpstarterdevv1alpha1.LeaseForward{
				forward("console", "headunit", "console", "peer", "console", "logger", "console")},
			want: []jumpstarterdevv1alpha1.LeaseForwardState{active}, endpoints: []int{2}, lost: []bool{false},
		},
		{
			name:     "a Lost member that is one end of a path",
			states:   map[string]jumpstarterdevv1alpha1.LeaseMemberState{"peer": lost},
			forwards: []jumpstarterdevv1alpha1.LeaseForward{forward("uart", "headunit", "uart", "peer", "uart")},
			want:     []jumpstarterdevv1alpha1.LeaseForwardState{failed},
			messages: []string{"Role `peer` is Lost"}, lost: []bool{true},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			exporters := tt.exporters
			if exporters == nil {
				exporters = all
			}
			plans := PlanForwards(planLease(roles, tt.states, tt.forwards...), exporters)
			if len(plans) != len(tt.want) {
				t.Fatalf("expected %d plans, got %d", len(tt.want), len(plans))
			}
			for i := range plans {
				if plans[i].Name != tt.forwards[i].Name {
					t.Fatalf("plan %d has the name %q", i, plans[i].Name)
				}
				if plans[i].State != tt.want[i] {
					t.Fatalf("forward %s: expected state %q, got %q (%s)", plans[i].Name, tt.want[i], plans[i].State, plans[i].Message)
				}
				if i < len(tt.messages) && !strings.Contains(plans[i].Message, tt.messages[i]) {
					t.Fatalf("forward %s: expected message with %q, got %q", plans[i].Name, tt.messages[i], plans[i].Message)
				}
				if i < len(tt.endpoints) && len(plans[i].Endpoints) != tt.endpoints[i] {
					t.Fatalf("forward %s: expected %d endpoints, got %v", plans[i].Name, tt.endpoints[i], plans[i].Endpoints)
				}
				if i < len(tt.lost) && plans[i].MemberLost != tt.lost[i] {
					t.Fatalf("forward %s: expected MemberLost %t", plans[i].Name, tt.lost[i])
				}
			}
		})
	}

	t.Run("a Disabled forward stays Disabled when one more member is lost", func(t *testing.T) {
		lease := planLease(roles,
			map[string]jumpstarterdevv1alpha1.LeaseMemberState{"peer": omitted, "headunit": lost},
			forward("bt", "headunit", "rootcanal", "peer", "controller"))
		lease.Status.Forwards = []jumpstarterdevv1alpha1.LeaseForwardStatus{{Name: "bt", State: disabled}}
		plans := PlanForwards(lease, all)
		if plans[0].State != disabled || plans[0].MemberLost {
			t.Fatalf("expected the forward to stay Disabled, got %+v", plans[0])
		}
	})
}

func TestForwardPlanPeersAndDialTarget(t *testing.T) {
	headunit := portExporter("headunit", devicePort("console", provides), devicePort("uart", connects))
	peer := portExporter("peer", devicePort("console", requires), devicePort("uart", connects))
	logger := portExporter("logger", devicePort("console", requires))
	lease := planLease([]string{"headunit", "peer", "logger"}, nil,
		forward("console", "logger", "console", "headunit", "console", "peer", "console"),
		forward("uart", "peer", "uart", "headunit", "uart"))
	plans := PlanForwards(lease, exporterMap(headunit, peer, logger))
	console, uart := &plans[0], &plans[1]

	names := func(endpoints []ForwardPlanEndpoint) string {
		var out []string
		for _, endpoint := range endpoints {
			out = append(out, endpoint.Member+"."+endpoint.Port+"@"+endpoint.Exporter)
		}
		return strings.Join(out, ",")
	}
	if got := names(console.Peers("headunit")); got != "logger.console@logger,peer.console@peer" {
		t.Fatalf("the provides end must have each requires end as peer, got %s", got)
	}
	if got := names(console.Peers("peer")); got != "headunit.console@headunit" {
		t.Fatalf("a requires end must have the provides end as peer, got %s", got)
	}
	if console.IsPath() || console.Initiator != "" {
		t.Fatalf("a forward with a provides port is not a path")
	}
	if target := console.DialTarget("logger"); target == nil || target.Member != "headunit" {
		t.Fatalf("a requires end opens connections to the provides end, got %v", target)
	}
	if target := console.DialTarget("headunit"); target != nil {
		t.Fatalf("the provides end cannot open a connection, got %v", target)
	}
	if target := console.DialTarget("stranger"); target != nil {
		t.Fatalf("a member that is not an endpoint cannot open a connection")
	}

	if !uart.IsPath() || uart.Initiator != "headunit" {
		t.Fatalf("the initiator of a path is the first role in sorted order, got %q", uart.Initiator)
	}
	if got := names(uart.Peers("peer")); got != "headunit.uart@headunit" {
		t.Fatalf("an end of a path has the other end as peer, got %s", got)
	}
	if target := uart.DialTarget("headunit"); target == nil || target.Member != "peer" {
		t.Fatalf("the initiator opens the path to the other end, got %v", target)
	}
	if target := uart.DialTarget("peer"); target != nil {
		t.Fatalf("only the initiator opens a path, got %v", target)
	}
}

func TestForwardStateFromEnds(t *testing.T) {
	plan := &ForwardPlan{Name: "bt", Endpoints: []ForwardPlanEndpoint{
		{Member: "headunit", Port: "rootcanal", Direction: provides},
		{Member: "peer", Port: "controller", Direction: requires},
	}}
	now := time.Now()
	ms := func(d time.Duration) int64 { return now.Add(d).UnixMilli() }
	ready := jumpstarterdevv1alpha1.ForwardEnd{State: jumpstarterdevv1alpha1.ForwardEndStateReady, Sequence: 1,
		SetupTime: ms(-10 * time.Second), ReportTime: ms(-9 * time.Second)}
	connected := ready
	connected.State = jumpstarterdevv1alpha1.ForwardEndStateConnected
	failed := ready
	failed.State = jumpstarterdevv1alpha1.ForwardEndStateFailed
	failed.Message = "address already in use"

	tests := []struct {
		name     string
		headunit jumpstarterdevv1alpha1.ForwardEnd
		peer     jumpstarterdevv1alpha1.ForwardEnd
		want     jumpstarterdevv1alpha1.LeaseForwardState
		message  string
		waits    bool
	}{
		{name: "no setup and no report", want: jumpstarterdevv1alpha1.LeaseForwardStatePending,
			message: "`headunit`, `peer`"},
		{name: "one end is ready", headunit: ready, want: jumpstarterdevv1alpha1.LeaseForwardStatePending,
			message: "Waiting for the setup of roles: `peer`"},
		{name: "each end is ready", headunit: ready, peer: ready, want: jumpstarterdevv1alpha1.LeaseForwardStateListening},
		{name: "one end has a connection", headunit: ready, peer: connected,
			want: jumpstarterdevv1alpha1.LeaseForwardStateConnected},
		{name: "one end failed", headunit: connected, peer: failed, want: jumpstarterdevv1alpha1.LeaseForwardStateFailed,
			message: "role `peer`: address already in use"},
		{name: "an end that got its setup a moment ago", headunit: ready,
			peer: jumpstarterdevv1alpha1.ForwardEnd{SetupTime: ms(-10 * time.Second)},
			want: jumpstarterdevv1alpha1.LeaseForwardStatePending, waits: true},
		{name: "an end that sent no report in the time limit", headunit: ready,
			peer: jumpstarterdevv1alpha1.ForwardEnd{SetupTime: ms(-61 * time.Second)},
			want: jumpstarterdevv1alpha1.LeaseForwardStateFailed, message: "Role `peer` sent no report in 1m0s"},
		{name: "a repeated setup keeps the last state while it waits", headunit: ready,
			peer: jumpstarterdevv1alpha1.ForwardEnd{State: jumpstarterdevv1alpha1.ForwardEndStateReady, Sequence: 1,
				ReportTime: ms(-time.Hour), SetupTime: ms(-10 * time.Second)},
			want: jumpstarterdevv1alpha1.LeaseForwardStateListening, waits: true},
		{name: "a repeated setup with no report in the time limit", headunit: ready,
			peer: jumpstarterdevv1alpha1.ForwardEnd{State: jumpstarterdevv1alpha1.ForwardEndStateReady, Sequence: 1,
				ReportTime: ms(-time.Hour), SetupTime: ms(-2 * time.Minute)},
			want: jumpstarterdevv1alpha1.LeaseForwardStateFailed, message: "Role `peer` sent no report"},
		{name: "a report after the time limit removes the failure", headunit: ready,
			peer: jumpstarterdevv1alpha1.ForwardEnd{State: jumpstarterdevv1alpha1.ForwardEndStateReady, Sequence: 2,
				ReportTime: ms(-time.Second), SetupTime: ms(-2 * time.Minute)},
			want: jumpstarterdevv1alpha1.LeaseForwardStateListening},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			ends := jumpstarterdevv1alpha1.ForwardEnds{
				jumpstarterdevv1alpha1.ForwardEndKey("bt", "headunit"): tt.headunit,
				jumpstarterdevv1alpha1.ForwardEndKey("bt", "peer"):     tt.peer,
				// The end of a different forward does not count.
				jumpstarterdevv1alpha1.ForwardEndKey("other", "peer"): failed,
			}
			state, message, wait := forwardStateFromEnds(plan, ends, now, DefaultForwardReportTimeout)
			if state != tt.want {
				t.Fatalf("expected %s, got %s (%s)", tt.want, state, message)
			}
			if !strings.Contains(message, tt.message) {
				t.Fatalf("expected message with %q, got %q", tt.message, message)
			}
			if tt.waits != (wait > 0) {
				t.Fatalf("expected a wait: %t, got %s", tt.waits, wait)
			}
			if wait > DefaultForwardReportTimeout {
				t.Fatalf("the wait %s is longer than the time limit", wait)
			}
		})
	}
}
