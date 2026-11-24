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
	"maps"
	"slices"
	"strings"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/utils"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/proto"
	"google.golang.org/protobuf/types/known/durationpb"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/types"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

// templateExpansion is a template request turned into the parts of a
// member-form lease.
type templateExpansion struct {
	// request is the lease of the request with the template defaults applied,
	// for the fields that a scalar request has too.
	request     *cpb.Lease
	members     []jumpstarterdevv1alpha1.LeaseMember
	constraints []jumpstarterdevv1alpha1.LeaseConstraint
	// forwards are the forwards of the template, without changes. A forward
	// with an endpoint on an excluded role stays: the controller disables it,
	// or serves its other endpoints.
	forwards    []jumpstarterdevv1alpha1.LeaseForward
	templateRef *jumpstarterdevv1alpha1.LeaseTemplateReference
}

// parseLeaseTemplateName accepts a bare template name or a resource name in
// the given namespace.
func parseLeaseTemplateName(identifier, namespace string) (string, error) {
	if !strings.Contains(identifier, "/") {
		return identifier, nil
	}
	key, err := utils.ParseObjectIdentifier(identifier, jumpstarterdevv1alpha1.LeaseTemplateResource)
	if err != nil {
		return "", err
	}
	if key.Namespace != namespace {
		return "", status.Errorf(codes.InvalidArgument,
			"template %q is not in the namespace of the request (%s)", identifier, namespace)
	}
	return key.Name, nil
}

func (s *ClientService) getLeaseTemplate(
	ctx context.Context,
	namespace, name string,
) (*jumpstarterdevv1alpha1.LeaseTemplate, error) {
	var template jumpstarterdevv1alpha1.LeaseTemplate
	if err := s.Get(ctx, types.NamespacedName{Namespace: namespace, Name: name}, &template); err != nil {
		if apierrors.IsNotFound(err) {
			return nil, status.Errorf(codes.NotFound, "lease template %q not found", name)
		}
		return nil, status.Errorf(codes.Internal, "failed to get lease template %q: %v", name, err)
	}
	return &template, nil
}

// expandLeaseTemplate turns a template request into a member-form lease. A
// request can only make the template narrower: it excludes optional roles and
// adds labels to the selector of a role.
func (s *ClientService) expandLeaseTemplate(
	ctx context.Context,
	namespace, clientName string,
	lease *cpb.Lease,
) (*templateExpansion, error) {
	use := lease.Template
	name, err := parseLeaseTemplateName(use.Template, namespace)
	if err != nil {
		return nil, err
	}
	template, err := s.getLeaseTemplate(ctx, namespace, name)
	if err != nil {
		return nil, err
	}

	members, err := expandTemplateMembers(template, use)
	if err != nil {
		return nil, err
	}

	request := proto.Clone(lease).(*cpb.Lease)
	request.Template = nil
	// A request with a begin time and an end time has its duration already.
	if request.Duration == nil && (request.BeginTime == nil || request.EndTime == nil) &&
		template.Spec.Defaults.Duration != nil {
		request.Duration = durationpb.New(template.Spec.Defaults.Duration.Duration)
	}
	if len(request.SharedWith) == 0 {
		request.SharedWith = slices.DeleteFunc(
			slices.Clone(template.Spec.Defaults.SharedWith),
			func(shared string) bool { return shared == clientName },
		)
	}

	var constraints []jumpstarterdevv1alpha1.LeaseConstraint
	for i := range template.Spec.Constraints {
		constraints = append(constraints, *template.Spec.Constraints[i].DeepCopy())
	}

	var forwards []jumpstarterdevv1alpha1.LeaseForward
	for i := range template.Spec.Forwards {
		forwards = append(forwards, *template.Spec.Forwards[i].DeepCopy())
	}

	return &templateExpansion{
		request:     request,
		members:     members,
		constraints: constraints,
		forwards:    forwards,
		templateRef: &jumpstarterdevv1alpha1.LeaseTemplateReference{
			Name:       template.Name,
			Generation: template.Generation,
		},
	}, nil
}

