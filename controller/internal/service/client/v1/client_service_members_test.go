package v1

import (
	"context"
	"strings"
	"testing"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/utils"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/durationpb"
	"google.golang.org/protobuf/types/known/fieldmaskpb"
	"google.golang.org/protobuf/types/known/timestamppb"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

const memberTestNamespace = "lab"

func selectorMember(name, selector string) *cpb.LeaseMember {
	return &cpb.LeaseMember{Name: name, Selection: &cpb.LeaseMember_Selector{Selector: selector}}
}

func sameLabel(key string, members ...string) *cpb.LeaseConstraint {
	return &cpb.LeaseConstraint{MemberNames: members, Rule: &cpb.LeaseConstraint_SameLabel{SameLabel: key}}
}

// requireStatus fails unless err is a gRPC status with this code whose
// message contains each of the given parts.
func requireStatus(t *testing.T, err error, code codes.Code, parts ...string) {
	t.Helper()
	st, ok := status.FromError(err)
	if err == nil || !ok {
		t.Fatalf("expected %v status error, got %v", code, err)
	}
	if st.Code() != code {
		t.Fatalf("expected %v, got %v: %s", code, st.Code(), st.Message())
	}
	for _, part := range parts {
		if !strings.Contains(st.Message(), part) {
			t.Fatalf("expected message to contain %q, got %q", part, st.Message())
		}
	}
}

func listLeases(t *testing.T, c kclient.Client) []jumpstarterdevv1alpha1.Lease {
	t.Helper()
	var leases jumpstarterdevv1alpha1.LeaseList
	if err := c.List(context.Background(), &leases); err != nil {
		t.Fatalf("failed to list leases: %v", err)
	}
	return leases.Items
}

// iviTemplate is the template of the JEP: a head unit, an optional phone and
// an ECU that is named by its exporter.
func iviTemplate() *jumpstarterdevv1alpha1.LeaseTemplate {
	return &jumpstarterdevv1alpha1.LeaseTemplate{
		ObjectMeta: metav1.ObjectMeta{Name: "ivi", Namespace: memberTestNamespace, Generation: 3},
		Spec: jumpstarterdevv1alpha1.LeaseTemplateSpec{
			Description: "AAOS head unit, phone, and body ECU on one CAN bus",
			Members: []jumpstarterdevv1alpha1.LeaseMember{
				{Name: "headunit", Selector: &metav1.LabelSelector{MatchLabels: map[string]string{"device-type": "aaos-headunit"}}},
				{Name: "phone", Selector: &metav1.LabelSelector{MatchLabels: map[string]string{"device-type": "phone"}}, Optional: true},
				{Name: "ecu", ExporterRef: &corev1.LocalObjectReference{Name: "ecu-11"}},
			},
			Constraints: []jumpstarterdevv1alpha1.LeaseConstraint{
				{Members: []string{"headunit", "ecu"}, SameLabel: "bus"},
				{Members: []string{"headunit", "phone"}, SameLabel: "rf-domain"},
			},
			Defaults: jumpstarterdevv1alpha1.LeaseTemplateDefaults{
				Duration:   &metav1.Duration{Duration: 2 * time.Hour},
				SharedWith: []string{"lab-support", "ci-runner"},
			},
		},
	}
}

func templateRequest(use *cpb.LeaseTemplateUse) *cpb.CreateLeaseRequest {
	return &cpb.CreateLeaseRequest{
		Parent: "namespaces/" + memberTestNamespace,
		Lease:  &cpb.Lease{Template: use},
	}
}

func templateService(extra ...kclient.Object) (*ClientService, kclient.Client) {
	objs := append(namedClients(memberTestNamespace, "ci-runner", "lab-support", "alice"), extra...)
	fc := testFakeClient(objs...)
	svc := authedClientService("ci-runner", memberTestNamespace, fc)
	svc.MaxTags = 10
	return svc, fc
}

func TestValidateLeaseTargetRequestForms(t *testing.T) {
	exporterName := "rpi-1"
	members := []*cpb.LeaseMember{selectorMember("phone", "device-type=phone"), selectorMember("headunit", "device-type=headunit")}
	constraints := []*cpb.LeaseConstraint{sameLabel("rf-domain", "phone", "headunit")}
	template := &cpb.LeaseTemplateUse{Template: "ivi"}

	valid := []struct {
		name  string
		lease *cpb.Lease
	}{
		{"template", &cpb.Lease{Template: template}},
		{"template with exclusions and selectors", &cpb.Lease{Template: &cpb.LeaseTemplateUse{
			Template: "ivi", ExcludedMembers: []string{"phone"}, MemberSelectors: map[string]string{"headunit": "soc=sa8775p"},
		}}},
		{"members", &cpb.Lease{Members: members}},
		{"members with constraints", &cpb.Lease{Members: members, Constraints: constraints}},
		{"member with exporter name", &cpb.Lease{Members: []*cpb.LeaseMember{
			{Name: "ecu", Selection: &cpb.LeaseMember_ExporterName{ExporterName: "ecu-11"}},
		}}},
	}
	for _, tt := range valid {
		t.Run("accepts "+tt.name, func(t *testing.T) {
			if err := validateLeaseTarget(tt.lease); err != nil {
				t.Fatalf("expected a valid request, got %v", err)
			}
		})
	}

	invalid := []struct {
		name  string
		lease *cpb.Lease
		want  []string
	}{
		{"selector with members", &cpb.Lease{Selector: "a=b", Members: members}, []string{"selector", "members"}},
		{"selector with constraints", &cpb.Lease{Selector: "a=b", Constraints: constraints}, []string{"selector", "constraints"}},
		{"selector with template", &cpb.Lease{Selector: "a=b", Template: template}, []string{"selector", "template"}},
		{"exporter_name with members", &cpb.Lease{ExporterName: &exporterName, Members: members}, []string{"exporter_name", "members"}},
		{"exporter_name with constraints", &cpb.Lease{ExporterName: &exporterName, Constraints: constraints}, []string{"exporter_name", "constraints"}},
		{"exporter_name with template", &cpb.Lease{ExporterName: &exporterName, Template: template}, []string{"exporter_name", "template"}},
		{"selector and exporter_name with members", &cpb.Lease{Selector: "a=b", ExporterName: &exporterName, Members: members},
			[]string{"selector", "exporter_name", "members"}},
		{"template with members", &cpb.Lease{Template: template, Members: members}, []string{"template", "members"}},
		{"template with constraints", &cpb.Lease{Template: template, Constraints: constraints}, []string{"template", "constraints"}},
		{"template with members and constraints", &cpb.Lease{Template: template, Members: members, Constraints: constraints},
			[]string{"template", "members", "constraints"}},
		{"all the forms", &cpb.Lease{Selector: "a=b", Template: template, Members: members}, []string{"template", "selector", "members"}},
		{"template without a name", &cpb.Lease{Template: &cpb.LeaseTemplateUse{}}, []string{"template.template"}},
		{"constraints without members", &cpb.Lease{Constraints: constraints}, []string{"constraints", "members"}},
		{"excluded on a member", &cpb.Lease{Members: []*cpb.LeaseMember{
			{Name: "phone", Selection: &cpb.LeaseMember_Selector{Selector: "a=b"}, Optional: true, Excluded: true},
		}}, []string{"members[phone].excluded"}},
		{"member without a name", &cpb.Lease{Members: []*cpb.LeaseMember{selectorMember("", "a=b")}}, []string{"members[0].name"}},
		{"member without a selection", &cpb.Lease{Members: []*cpb.LeaseMember{{Name: "phone"}}}, []string{"members[phone]", "selector", "exporter_name"}},
		{"member with an empty selector", &cpb.Lease{Members: []*cpb.LeaseMember{selectorMember("phone", "")}}, []string{"members[phone]"}},
		{"duplicate roles", &cpb.Lease{Members: []*cpb.LeaseMember{selectorMember("phone", "a=b"), selectorMember("phone", "a=c")}},
			[]string{"duplicate role", "phone"}},
		{"constraint for an unknown role", &cpb.Lease{Members: members, Constraints: []*cpb.LeaseConstraint{sameLabel("bus", "phone", "ecu")}},
			[]string{"constraints[0].member_names", "ecu"}},
		{"constraint without a rule", &cpb.Lease{Members: members, Constraints: []*cpb.LeaseConstraint{{MemberNames: []string{"phone", "headunit"}}}},
			[]string{"constraints[0].same_label"}},
	}
	for _, tt := range invalid {
		t.Run("rejects "+tt.name, func(t *testing.T) {
			requireStatus(t, validateLeaseTarget(tt.lease), codes.InvalidArgument, tt.want...)
		})
	}
}

func TestCreateLeaseMixedFormsCreateNothing(t *testing.T) {
	exporterName := "rpi-1"
	members := []*cpb.LeaseMember{selectorMember("phone", "device-type=phone")}
	template := &cpb.LeaseTemplateUse{Template: "ivi"}

	tests := map[string]*cpb.Lease{
		"scalar and members":   {Selector: "a=b", Members: members},
		"scalar and template":  {ExporterName: &exporterName, Template: template},
		"template and members": {Template: template, Members: members},
		"excluded member": {Members: []*cpb.LeaseMember{
			{Name: "phone", Selection: &cpb.LeaseMember_Selector{Selector: "a=b"}, Optional: true, Excluded: true},
		}},
		"no form": {},
	}
	for name, lease := range tests {
		t.Run(name, func(t *testing.T) {
			svc, fc := templateService(iviTemplate())
			lease.Duration = durationpb.New(time.Hour)
			_, err := svc.CreateLease(context.Background(), &cpb.CreateLeaseRequest{
				Parent: "namespaces/" + memberTestNamespace,
				Lease:  lease,
			})
			requireStatus(t, err, codes.InvalidArgument)
			if leases := listLeases(t, fc); len(leases) != 0 {
				t.Fatalf("expected no lease, got %d", len(leases))
			}
		})
	}
}

func TestCreateLeaseWithMembers(t *testing.T) {
	svc, fc := templateService()

	result, err := svc.CreateLease(context.Background(), &cpb.CreateLeaseRequest{
		Parent:  "namespaces/" + memberTestNamespace,
		LeaseId: "lease-9m2qd",
		Lease: &cpb.Lease{
			Duration: durationpb.New(45 * time.Minute),
			Members: []*cpb.LeaseMember{
				selectorMember("phone", "device-type=phone"),
				selectorMember("headunit", "device-type=headunit"),
			},
			Constraints: []*cpb.LeaseConstraint{sameLabel("rf-domain", "phone", "headunit")},
			SharedWith:  []string{"alice"},
		},
	})
	if err != nil {
		t.Fatalf("expected the lease to be created, got %v", err)
	}

	if result.Name != "namespaces/lab/leases/lease-9m2qd" {
		t.Fatalf("unexpected lease name %q", result.Name)
	}
	if result.Selector != "" || result.Exporter != nil || result.Template != nil {
		t.Fatalf("expected no selector, exporter or template, got %q, %v, %v", result.Selector, result.Exporter, result.Template)
	}
	if len(result.Members) != 2 || result.Members[0].GetSelector() != "device-type=phone" {
		t.Fatalf("unexpected members %v", result.Members)
	}

	leases := listLeases(t, fc)
	if len(leases) != 1 {
		t.Fatalf("expected one lease, got %d", len(leases))
	}
	spec := leases[0].Spec
	if spec.ClientRef.Name != "ci-runner" || spec.TemplateRef != nil || spec.ExporterRef != nil {
		t.Fatalf("unexpected spec %+v", spec)
	}
	if len(spec.Selector.MatchLabels) != 0 || len(spec.Selector.MatchExpressions) != 0 {
		t.Fatalf("expected an empty lease selector, got %+v", spec.Selector)
	}
	if len(spec.Members) != 2 || spec.Members[1].Name != "headunit" ||
		spec.Members[1].Selector.MatchLabels["device-type"] != "headunit" {
		t.Fatalf("unexpected members %+v", spec.Members)
	}
	if len(spec.Constraints) != 1 || spec.Constraints[0].SameLabel != "rf-domain" {
		t.Fatalf("unexpected constraints %+v", spec.Constraints)
	}
	if len(spec.SharedWith) != 1 || spec.SharedWith[0] != "alice" {
		t.Fatalf("unexpected sharedWith %v", spec.SharedWith)
	}
}

func TestCreateLeaseWithMembersRejectsBadSelector(t *testing.T) {
	svc, fc := templateService()
	_, err := svc.CreateLease(context.Background(), &cpb.CreateLeaseRequest{
		Parent: "namespaces/" + memberTestNamespace,
		Lease: &cpb.Lease{
			Duration: durationpb.New(time.Hour),
			Members:  []*cpb.LeaseMember{selectorMember("phone", "a==b==c")},
		},
	})
	requireStatus(t, err, codes.InvalidArgument, "phone")
	if leases := listLeases(t, fc); len(leases) != 0 {
		t.Fatalf("expected no lease, got %d", len(leases))
	}
}

func TestCreateLeaseFromTemplate(t *testing.T) {
	ctx := context.Background()

	t.Run("expands the template into a member-form lease", func(t *testing.T) {
		svc, fc := templateService(iviTemplate())
		begin := time.Now().Add(time.Hour).Truncate(time.Second)

		req := templateRequest(&cpb.LeaseTemplateUse{
			Template:        "ivi",
			ExcludedMembers: []string{"phone"},
			MemberSelectors: map[string]string{"headunit": "soc=sa8775p,rev in (b,c)"},
		})
		req.LeaseId = "ivi-4fj2k"
		req.Lease.Duration = durationpb.New(4 * time.Hour)
		req.Lease.BeginTime = timestamppb.New(begin)
		req.Lease.Tags = map[string]string{"suite": "smoke"}
		req.Lease.Context = map[string]string{"build": "42"}

		result, err := svc.CreateLease(ctx, req)
		if err != nil {
			t.Fatalf("expected the lease to be created, got %v", err)
		}

		leases := listLeases(t, fc)
		if len(leases) != 1 || leases[0].Name != "ivi-4fj2k" {
			t.Fatalf("expected the lease ivi-4fj2k, got %+v", leases)
		}
		spec := leases[0].Spec

		if spec.TemplateRef == nil || spec.TemplateRef.Name != "ivi" || spec.TemplateRef.Generation != 3 {
			t.Fatalf("unexpected templateRef %+v", spec.TemplateRef)
		}
		if spec.ClientRef.Name != "ci-runner" || spec.ExporterRef != nil ||
			len(spec.Selector.MatchLabels) != 0 || len(spec.Selector.MatchExpressions) != 0 {
			t.Fatalf("unexpected scalar fields in %+v", spec)
		}

		if len(spec.Members) != 3 {
			t.Fatalf("expected 3 members, got %+v", spec.Members)
		}
		headunit, phone, ecu := spec.Members[0], spec.Members[1], spec.Members[2]
		if headunit.Name != "headunit" || headunit.Excluded ||
			headunit.Selector.MatchLabels["device-type"] != "aaos-headunit" ||
			headunit.Selector.MatchLabels["soc"] != "sa8775p" ||
			len(headunit.Selector.MatchExpressions) != 1 || headunit.Selector.MatchExpressions[0].Key != "rev" {
			t.Fatalf("unexpected headunit member %+v", headunit)
		}
		if phone.Name != "phone" || !phone.Optional || !phone.Excluded ||
			len(phone.Selector.MatchLabels) != 1 || phone.Selector.MatchLabels["device-type"] != "phone" {
			t.Fatalf("unexpected phone member %+v", phone)
		}
		if ecu.Name != "ecu" || ecu.Excluded || ecu.ExporterRef == nil || ecu.ExporterRef.Name != "ecu-11" || ecu.Selector != nil {
			t.Fatalf("unexpected ecu member %+v", ecu)
		}

		if len(spec.Constraints) != 2 || spec.Constraints[0].SameLabel != "bus" || spec.Constraints[1].SameLabel != "rf-domain" ||
			len(spec.Constraints[0].Members) != 2 || spec.Constraints[0].Members[1] != "ecu" {
			t.Fatalf("unexpected constraints %+v", spec.Constraints)
		}

		if spec.Duration == nil || spec.Duration.Duration != 4*time.Hour {
			t.Fatalf("expected the request duration, got %v", spec.Duration)
		}
		if spec.BeginTime == nil || !spec.BeginTime.Time.Equal(begin) {
			t.Fatalf("expected the request begin time, got %v", spec.BeginTime)
		}
		if spec.Tags["suite"] != "smoke" || spec.Context["build"] != "42" {
			t.Fatalf("expected tags and context of the request, got %v and %v", spec.Tags, spec.Context)
		}
		if leases[0].Labels[jumpstarterdevv1alpha1.LeaseTagMetadataPrefix+"suite"] != "smoke" {
			t.Fatalf("expected the tag label, got %v", leases[0].Labels)
		}

		// The template itself is not changed by a request that narrows it.
		var template jumpstarterdevv1alpha1.LeaseTemplate
		if err := fc.Get(ctx, kclient.ObjectKey{Namespace: memberTestNamespace, Name: "ivi"}, &template); err != nil {
			t.Fatalf("failed to get template: %v", err)
		}
		if len(template.Spec.Members[0].Selector.MatchLabels) != 1 || template.Spec.Members[1].Excluded {
			t.Fatalf("the template was changed: %+v", template.Spec.Members)
		}

		if result.Template == nil || result.Template.Template != "namespaces/lab/leasetemplates/ivi" || result.Template.Generation != 3 {
			t.Fatalf("unexpected template in the response: %v", result.Template)
		}
		if len(result.Members) != 3 || !result.Members[1].Excluded || result.Selector != "" {
			t.Fatalf("unexpected response %v", result)
		}
	})

	t.Run("accepts the resource name of the template", func(t *testing.T) {
		svc, fc := templateService(iviTemplate())
		_, err := svc.CreateLease(ctx, templateRequest(&cpb.LeaseTemplateUse{Template: "namespaces/lab/leasetemplates/ivi"}))
		if err != nil {
			t.Fatalf("expected the lease to be created, got %v", err)
		}
		if leases := listLeases(t, fc); len(leases) != 1 || leases[0].Spec.TemplateRef.Name != "ivi" {
			t.Fatalf("unexpected leases %+v", leases)
		}
	})

	t.Run("a template with one member gives a member-form lease", func(t *testing.T) {
		single := &jumpstarterdevv1alpha1.LeaseTemplate{
			ObjectMeta: metav1.ObjectMeta{Name: "single", Namespace: memberTestNamespace, Generation: 1},
			Spec: jumpstarterdevv1alpha1.LeaseTemplateSpec{
				Members: []jumpstarterdevv1alpha1.LeaseMember{
					{Name: "dut", Selector: &metav1.LabelSelector{MatchLabels: map[string]string{"board": "rpi4"}}},
				},
			},
		}
		svc, fc := templateService(single)
		req := templateRequest(&cpb.LeaseTemplateUse{Template: "single"})
		req.Lease.Duration = durationpb.New(time.Hour)
		if _, err := svc.CreateLease(ctx, req); err != nil {
			t.Fatalf("expected the lease to be created, got %v", err)
		}
		leases := listLeases(t, fc)
		if len(leases) != 1 || !leases[0].IsMemberForm() || len(leases[0].Spec.Selector.MatchLabels) != 0 {
			t.Fatalf("expected a member-form lease, got %+v", leases)
		}
	})

	errorCases := []struct {
		name string
		use  *cpb.LeaseTemplateUse
		code codes.Code
		want []string
	}{
		{"a missing template", &cpb.LeaseTemplateUse{Template: "nope"}, codes.NotFound, []string{"nope"}},
		{"a template of another namespace", &cpb.LeaseTemplateUse{Template: "namespaces/other/leasetemplates/ivi"},
			codes.InvalidArgument, []string{"namespace"}},
		{"a resource name of another kind", &cpb.LeaseTemplateUse{Template: "namespaces/lab/leases/ivi"},
			codes.InvalidArgument, []string{"leasetemplates"}},
		{"an excluded role that the template does not have", &cpb.LeaseTemplateUse{Template: "ivi", ExcludedMembers: []string{"camera"}},
			codes.InvalidArgument, []string{"excluded_members", "camera"}},
		{"a narrower selector for a role that the template does not have",
			&cpb.LeaseTemplateUse{Template: "ivi", MemberSelectors: map[string]string{"camera": "a=b"}},
			codes.InvalidArgument, []string{"member_selectors", "camera"}},
		{"an excluded role that is not optional", &cpb.LeaseTemplateUse{Template: "ivi", ExcludedMembers: []string{"headunit"}},
			codes.InvalidArgument, []string{"excluded_members", "headunit", "not optional"}},
		{"a role that is excluded and made narrower",
			&cpb.LeaseTemplateUse{Template: "ivi", ExcludedMembers: []string{"phone"}, MemberSelectors: map[string]string{"phone": "os=android"}},
			codes.InvalidArgument, []string{"member_selectors", "phone", "excluded_members"}},
		{"a narrower selector for an exporterRef member",
			&cpb.LeaseTemplateUse{Template: "ivi", MemberSelectors: map[string]string{"ecu": "a=b"}},
			codes.InvalidArgument, []string{"member_selectors", "ecu", "names an exporter"}},
		{"a different value for a label of the template selector",
			&cpb.LeaseTemplateUse{Template: "ivi", MemberSelectors: map[string]string{"headunit": "device-type=phone"}},
			codes.InvalidArgument, []string{"member_selectors[headunit]", "device-type", "aaos-headunit", "phone"}},
		{"a narrower selector that does not parse",
			&cpb.LeaseTemplateUse{Template: "ivi", MemberSelectors: map[string]string{"headunit": "a==b==c"}},
			codes.InvalidArgument, []string{"member_selectors[headunit]"}},
		{"an empty narrower selector",
			&cpb.LeaseTemplateUse{Template: "ivi", MemberSelectors: map[string]string{"headunit": ""}},
			codes.InvalidArgument, []string{"member_selectors[headunit]", "empty"}},
	}
	for _, tt := range errorCases {
		t.Run("rejects "+tt.name, func(t *testing.T) {
			svc, fc := templateService(iviTemplate())
			_, err := svc.CreateLease(ctx, templateRequest(tt.use))
			requireStatus(t, err, tt.code, tt.want...)
			if leases := listLeases(t, fc); len(leases) != 0 {
				t.Fatalf("expected no lease, got %d", len(leases))
			}
		})
	}

	t.Run("accepts the same value for a label of the template selector", func(t *testing.T) {
		svc, fc := templateService(iviTemplate())
		_, err := svc.CreateLease(ctx, templateRequest(&cpb.LeaseTemplateUse{
			Template:        "ivi",
			MemberSelectors: map[string]string{"headunit": "device-type=aaos-headunit,soc=sa8775p"},
		}))
		if err != nil {
			t.Fatalf("expected the lease to be created, got %v", err)
		}
		labels := listLeases(t, fc)[0].Spec.Members[0].Selector.MatchLabels
		if len(labels) != 2 || labels["soc"] != "sa8775p" {
			t.Fatalf("unexpected selector %v", labels)
		}
	})
}

func TestCreateLeaseFromTemplateDurationDefault(t *testing.T) {
	ctx := context.Background()
	begin := time.Now().Add(time.Hour).Truncate(time.Second)
	end := begin.Add(30 * time.Minute)

	noDefault := iviTemplate()
	noDefault.Name = "no-default"
	noDefault.Spec.Defaults.Duration = nil

	tests := []struct {
		name     string
		template string
		lease    *cpb.Lease
		want     time.Duration
		wantErr  bool
	}{
		{name: "uses the default without a request duration", template: "ivi", lease: &cpb.Lease{}, want: 2 * time.Hour},
		{name: "the request duration replaces the default", template: "ivi",
			lease: &cpb.Lease{Duration: durationpb.New(4 * time.Hour)}, want: 4 * time.Hour},
		{name: "a begin time and an end time give the duration", template: "ivi",
			lease: &cpb.Lease{BeginTime: timestamppb.New(begin), EndTime: timestamppb.New(end)}, want: 30 * time.Minute},
		{name: "uses the default with only a begin time", template: "ivi",
			lease: &cpb.Lease{BeginTime: timestamppb.New(begin)}, want: 2 * time.Hour},
		{name: "uses the default with only an end time", template: "ivi",
			lease: &cpb.Lease{EndTime: timestamppb.New(end)}, want: 2 * time.Hour},
		{name: "without a default the request needs a duration", template: "no-default", lease: &cpb.Lease{}, wantErr: true},
		{name: "without a default the request duration applies", template: "no-default",
			lease: &cpb.Lease{Duration: durationpb.New(time.Hour)}, want: time.Hour},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			svc, fc := templateService(iviTemplate(), noDefault)
			tt.lease.Template = &cpb.LeaseTemplateUse{Template: tt.template}
			_, err := svc.CreateLease(ctx, &cpb.CreateLeaseRequest{Parent: "namespaces/" + memberTestNamespace, Lease: tt.lease})
			if tt.wantErr {
				requireStatus(t, err, codes.InvalidArgument, "duration is required")
				if leases := listLeases(t, fc); len(leases) != 0 {
					t.Fatalf("expected no lease, got %d", len(leases))
				}
				return
			}
			if err != nil {
				t.Fatalf("expected the lease to be created, got %v", err)
			}
			spec := listLeases(t, fc)[0].Spec
			if spec.Duration == nil || spec.Duration.Duration != tt.want {
				t.Fatalf("expected duration %v, got %v", tt.want, spec.Duration)
			}
		})
	}
}

