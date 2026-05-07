/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package v1

import (
	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/identity"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/managedby"
	adminv1 "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/admin/v1"
	jumpstarterv1 "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/service/utils"
	"google.golang.org/protobuf/types/known/durationpb"
	"google.golang.org/protobuf/types/known/timestamppb"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

// stampOwner copies identity-derived ownership annotations onto m. Existing
// values are overwritten only when present in id; this keeps ad-hoc CRDs
// (e.g. created by `kubectl apply`) compatible.
func stampOwner(m *metav1.ObjectMeta, id identity.Identity) {
	if m.Annotations == nil {
		m.Annotations = map[string]string{}
	}
	if hash := id.OwnerHash(); hash != "" {
		m.Annotations[identity.OwnerAnnotation] = hash
	}
	if id.Username != "" {
		m.Annotations[identity.CreatedByAnnotation] = id.Username
	}
	if id.Issuer != "" {
		m.Annotations[identity.OwnerIssuerAnnotation] = id.Issuer
	}
}

// resourceMetadata builds an admin.v1.ResourceMetadata from a CRD's
// ObjectMeta. created_by and owner_issuer come from annotations; the
// resource_version is the kube-apiserver's optimistic-concurrency
// cursor; externally_managed is derived from the well-known
// `app.kubernetes.io/managed-by` label and ArgoCD/Helm fingerprints.
func resourceMetadata(m metav1.ObjectMeta) *jumpstarterv1.ResourceMetadata {
	out := &jumpstarterv1.ResourceMetadata{
		ResourceVersion:    m.ResourceVersion,
		ExternallyManaged:  managedby.IsExternal(&m),
	}
	if v, ok := m.Annotations[identity.CreatedByAnnotation]; ok {
		s := v
		out.CreatedBy = &s
	}
	if v, ok := m.Annotations[identity.OwnerIssuerAnnotation]; ok {
		s := v
		out.OwnerIssuer = &s
	}
	return out
}

// --- Lease conversions ----------------------------------------------------

func leaseToProto(l *jumpstarterdevv1alpha1.Lease) *jumpstarterv1.Lease {
	if l == nil {
		return nil
	}
	out := &jumpstarterv1.Lease{
		Name:       utils.UnparseLeaseIdentifier(kclient.ObjectKey{Namespace: l.Namespace, Name: l.Name}),
		Labels:     l.Labels,
		Tags:       l.Spec.Tags,
		Ended:      l.Status.Ended,
		Conditions: conditionsToProto(l.Status.Conditions),
		Metadata:   resourceMetadata(l.ObjectMeta),
	}
	if l.Spec.ExporterRef != nil {
		s := utils.UnparseExporterIdentifier(kclient.ObjectKey{Namespace: l.Namespace, Name: l.Spec.ExporterRef.Name})
		out.ExporterName = &s
	}
	if l.Spec.Duration != nil {
		out.Duration = durationpb.New(l.Spec.Duration.Duration)
	}
	if l.Spec.BeginTime != nil {
		out.BeginTime = timestamppb.New(l.Spec.BeginTime.Time)
	}
	if l.Spec.EndTime != nil {
		out.EndTime = timestamppb.New(l.Spec.EndTime.Time)
	}
	if l.Spec.ClientRef.Name != "" {
		s := utils.UnparseClientIdentifier(kclient.ObjectKey{Namespace: l.Namespace, Name: l.Spec.ClientRef.Name})
		out.Client = &s
	}
	if l.Status.ExporterRef != nil {
		s := utils.UnparseExporterIdentifier(kclient.ObjectKey{Namespace: l.Namespace, Name: l.Status.ExporterRef.Name})
		out.Exporter = &s
	}
	if l.Status.BeginTime != nil {
		out.EffectiveBeginTime = timestamppb.New(l.Status.BeginTime.Time)
	}
	if l.Status.EndTime != nil {
		out.EffectiveEndTime = timestamppb.New(l.Status.EndTime.Time)
	}
	if l.Status.BeginTime != nil && l.Status.EndTime != nil {
		out.EffectiveDuration = durationpb.New(l.Status.EndTime.Sub(l.Status.BeginTime.Time))
	}
	return out
}

// --- Exporter conversions -------------------------------------------------

func exporterToProto(e *jumpstarterdevv1alpha1.Exporter) *jumpstarterv1.Exporter {
	if e == nil {
		return nil
	}
	out := &jumpstarterv1.Exporter{
		Name:          utils.UnparseExporterIdentifier(kclient.ObjectKey{Namespace: e.Namespace, Name: e.Name}),
		Labels:        e.Labels,
		StatusMessage: e.Status.StatusMessage,
		Status:        exporterStatusValue(e.Status.ExporterStatusValue),
		Metadata:      resourceMetadata(e.ObjectMeta),
	}
	if ep := e.Status.Endpoint; ep != "" {
		out.Endpoint = &ep
	}
	if e.Spec.Username != nil {
		s := *e.Spec.Username
		out.Username = &s
	}
	return out
}

func exporterStatusValue(s string) jumpstarterv1.ExporterStatus {
	switch s {
	case jumpstarterdevv1alpha1.ExporterStatusOffline:
		return jumpstarterv1.ExporterStatus_EXPORTER_STATUS_OFFLINE
	case jumpstarterdevv1alpha1.ExporterStatusAvailable:
		return jumpstarterv1.ExporterStatus_EXPORTER_STATUS_AVAILABLE
	case jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHook:
		return jumpstarterv1.ExporterStatus_EXPORTER_STATUS_BEFORE_LEASE_HOOK
	case jumpstarterdevv1alpha1.ExporterStatusLeaseReady:
		return jumpstarterv1.ExporterStatus_EXPORTER_STATUS_LEASE_READY
	case jumpstarterdevv1alpha1.ExporterStatusAfterLeaseHook:
		return jumpstarterv1.ExporterStatus_EXPORTER_STATUS_AFTER_LEASE_HOOK
	case jumpstarterdevv1alpha1.ExporterStatusBeforeLeaseHookFailed:
		return jumpstarterv1.ExporterStatus_EXPORTER_STATUS_BEFORE_LEASE_HOOK_FAILED
	case jumpstarterdevv1alpha1.ExporterStatusAfterLeaseHookFailed:
		return jumpstarterv1.ExporterStatus_EXPORTER_STATUS_AFTER_LEASE_HOOK_FAILED
	default:
		return jumpstarterv1.ExporterStatus_EXPORTER_STATUS_UNSPECIFIED
	}
}

// --- Client conversions ---------------------------------------------------

// clientTypeOf classifies a Client by its authentication mechanism.
//
//   - OIDC: auto-provisioned by the legacy client.v1 reconciler on first
//     OIDC contact. The reconciler stamps spec.username from the OIDC
//     subject but does NOT stamp the admin-only owner annotation.
//   - TOKEN: any Client that uses a static bootstrap credential —
//     admin.v1.CreateClient mints these (and stamps the owner
//     annotation in the process); kubectl-applied and pre-admin.v1
//     bots fall here too.
//
// The owner annotation is the disambiguator: admin.v1 always stamps it
// when admin.v1 created the Client, the legacy reconciler never does.
func clientTypeOf(c *jumpstarterdevv1alpha1.Client) jumpstarterv1.ClientType {
	if c.Spec.Username != nil && *c.Spec.Username != "" {
		if _, owned := c.Annotations[identity.OwnerAnnotation]; !owned {
			return jumpstarterv1.ClientType_CLIENT_TYPE_OIDC
		}
	}
	return jumpstarterv1.ClientType_CLIENT_TYPE_TOKEN
}

func clientToProto(c *jumpstarterdevv1alpha1.Client) *jumpstarterv1.Client {
	if c == nil {
		return nil
	}
	out := &jumpstarterv1.Client{
		Name:     utils.UnparseClientIdentifier(kclient.ObjectKey{Namespace: c.Namespace, Name: c.Name}),
		Labels:   c.Labels,
		Metadata: resourceMetadata(c.ObjectMeta),
		Type:     clientTypeOf(c).Enum(),
	}
	if ep := c.Status.Endpoint; ep != "" {
		out.Endpoint = &ep
	}
	if c.Spec.Username != nil {
		s := *c.Spec.Username
		out.Username = &s
	}
	return out
}

// --- Webhook conversions --------------------------------------------------

func webhookToProto(w *jumpstarterdevv1alpha1.Webhook) *adminv1.Webhook {
	if w == nil {
		return nil
	}
	out := &adminv1.Webhook{
		Name:                utils.UnparseWebhookIdentifier(kclient.ObjectKey{Namespace: w.Namespace, Name: w.Name}),
		Url:                 w.Spec.URL,
		SecretRef:           w.Spec.SecretRef.Name + "/" + w.Spec.SecretRef.Key,
		ConsecutiveFailures: w.Status.ConsecutiveFailures,
		Metadata:            resourceMetadata(w.ObjectMeta),
	}
	for _, e := range w.Spec.Events {
		out.Events = append(out.Events, eventClassFromString(e))
	}
	if w.Status.LastSuccess != nil {
		out.LastSuccess = timestamppb.New(w.Status.LastSuccess.Time)
	}
	if w.Status.LastFailure != nil {
		out.LastFailure = timestamppb.New(w.Status.LastFailure.Time)
	}
	return out
}

func eventClassFromString(s string) adminv1.EventClass {
	switch s {
	case jumpstarterdevv1alpha1.WebhookEventLeaseCreated:
		return adminv1.EventClass_EVENT_CLASS_LEASE_CREATED
	case jumpstarterdevv1alpha1.WebhookEventLeaseEnded:
		return adminv1.EventClass_EVENT_CLASS_LEASE_ENDED
	case jumpstarterdevv1alpha1.WebhookEventExporterOffline:
		return adminv1.EventClass_EVENT_CLASS_EXPORTER_OFFLINE
	case jumpstarterdevv1alpha1.WebhookEventExporterAvailable:
		return adminv1.EventClass_EVENT_CLASS_EXPORTER_AVAILABLE
	case jumpstarterdevv1alpha1.WebhookEventClientCreated:
		return adminv1.EventClass_EVENT_CLASS_CLIENT_CREATED
	case jumpstarterdevv1alpha1.WebhookEventClientDeleted:
		return adminv1.EventClass_EVENT_CLASS_CLIENT_DELETED
	default:
		return adminv1.EventClass_EVENT_CLASS_UNSPECIFIED
	}
}

func eventClassToString(c adminv1.EventClass) string {
	switch c {
	case adminv1.EventClass_EVENT_CLASS_LEASE_CREATED:
		return jumpstarterdevv1alpha1.WebhookEventLeaseCreated
	case adminv1.EventClass_EVENT_CLASS_LEASE_ENDED:
		return jumpstarterdevv1alpha1.WebhookEventLeaseEnded
	case adminv1.EventClass_EVENT_CLASS_EXPORTER_OFFLINE:
		return jumpstarterdevv1alpha1.WebhookEventExporterOffline
	case adminv1.EventClass_EVENT_CLASS_EXPORTER_AVAILABLE:
		return jumpstarterdevv1alpha1.WebhookEventExporterAvailable
	case adminv1.EventClass_EVENT_CLASS_CLIENT_CREATED:
		return jumpstarterdevv1alpha1.WebhookEventClientCreated
	case adminv1.EventClass_EVENT_CLASS_CLIENT_DELETED:
		return jumpstarterdevv1alpha1.WebhookEventClientDeleted
	default:
		return ""
	}
}

// --- common helpers -------------------------------------------------------

func conditionsToProto(in []metav1.Condition) []*jumpstarterv1.Condition {
	out := make([]*jumpstarterv1.Condition, 0, len(in))
	for _, c := range in {
		typ := c.Type
		st := string(c.Status)
		reason := c.Reason
		message := c.Message
		pc := &jumpstarterv1.Condition{
			Type:    &typ,
			Status:  &st,
			Reason:  &reason,
			Message: &message,
		}
		if !c.LastTransitionTime.IsZero() {
			secs := c.LastTransitionTime.Unix()
			pc.LastTransitionTime = &jumpstarterv1.Time{Seconds: &secs}
		}
		out = append(out, pc)
	}
	return out
}

// secretRefFromString parses a "name/key" reference into a SecretKeySelector.
func secretRefFromString(s string) (corev1.SecretKeySelector, bool) {
	for i := 0; i < len(s); i++ {
		if s[i] == '/' {
			return corev1.SecretKeySelector{
				LocalObjectReference: corev1.LocalObjectReference{Name: s[:i]},
				Key:                  s[i+1:],
			}, true
		}
	}
	return corev1.SecretKeySelector{}, false
}
