package v1alpha1

import (
	"context"
	"fmt"
	"maps"
	"slices"
	"strings"
	"time"

	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/utils"
	"google.golang.org/protobuf/types/known/durationpb"
	"google.golang.org/protobuf/types/known/timestamppb"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/selection"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/validation"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// ReconcileLeaseTimeFields calculates missing time fields and validates consistency
// between BeginTime, EndTime, and Duration. Modifies pointers in place.
//
// Supported lease specification patterns:
// 1. Duration only (no BeginTime/EndTime): immediate start, runs for Duration
//   - BeginTime set by controller when exporter acquired
//   - EndTime = Status.BeginTime + Duration (calculated at runtime)
//
// 2. EndTime only: INVALID - cannot infer Duration without BeginTime or explicit Duration
//   - Returns error: "duration is required (must specify Duration, or both BeginTime and EndTime)"
//
// 3. BeginTime + Duration: scheduled start at BeginTime, runs for Duration
//   - Lease waits until BeginTime, then acquires exporter
//   - EndTime = BeginTime + Duration (calculated at runtime)
//
// 4. BeginTime + EndTime: scheduled window, Duration computed from times
//   - Duration = EndTime - BeginTime (auto-calculated here)
//   - Validates EndTime > BeginTime (positive duration)
//
// 5. EndTime + Duration: scheduled end, BeginTime computed as EndTime - Duration
//   - BeginTime = EndTime - Duration (auto-calculated here)
//   - Useful for "finish by" scheduling
//
// 6. BeginTime + EndTime + Duration: all three specified, validates consistency
//   - Validates Duration == EndTime - BeginTime
//   - Returns error if inconsistent: "duration conflicts with begin_time and end_time"
//
// Note: The controller never auto-populates Spec.EndTime. It calculates expiration time
// on-demand from available fields, keeping Spec.EndTime meaningful only when explicitly
// set by the user. See lease_controller.go reconcileStatusEnded for expiration logic.
func ReconcileLeaseTimeFields(beginTime, endTime **metav1.Time, duration **metav1.Duration) error {
	if *beginTime != nil && *endTime != nil {
		// Calculate duration from explicit begin/end times
		calculated := (*endTime).Sub((*beginTime).Time)
		if *duration != nil && (*duration).Duration > 0 && (*duration).Duration != calculated {
			return fmt.Errorf("duration conflicts with begin_time and end_time")
		}
		*duration = &metav1.Duration{Duration: calculated}
	} else if *endTime != nil && *duration != nil && (*duration).Duration > 0 {
		// Calculate BeginTime from EndTime - Duration (scheduled lease ending at specific time)
		*beginTime = &metav1.Time{Time: (*endTime).Add(-(*duration).Duration)}
	}

	// Validate final duration is positive (rejects nil, negative, zero)
	if *duration == nil {
		return fmt.Errorf("duration is required (must specify Duration, or both BeginTime and EndTime)")
	}
	if (*duration).Duration <= 0 {
		return fmt.Errorf("duration must be positive, got %v", (*duration).Duration)
	}
	return nil
}

// ParseLabelSelector parses a label selector string and converts it to metav1.LabelSelector.
// This function supports the != operator by first parsing with labels.Parse() which supports
// all label selector syntax including !=, then converting to metav1.LabelSelector format.
func ParseLabelSelector(selectorStr string) (*metav1.LabelSelector, error) {
	// First, try to parse using labels.Parse() which supports != operator
	parsedSelector, err := labels.Parse(selectorStr)
	if err != nil {
		return nil, fmt.Errorf("failed to parse label selector: %w", err)
	}

	// Extract requirements from the parsed selector
	requirements, selectable := parsedSelector.Requirements()
	if !selectable {
		return &metav1.LabelSelector{}, nil
	}

	// Convert requirements to metav1.LabelSelector format
	matchLabels := make(map[string]string)
	var matchExpressions []metav1.LabelSelectorRequirement

	// Track NotEquals requirements by key so we can combine them into NotIn
	notEqualsByKey := make(map[string][]string)

	for _, req := range requirements {
		key := req.Key()
		operator := req.Operator()
		values := req.ValuesUnsorted()

		switch operator {
		case selection.Equals:
			// For exact match with single value, use matchLabels
			if len(values) == 1 {
				// Check if we already have an equality requirement for this key with a different value
				if existingValue, exists := matchLabels[key]; exists && existingValue != values[0] {
					return nil, fmt.Errorf("invalid selector: label %s cannot have multiple equality requirements with different values (%s and %s)", key, existingValue, values[0])
				}
				matchLabels[key] = values[0]
			} else {
				// Multiple values should use In operator
				matchExpressions = append(matchExpressions, metav1.LabelSelectorRequirement{
					Key:      key,
					Operator: metav1.LabelSelectorOpIn,
					Values:   values,
				})
			}
		case selection.NotEquals:
			// Accumulate != requirements for the same key to combine into NotIn
			if len(values) != 1 {
				return nil, fmt.Errorf("invalid selector: != operator requires exactly one value")
			}
			if !slices.Contains(notEqualsByKey[key], values[0]) {
				notEqualsByKey[key] = append(notEqualsByKey[key], values[0])
			}
		case selection.In:
			matchExpressions = append(matchExpressions, metav1.LabelSelectorRequirement{
				Key:      key,
				Operator: metav1.LabelSelectorOpIn,
				Values:   values,
			})
		case selection.NotIn:
			matchExpressions = append(matchExpressions, metav1.LabelSelectorRequirement{
				Key:      key,
				Operator: metav1.LabelSelectorOpNotIn,
				Values:   values,
			})
		case selection.Exists:
			matchExpressions = append(matchExpressions, metav1.LabelSelectorRequirement{
				Key:      key,
				Operator: metav1.LabelSelectorOpExists,
				Values:   []string{},
			})
		case selection.DoesNotExist:
			matchExpressions = append(matchExpressions, metav1.LabelSelectorRequirement{
				Key:      key,
				Operator: metav1.LabelSelectorOpDoesNotExist,
				Values:   []string{},
			})
		default:
			return nil, fmt.Errorf("unsupported label selector operator: %v", operator)
		}
	}

	// Convert accumulated NotEquals requirements to NotIn expressions
	for key, vals := range notEqualsByKey {
		matchExpressions = append(matchExpressions, metav1.LabelSelectorRequirement{
			Key:      key,
			Operator: metav1.LabelSelectorOpNotIn,
			Values:   vals,
		})
	}

	return &metav1.LabelSelector{
		MatchLabels:      matchLabels,
		MatchExpressions: matchExpressions,
	}, nil
}

// ValidateLeaseTags validates user-defined lease tags against the given maxTags limit.
func ValidateLeaseTags(tags map[string]string, maxTags int) error {
	if len(tags) > maxTags {
		return fmt.Errorf("too many tags: %d (maximum %d)", len(tags), maxTags)
	}
	for k, v := range tags {
		if strings.HasPrefix(k, LeaseTagMetadataPrefix) || strings.HasPrefix(k, "jumpstarter.dev/") {
			return fmt.Errorf("tag key %q must not use reserved prefix", k)
		}
		if strings.Contains(k, "/") {
			return fmt.Errorf("tag key %q must not contain '/' (use simple names without prefix)", k)
		}
		prefixedKey := LeaseTagMetadataPrefix + k
		if errs := validation.IsQualifiedName(prefixedKey); len(errs) > 0 {
			return fmt.Errorf("tag key %q is not a valid label key: %s", k, strings.Join(errs, "; "))
		}
		if errs := validation.IsValidLabelValue(v); len(errs) > 0 {
			return fmt.Errorf("tag value for key %q is not a valid label value: %s", k, strings.Join(errs, "; "))
		}
	}
	return nil
}

func LeaseFromProtobuf(
	req *cpb.Lease,
	key types.NamespacedName,
	clientRef corev1.LocalObjectReference,
) (*Lease, error) {
	selector, err := ParseLabelSelector(req.Selector)
	if err != nil {
		return nil, err
	}

	var beginTime, endTime *metav1.Time
	var duration *metav1.Duration

	if req.BeginTime != nil {
		beginTime = &metav1.Time{Time: req.BeginTime.AsTime()}
	}
	if req.EndTime != nil {
		endTime = &metav1.Time{Time: req.EndTime.AsTime()}
	}
	if req.Duration != nil {
		duration = &metav1.Duration{Duration: req.Duration.AsDuration()}
	}
	if err := ReconcileLeaseTimeFields(&beginTime, &endTime, &duration); err != nil {
		return nil, err
	}

	// Build ObjectMeta labels: start with selector matchLabels (excluding reserved prefix), then add prefixed user tags
	metaLabels := make(map[string]string)
	for k, v := range selector.MatchLabels {
		if strings.HasPrefix(k, LeaseTagMetadataPrefix) {
			continue
		}
		metaLabels[k] = v
	}
	for k, v := range req.Tags {
		metaLabels[LeaseTagMetadataPrefix+k] = v
	}

	// Store user tags in spec (without prefix)
	var specTags map[string]string
	if len(req.Tags) > 0 {
		specTags = make(map[string]string, len(req.Tags))
		maps.Copy(specTags, req.Tags)
	}

	// Store user context in spec
	var specContext map[string]string
	if len(req.Context) > 0 {
		specContext = make(map[string]string, len(req.Context))
		maps.Copy(specContext, req.Context)
	}

	var members []LeaseMember
	for _, m := range req.Members {
		member, err := LeaseMemberFromProtobuf(m)
		if err != nil {
			return nil, err
		}
		members = append(members, *member)
	}
	var constraints []LeaseConstraint
	for _, c := range req.Constraints {
		constraints = append(constraints, LeaseConstraintFromProtobuf(c))
	}
	var forwards []LeaseForward
	for _, f := range req.Forwards {
		forwards = append(forwards, LeaseForwardFromProtobuf(f))
	}

	return &Lease{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: key.Namespace,
			Name:      key.Name,
			Labels:    metaLabels,
		},
		Spec: LeaseSpec{
			ClientRef:   clientRef,
			Duration:    duration,
			Selector:    *selector,
			Members:     members,
			Constraints: constraints,
			Forwards:    forwards,
			Tags:        specTags,
			Context:     specContext,
			ExporterRef: func() *corev1.LocalObjectReference {
				if req.ExporterName == nil || *req.ExporterName == "" {
					return nil
				}
				return &corev1.LocalObjectReference{Name: *req.ExporterName}
			}(),
			AllowDisabled: req.AllowDisabled,
			BeginTime:     beginTime,
			EndTime:       endTime,
			SharedWith:    req.SharedWith,
		},
	}, nil
}

