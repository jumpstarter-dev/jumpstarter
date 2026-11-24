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

package v1

import (
	"context"
	"fmt"
	"testing"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/protobuf/types/known/durationpb"
	"google.golang.org/protobuf/types/known/fieldmaskpb"
)

// The forwards of a lease in the client service (JEP-0016).

func protoForward(name string, endpoints ...string) *cpb.LeaseForward {
	f := &cpb.LeaseForward{Name: name}
	for i := 0; i+1 < len(endpoints); i += 2 {
		f.Endpoints = append(f.Endpoints, &cpb.ForwardEndpoint{MemberName: endpoints[i], PortName: endpoints[i+1]})
	}
	return f
}

func forwardMembers() []*cpb.LeaseMember {
	return []*cpb.LeaseMember{
		selectorMember("peer", "device-type=bt-peer"),
		selectorMember("headunit", "device-type=aaos-headunit"),
	}
}

func TestCreateLeaseWithForwards(t *testing.T) {
	svc, fc := templateService()

	result, err := svc.CreateLease(context.Background(), &cpb.CreateLeaseRequest{
		Parent:  "namespaces/" + memberTestNamespace,
		LeaseId: "lease-9m2qd",
		Lease: &cpb.Lease{
			Duration: durationpb.New(45 * time.Minute),
			Members:  forwardMembers(),
			Forwards: []*cpb.LeaseForward{protoForward("bt", "headunit", "rootcanal", "peer", "controller")},
		},
	})
	if err != nil {
		t.Fatalf("expected the lease to be created, got %v", err)
	}
	// The response has the forwards: a client knows from it that the
	// controller has this JEP.
	if len(result.Forwards) != 1 || result.Forwards[0].Name != "bt" || len(result.Forwards[0].Endpoints) != 2 ||
		result.Forwards[0].Endpoints[0].MemberName != "headunit" || result.Forwards[0].Endpoints[0].PortName != "rootcanal" {
		t.Fatalf("unexpected forwards in the response: %v", result.Forwards)
	}

	leases := listLeases(t, fc)
	if len(leases) != 1 {
		t.Fatalf("expected one lease, got %d", len(leases))
	}
	forwards := leases[0].Spec.Forwards
	if len(forwards) != 1 || forwards[0].Name != "bt" || len(forwards[0].Endpoints) != 2 ||
		forwards[0].Endpoints[1] != (jumpstarterdevv1alpha1.ForwardEndpoint{Member: "peer", Port: "controller"}) {
		t.Fatalf("unexpected forwards in the lease: %+v", forwards)
	}
}