func TestCreateLeaseFromTemplateSharedWithDefault(t *testing.T) {
	ctx := context.Background()

	t.Run("uses the default without the requesting client", func(t *testing.T) {
		svc, fc := templateService(iviTemplate())
		if _, err := svc.CreateLease(ctx, templateRequest(&cpb.LeaseTemplateUse{Template: "ivi"})); err != nil {
			t.Fatalf("expected the lease to be created, got %v", err)
		}
		shared := listLeases(t, fc)[0].Spec.SharedWith
		if len(shared) != 1 || shared[0] != "lab-support" {
			t.Fatalf("expected [lab-support], got %v", shared)
		}
	})

	t.Run("the request list replaces the default", func(t *testing.T) {
		svc, fc := templateService(iviTemplate())
		req := templateRequest(&cpb.LeaseTemplateUse{Template: "ivi"})
		req.Lease.SharedWith = []string{"alice"}
		if _, err := svc.CreateLease(ctx, req); err != nil {
			t.Fatalf("expected the lease to be created, got %v", err)
		}
		shared := listLeases(t, fc)[0].Spec.SharedWith
		if len(shared) != 1 || shared[0] != "alice" {
			t.Fatalf("expected [alice], got %v", shared)
		}
	})

	t.Run("the shared-client checks apply to the default", func(t *testing.T) {
		template := iviTemplate()
		template.Spec.Defaults.SharedWith = []string{"ghost"}
		svc, fc := templateService(template)
		_, err := svc.CreateLease(ctx, templateRequest(&cpb.LeaseTemplateUse{Template: "ivi"}))
		requireStatus(t, err, codes.InvalidArgument, "ghost")
		if leases := listLeases(t, fc); len(leases) != 0 {
			t.Fatalf("expected no lease, got %d", len(leases))
		}
	})

	t.Run("a template without a default shares with nobody", func(t *testing.T) {
		template := iviTemplate()
		template.Spec.Defaults.SharedWith = nil
		svc, fc := templateService(template)
		if _, err := svc.CreateLease(ctx, templateRequest(&cpb.LeaseTemplateUse{Template: "ivi"})); err != nil {
			t.Fatalf("expected the lease to be created, got %v", err)
		}
		if shared := listLeases(t, fc)[0].Spec.SharedWith; len(shared) != 0 {
			t.Fatalf("expected no shared clients, got %v", shared)
		}
	})
}