func (l *Lease) ToProtobuf() *cpb.Lease {
	var conditions []*pb.Condition
	for _, condition := range l.Status.Conditions {
		conditions = append(conditions, &pb.Condition{
			Type:               &condition.Type,
			Status:             (*string)(&condition.Status),
			ObservedGeneration: &condition.ObservedGeneration,
			LastTransitionTime: &pb.Time{
				Seconds: &condition.LastTransitionTime.ProtoTime().Seconds,
				Nanos:   &condition.LastTransitionTime.ProtoTime().Nanos,
			},
			Reason:  &condition.Reason,
			Message: &condition.Message,
		})
	}

	lease := cpb.Lease{
		Name:          fmt.Sprintf("namespaces/%s/leases/%s", l.Namespace, l.Name),
		Selector:      metav1.FormatLabelSelector(&l.Spec.Selector),
		Client:        new(fmt.Sprintf("namespaces/%s/clients/%s", l.Namespace, l.Spec.ClientRef.Name)),
		Conditions:    conditions,
		Tags:          l.Spec.Tags,
		AllowDisabled: l.Spec.AllowDisabled,
		Context:       l.Spec.Context,
		// shared_with is the owner's desired intent (Spec); effective_shared_with is
		// the controller-derived set actually granted (Status), after policy/existence
		// filtering. A name in the former but not the latter was denied or doesn't exist.
		SharedWith:          l.Spec.SharedWith,
		EffectiveSharedWith: l.Status.SharedWith,
	}
	if l.Spec.ExporterRef != nil {
		lease.ExporterName = new(l.Spec.ExporterRef.Name)
	}
	if l.Spec.Duration != nil {
		lease.Duration = durationpb.New(l.Spec.Duration.Duration)
	}

	// Requested/planned times from Spec
	if l.Spec.BeginTime != nil {
		lease.BeginTime = timestamppb.New(l.Spec.BeginTime.Time)
	}
	if l.Spec.EndTime != nil {
		lease.EndTime = timestamppb.New(l.Spec.EndTime.Time)
	}

	// Actual times from Status
	if l.Status.BeginTime != nil {
		lease.EffectiveBeginTime = timestamppb.New(l.Status.BeginTime.Time)
		endTime := time.Now()
		if l.Status.EndTime != nil {
			endTime = l.Status.EndTime.Time
			lease.EffectiveEndTime = timestamppb.New(endTime)
		}
		// Final effective duration or current one so far while active. Non-negative to handle clock skew.
		effectiveDuration := max(endTime.Sub(l.Status.BeginTime.Time), 0)
		lease.EffectiveDuration = durationpb.New(effectiveDuration)
	}
	if l.IsMemberForm() {
		// A member-form lease has no selector and no exporter of its own: the
		// members and their statuses carry them.
		lease.Selector = ""
		for i := range l.Spec.Members {
			lease.Members = append(lease.Members, l.Spec.Members[i].ToProtobuf())
		}
		for i := range l.Spec.Constraints {
			lease.Constraints = append(lease.Constraints, l.Spec.Constraints[i].ToProtobuf())
		}
		for i := range l.Status.Members {
			lease.MemberStatuses = append(lease.MemberStatuses, l.Status.Members[i].ToProtobuf(l.Namespace))
		}
		for i := range l.Spec.Forwards {
			lease.Forwards = append(lease.Forwards, l.Spec.Forwards[i].ToProtobuf())
		}
		for i := range l.Status.Forwards {
			lease.ForwardStatuses = append(lease.ForwardStatuses, l.Status.Forwards[i].ToProtobuf())
		}
	} else if l.Status.ExporterRef != nil {
		lease.Exporter = new(utils.UnparseExporterIdentifier(kclient.ObjectKey{
			Namespace: l.Namespace,
			Name:      l.Status.ExporterRef.Name,
		}))
	}
	if l.Spec.TemplateRef != nil {
		lease.Template = &cpb.LeaseTemplateUse{
			Template: UnparseLeaseTemplateIdentifier(kclient.ObjectKey{
				Namespace: l.Namespace,
				Name:      l.Spec.TemplateRef.Name,
			}),
			Generation: l.Spec.TemplateRef.Generation,
		}
	}

	return &lease
}