func TestCreateLeaseForwardsRequestForms(t *testing.T) {
	exporterName := "rpi-1"
	bt := protoForward("bt", "headunit", "rootcanal", "peer", "controller")
	nine := protoForward("wide")
	var nineMembers []*cpb.LeaseMember
	for i := range 9 {
		role := fmt.Sprintf("role-%d", i)
		nine.Endpoints = append(nine.Endpoints, &cpb.ForwardEndpoint{MemberName: role, PortName: "p"})
		nineMembers = append(nineMembers, selectorMember(role, "a=b"))
	}
	var seventeen []*cpb.LeaseForward
	for i := range 17 {
		seventeen = append(seventeen, protoForward(fmt.Sprintf("f-%d", i), "headunit", "p", "peer", fmt.Sprintf("q-%d", i)))
	}

	tests := []struct {
		name    string
		lease   *cpb.Lease
		message []string
	}{
		{name: "forwards in a scalar request with a selector",
			lease:   &cpb.Lease{Selector: "board=rpi4", Forwards: []*cpb.LeaseForward{bt}},
			message: []string{"forwards requires members"}},
		{name: "forwards in a scalar request with an exporter name",
			lease:   &cpb.Lease{ExporterName: &exporterName, Forwards: []*cpb.LeaseForward{bt}},
			message: []string{"forwards requires members"}},
		{name: "forwards with nothing else",
			lease:   &cpb.Lease{Forwards: []*cpb.LeaseForward{bt}},
			message: []string{"forwards requires members"}},
		{name: "forwards in a template request",
			lease:   &cpb.Lease{Template: &cpb.LeaseTemplateUse{Template: "ivi"}, Forwards: []*cpb.LeaseForward{bt}},
			message: []string{"forwards cannot be used with template"}},
		{name: "an endpoint on a role that the lease does not have",
			lease: &cpb.Lease{Members: forwardMembers(),
				Forwards: []*cpb.LeaseForward{protoForward("bt", "headunit", "rootcanal", "phone", "controller")}},
			message: []string{"forwards[bt].endpoints", `"phone" is not a member of the lease`}},
		{name: "a forward with the name of a role",
			lease: &cpb.Lease{Members: forwardMembers(),
				Forwards: []*cpb.LeaseForward{protoForward("peer", "headunit", "rootcanal", "peer", "controller")}},
			message: []string{"forwards[peer]", "a forward and a member cannot have the same name"}},
		{name: "two forwards with one name",
			lease:   &cpb.Lease{Members: forwardMembers(), Forwards: []*cpb.LeaseForward{bt, bt}},
			message: []string{`forwards: duplicate forward "bt"`}},
		{name: "a forward without a name",
			lease: &cpb.Lease{Members: forwardMembers(),
				Forwards: []*cpb.LeaseForward{protoForward("", "headunit", "rootcanal", "peer", "controller")}},
			message: []string{"forwards[0].name is required"}},
		{name: "a forward with one endpoint",
			lease: &cpb.Lease{Members: forwardMembers(),
				Forwards: []*cpb.LeaseForward{protoForward("bt", "headunit", "rootcanal")}},
			message: []string{"forwards[bt].endpoints", "2 to 8 endpoints"}},
		{name: "a forward with nine endpoints",
			lease:   &cpb.Lease{Members: nineMembers, Forwards: []*cpb.LeaseForward{nine}},
			message: []string{"forwards[wide].endpoints", "2 to 8 endpoints"}},
		{name: "two endpoints on one member",
			lease: &cpb.Lease{Members: forwardMembers(),
				Forwards: []*cpb.LeaseForward{protoForward("bt", "headunit", "rootcanal", "headunit", "controller")}},
			message: []string{"forwards[bt].endpoints", `member "headunit" has more than one endpoint`}},
		{name: "an endpoint without a port",
			lease: &cpb.Lease{Members: forwardMembers(),
				Forwards: []*cpb.LeaseForward{protoForward("bt", "headunit", "rootcanal", "peer", "")}},
			message: []string{"forwards[bt].endpoints[1].port_name is required"}},
		{name: "an endpoint without a member",
			lease: &cpb.Lease{Members: forwardMembers(),
				Forwards: []*cpb.LeaseForward{protoForward("bt", "", "rootcanal", "peer", "controller")}},
			message: []string{"forwards[bt].endpoints[0].member_name is required"}},
		{name: "more than sixteen forwards",
			lease:   &cpb.Lease{Members: forwardMembers(), Forwards: seventeen},
			message: []string{"forwards: a lease has at most 16 forwards"}},
	}
	for _, tt := range tests {
		t.Run("rejects "+tt.name, func(t *testing.T) {
			svc, fc := templateService(iviTemplate())
			tt.lease.Duration = durationpb.New(time.Hour)
			_, err := svc.CreateLease(context.Background(), &cpb.CreateLeaseRequest{
				Parent: "namespaces/" + memberTestNamespace,
				Lease:  tt.lease,
			})
			requireStatus(t, err, codes.InvalidArgument, tt.message...)
			if leases := listLeases(t, fc); len(leases) != 0 {
				t.Fatalf("a rejected request created a lease: %+v", leases)
			}
		})
	}
}