func onlineExporter(name string, labels map[string]string) *jumpstarterdevv1alpha1.Exporter {
	return &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: memberTestNamespace, Labels: labels},
		Status: jumpstarterdevv1alpha1.ExporterStatus{
			Conditions: []metav1.Condition{
				{Type: string(jumpstarterdevv1alpha1.ExporterConditionTypeRegistered), Status: metav1.ConditionTrue},
				{Type: string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline), Status: metav1.ConditionTrue},
			},
		},
	}
}

func accessPolicy(name string, exporterLabels, clientLabels map[string]string) *jumpstarterdevv1alpha1.ExporterAccessPolicy {
	return &jumpstarterdevv1alpha1.ExporterAccessPolicy{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: memberTestNamespace},
		Spec: jumpstarterdevv1alpha1.ExporterAccessPolicySpec{
			ExporterSelector: metav1.LabelSelector{MatchLabels: exporterLabels},
			Policies: []jumpstarterdevv1alpha1.Policy{{
				From: []jumpstarterdevv1alpha1.From{{
					ClientSelector: metav1.LabelSelector{MatchLabels: clientLabels},
				}},
			}},
		},
	}
}

func labeledClient(name string, labels map[string]string) *jumpstarterdevv1alpha1.Client {
	return &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: memberTestNamespace, Labels: labels},
	}
}