// LeaseTemplateResource is the collection segment of a lease template
// resource name: namespaces/{namespace}/leasetemplates/{name}.
const LeaseTemplateResource = "leasetemplates"

// UnparseLeaseTemplateIdentifier returns the resource name of a lease template.
func UnparseLeaseTemplateIdentifier(key kclient.ObjectKey) string {
	return utils.UnparseObjectIdentifier(key, LeaseTemplateResource)
}

// LeaseMemberFromProtobuf converts a member of a lease request. Its selector
// uses the syntax of Lease.selector.
func LeaseMemberFromProtobuf(m *cpb.LeaseMember) (*LeaseMember, error) {
	member := &LeaseMember{
		Name:          m.GetName(),
		Optional:      m.GetOptional(),
		Excluded:      m.GetExcluded(),
		AllowDisabled: m.GetAllowDisabled(),
	}
	switch selection := m.GetSelection().(type) {
	case *cpb.LeaseMember_Selector:
		selector, err := ParseLabelSelector(selection.Selector)
		if err != nil {
			return nil, fmt.Errorf("member %q: %w", m.GetName(), err)
		}
		if len(selector.MatchLabels) == 0 {
			selector.MatchLabels = nil
		}
		member.Selector = selector
	case *cpb.LeaseMember_ExporterName:
		member.ExporterRef = &corev1.LocalObjectReference{Name: selection.ExporterName}
	}
	return member, nil
}