// forwardTemplate is iviTemplate with two forwards: bt between the head unit
// and the optional phone, and can between the head unit and the ECU.
func forwardTemplate() *jumpstarterdevv1alpha1.LeaseTemplate {
	template := iviTemplate()
	template.Spec.Forwards = []jumpstarterdevv1alpha1.LeaseForward{
		{Name: "bt", Endpoints: []jumpstarterdevv1alpha1.ForwardEndpoint{
			{Member: "headunit", Port: "rootcanal"}, {Member: "phone", Port: "controller"}}},
		{Name: "can", Endpoints: []jumpstarterdevv1alpha1.ForwardEndpoint{
			{Member: "ecu", Port: "can"}, {Member: "headunit", Port: "can-remote"}}},
	}
	return template
}

func TestCreateLeaseFromTemplateCopiesForwards(t *testing.T) {
	t.Run("without changes", func(t *testing.T) {
		template := forwardTemplate()
		svc, fc := templateService(template)
		result, err := svc.CreateLease(context.Background(), templateRequest(&cpb.LeaseTemplateUse{Template: "ivi"}))
		if err != nil {
			t.Fatalf("expected the lease to be created, got %v", err)
		}
		if len(result.Forwards) != 2 || result.Forwards[0].Name != "bt" || result.Forwards[1].Name != "can" {
			t.Fatalf("unexpected forwards in the response: %v", result.Forwards)
		}
		leases := listLeases(t, fc)
		if len(leases) != 1 {
			t.Fatalf("expected one lease, got %d", len(leases))
		}
		got := leases[0].Spec.Forwards
		if len(got) != len(template.Spec.Forwards) {
			t.Fatalf("expected the forwards of the template, got %+v", got)
		}
		for i := range got {
			want := template.Spec.Forwards[i]
			if got[i].Name != want.Name || len(got[i].Endpoints) != len(want.Endpoints) {
				t.Fatalf("forward %d: expected %+v, got %+v", i, want, got[i])
			}
			for j := range got[i].Endpoints {
				if got[i].Endpoints[j] != want.Endpoints[j] {
					t.Fatalf("forward %d: expected %+v, got %+v", i, want, got[i])
				}
			}
		}
	})

	t.Run("keeps the forward of an excluded role: the controller disables it", func(t *testing.T) {
		svc, fc := templateService(forwardTemplate())
		_, err := svc.CreateLease(context.Background(), templateRequest(&cpb.LeaseTemplateUse{
			Template: "ivi", ExcludedMembers: []string{"phone"},
		}))
		if err != nil {
			t.Fatalf("expected the lease to be created, got %v", err)
		}
		spec := listLeases(t, fc)[0].Spec
		if len(spec.Forwards) != 2 || spec.Forwards[0].Name != "bt" || spec.Forwards[0].Endpoints[1].Member != "phone" {
			t.Fatalf("expected the forwards of the template without changes, got %+v", spec.Forwards)
		}
		// The role stays a member of the lease, so that the endpoint refers
		// to a declared member.
		if len(spec.Members) != 3 || !spec.Members[1].Excluded {
			t.Fatalf("expected the excluded role to stay in the members, got %+v", spec.Members)
		}
	})

	t.Run("a template without forwards gives a lease without forwards", func(t *testing.T) {
		svc, fc := templateService(iviTemplate())
		result, err := svc.CreateLease(context.Background(), templateRequest(&cpb.LeaseTemplateUse{Template: "ivi"}))
		if err != nil {
			t.Fatalf("expected the lease to be created, got %v", err)
		}
		if len(result.Forwards) != 0 || len(listLeases(t, fc)[0].Spec.Forwards) != 0 {
			t.Fatalf("expected no forwards, got %v", result.Forwards)
		}
	})
}