func availableByRole(template *cpb.LeaseTemplate) map[string]int32 {
	available := make(map[string]int32, len(template.Members))
	for _, member := range template.Members {
		available[member.Member.Name] = member.Available
	}
	return available
}

func TestLeaseTemplateAvailable(t *testing.T) {
	ctx := context.Background()
	headunit := map[string]string{"device-type": "aaos-headunit"}
	phone := map[string]string{"device-type": "phone"}
	disabled := false

	leased := onlineExporter("hu-leased", headunit)
	leased.Status.LeaseRef = &corev1.LocalObjectReference{Name: "other"}
	offline := onlineExporter("hu-offline", headunit)
	offline.Status.Conditions[1].Status = metav1.ConditionFalse
	unregistered := onlineExporter("hu-unregistered", headunit)
	unregistered.Status.Conditions = unregistered.Status.Conditions[1:]
	off := onlineExporter("hu-disabled", headunit)
	off.Spec.Enabled = &disabled
	otherNamespace := onlineExporter("hu-elsewhere", headunit)
	otherNamespace.Namespace = "other"

	exporters := []kclient.Object{
		onlineExporter("hu-1", headunit), onlineExporter("hu-2", headunit),
		leased, offline, unregistered, off, otherNamespace,
		onlineExporter("phone-1", phone),
		onlineExporter("ecu-11", map[string]string{"device-type": "ecu"}),
	}

	t.Run("counts the exporters that the client can lease now", func(t *testing.T) {
		svc := authedClientService("ci-runner", memberTestNamespace,
			testFakeClient(append(exporters, labeledClient("ci-runner", nil), iviTemplate())...))

		template, err := svc.GetLeaseTemplate(ctx, &cpb.GetLeaseTemplateRequest{Name: "namespaces/lab/leasetemplates/ivi"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		got := availableByRole(template)
		if got["headunit"] != 2 || got["phone"] != 1 || got["ecu"] != 1 {
			t.Fatalf("expected headunit=2 phone=1 ecu=1, got %v", got)
		}
		if template.Name != "namespaces/lab/leasetemplates/ivi" || template.Generation != 3 ||
			template.DefaultDuration.AsDuration() != 2*time.Hour || len(template.Constraints) != 2 {
			t.Fatalf("unexpected template %v", template)
		}
	})

	t.Run("applies the access policies of the client", func(t *testing.T) {
		svc := authedClientService("ci-runner", memberTestNamespace,
			testFakeClient(append(exporters,
				labeledClient("ci-runner", map[string]string{"team": "ivi"}),
				accessPolicy("headunits", headunit, map[string]string{"team": "ivi"}),
				accessPolicy("phones", phone, map[string]string{"team": "mobile"}),
				iviTemplate())...))

		template, err := svc.GetLeaseTemplate(ctx, &cpb.GetLeaseTemplateRequest{Name: "namespaces/lab/leasetemplates/ivi"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		// No policy selects the ECU, so no client can lease it.
		got := availableByRole(template)
		if got["headunit"] != 2 || got["phone"] != 0 || got["ecu"] != 0 {
			t.Fatalf("expected headunit=2 phone=0 ecu=0, got %v", got)
		}
	})

	t.Run("an exporterRef member counts a disabled exporter only with allowDisabled", func(t *testing.T) {
		template := iviTemplate()
		template.Spec.Members = []jumpstarterdevv1alpha1.LeaseMember{
			{Name: "strict", ExporterRef: &corev1.LocalObjectReference{Name: "hu-disabled"}},
			{Name: "lenient", ExporterRef: &corev1.LocalObjectReference{Name: "hu-disabled"}, AllowDisabled: true},
			{Name: "missing", ExporterRef: &corev1.LocalObjectReference{Name: "nope"}},
		}
		template.Spec.Constraints = nil
		svc := authedClientService("ci-runner", memberTestNamespace,
			testFakeClient(append(exporters, labeledClient("ci-runner", nil), template)...))

		result, err := svc.GetLeaseTemplate(ctx, &cpb.GetLeaseTemplateRequest{Name: "namespaces/lab/leasetemplates/ivi"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		got := availableByRole(result)
		if got["strict"] != 0 || got["lenient"] != 1 || got["missing"] != 0 {
			t.Fatalf("expected strict=0 lenient=1 missing=0, got %v", got)
		}
	})
}

func TestGetLeaseTemplateErrors(t *testing.T) {
	ctx := context.Background()
	svc, _ := templateService(iviTemplate())

	_, err := svc.GetLeaseTemplate(ctx, &cpb.GetLeaseTemplateRequest{Name: "namespaces/lab/leasetemplates/nope"})
	requireStatus(t, err, codes.NotFound, "nope")

	_, err = svc.GetLeaseTemplate(ctx, &cpb.GetLeaseTemplateRequest{Name: "ivi"})
	requireStatus(t, err, codes.InvalidArgument)

	_, err = svc.GetLeaseTemplate(ctx, &cpb.GetLeaseTemplateRequest{Name: "namespaces/other/leasetemplates/ivi"})
	requireStatus(t, err, codes.PermissionDenied, "namespace mismatch")
}

func TestListLeaseTemplates(t *testing.T) {
	ctx := context.Background()
	projection := iviTemplate()
	projection.Name = "projection"
	projection.Spec.Members = projection.Spec.Members[:2]
	projection.Spec.Constraints = nil
	elsewhere := iviTemplate()
	elsewhere.Namespace = "other"

	svc, _ := templateService(iviTemplate(), projection, elsewhere,
		onlineExporter("phone-1", map[string]string{"device-type": "phone"}))

	response, err := svc.ListLeaseTemplates(ctx, &cpb.ListLeaseTemplatesRequest{Parent: "namespaces/lab"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(response.LeaseTemplates) != 2 {
		t.Fatalf("expected the 2 templates of the namespace, got %v", response.LeaseTemplates)
	}
	byName := make(map[string]*cpb.LeaseTemplate)
	for _, template := range response.LeaseTemplates {
		byName[template.Name] = template
	}
	ivi, ok := byName["namespaces/lab/leasetemplates/ivi"]
	if !ok || len(ivi.Members) != 3 {
		t.Fatalf("unexpected templates %v", byName)
	}
	if got := availableByRole(ivi); got["headunit"] != 0 || got["phone"] != 1 || got["ecu"] != 0 {
		t.Fatalf("expected headunit=0 phone=1 ecu=0, got %v", got)
	}
	if got := availableByRole(byName["namespaces/lab/leasetemplates/projection"]); len(got) != 2 || got["phone"] != 1 {
		t.Fatalf("unexpected availability of projection: %v", got)
	}

	_, err = svc.ListLeaseTemplates(ctx, &cpb.ListLeaseTemplatesRequest{Parent: "namespaces/other"})
	requireStatus(t, err, codes.PermissionDenied, "namespace mismatch")

	empty, _ := templateService()
	response, err = empty.ListLeaseTemplates(ctx, &cpb.ListLeaseTemplatesRequest{Parent: "namespaces/lab"})
	if err != nil || len(response.LeaseTemplates) != 0 {
		t.Fatalf("expected an empty list, got %v, %v", response, err)
	}
}

// boundMemberLease is a member-form lease whose members the controller bound:
// the service reads that state and never writes it.
func boundMemberLease(started bool) *jumpstarterdevv1alpha1.Lease {
	lease := &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{Name: "lease", Namespace: memberTestNamespace},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			ClientRef: corev1.LocalObjectReference{Name: "owner"},
			Duration:  &metav1.Duration{Duration: time.Hour},
			Members: []jumpstarterdevv1alpha1.LeaseMember{
				{Name: "headunit", Selector: &metav1.LabelSelector{MatchLabels: map[string]string{"device-type": "headunit"}}},
				{Name: "phone", Selector: &metav1.LabelSelector{MatchLabels: map[string]string{"device-type": "phone"}}},
				{Name: "camera", Selector: &metav1.LabelSelector{MatchLabels: map[string]string{"device-type": "camera"}}, Optional: true},
			},
		},
	}
	if started {
		lease.Status.BeginTime = &metav1.Time{Time: time.Now()}
		lease.Status.Members = []jumpstarterdevv1alpha1.LeaseMemberStatus{
			{Name: "headunit", State: jumpstarterdevv1alpha1.LeaseMemberStateBound, ExporterRef: &corev1.LocalObjectReference{Name: "hu-1"}},
			{Name: "phone", State: jumpstarterdevv1alpha1.LeaseMemberStateBound, ExporterRef: &corev1.LocalObjectReference{Name: "phone-1"}},
			{Name: "camera", State: jumpstarterdevv1alpha1.LeaseMemberStateOmitted, Reason: "NoAccess"},
		}
	}
	return lease
}

func memberLeaseName() string {
	return utils.UnparseLeaseIdentifier(kclient.ObjectKey{Namespace: memberTestNamespace, Name: "lease"})
}

func memberUpdateService(lease *jumpstarterdevv1alpha1.Lease, targetLabels map[string]string, policies ...kclient.Object) (*ClientService, kclient.Client) {
	objs := []kclient.Object{
		lease,
		labeledClient("owner", map[string]string{"team": "all"}),
		labeledClient("alice", targetLabels),
		onlineExporter("hu-1", map[string]string{"device-type": "headunit"}),
		onlineExporter("phone-1", map[string]string{"device-type": "phone"}),
	}
	fc := testFakeClient(append(objs, policies...)...)
	return authedClientService("owner", memberTestNamespace, fc), fc
}

func TestUpdateLeaseTransferMemberForm(t *testing.T) {
	ctx := context.Background()
	target := "namespaces/lab/clients/alice"
	headunits := accessPolicy("headunits", map[string]string{"device-type": "headunit"}, map[string]string{"headunit": "yes"})
	phones := accessPolicy("phones", map[string]string{"device-type": "phone"}, map[string]string{"phone": "yes"})
	transfer := func(svc *ClientService) (*cpb.Lease, error) {
		return svc.UpdateLease(ctx, &cpb.UpdateLeaseRequest{Lease: &cpb.Lease{Name: memberLeaseName(), Client: &target}})
	}

	t.Run("transfers a started lease without policies", func(t *testing.T) {
		svc, fc := memberUpdateService(boundMemberLease(true), nil)
		got, err := transfer(svc)
		if err != nil {
			t.Fatalf("expected the transfer to succeed, got %v", err)
		}
		if got.GetClient() != target {
			t.Fatalf("expected client %q, got %q", target, got.GetClient())
		}
		if owner := listLeases(t, fc)[0].Spec.ClientRef.Name; owner != "alice" {
			t.Fatalf("expected the lease to belong to alice, got %q", owner)
		}
	})

	t.Run("rejects a lease whose members did not bind", func(t *testing.T) {
		svc, _ := memberUpdateService(boundMemberLease(false), nil)
		_, err := transfer(svc)
		requireStatus(t, err, codes.FailedPrecondition, "has not started")
	})

	t.Run("transfers when the policy lets the target use each bound member", func(t *testing.T) {
		svc, fc := memberUpdateService(boundMemberLease(true),
			map[string]string{"headunit": "yes", "phone": "yes"}, headunits, phones)
		if _, err := transfer(svc); err != nil {
			t.Fatalf("expected the transfer to succeed, got %v", err)
		}
		if owner := listLeases(t, fc)[0].Spec.ClientRef.Name; owner != "alice" {
			t.Fatalf("expected the lease to belong to alice, got %q", owner)
		}
	})

	t.Run("rejects a target that the policy keeps from one member", func(t *testing.T) {
		svc, fc := memberUpdateService(boundMemberLease(true), map[string]string{"headunit": "yes"}, headunits, phones)
		_, err := transfer(svc)
		requireStatus(t, err, codes.PermissionDenied, "alice", "phone-1")
		if owner := listLeases(t, fc)[0].Spec.ClientRef.Name; owner != "owner" {
			t.Fatalf("expected the lease to stay with owner, got %q", owner)
		}
	})

	t.Run("does not check the exporter of a lost member", func(t *testing.T) {
		lease := boundMemberLease(true)
		lease.Status.Members[1].State = jumpstarterdevv1alpha1.LeaseMemberStateLost
		svc, _ := memberUpdateService(lease, map[string]string{"headunit": "yes"}, headunits, phones)
		if _, err := transfer(svc); err != nil {
			t.Fatalf("expected the transfer to succeed, got %v", err)
		}
	})
}

func TestUpdateLeaseShareMemberForm(t *testing.T) {
	ctx := context.Background()
	headunits := accessPolicy("headunits", map[string]string{"device-type": "headunit"}, map[string]string{"headunit": "yes"})
	phones := accessPolicy("phones", map[string]string{"device-type": "phone"}, map[string]string{"phone": "yes"})
	share := func(svc *ClientService) (*cpb.Lease, error) {
		return svc.UpdateLease(ctx, &cpb.UpdateLeaseRequest{
			Lease:         &cpb.Lease{Name: memberLeaseName()},
			AddSharedWith: []string{"alice"},
		})
	}

	t.Run("shares a started lease without policies", func(t *testing.T) {
		svc, fc := memberUpdateService(boundMemberLease(true), nil)
		got, err := share(svc)
		if err != nil {
			t.Fatalf("expected the share to succeed, got %v", err)
		}
		if len(got.SharedWith) != 1 || got.SharedWith[0] != "alice" {
			t.Fatalf("expected shared_with [alice], got %v", got.SharedWith)
		}
		if shared := listLeases(t, fc)[0].Spec.SharedWith; len(shared) != 1 || shared[0] != "alice" {
			t.Fatalf("expected spec.sharedWith [alice], got %v", shared)
		}
	})

	t.Run("shares when the policy lets the client use each bound member", func(t *testing.T) {
		svc, _ := memberUpdateService(boundMemberLease(true),
			map[string]string{"headunit": "yes", "phone": "yes"}, headunits, phones)
		if _, err := share(svc); err != nil {
			t.Fatalf("expected the share to succeed, got %v", err)
		}
	})

	t.Run("rejects a client that the policy keeps from one member", func(t *testing.T) {
		svc, fc := memberUpdateService(boundMemberLease(true), map[string]string{"phone": "yes"}, headunits, phones)
		_, err := share(svc)
		requireStatus(t, err, codes.PermissionDenied, "alice", "hu-1")
		if shared := listLeases(t, fc)[0].Spec.SharedWith; len(shared) != 0 {
			t.Fatalf("expected no shared clients, got %v", shared)
		}
	})

	t.Run("shares before the bind without a policy check", func(t *testing.T) {
		svc, _ := memberUpdateService(boundMemberLease(false), nil, headunits, phones)
		if _, err := share(svc); err != nil {
			t.Fatalf("expected the share to succeed, got %v", err)
		}
	})
}

func TestUpdateLeaseMemberFormBeginTime(t *testing.T) {
	ctx := context.Background()
	update := func(svc *ClientService) error {
		_, err := svc.UpdateLease(ctx, &cpb.UpdateLeaseRequest{Lease: &cpb.Lease{
			Name:      memberLeaseName(),
			BeginTime: timestamppb.New(time.Now().Add(time.Hour)),
			Duration:  durationpb.New(time.Hour),
		}})
		return err
	}

	started, _ := memberUpdateService(boundMemberLease(true), nil)
	requireStatus(t, update(started), codes.FailedPrecondition, "lease has already started")

	pending, _ := memberUpdateService(boundMemberLease(false), nil)
	if err := update(pending); err != nil {
		t.Fatalf("expected the update to succeed before the bind, got %v", err)
	}
}

func TestUpdateLeaseRejectsImmutableFields(t *testing.T) {
	ctx := context.Background()
	for _, path := range []string{"members", "constraints", "template", "template.member_selectors", "members.selector"} {
		t.Run(path, func(t *testing.T) {
			svc, fc := memberUpdateService(boundMemberLease(true), nil)
			_, err := svc.UpdateLease(ctx, &cpb.UpdateLeaseRequest{
				Lease:      &cpb.Lease{Name: memberLeaseName(), Duration: durationpb.New(2 * time.Hour)},
				UpdateMask: &fieldmaskpb.FieldMask{Paths: []string{"duration", path}},
			})
			field, _, _ := strings.Cut(path, ".")
			requireStatus(t, err, codes.InvalidArgument, field, "immutable")
			if got := listLeases(t, fc)[0].Spec.Duration.Duration; got != time.Hour {
				t.Fatalf("expected the lease to stay as it was, got duration %v", got)
			}
		})
	}

	t.Run("a mask of mutable fields is accepted", func(t *testing.T) {
		svc, _ := memberUpdateService(boundMemberLease(true), nil)
		_, err := svc.UpdateLease(ctx, &cpb.UpdateLeaseRequest{
			Lease:      &cpb.Lease{Name: memberLeaseName(), Duration: durationpb.New(2 * time.Hour)},
			UpdateMask: &fieldmaskpb.FieldMask{Paths: []string{"duration"}},
		})
		if err != nil {
			t.Fatalf("expected the update to succeed, got %v", err)
		}
	})
}

func TestGetAndListMemberFormLeases(t *testing.T) {
	ctx := context.Background()
	scalar := &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{Name: "scalar", Namespace: memberTestNamespace, Labels: map[string]string{"board": "rpi4"}},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			ClientRef: corev1.LocalObjectReference{Name: "owner"},
			Selector:  metav1.LabelSelector{MatchLabels: map[string]string{"board": "rpi4"}},
		},
	}
	svc, _ := memberUpdateService(boundMemberLease(true), nil, scalar)
	svc.deprecatedMessages = map[string]string{"device-type": "use kind"}

	got, err := svc.GetLease(ctx, &cpb.GetLeaseRequest{Name: memberLeaseName()})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if got.Selector != "" || got.Exporter != nil || len(got.Members) != 3 || len(got.MemberStatuses) != 3 {
		t.Fatalf("unexpected lease %v", got)
	}
	if got.MemberStatuses[0].GetExporter() != "namespaces/lab/exporters/hu-1" {
		t.Fatalf("unexpected member status %v", got.MemberStatuses[0])
	}
	if got.DeprecatedLabels["device-type"] != "use kind" {
		t.Fatalf("expected the deprecated label of a member selector, got %v", got.DeprecatedLabels)
	}

	all, err := svc.ListLeases(ctx, &cpb.ListLeasesRequest{Parent: "namespaces/lab"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(all.Leases) != 2 {
		t.Fatalf("expected 2 leases, got %v", all.Leases)
	}

	// A selector filter matches the labels of a scalar lease; a member-form
	// lease has none.
	filtered, err := svc.ListLeases(ctx, &cpb.ListLeasesRequest{Parent: "namespaces/lab", Filter: "board=rpi4"})
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}
	if len(filtered.Leases) != 1 || filtered.Leases[0].Name != "namespaces/lab/leases/scalar" {
		t.Fatalf("expected only the scalar lease, got %v", filtered.Leases)
	}
}