// SelectorString formats the selector of the member in the syntax of
// Lease.selector. It is empty for a member without a selector.
func (m *LeaseMember) SelectorString() string {
	if m.Selector == nil {
		return ""
	}
	selector, err := metav1.LabelSelectorAsSelector(m.Selector)
	if err != nil {
		return ""
	}
	return selector.String()
}

func (m *LeaseMember) ToProtobuf() *cpb.LeaseMember {
	member := &cpb.LeaseMember{
		Name:          m.Name,
		Optional:      m.Optional,
		Excluded:      m.Excluded,
		AllowDisabled: m.AllowDisabled,
	}
	if m.ExporterRef != nil && m.ExporterRef.Name != "" {
		member.Selection = &cpb.LeaseMember_ExporterName{ExporterName: m.ExporterRef.Name}
	} else if selector := m.SelectorString(); selector != "" {
		member.Selection = &cpb.LeaseMember_Selector{Selector: selector}
	}
	return member
}

func LeaseConstraintFromProtobuf(c *cpb.LeaseConstraint) LeaseConstraint {
	return LeaseConstraint{
		Members:   slices.Clone(c.GetMemberNames()),
		SameLabel: c.GetSameLabel(),
	}
}

func (c *LeaseConstraint) ToProtobuf() *cpb.LeaseConstraint {
	return &cpb.LeaseConstraint{
		MemberNames: slices.Clone(c.Members),
		Rule:        &cpb.LeaseConstraint_SameLabel{SameLabel: c.SameLabel},
	}
}

