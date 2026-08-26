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

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/auth"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/utils"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/emptypb"
	"google.golang.org/protobuf/types/known/timestamppb"
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
	if len(deprecatedMessages) == 0 || lease.Selector == "" {
		return
	}
	selector, err := labels.Parse(lease.Selector)
	if err != nil {
		return
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

	// Use provided lease_id if specified, otherwise generate a UUIDv7
	name := req.LeaseId
	if name == "" {
		id, err := uuid.NewV7()
		if err != nil {
			return nil, err
		}
		name = id.String()
	}

	jlease, err := jumpstarterdevv1alpha1.LeaseFromProtobuf(req.Lease, types.NamespacedName{
		Namespace: namespace,
		Name:      name,
	}, corev1.LocalObjectReference{
		Name: jclient.Name,
	})
	if err != nil {
		return nil, err
	}

	if len(jlease.Spec.SharedWith) > jumpstarterdevv1alpha1.MaxSharedWithEntries {
		return nil, status.Errorf(codes.InvalidArgument, "shared_with list exceeds maximum of %d entries", jumpstarterdevv1alpha1.MaxSharedWithEntries)
	}
	if len(jlease.Spec.SharedWith) > 0 {
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
		jlease.Spec.SharedWith = deduped
	}

	if err := s.Create(ctx, jlease); err != nil {
		return nil, err
	}

	result := jlease.ToProtobuf()
	annotateLeaseDeprecatedLabels(result, s.deprecatedMessages)
	return result, nil
}

func validateLeaseTarget(lease *cpb.Lease) error {
	if lease == nil {
		return status.Error(codes.InvalidArgument, "lease is required")
	}

	hasSelector := lease.Selector != ""
	hasExporterName := lease.ExporterName != nil && *lease.ExporterName != ""
	if !hasSelector && !hasExporterName {
		return status.Error(codes.InvalidArgument, "one of selector or exporter_name is required")
	}

	return nil
}

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
		if jlease.Status.ExporterRef != nil {
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
	if jlease.Status.ExporterRef == nil {
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
	if jlease.Status.ExporterRef == nil {
		return nil
	}
	var policyList jumpstarterdevv1alpha1.ExporterAccessPolicyList
	if err := s.List(ctx, &policyList, kclient.InNamespace(namespace)); err != nil {
		return fmt.Errorf("failed to list access policies: %w", err)
	}
	if len(policyList.Items) == 0 {
		return nil
	}
	var exporter jumpstarterdevv1alpha1.Exporter
	if err := s.Get(ctx, types.NamespacedName{
		Namespace: namespace,
		Name:      jlease.Status.ExporterRef.Name,
	}, &exporter); err != nil {
		return fmt.Errorf("failed to get exporter: %w", err)
	}
	allowed, err := jumpstarterdevv1alpha1.ClientAllowedByPolicy(policyList.Items, &exporter, targetClient)
	if err != nil {
		return err
	}
	if !allowed {
		return status.Errorf(codes.PermissionDenied, "target client %q not authorized for this exporter by policy", targetClient.Name)
	}
	return nil
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

	if !jlease.IsOwnedBy(jclient.Name) {
		return nil, fmt.Errorf("DeleteLease permission denied: only lease owner can release")
	}

	if jlease.Spec.Release {
		return nil, status.Errorf(codes.FailedPrecondition, "lease %q has already been released", req.Name)
	}

	original := kclient.MergeFrom(jlease.DeepCopy())

	jlease.Spec.Release = true

	if err := s.Patch(ctx, &jlease, original); err != nil {
		return nil, err
	}

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

	token, err := s.Signer.Token(jclient.InternalSubject())
	if err != nil {
		return nil, status.Errorf(codes.Internal, "failed to sign token: %s", err)
	}

	secretName := jclient.Name + "-client"
	var secret corev1.Secret
	if err := s.Get(ctx, types.NamespacedName{
		Namespace: namespace,
		Name:      secretName,
	}, &secret); err != nil {
		return nil, status.Errorf(codes.Internal, "failed to get credential secret: %s", err)
	}

	original := kclient.MergeFrom(secret.DeepCopy())
	if secret.Data == nil {
		secret.Data = map[string][]byte{}
	}
	secret.Data["token"] = []byte(token)
	if err := s.Patch(ctx, &secret, original); err != nil {
		return nil, status.Errorf(codes.Internal, "failed to update credential secret: %s", err)
	}

	log.FromContext(ctx).Info("token rotated", "client", jclient.Name, "namespace", namespace)

	claims := &struct {
		jwt.RegisteredClaims
	}{}
	parser := jwt.NewParser(jwt.WithoutClaimsValidation())
	if _, _, err := parser.ParseUnverified(token, claims); err != nil {
		return nil, status.Errorf(codes.Internal, "failed to parse token claims: %s", err)
	}

	var expiry *timestamppb.Timestamp
	if claims.ExpiresAt != nil {
		expiry = timestamppb.New(claims.ExpiresAt.Time)
	}

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
	var exporter *jumpstarterdevv1alpha1.Exporter
	if len(addClients) > 0 && jlease.Status.ExporterRef != nil {
		var policyList jumpstarterdevv1alpha1.ExporterAccessPolicyList
		if err := s.List(ctx, &policyList, kclient.InNamespace(namespace)); err != nil {
			return nil, fmt.Errorf("failed to list access policies: %w", err)
		}
		policies = policyList.Items
		if len(policies) > 0 {
			var exp jumpstarterdevv1alpha1.Exporter
			if err := s.Get(ctx, types.NamespacedName{
				Namespace: namespace,
				Name:      jlease.Status.ExporterRef.Name,
			}, &exp); err != nil {
				return nil, fmt.Errorf("failed to get exporter: %w", err)
			}
			exporter = &exp
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
		if exporter != nil {
			allowedByPolicy, err := jumpstarterdevv1alpha1.ClientAllowedByPolicy(policies, exporter, &sharedClient)
			if err != nil {
				return nil, err
			}
			if !allowedByPolicy {
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