// expandTemplateMembers copies the members of the template and applies the
// excluded roles and the narrower selectors of the request.
func expandTemplateMembers(
	template *jumpstarterdevv1alpha1.LeaseTemplate,
	use *cpb.LeaseTemplateUse,
) ([]jumpstarterdevv1alpha1.LeaseMember, error) {
	members := make([]jumpstarterdevv1alpha1.LeaseMember, len(template.Spec.Members))
	byName := make(map[string]*jumpstarterdevv1alpha1.LeaseMember, len(members))
	for i := range template.Spec.Members {
		members[i] = *template.Spec.Members[i].DeepCopy()
		byName[members[i].Name] = &members[i]
	}

	for _, role := range use.ExcludedMembers {
		member, ok := byName[role]
		if !ok {
			return nil, status.Errorf(codes.InvalidArgument,
				"template.excluded_members: template %q has no role %q", template.Name, role)
		}
		if !member.Optional {
			return nil, status.Errorf(codes.InvalidArgument,
				"template.excluded_members: role %q is not optional", role)
		}
		member.Excluded = true
	}

	// Sorted, so that the same request gives the same error.
	for _, role := range slices.Sorted(maps.Keys(use.MemberSelectors)) {
		member, ok := byName[role]
		if !ok {
			return nil, status.Errorf(codes.InvalidArgument,
				"template.member_selectors: template %q has no role %q", template.Name, role)
		}
		if member.Excluded {
			return nil, status.Errorf(codes.InvalidArgument,
				"template.member_selectors: role %q is also in excluded_members", role)
		}
		if member.ExporterRef != nil && member.ExporterRef.Name != "" {
			return nil, status.Errorf(codes.InvalidArgument,
				"template.member_selectors: role %q names an exporter and cannot be made narrower", role)
		}
		narrower, err := jumpstarterdevv1alpha1.ParseLabelSelector(use.MemberSelectors[role])
		if err != nil {
			return nil, status.Errorf(codes.InvalidArgument, "template.member_selectors[%s]: %v", role, err)
		}
		if len(narrower.MatchLabels) == 0 && len(narrower.MatchExpressions) == 0 {
			return nil, status.Errorf(codes.InvalidArgument, "template.member_selectors[%s]: selector is empty", role)
		}
		if member.Selector == nil {
			member.Selector = &metav1.LabelSelector{}
		}
		for _, key := range slices.Sorted(maps.Keys(narrower.MatchLabels)) {
			value := narrower.MatchLabels[key]
			if templateValue, ok := member.Selector.MatchLabels[key]; ok && templateValue != value {
				return nil, status.Errorf(codes.InvalidArgument,
					"template.member_selectors[%s]: label %q is %q in the template and cannot be changed to %q",
					role, key, templateValue, value)
			}
			if member.Selector.MatchLabels == nil {
				member.Selector.MatchLabels = make(map[string]string)
			}
			member.Selector.MatchLabels[key] = value
		}
		member.Selector.MatchExpressions = append(member.Selector.MatchExpressions, narrower.MatchExpressions...)
	}

	return members, nil
}

func (s *ClientService) GetLeaseTemplate(ctx context.Context, req *cpb.GetLeaseTemplateRequest) (*cpb.LeaseTemplate, error) {
	key, err := utils.ParseObjectIdentifier(req.Name, jumpstarterdevv1alpha1.LeaseTemplateResource)
	if err != nil {
		return nil, err
	}

	jclient, err := s.AuthClient(ctx, key.Namespace)
	if err != nil {
		return nil, err
	}

	template, err := s.getLeaseTemplate(ctx, key.Namespace, key.Name)
	if err != nil {
		return nil, err
	}

	counter, err := s.newAvailabilityCounter(ctx, jclient)
	if err != nil {
		return nil, err
	}
	return counter.templateToProtobuf(template)
}

func (s *ClientService) ListLeaseTemplates(ctx context.Context, req *cpb.ListLeaseTemplatesRequest) (*cpb.ListLeaseTemplatesResponse, error) {
	namespace, err := utils.ParseNamespaceIdentifier(req.Parent)
	if err != nil {
		return nil, err
	}

	jclient, err := s.AuthClient(ctx, namespace)
	if err != nil {
		return nil, err
	}

	var templates jumpstarterdevv1alpha1.LeaseTemplateList
	if err := s.List(ctx, &templates, &kclient.ListOptions{
		Namespace: namespace,
		Limit:     int64(req.PageSize),
		Continue:  req.PageToken,
	}); err != nil {
		return nil, err
	}

	response := &cpb.ListLeaseTemplatesResponse{NextPageToken: templates.Continue}
	if len(templates.Items) == 0 {
		return response, nil
	}
	counter, err := s.newAvailabilityCounter(ctx, jclient)
	if err != nil {
		return nil, err
	}
	for i := range templates.Items {
		template, err := counter.templateToProtobuf(&templates.Items[i])
		if err != nil {
			return nil, err
		}
		response.LeaseTemplates = append(response.LeaseTemplates, template)
	}
	return response, nil
}