var leaseMemberStateToProto = map[LeaseMemberState]cpb.LeaseMemberState{
	LeaseMemberStatePending: cpb.LeaseMemberState_LEASE_MEMBER_STATE_PENDING,
	LeaseMemberStateBound:   cpb.LeaseMemberState_LEASE_MEMBER_STATE_BOUND,
	LeaseMemberStateOmitted: cpb.LeaseMemberState_LEASE_MEMBER_STATE_OMITTED,
	LeaseMemberStateLost:    cpb.LeaseMemberState_LEASE_MEMBER_STATE_LOST,
}

// ToProtobuf converts the status of a member of a lease in this namespace.
func (s *LeaseMemberStatus) ToProtobuf(namespace string) *cpb.LeaseMemberStatus {
	memberStatus := &cpb.LeaseMemberStatus{
		Name:       s.Name,
		State:      leaseMemberStateToProto[s.State],
		Priority:   int32(s.Priority),
		SpotAccess: s.SpotAccess,
	}
	if s.ExporterRef != nil {
		memberStatus.Exporter = new(utils.UnparseExporterIdentifier(kclient.ObjectKey{
			Namespace: namespace,
			Name:      s.ExporterRef.Name,
		}))
	}
	if s.Reason != "" {
		memberStatus.Reason = new(s.Reason)
	}
	if s.Message != "" {
		memberStatus.Message = new(s.Message)
	}
	return memberStatus
}

// ToProtobuf converts the template. LeaseTemplateMember.available is not a
// property of the template: the caller sets it.
func (t *LeaseTemplate) ToProtobuf() *cpb.LeaseTemplate {
	template := &cpb.LeaseTemplate{
		Name:              UnparseLeaseTemplateIdentifier(kclient.ObjectKeyFromObject(t)),
		Description:       t.Spec.Description,
		Docs:              slices.Clone(t.Spec.Docs),
		Generation:        t.Generation,
		DefaultSharedWith: slices.Clone(t.Spec.Defaults.SharedWith),
	}
	if t.Spec.Image != "" {
		template.Image = new(t.Spec.Image)
	}
	if t.Spec.Instructions != "" {
		template.Instructions = new(t.Spec.Instructions)
	}
	if t.Spec.Defaults.Duration != nil {
		template.DefaultDuration = durationpb.New(t.Spec.Defaults.Duration.Duration)
	}
	for i := range t.Spec.Members {
		template.Members = append(template.Members, &cpb.LeaseTemplateMember{
			Member: t.Spec.Members[i].ToProtobuf(),
		})
	}
	for i := range t.Spec.Constraints {
		template.Constraints = append(template.Constraints, t.Spec.Constraints[i].ToProtobuf())
	}
	for i := range t.Spec.Forwards {
		template.Forwards = append(template.Forwards, t.Spec.Forwards[i].ToProtobuf())
	}
	return template
}

func (l *LeaseList) ToProtobuf() *cpb.ListLeasesResponse {
	var jleases []*cpb.Lease
	for _, jlease := range l.Items {
		jleases = append(jleases, jlease.ToProtobuf())
	}
	return &cpb.ListLeasesResponse{
		Leases:        jleases,
		NextPageToken: l.Continue,
	}
}

func (l *Lease) GetExporterSelector() (labels.Selector, error) {
	return metav1.LabelSelectorAsSelector(&l.Spec.Selector)
}

func (l *Lease) SetStatusPending(reason, messageFormat string, a ...any) {
	l.SetStatusCondition(LeaseConditionTypePending, true, reason, messageFormat, a...)
}

