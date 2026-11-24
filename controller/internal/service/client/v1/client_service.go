/*
Copyright 2024.

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
	"slices"
	"strings"

	"github.com/google/uuid"
	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/auth"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/utils"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/emptypb"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/selection"
	"k8s.io/apimachinery/pkg/types"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

type ClientService struct {
	cpb.UnimplementedClientServiceServer
	kclient.Client
	auth.Auth
	MaxTags            int32
	Signer             *oidc.Signer
	hiddenLabelSet     map[string]struct{}
	deprecatedLabelSet map[string]struct{}
	deprecatedMessages map[string]string
}

func NewClientService(client kclient.Client, auth auth.Auth, maxTags int32, signer *oidc.Signer, hiddenLabelKeys []string, deprecatedLabelKeys map[string]string) *ClientService {
	hiddenSet := make(map[string]struct{}, len(hiddenLabelKeys))
	for _, k := range hiddenLabelKeys {
		hiddenSet[k] = struct{}{}
	}
	deprecatedSet := make(map[string]struct{}, len(deprecatedLabelKeys))
	for k := range deprecatedLabelKeys {
		deprecatedSet[k] = struct{}{}
	}
	return &ClientService{
		Client:             client,
		Auth:               auth,
		MaxTags:            maxTags,
		Signer:             signer,
		hiddenLabelSet:     hiddenSet,
		deprecatedLabelSet: deprecatedSet,
		deprecatedMessages: deprecatedLabelKeys,
	}
}

func annotateDeprecatedLabels(exporter *cpb.Exporter, deprecatedMessages map[string]string) {
	if len(deprecatedMessages) == 0 || len(exporter.Labels) == 0 {
		return
	}
	if exporter.DeprecatedLabels == nil {
		exporter.DeprecatedLabels = make(map[string]string)
	}
	for k := range exporter.Labels {
		if msg, deprecated := deprecatedMessages[k]; deprecated {
			exporter.DeprecatedLabels[k] = msg
		}
	}
}

func annotateLeaseDeprecatedLabels(lease *cpb.Lease, deprecatedMessages map[string]string) {
	if len(deprecatedMessages) == 0 {
		return
	}
	selectors := []string{lease.Selector}
	for _, member := range lease.Members {
		selectors = append(selectors, member.GetSelector())
	}
	for _, selectorStr := range selectors {
		if selectorStr == "" {
			continue
		}
		selector, err := labels.Parse(selectorStr)
		if err != nil {
			continue
		}
		requirements, _ := selector.Requirements()
		for _, r := range requirements {
			if msg, deprecated := deprecatedMessages[r.Key()]; deprecated {
				if lease.DeprecatedLabels == nil {
					lease.DeprecatedLabels = make(map[string]string)
				}
				lease.DeprecatedLabels[r.Key()] = msg
			}
		}
	}
}

func filterHiddenLabels(exporter *cpb.Exporter, hiddenSet map[string]struct{}, showHidden bool) {
	if showHidden || len(hiddenSet) == 0 || len(exporter.Labels) == 0 {
		return
	}
	filtered := make(map[string]string, len(exporter.Labels))
	for k, v := range exporter.Labels {
		if _, hidden := hiddenSet[k]; !hidden {
			filtered[k] = v
		}
	}
	exporter.Labels = filtered
}

func (s *ClientService) GetExporter(
	ctx context.Context,
	req *cpb.GetExporterRequest,
) (*cpb.Exporter, error) {
	key, err := utils.ParseExporterIdentifier(req.Name)
	if err != nil {
		return nil, err
	}

	_, err = s.AuthClient(ctx, key.Namespace)
	if err != nil {
		return nil, err
	}

	var jexporter jumpstarterdevv1alpha1.Exporter
	if err := s.Get(ctx, *key, &jexporter); err != nil {
		return nil, err
	}

	result := jexporter.ToProtobuf()
	annotateDeprecatedLabels(result, s.deprecatedMessages)
	filterHiddenLabels(result, s.hiddenLabelSet, req.ShowHiddenLabels)
	filterHiddenLabels(result, s.deprecatedLabelSet, req.ShowHiddenLabels)
	return result, nil
}

func (s *ClientService) ListExporters(
	ctx context.Context,
	req *cpb.ListExportersRequest,
) (*cpb.ListExportersResponse, error) {
	namespace, err := utils.ParseNamespaceIdentifier(req.Parent)
	if err != nil {
		return nil, err
	}

	_, err = s.AuthClient(ctx, namespace)
	if err != nil {
		return nil, err
	}

	selector, err := labels.Parse(req.Filter)
	if err != nil {
		return nil, err
	}

	var jexporters jumpstarterdevv1alpha1.ExporterList
	if err := s.List(ctx, &jexporters, &kclient.ListOptions{
		Namespace:     namespace,
		LabelSelector: selector,
		Limit:         int64(req.PageSize),
		Continue:      req.PageToken,
	}); err != nil {
		return nil, err
	}

	response := jexporters.ToProtobuf()
	for _, exp := range response.Exporters {
		annotateDeprecatedLabels(exp, s.deprecatedMessages)
		filterHiddenLabels(exp, s.hiddenLabelSet, req.ShowHiddenLabels)
		filterHiddenLabels(exp, s.deprecatedLabelSet, req.ShowHiddenLabels)
	}
	return response, nil
}

func (s *ClientService) GetLease(ctx context.Context, req *cpb.GetLeaseRequest) (*cpb.Lease, error) {
	key, err := utils.ParseLeaseIdentifier(req.Name)
	if err != nil {
		return nil, err
	}

	_, err = s.AuthClient(ctx, key.Namespace)
	if err != nil {
		return nil, err
	}

	var jlease jumpstarterdevv1alpha1.Lease
	if err := s.Get(ctx, *key, &jlease); err != nil {
		return nil, err
	}

	result := jlease.ToProtobuf()
	annotateLeaseDeprecatedLabels(result, s.deprecatedMessages)
	return result, nil
}

func (s *ClientService) ListLeases(ctx context.Context, req *cpb.ListLeasesRequest) (*cpb.ListLeasesResponse, error) {
	namespace, err := utils.ParseNamespaceIdentifier(req.Parent)
	if err != nil {
		return nil, err
	}

	_, err = s.AuthClient(ctx, namespace)
	if err != nil {
		return nil, err
	}

	selector, err := labels.Parse(req.Filter)
	if err != nil {
		return nil, err
	}

	// Apply user tag filter by auto-prefixing keys with metadata.jumpstarter.dev/
	if req.TagFilter != "" {
		tagSelector, err := labels.Parse(req.TagFilter)
		if err != nil {
			return nil, status.Errorf(codes.InvalidArgument, "invalid tag_filter: %v", err)
		}
		requirements, _ := tagSelector.Requirements()
		for _, r := range requirements {
			prefixedKey := jumpstarterdevv1alpha1.LeaseTagMetadataPrefix + r.Key()
			requirement, err := labels.NewRequirement(
				prefixedKey,
				r.Operator(),
				r.ValuesUnsorted(),
			)
			if err != nil {
				return nil, status.Errorf(codes.InvalidArgument, "invalid tag_filter requirement: %v", err)
			}
			selector = selector.Add(*requirement)
		}
	}

	// Apply active-only filter by default (when only_active is nil or true)
	// We must combine this with the user's filter selector into a single
	// MatchingLabelsSelector, because multiple MatchingLabelsSelector options
	// would override each other instead of being ANDed together.
	if req.OnlyActive == nil || *req.OnlyActive {
		requirement, err := labels.NewRequirement(
			string(jumpstarterdevv1alpha1.LeaseLabelEnded),
			selection.DoesNotExist,
			[]string{},
		)
		if err != nil {
			return nil, err
		}
		selector = selector.Add(*requirement)
	}

	listOptions := []kclient.ListOption{
		kclient.InNamespace(namespace),
		kclient.MatchingLabelsSelector{Selector: selector},
		kclient.Limit(int64(req.PageSize)),
		kclient.Continue(req.PageToken),
	}

	var jleases jumpstarterdevv1alpha1.LeaseList
	if err := s.List(ctx, &jleases, listOptions...); err != nil {
		return nil, err
	}

	var results []*cpb.Lease
	for _, lease := range jleases.Items {
		result := lease.ToProtobuf()
		annotateLeaseDeprecatedLabels(result, s.deprecatedMessages)
		results = append(results, result)
	}

	return &cpb.ListLeasesResponse{
		Leases:        results,
		NextPageToken: jleases.Continue,
	}, nil
}

// CreateLease validates and persists a lease request for the authenticated client.
func (s *ClientService) CreateLease(ctx context.Context, req *cpb.CreateLeaseRequest) (*cpb.Lease, error) {
	if req == nil {
		return nil, status.Error(codes.InvalidArgument, "request is required")
	}

	if err := validateLeaseTarget(req.Lease); err != nil {
		return nil, err
	}

	if err := jumpstarterdevv1alpha1.ValidateLeaseTags(req.Lease.Tags, int(s.MaxTags)); err != nil {
		return nil, status.Errorf(codes.InvalidArgument, "invalid lease tags: %v", err)
	}

	namespace, err := utils.ParseNamespaceIdentifier(req.Parent)
	if err != nil {
		return nil, err
	}

	jclient, err := s.AuthClient(ctx, namespace)
	if err != nil {
		return nil, err
	}

	// A template request becomes a member-form request here: the rest of this
	// function handles both alike.
	leaseReq := req.Lease
	var expansion *templateExpansion
	if req.Lease.Template != nil {
		expansion, err = s.expandLeaseTemplate(ctx, namespace, jclient.Name, req.Lease)
		if err != nil {
			return nil, err
		}
		leaseReq = expansion.request
	}

	if err := s.validateExplicitExporter(ctx, namespace, leaseReq); err != nil {
		return nil, err
	}

	// Use provided lease_id if specified, otherwise generate a UUIDv7
	name := req.LeaseId
	if name == "" {
		id, err := uuid.NewV7()
		if err != nil {
			return nil, err
		}
		name = id.String()
	}

	jlease, err := jumpstarterdevv1alpha1.LeaseFromProtobuf(leaseReq, types.NamespacedName{
		Namespace: namespace,
		Name:      name,
	}, corev1.LocalObjectReference{
		Name: jclient.Name,
	})
	if err != nil {
		if expansion != nil || len(leaseReq.Members) > 0 {
			return nil, status.Errorf(codes.InvalidArgument, "invalid lease: %v", err)
		}
		return nil, err
	}
	if expansion != nil {
		jlease.Spec.Members = expansion.members
		jlease.Spec.Constraints = expansion.constraints
		jlease.Spec.TemplateRef = expansion.templateRef
	}
	if err := s.validateMemberExporters(ctx, namespace, jlease.Spec.Members); err != nil {
		return nil, err
	}

	if len(jlease.Spec.SharedWith) > 0 {
		// Deduplicate first, then enforce the limit against the deduplicated list,
		// so a request with repeated names that collapses to a list within the
		// limit is accepted rather than rejected on its raw length.
		var deduped []string
		for _, name := range jlease.Spec.SharedWith {
			if name == jclient.Name {
				return nil, status.Errorf(codes.InvalidArgument, "cannot share lease with the owner")
			}
			if slices.Contains(deduped, name) {
				continue
			}
			var sharedClient jumpstarterdevv1alpha1.Client
			if err := s.Get(ctx, types.NamespacedName{Namespace: namespace, Name: name}, &sharedClient); err != nil {
				if apierrors.IsNotFound(err) {
					return nil, status.Errorf(codes.InvalidArgument, "shared client %q not found", name)
				}
				return nil, status.Errorf(codes.Internal, "failed to get shared client %q: %v", name, err)
			}
			deduped = append(deduped, name)
		}
		if len(deduped) > jumpstarterdevv1alpha1.MaxSharedWithEntries {
			return nil, status.Errorf(codes.InvalidArgument, "shared_with list exceeds maximum of %d entries", jumpstarterdevv1alpha1.MaxSharedWithEntries)
		}
		jlease.Spec.SharedWith = deduped
	}

	if err := s.Create(ctx, jlease); err != nil {
		// The CRD rules for members are part of this request's validation.
		if jlease.IsMemberForm() && apierrors.IsInvalid(err) {
			return nil, status.Errorf(codes.InvalidArgument, "invalid lease: %v", err)
		}
		return nil, err
	}

	result := jlease.ToProtobuf()
	annotateLeaseDeprecatedLabels(result, s.deprecatedMessages)
	return result, nil
}

// validateExplicitExporter rejects a known disabled exporter before creating a
// lease. Selector-based requests and missing exporters remain asynchronous so
// the controller can continue to handle queueing and discovery normally.
func (s *ClientService) validateExplicitExporter(
	ctx context.Context,
	namespace string,
	lease *cpb.Lease,
) error {
	if lease.AllowDisabled || lease.ExporterName == nil || *lease.ExporterName == "" {
		return nil
	}

	var exporter jumpstarterdevv1alpha1.Exporter
	err := s.Get(ctx, types.NamespacedName{Namespace: namespace, Name: *lease.ExporterName}, &exporter)
	if err != nil {
		if apierrors.IsNotFound(err) {
			return nil
		}
		return err
	}

	if err := jumpstarterdevv1alpha1.ValidateExporterEnabledForLease(&exporter, lease.AllowDisabled); err != nil {
		return status.Error(codes.FailedPrecondition, err.Error())
	}
	return nil
}

// validateMemberExporters does for the members what validateExplicitExporter
// does for a scalar request: it rejects a required member that names a known
// disabled exporter without allowDisabled. An optional member with such an
// exporter is omitted by the controller, and a missing exporter stays
// asynchronous.
func (s *ClientService) validateMemberExporters(
	ctx context.Context,
	namespace string,
	members []jumpstarterdevv1alpha1.LeaseMember,
) error {
	for i := range members {
		member := &members[i]
		if member.ExporterRef == nil || member.Optional || member.Excluded || member.AllowDisabled {
			continue
		}
		var exporter jumpstarterdevv1alpha1.Exporter
		err := s.Get(ctx, types.NamespacedName{Namespace: namespace, Name: member.ExporterRef.Name}, &exporter)
		if err != nil {
			if apierrors.IsNotFound(err) {
				continue
			}
			return err
		}
		if err := jumpstarterdevv1alpha1.ValidateExporterEnabledForLease(&exporter, false); err != nil {
			return status.Errorf(codes.FailedPrecondition, "members[%s]: %v", member.Name, err)
		}
	}
	return nil
}

func validateLeaseTarget(lease *cpb.Lease) error {
	if lease == nil {
		return status.Error(codes.InvalidArgument, "lease is required")
	}

	// A request has exactly one form: scalar (selector and/or exporter_name),
	// template, or members (with optional constraints).
	var scalar []string
	if lease.Selector != "" {
		scalar = append(scalar, "selector")
	}
	if lease.ExporterName != nil && *lease.ExporterName != "" {
		scalar = append(scalar, "exporter_name")
	}
	var memberForm []string
	if len(lease.Members) > 0 {
		memberForm = append(memberForm, "members")
	}
	if len(lease.Constraints) > 0 {
		memberForm = append(memberForm, "constraints")
	}
	hasTemplate := lease.Template != nil

	switch {
	case hasTemplate && len(scalar)+len(memberForm) > 0:
		return status.Errorf(codes.InvalidArgument,
			"template cannot be used with %s: a lease request sets only one of selector/exporter_name, template, or members",
			strings.Join(append(scalar, memberForm...), ", "))
	case len(scalar) > 0 && len(memberForm) > 0:
		return status.Errorf(codes.InvalidArgument,
			"%s cannot be used with %s: a lease request sets only one of selector/exporter_name, template, or members",
			strings.Join(memberForm, ", "), strings.Join(scalar, ", "))
	case hasTemplate:
		if lease.Template.Template == "" {
			return status.Error(codes.InvalidArgument, "template.template is required")
		}
		return nil
	case len(lease.Members) > 0:
		return validateRequestedMembers(lease)
	case len(lease.Constraints) > 0:
		return status.Error(codes.InvalidArgument, "constraints requires members")
	case len(scalar) == 0:
		return status.Error(codes.InvalidArgument, "one of selector or exporter_name is required")
	}

	return nil
}

// validateRequestedMembers checks the members and constraints of a request
// that gives them itself, without a template.
func validateRequestedMembers(lease *cpb.Lease) error {
	names := make(map[string]struct{}, len(lease.Members))
	for i, member := range lease.Members {
		if member.GetName() == "" {
			return status.Errorf(codes.InvalidArgument, "members[%d].name is required", i)
		}
		if _, duplicate := names[member.Name]; duplicate {
			return status.Errorf(codes.InvalidArgument, "members: duplicate role %q", member.Name)
		}
		names[member.Name] = struct{}{}
		if member.Excluded {
			return status.Errorf(codes.InvalidArgument,
				"members[%s].excluded cannot be set in a request: only template expansion sets it", member.Name)
		}
		if member.GetSelector() == "" && member.GetExporterName() == "" {
			return status.Errorf(codes.InvalidArgument,
				"members[%s]: one of selector or exporter_name is required", member.Name)
		}
	}
	// A lease with only optional members can bind no exporter. A template
	// always has a required member, and a request cannot exclude it.
	if !slices.ContainsFunc(lease.Members, func(member *cpb.LeaseMember) bool { return !member.GetOptional() }) {
		return status.Error(codes.InvalidArgument, "members: a lease must have a required member")
	}
	for i, constraint := range lease.Constraints {
		if constraint.GetSameLabel() == "" {
			return status.Errorf(codes.InvalidArgument, "constraints[%d].same_label is required", i)
		}
		for _, name := range constraint.MemberNames {
			if _, declared := names[name]; !declared {
				return status.Errorf(codes.InvalidArgument,
					"constraints[%d].member_names: %q is not a member of the lease", i, name)
			}
		}
	}
	return nil
}

// immutableLeaseFields are the Lease fields of a member-form lease that an
// update mask cannot name.
var immutableLeaseFields = []string{"members", "constraints", "template"}

func (s *ClientService) UpdateLease(ctx context.Context, req *cpb.UpdateLeaseRequest) (*cpb.Lease, error) {
	key, err := utils.ParseLeaseIdentifier(req.Lease.Name)
	if err != nil {
		return nil, err
	}

	jclient, err := s.AuthClient(ctx, key.Namespace)
	if err != nil {
		return nil, err
	}

	var jlease jumpstarterdevv1alpha1.Lease
	if err := s.Get(ctx, *key, &jlease); err != nil {
		return nil, err
	}

	for _, path := range req.GetUpdateMask().GetPaths() {
		field, _, _ := strings.Cut(path, ".")
		if slices.Contains(immutableLeaseFields, field) {
			return nil, status.Errorf(codes.InvalidArgument, "UpdateLease: %s is immutable", field)
		}
	}

	hasShareChanges := len(req.AddSharedWith) > 0 || len(req.RemoveSharedWith) > 0
	hasTransfer := req.Lease.Client != nil && *req.Lease.Client != ""

	if hasTransfer && hasShareChanges {
		return nil, status.Error(codes.InvalidArgument, "UpdateLease: cannot transfer and modify sharing in the same request")
	}

	// Sharing is modified only via add_shared_with/remove_shared_with. Reject a
	// populated lease.shared_with rather than silently dropping it, so callers
	// don't believe a wholesale replacement took effect.
	if len(req.Lease.SharedWith) > 0 {
		return nil, status.Error(codes.InvalidArgument, "UpdateLease: use add_shared_with/remove_shared_with to modify sharing, not lease.shared_with")
	}

	if hasShareChanges {
		if !jlease.IsOwnedBy(jclient.Name) {
			return nil, status.Error(codes.PermissionDenied, "UpdateLease permission denied: only lease owner can modify sharing")
		}
	} else if !jlease.IsAccessibleBy(jclient.Name) {
		return nil, status.Error(codes.PermissionDenied, "UpdateLease permission denied")
	}

	// Share changes are a read-modify-write on Spec.SharedWith; use an optimistic
	// lock so concurrent add/remove calls conflict instead of clobbering each other.
	var original kclient.Patch
	if hasShareChanges {
		original = kclient.MergeFromWithOptions(jlease.DeepCopy(), kclient.MergeFromWithOptimisticLock{})
	} else {
		original = kclient.MergeFrom(jlease.DeepCopy())
	}

	hasTimeChanges := req.Lease.BeginTime != nil || req.Lease.Duration != nil || req.Lease.EndTime != nil
	if hasTimeChanges {
		if !jlease.IsOwnedBy(jclient.Name) {
			return nil, status.Error(codes.PermissionDenied, "UpdateLease permission denied: only lease owner can modify time fields")
		}
		if jlease.Status.Ended {
			return nil, status.Error(codes.FailedPrecondition, "cannot modify time fields: lease has already ended")
		}
	}

	if err := s.updateLeaseTimeFields(req.Lease, key, jclient.Name, &jlease); err != nil {
		return nil, err
	}

	if err := s.transferLease(ctx, req.Lease, key, jclient.Name, &jlease); err != nil {
		return nil, err
	}

	if hasShareChanges {
		if jlease.Status.Ended {
			return nil, status.Error(codes.FailedPrecondition, "cannot modify sharing: lease has already ended")
		}
		newShared, err := s.applySharedWithChanges(ctx, &jlease, key.Namespace, req.AddSharedWith, req.RemoveSharedWith)
		if err != nil {
			return nil, err
		}
		jlease.Spec.SharedWith = newShared
	}

	if req.Lease.BeginTime != nil || req.Lease.Duration != nil || req.Lease.EndTime != nil {
		if err := jumpstarterdevv1alpha1.ReconcileLeaseTimeFields(&jlease.Spec.BeginTime, &jlease.Spec.EndTime, &jlease.Spec.Duration); err != nil {
			return nil, err
		}
	}

	if err := s.Patch(ctx, &jlease, original); err != nil {
		return nil, err
	}

	return jlease.ToProtobuf(), nil
}

func (s *ClientService) updateLeaseTimeFields(
	lease *cpb.Lease,
	key *types.NamespacedName,
	clientName string,
	jlease *jumpstarterdevv1alpha1.Lease,
) error {
	if lease.BeginTime == nil && lease.Duration == nil && lease.EndTime == nil {
		return nil
	}

	desired, err := jumpstarterdevv1alpha1.LeaseFromProtobuf(lease, *key,
		corev1.LocalObjectReference{Name: clientName},
	)
	if err != nil {
		return err
	}

	if lease.BeginTime != nil {
		if jlease.HasStarted() {
			if jlease.Spec.BeginTime == nil || !jlease.Spec.BeginTime.Equal(desired.Spec.BeginTime) {
				return status.Error(codes.FailedPrecondition, "cannot update BeginTime: lease has already started")
			}
		}
		jlease.Spec.BeginTime = desired.Spec.BeginTime
	}
	if lease.Duration != nil {
		jlease.Spec.Duration = desired.Spec.Duration
	}
	if lease.EndTime != nil {
		jlease.Spec.EndTime = desired.Spec.EndTime
	}
	return nil
}

func (s *ClientService) transferLease(
	ctx context.Context,
	lease *cpb.Lease,
	key *types.NamespacedName,
	clientName string,
	jlease *jumpstarterdevv1alpha1.Lease,
) error {
	if lease.Client == nil || *lease.Client == "" {
		return nil
	}
	if !jlease.IsOwnedBy(clientName) {
		return status.Error(codes.PermissionDenied, "UpdateLease permission denied: only lease owner can transfer")
	}
	if !jlease.HasStarted() {
		return status.Error(codes.FailedPrecondition, "cannot transfer lease: lease has not started yet")
	}
	if jlease.Status.Ended {
		return status.Error(codes.FailedPrecondition, "cannot transfer lease: lease has already ended")
	}
	newClientKey, err := utils.ParseClientIdentifier(*lease.Client)
	if err != nil {
		return err
	}
	if newClientKey.Namespace != key.Namespace {
		return status.Error(codes.InvalidArgument, "cannot transfer lease to client in different namespace")
	}
	var newClient jumpstarterdevv1alpha1.Client
	if err := s.Get(ctx, *newClientKey, &newClient); err != nil {
		if apierrors.IsNotFound(err) {
			return status.Errorf(codes.InvalidArgument, "target client %q not found", newClientKey.Name)
		}
		return status.Errorf(codes.Internal, "failed to get target client %q: %v", newClientKey.Name, err)
	}

	if err := s.validateClientPolicyAccess(ctx, key.Namespace, jlease, &newClient); err != nil {
		return err
	}

	jlease.Spec.ClientRef.Name = newClientKey.Name
	jlease.Spec.SharedWith = nil
	return nil
}

func (s *ClientService) validateClientPolicyAccess(
	ctx context.Context,
	namespace string,
	jlease *jumpstarterdevv1alpha1.Lease,
	targetClient *jumpstarterdevv1alpha1.Client,
) error {
	policies, exporters, err := s.boundExporterPolicies(ctx, namespace, jlease)
	if err != nil {
		return err
	}
	for i := range exporters {
		allowed, err := jumpstarterdevv1alpha1.ClientAllowedByPolicy(policies, &exporters[i], targetClient)
		if err != nil {
			return err
		}
		if !allowed {
			if jlease.IsMemberForm() {
				return status.Errorf(codes.PermissionDenied,
					"target client %q not authorized for exporter %q of this lease by policy", targetClient.Name, exporters[i].Name)
			}
			return status.Errorf(codes.PermissionDenied, "target client %q not authorized for this exporter by policy", targetClient.Name)
		}
	}
	return nil
}

// boundExporterPolicies returns the access policies of the namespace and the
// exporters that the lease gives access to: the one of a scalar lease, or the
// one of each Bound member. A client that gets the lease, or a share of it,
// must pass the policy for each of them. It returns no exporters when there is
// nothing to check: the lease has none yet, or the namespace has no policies.
func (s *ClientService) boundExporterPolicies(
	ctx context.Context,
	namespace string,
	jlease *jumpstarterdevv1alpha1.Lease,
) ([]jumpstarterdevv1alpha1.ExporterAccessPolicy, []jumpstarterdevv1alpha1.Exporter, error) {
	names := jlease.BoundExporterNames()
	if len(names) == 0 {
		return nil, nil, nil
	}
	var policyList jumpstarterdevv1alpha1.ExporterAccessPolicyList
	if err := s.List(ctx, &policyList, kclient.InNamespace(namespace)); err != nil {
		return nil, nil, fmt.Errorf("failed to list access policies: %w", err)
	}
	if len(policyList.Items) == 0 {
		return nil, nil, nil
	}
	exporters := make([]jumpstarterdevv1alpha1.Exporter, len(names))
	for i, name := range names {
		if err := s.Get(ctx, types.NamespacedName{Namespace: namespace, Name: name}, &exporters[i]); err != nil {
			return nil, nil, fmt.Errorf("failed to get exporter: %w", err)
		}
	}
	return policyList.Items, exporters, nil
}

func (s *ClientService) DeleteLease(ctx context.Context, req *cpb.DeleteLeaseRequest) (*emptypb.Empty, error) {
	key, err := utils.ParseLeaseIdentifier(req.Name)
	if err != nil {
		return nil, err
	}

	jclient, err := s.AuthClient(ctx, key.Namespace)
	if err != nil {
		return nil, err
	}

	var jlease jumpstarterdevv1alpha1.Lease
	if err := s.Get(ctx, *key, &jlease); err != nil {
		return nil, err
	}

	logger := log.FromContext(ctx).WithValues(
		"lease", jlease.Name,
		"namespace", jlease.Namespace,
		"client", jclient.Name,
		"path", "DeleteLease",
	)

	if !jlease.IsOwnedBy(jclient.Name) {
		logger.Info("Lease release request rejected", "outcome", "rejected")
		return nil, status.Errorf(codes.PermissionDenied, "DeleteLease permission denied: only lease owner can release")
	}

	if jlease.Spec.Release {
		logger.Info("Lease release request is idempotent", "outcome", "already_released")
		return nil, status.Errorf(codes.FailedPrecondition, "lease %q has already been released", req.Name)
	}

	original := kclient.MergeFrom(jlease.DeepCopy())

	jlease.Spec.Release = true

	if err := s.Patch(ctx, &jlease, original); err != nil {
		logger.Error(err, "failed to mark lease for release", "outcome", "failed")
		return nil, err
	}

	logger.Info("Lease marked for release by client", "outcome", "newly_marked")

	return &emptypb.Empty{}, nil
}

func (s *ClientService) RotateToken(ctx context.Context, req *cpb.RotateTokenRequest) (*cpb.RotateTokenResponse, error) {
	namespace, err := utils.ParseNamespaceIdentifier(req.Parent)
	if err != nil {
		return nil, err
	}

	jclient, err := s.AuthClient(ctx, namespace)
	if err != nil {
		return nil, err
	}

	token, expiry, err := auth.RotateCredential(ctx, s.Client, s.Signer, kclient.ObjectKey{
		Namespace: namespace,
		Name:      jclient.CredentialSecretName(),
	}, jclient.InternalSubject())
	if err != nil {
		return nil, err
	}
	log.FromContext(ctx).Info("token rotated", "client", jclient.Name, "namespace", namespace)

	return &cpb.RotateTokenResponse{
		Token:  token,
		Expiry: expiry,
	}, nil
}

func (s *ClientService) applySharedWithChanges(
	ctx context.Context,
	jlease *jumpstarterdevv1alpha1.Lease,
	namespace string,
	addClients, removeClients []string,
) ([]string, error) {
	shared := make([]string, len(jlease.Spec.SharedWith))
	copy(shared, jlease.Spec.SharedWith)

	for _, name := range removeClients {
		shared = slices.DeleteFunc(shared, func(s string) bool { return s == name })
	}

	var policies []jumpstarterdevv1alpha1.ExporterAccessPolicy
	var exporters []jumpstarterdevv1alpha1.Exporter
	if len(addClients) > 0 {
		var err error
		policies, exporters, err = s.boundExporterPolicies(ctx, namespace, jlease)
		if err != nil {
			return nil, err
		}
	}

	for _, name := range addClients {
		if name == jlease.Spec.ClientRef.Name {
			return nil, status.Error(codes.InvalidArgument, "cannot share lease with the owner")
		}
		if slices.Contains(shared, name) {
			continue
		}
		var sharedClient jumpstarterdevv1alpha1.Client
		if err := s.Get(ctx, types.NamespacedName{Namespace: namespace, Name: name}, &sharedClient); err != nil {
			if apierrors.IsNotFound(err) {
				return nil, status.Errorf(codes.InvalidArgument, "shared client %q not found", name)
			}
			return nil, status.Errorf(codes.Internal, "failed to get shared client %q: %v", name, err)
		}
		for i := range exporters {
			allowedByPolicy, err := jumpstarterdevv1alpha1.ClientAllowedByPolicy(policies, &exporters[i], &sharedClient)
			if err != nil {
				return nil, err
			}
			if !allowedByPolicy {
				if jlease.IsMemberForm() {
					return nil, status.Errorf(codes.PermissionDenied,
						"client %q not authorized for exporter %q of this lease", name, exporters[i].Name)
				}
				return nil, status.Errorf(codes.PermissionDenied, "client %q not authorized for this exporter", name)
			}
		}
		shared = append(shared, name)
	}

	if len(shared) > jumpstarterdevv1alpha1.MaxSharedWithEntries {
		return nil, status.Errorf(codes.InvalidArgument, "shared_with list exceeds maximum of %d entries", jumpstarterdevv1alpha1.MaxSharedWithEntries)
	}
	return shared, nil
}