func TestLeaseTemplateForwardsToProtobuf(t *testing.T) {
	svc, _ := templateService(forwardTemplate())
	got, err := svc.GetLeaseTemplate(context.Background(), &cpb.GetLeaseTemplateRequest{
		Name: "namespaces/" + memberTestNamespace + "/leasetemplates/ivi",
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(got.Forwards) != 2 || got.Forwards[1].Name != "can" || len(got.Forwards[1].Endpoints) != 2 ||
		got.Forwards[1].Endpoints[0].MemberName != "ecu" || got.Forwards[1].Endpoints[0].PortName != "can" {
		t.Fatalf("unexpected forwards of the template: %v", got.Forwards)
	}

	list, err := svc.ListLeaseTemplates(context.Background(), &cpb.ListLeaseTemplatesRequest{
		Parent: "namespaces/" + memberTestNamespace,
	})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(list.LeaseTemplates) != 1 || len(list.LeaseTemplates[0].Forwards) != 2 {
		t.Fatalf("unexpected templates: %v", list.LeaseTemplates)
	}
}

func TestUpdateLeaseForwardsAreImmutable(t *testing.T) {
	for _, path := range []string{"forwards", "forwards.endpoints"} {
		t.Run(path, func(t *testing.T) {
			lease := boundMemberLease(true)
			lease.Spec.Forwards = []jumpstarterdevv1alpha1.LeaseForward{{Name: "bt", Endpoints: []jumpstarterdevv1alpha1.ForwardEndpoint{
				{Member: "headunit", Port: "rootcanal"}, {Member: "phone", Port: "controller"}}}}
			svc, fc := memberUpdateService(lease, nil)
			_, err := svc.UpdateLease(context.Background(), &cpb.UpdateLeaseRequest{
				Lease: &cpb.Lease{Name: memberLeaseName(), Duration: durationpb.New(2 * time.Hour),
					Forwards: []*cpb.LeaseForward{protoForward("other", "headunit", "x", "phone", "y")}},
				UpdateMask: &fieldmaskpb.FieldMask{Paths: []string{"duration", path}},
			})
			requireStatus(t, err, codes.InvalidArgument, "forwards", "immutable")
			got := listLeases(t, fc)[0].Spec
			if got.Duration.Duration != time.Hour || len(got.Forwards) != 1 || got.Forwards[0].Name != "bt" {
				t.Fatalf("expected the lease to stay as it was, got %+v", got)
			}
		})
	}

	t.Run("an update of a mutable field keeps the forwards", func(t *testing.T) {
		lease := boundMemberLease(true)
		lease.Spec.Forwards = []jumpstarterdevv1alpha1.LeaseForward{{Name: "bt", Endpoints: []jumpstarterdevv1alpha1.ForwardEndpoint{
			{Member: "headunit", Port: "rootcanal"}, {Member: "phone", Port: "controller"}}}}
		svc, fc := memberUpdateService(lease, nil)
		_, err := svc.UpdateLease(context.Background(), &cpb.UpdateLeaseRequest{
			Lease: &cpb.Lease{Name: memberLeaseName(), Duration: durationpb.New(2 * time.Hour)},
		})
		if err != nil {
			t.Fatalf("expected the update to succeed, got %v", err)
		}
		got := listLeases(t, fc)[0].Spec
		if got.Duration.Duration != 2*time.Hour || len(got.Forwards) != 1 || got.Forwards[0].Name != "bt" {
			t.Fatalf("expected the new duration and the forwards, got %+v", got)
		}
	})
}

func TestGetLeaseForwardStatuses(t *testing.T) {
	lease := boundMemberLease(true)
	lease.Spec.Forwards = []jumpstarterdevv1alpha1.LeaseForward{
		{Name: "bt", Endpoints: []jumpstarterdevv1alpha1.ForwardEndpoint{
			{Member: "headunit", Port: "rootcanal"}, {Member: "phone", Port: "controller"}}},
		{Name: "cam", Endpoints: []jumpstarterdevv1alpha1.ForwardEndpoint{
			{Member: "headunit", Port: "video"}, {Member: "camera", Port: "video"}}},
	}
	lease.Status.Forwards = []jumpstarterdevv1alpha1.LeaseForwardStatus{
		{Name: "bt", State: jumpstarterdevv1alpha1.LeaseForwardStateConnected},
		{Name: "cam", State: jumpstarterdevv1alpha1.LeaseForwardStateDisabled, Message: "Role `camera` is Omitted"},
	}
	svc, _ := memberUpdateService(lease, nil)
	got, err := svc.GetLease(context.Background(), &cpb.GetLeaseRequest{Name: memberLeaseName()})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(got.Forwards) != 2 || len(got.ForwardStatuses) != 2 {
		t.Fatalf("unexpected forwards: %v, %v", got.Forwards, got.ForwardStatuses)
	}
	if got.ForwardStatuses[0].State != cpb.LeaseForwardState_LEASE_FORWARD_STATE_CONNECTED || got.ForwardStatuses[0].Message != nil {
		t.Fatalf("unexpected status of bt: %v", got.ForwardStatuses[0])
	}
	if got.ForwardStatuses[1].State != cpb.LeaseForwardState_LEASE_FORWARD_STATE_DISABLED ||
		got.ForwardStatuses[1].GetMessage() != "Role `camera` is Omitted" {
		t.Fatalf("unexpected status of cam: %v", got.ForwardStatuses[1])
	}
}

func TestExporterPortsToProtobuf(t *testing.T) {
	withPorts := onlineExporter("hu-1", map[string]string{"device-type": "headunit"})
	withPorts.Status.Devices = []jumpstarterdevv1alpha1.Device{
		{Uuid: "root"},
		{Uuid: "child", Ports: []jumpstarterdevv1alpha1.DevicePort{
			{Name: "rootcanal", Direction: jumpstarterdevv1alpha1.PortDirectionProvides, Protocol: "hci-h4"},
			{Name: "can", Direction: jumpstarterdevv1alpha1.PortDirectionRequires,
				Attachment: jumpstarterdevv1alpha1.PortAttachmentWired, Transport: jumpstarterdevv1alpha1.PortTransportFrame},
		}},
	}
	old := onlineExporter("old-1", nil)
	old.Status.Devices = []jumpstarterdevv1alpha1.Device{{Uuid: "root"}}
	fc := testFakeClient(append(namedClients(memberTestNamespace, "owner"), withPorts, old)...)
	svc := authedClientService("owner", memberTestNamespace, fc)

	got, err := svc.GetExporter(context.Background(), &cpb.GetExporterRequest{Name: "namespaces/lab/exporters/hu-1"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(got.Ports) != 2 {
		t.Fatalf("expected two ports, got %v", got.Ports)
	}
	rootcanal, can := got.Ports[0], got.Ports[1]
	if rootcanal.Name != "rootcanal" || rootcanal.Direction != pb.PortDirection_PORT_DIRECTION_PROVIDES ||
		rootcanal.GetProtocol() != "hci-h4" || rootcanal.Attachment != pb.PortAttachment_PORT_ATTACHMENT_SOCKET ||
		rootcanal.Transport != pb.PortTransport_PORT_TRANSPORT_STREAM {
		t.Fatalf("unexpected port: %v", rootcanal)
	}
	if can.Direction != pb.PortDirection_PORT_DIRECTION_REQUIRES || can.Protocol != nil ||
		can.Attachment != pb.PortAttachment_PORT_ATTACHMENT_WIRED || can.Transport != pb.PortTransport_PORT_TRANSPORT_FRAME {
		t.Fatalf("unexpected port: %v", can)
	}

	none, err := svc.GetExporter(context.Background(), &cpb.GetExporterRequest{Name: "namespaces/lab/exporters/old-1"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(none.Ports) != 0 {
		t.Fatalf("an exporter that reports no ports has none, got %v", none.Ports)
	}

	list, err := svc.ListExporters(context.Background(), &cpb.ListExportersRequest{Parent: "namespaces/lab"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	ports := map[string]int{}
	for _, exporter := range list.Exporters {
		ports[exporter.Name] = len(exporter.Ports)
	}
	if ports["namespaces/lab/exporters/hu-1"] != 2 || ports["namespaces/lab/exporters/old-1"] != 0 {
		t.Fatalf("unexpected ports in the list: %v", ports)
	}
}