func (l *Lease) SetStatusReady(status bool, reason, messageFormat string, a ...any) {
	l.SetStatusCondition(LeaseConditionTypeReady, status, reason, messageFormat, a...)
}

func (l *Lease) SetStatusUnsatisfiable(reason, messageFormat string, a ...any) {
	l.SetStatusCondition(LeaseConditionTypeUnsatisfiable, true, reason, messageFormat, a...)
}

func (l *Lease) SetStatusInvalid(reason, messageFormat string, a ...any) {
	l.SetStatusCondition(LeaseConditionTypeInvalid, true, reason, messageFormat, a...)
}

func (l *Lease) SetStatusCondition(
	condition LeaseConditionType,
	status bool,
	reason, messageFormat string, a ...any) {

	var statusCondition metav1.ConditionStatus

	if status {
		statusCondition = metav1.ConditionTrue
	} else {
		statusCondition = metav1.ConditionFalse
	}

	meta.SetStatusCondition(&l.Status.Conditions, metav1.Condition{
		Type:               string(condition),
		Status:             statusCondition,
		ObservedGeneration: l.Generation,
		LastTransitionTime: metav1.Time{
			Time: time.Now(),
		},
		Reason:  reason,
		Message: fmt.Sprintf(messageFormat, a...),
	})
}

func (l *Lease) IsAccessibleBy(clientName string) bool {
	if l.Spec.ClientRef.Name == clientName {
		return true
	}
	// Access is granted based on the effective, policy-filtered set the controller
	// computes in Status.SharedWith, not the owner's raw Spec.SharedWith intent.
	return slices.Contains(l.Status.SharedWith, clientName)
}

// HasStarted reports whether the lease got its exporters: a scalar lease has
// status.exporterRef; a member-form lease has status.beginTime, which the
// controller sets when the members bind.
func (l *Lease) HasStarted() bool {
	if l.IsMemberForm() {
		return l.Status.BeginTime != nil
	}
	return l.Status.ExporterRef != nil
}

func (l *Lease) IsOwnedBy(clientName string) bool {
	return l.Spec.ClientRef.Name == clientName
}

func ClientAllowedByPolicy(
	policies []ExporterAccessPolicy,
	exporter *Exporter,
	jclient *Client,
) (bool, error) {
	for _, policy := range policies {
		exporterSelector, err := metav1.LabelSelectorAsSelector(&policy.Spec.ExporterSelector)
		if err != nil {
			return false, fmt.Errorf("failed to convert exporter selector: %w", err)
		}
		if !exporterSelector.Matches(labels.Set(exporter.Labels)) {
			continue
		}
		for _, p := range policy.Spec.Policies {
			for _, from := range p.From {
				clientSelector, err := metav1.LabelSelectorAsSelector(&from.ClientSelector)
				if err != nil {
					return false, fmt.Errorf("failed to convert client selector: %w", err)
				}
				if clientSelector.Matches(labels.Set(jclient.Labels)) {
					return true, nil
				}
			}
		}
	}
	return false, nil
}

func (l *Lease) GetExporterName() string {
	if l.IsMemberForm() {
		if held := l.HeldExporterNames(); len(held) > 0 {
			return strings.Join(held, ",")
		}
		return "(none)"
	}
	if l.Status.ExporterRef == nil {
		return "(none)"
	}
	return l.Status.ExporterRef.Name
}

func (l *Lease) GetClientName() string {
	return l.Spec.ClientRef.Name
}

func (l *Lease) Release(ctx context.Context) {
	logger := log.FromContext(ctx)
	logger.Info("The lease has been marked for release", "lease", l.Name, "exporter", l.GetExporterName(), "client", l.GetClientName())
	l.SetStatusReady(false, "Released", "The lease was marked for release")
	l.Status.Ended = true
	l.Status.EndTime = &metav1.Time{Time: time.Now()}
}

func (l *Lease) Expire(ctx context.Context) {
	logger := log.FromContext(ctx)
	logger.Info("The lease has expired", "lease", l.Name, "exporter", l.GetExporterName(), "client", l.GetClientName())
	l.SetStatusReady(false, "Expired", "The lease has expired")
	l.Status.Ended = true
	l.Status.EndTime = &metav1.Time{Time: time.Now()}
}