// availabilityCounter counts, for a template member, the exporters that a
// client can lease now. It is a guide for a person who selects a template: it
// looks at each member by itself, and does not assign exporters to members or
// apply the constraints, the exclusion groups or the duration limit of a policy.
type availabilityCounter struct {
	client    *jumpstarterdevv1alpha1.Client
	policies  []jumpstarterdevv1alpha1.ExporterAccessPolicy
	exporters []jumpstarterdevv1alpha1.Exporter
	// allowed caches the policy result for each index of exporters.
	allowed map[int]bool
}

func (s *ClientService) newAvailabilityCounter(
	ctx context.Context,
	jclient *jumpstarterdevv1alpha1.Client,
) (*availabilityCounter, error) {
	var exporters jumpstarterdevv1alpha1.ExporterList
	if err := s.List(ctx, &exporters, kclient.InNamespace(jclient.Namespace)); err != nil {
		return nil, status.Errorf(codes.Internal, "failed to list exporters: %v", err)
	}
	var policies jumpstarterdevv1alpha1.ExporterAccessPolicyList
	if err := s.List(ctx, &policies, kclient.InNamespace(jclient.Namespace)); err != nil {
		return nil, status.Errorf(codes.Internal, "failed to list access policies: %v", err)
	}
	return &availabilityCounter{
		client:    jclient,
		policies:  policies.Items,
		exporters: exporters.Items,
		allowed:   make(map[int]bool),
	}, nil
}

func (c *availabilityCounter) templateToProtobuf(template *jumpstarterdevv1alpha1.LeaseTemplate) (*cpb.LeaseTemplate, error) {
	result := template.ToProtobuf()
	for i := range template.Spec.Members {
		available, err := c.count(&template.Spec.Members[i])
		if err != nil {
			return nil, err
		}
		result.Members[i].Available = available
	}
	return result, nil
}

func (c *availabilityCounter) count(member *jumpstarterdevv1alpha1.LeaseMember) (int32, error) {
	var selector labels.Selector
	if member.ExporterRef == nil && member.Selector != nil {
		var err error
		selector, err = metav1.LabelSelectorAsSelector(member.Selector)
		if err != nil {
			return 0, status.Errorf(codes.Internal, "invalid selector of member %q: %v", member.Name, err)
		}
	}

	var available int32
	for i := range c.exporters {
		exporter := &c.exporters[i]
		switch {
		case member.ExporterRef != nil:
			if exporter.Name != member.ExporterRef.Name {
				continue
			}
			if !exporter.IsEnabled() && !member.AllowDisabled {
				continue
			}
		case selector != nil:
			if !selector.Matches(labels.Set(exporter.Labels)) || !exporter.IsEnabled() {
				continue
			}
		default:
			continue
		}
		if exporter.Status.LeaseRef != nil || !exporterOnline(exporter) {
			continue
		}
		allowed, err := c.allowedByPolicy(i)
		if err != nil {
			return 0, err
		}
		if allowed {
			available++
		}
	}
	return available, nil
}

// allowedByPolicy applies the access policies as the lease controller does:
// without policies in the namespace, each client can lease each exporter.
func (c *availabilityCounter) allowedByPolicy(i int) (bool, error) {
	if len(c.policies) == 0 {
		return true, nil
	}
	if allowed, ok := c.allowed[i]; ok {
		return allowed, nil
	}
	allowed, err := jumpstarterdevv1alpha1.ClientAllowedByPolicy(c.policies, &c.exporters[i], c.client)
	if err != nil {
		return false, status.Errorf(codes.Internal, "failed to evaluate access policies: %v", err)
	}
	c.allowed[i] = allowed
	return allowed, nil
}

func exporterOnline(exporter *jumpstarterdevv1alpha1.Exporter) bool {
	return meta.IsStatusConditionTrue(exporter.Status.Conditions,
		string(jumpstarterdevv1alpha1.ExporterConditionTypeRegistered)) &&
		meta.IsStatusConditionTrue(exporter.Status.Conditions,
			string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline))
}
