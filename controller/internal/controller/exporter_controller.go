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

package controller

import (
	"context"
	"fmt"
	"time"

	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/tools/record"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/oidc"
)

const reasonSeen = "Seen"

// ExporterReconciler reconciles a Exporter object
type ExporterReconciler struct {
	client.Client
	Scheme   *runtime.Scheme
	Signer   *oidc.Signer
	Recorder record.EventRecorder
}

// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporters,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporters/status,verbs=get;update;patch
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporters/finalizers,verbs=update
// +kubebuilder:rbac:groups=jumpstarter.dev,resources=exporteraccesspolicies,verbs=get;list;watch
// +kubebuilder:rbac:groups=core,resources=secrets,verbs=get;list;watch;create;update;patch;delete
// +kubebuilder:rbac:groups="",resources=events,verbs=create;patch

// Reconcile is part of the main kubernetes reconciliation loop which aims to
// move the current state of the cluster closer to the desired state.
// TODO(user): Modify the Reconcile function to compare the state specified by
// the Exporter object against the actual cluster state, and then
// perform operations to make the cluster state reflect the state specified by
// the user.
//
// For more details, check Reconcile and its Result here:
// - https://pkg.go.dev/sigs.k8s.io/controller-runtime@v0.18.2/pkg/reconcile
func (r *ExporterReconciler) Reconcile(ctx context.Context, req ctrl.Request) (ctrl.Result, error) {
	logger := log.FromContext(ctx)

	var exporter jumpstarterdevv1alpha1.Exporter
	if err := r.Get(ctx, req.NamespacedName, &exporter); err != nil {
		return ctrl.Result{}, client.IgnoreNotFound(
			fmt.Errorf("Reconcile: unable to get exporter: %w", err),
		)
	}

	original := client.MergeFrom(exporter.DeepCopy())

	prevOnline := meta.IsStatusConditionTrue(exporter.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline))
	prevRegistered := meta.IsStatusConditionTrue(exporter.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeRegistered))
	prevCredential := exporter.Status.Credential
	prevExpiry := tokenExpiringCondition(&exporter)

	warningThreshold, err := r.reconcileStatusCredential(ctx, &exporter)
	if err != nil {
		return RequeueConflict(logger, ctrl.Result{}, err)
	}

	if err := r.reconcileStatusLeaseRef(ctx, &exporter); err != nil {
		return ctrl.Result{}, err
	}

	result, err := r.reconcileStatusConditionsOnline(ctx, &exporter)
	if err != nil {
		return ctrl.Result{}, err
	}

	if err := r.reconcileStatusEndpoint(ctx, &exporter); err != nil {
		return ctrl.Result{}, err
	}

	newOnline := meta.IsStatusConditionTrue(exporter.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline))
	newRegistered := meta.IsStatusConditionTrue(exporter.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeRegistered))

	if err := r.Status().Patch(ctx, &exporter, original); err != nil {
		return RequeueConflict(logger, ctrl.Result{}, err)
	}

	// Emit only after status patch succeeds.
	if prevCredential == nil && exporter.Status.Credential != nil {
		r.emitEventf(&exporter, corev1.EventTypeNormal, "CredentialCreated",
			"Credential secret created for exporter: secret=%s", exporter.Status.Credential.Name)
	}

	newExpiry := tokenExpiringCondition(&exporter)
	if newExpiry.Reason == "Regenerated" && prevExpiry.Reason != "Regenerated" {
		r.emitEventf(&exporter, corev1.EventTypeWarning, "CredentialRegenerated",
			"Exporter token expired on %s; retrieve the replacement from Secret %s and reconnect", exporter.Status.TokenExpiresAt.UTC().Format(time.RFC3339), exporter.Status.Credential.Name)
	} else if newExpiry.Reason != "Regenerated" && prevExpiry.Status != metav1.ConditionTrue && newExpiry.Status == metav1.ConditionTrue {
		r.emitEventf(&exporter, corev1.EventTypeWarning, "TokenExpiringSoon",
			"Token for exporter %s is expiring soon: expires=%s", exporter.Name, exporter.Status.TokenExpiresAt.UTC().Format(time.RFC3339))
	}

	if !prevRegistered && newRegistered {
		r.emitEventf(&exporter, corev1.EventTypeNormal, "ExporterRegistered",
			"Exporter registered its capabilities: deviceCount=%d", len(exporter.Status.Devices))
	} else if prevRegistered && !newRegistered {
		r.emitEventf(&exporter, corev1.EventTypeWarning, "ExporterUnregistered",
			"Exporter lost its device registration: exporter=%s", exporter.Name)
	}

	if !prevOnline && newOnline {
		r.emitEventf(&exporter, corev1.EventTypeNormal, "ExporterOnline",
			"Exporter is online: exporter=%s lastSeen=%s",
			exporter.Name, exporter.Status.LastSeen.String())
	} else if prevOnline && !newOnline {
		if exporter.Status.ExporterStatusValue == jumpstarterdevv1alpha1.ExporterStatusOffline &&
			time.Since(exporter.Status.LastSeen.Time) <= time.Minute {
			r.emitEventf(&exporter, corev1.EventTypeWarning, "ExporterOffline",
				"Exporter reported offline (graceful shutdown): exporter=%s message=%s",
				exporter.Name, exporter.Status.StatusMessage)
		} else {
			r.emitEventf(&exporter, corev1.EventTypeWarning, "ExporterOffline",
				"Exporter went offline (connection lost): exporter=%s lastSeen=%s",
				exporter.Name, exporter.Status.LastSeen.String())
		}
	}

	expiryCheckInterval := min(tokenExpiryRequeueInterval, warningThreshold)
	if result.RequeueAfter == 0 || result.RequeueAfter > expiryCheckInterval {
		result.RequeueAfter = expiryCheckInterval
	}

	return result, nil
}

func tokenExpiringCondition(exporter *jumpstarterdevv1alpha1.Exporter) metav1.Condition {
	if condition := meta.FindStatusCondition(exporter.Status.Conditions, string(jumpstarterdevv1alpha1.ExporterConditionTypeTokenExpiring)); condition != nil {
		return *condition
	}
	return metav1.Condition{}
}

// reconcileStatusCredential returns the interval at which token expiry must be rechecked.
func (r *ExporterReconciler) reconcileStatusCredential(
	ctx context.Context,
	exporter *jumpstarterdevv1alpha1.Exporter,
) (time.Duration, error) {
	secret, expiry, err := ensureSecret(ctx, client.ObjectKey{
		Name:      exporter.CredentialSecretName(),
		Namespace: exporter.Namespace,
	}, r.Client, r.Scheme, r.Signer, exporter.InternalSubject(), exporter, r.markExpiredCredential)
	if err != nil {
		return 0, fmt.Errorf("reconcileStatusCredential: failed to prepare credential for exporter: %w", err)
	}
	exporter.Status.Credential = &corev1.LocalObjectReference{
		Name: secret.Name,
	}
	conditionType := string(jumpstarterdevv1alpha1.ExporterConditionTypeTokenExpiring)
	if exporter.Spec.Username != nil {
		exporter.Status.TokenExpiresAt = nil
		meta.RemoveStatusCondition(&exporter.Status.Conditions, conditionType)
		return tokenExpiryWarningThreshold, nil
	}
	warningThreshold := tokenExpiryWarningThreshold
	lifetime, err := r.Signer.TokenLifetime(string(secret.Data[TokenKey]))
	if err != nil {
		return 0, fmt.Errorf("reconcileStatusCredential: failed to read token lifetime: %w", err)
	}
	// Automatic renewal happens with 20% remaining (capped at 31 days).
	// Warn later, including for controllers configured with short lifetimes.
	if lifetime > 0 {
		warningThreshold = min(warningThreshold, lifetime/10)
	}
	if value, ok := secret.Annotations[expiredTokenAnnotation]; ok {
		expiredAt, parseErr := time.Parse(time.RFC3339, value)
		invalid := parseErr != nil || expiredAt.IsZero()
		// Heartbeats on an already authenticated stream do not prove receipt.
		authenticatedWithCurrentToken := exporter.Annotations[AuthenticatedTokenHashAnnotation] == TokenHash(string(secret.Data[TokenKey]))
		if !invalid && !authenticatedWithCurrentToken {
			expiresAt := metav1.NewTime(expiredAt)
			exporter.Status.TokenExpiresAt = &expiresAt
			meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
				Type:               conditionType,
				Status:             metav1.ConditionTrue,
				ObservedGeneration: exporter.Generation,
				Reason:             "Regenerated",
				Message:            fmt.Sprintf("Token expired on %s; replacement credential is available in Secret %s; exporter must reconnect", expiredAt.UTC().Format(time.RFC3339), secret.Name),
			})
			return warningThreshold, nil
		}
		original := client.MergeFromWithOptions(secret.DeepCopy(), client.MergeFromWithOptimisticLock{})
		delete(secret.Annotations, expiredTokenAnnotation)
		if err := r.Patch(ctx, secret, original); err != nil {
			return 0, err
		}
		if invalid {
			log.FromContext(ctx).Info("Cleared malformed credential recovery annotation", "secret", secret.Name)
		}
	}
	reconcileTokenExpiryWithThreshold(&exporter.Status.Conditions, &exporter.Status.TokenExpiresAt, expiry, exporter.Generation, conditionType, warningThreshold, exporterExpiringReason)
	return warningThreshold, nil
}

// markExpiredCredential flags a credential regenerated after its token expired;
// status reports it until the exporter authenticates with the replacement.
func (r *ExporterReconciler) markExpiredCredential(secret *corev1.Secret, replaced []byte) {
	expiredAt, err := r.Signer.TokenExpiry(string(replaced))
	if err != nil || expiredAt.IsZero() || !expiredAt.Before(time.Now()) {
		return
	}
	if secret.Annotations == nil {
		secret.Annotations = map[string]string{}
	}
	secret.Annotations[expiredTokenAnnotation] = expiredAt.UTC().Format(time.RFC3339)
}

func (r *ExporterReconciler) reconcileStatusLeaseRef(
	ctx context.Context,
	exporter *jumpstarterdevv1alpha1.Exporter,
) error {
	var leases jumpstarterdevv1alpha1.LeaseList
	if err := r.List(
		ctx,
		&leases,
		client.InNamespace(exporter.Namespace),
		MatchingActiveLeases(),
	); err != nil {
		return fmt.Errorf("reconcileStatusLeaseRef: failed to list active leases: %w", err)
	}

	exporter.Status.LeaseRef = nil
	for _, lease := range leases.Items {
		if !lease.Status.Ended && lease.Status.ExporterRef != nil {
			if lease.Status.ExporterRef.Name == exporter.Name {
				exporter.Status.LeaseRef = &corev1.LocalObjectReference{
					Name: lease.Name,
				}
			}
		}
	}

	return nil
}

// nolint:unparam
func (r *ExporterReconciler) reconcileStatusEndpoint(
	ctx context.Context,
	exporter *jumpstarterdevv1alpha1.Exporter,
) error {
	logger := log.FromContext(ctx)

	endpoint := controllerEndpoint()
	if exporter.Status.Endpoint != endpoint {
		logger.Info("reconcileStatusEndpoint: updating controller endpoint")
		exporter.Status.Endpoint = endpoint
	}

	return nil
}

// nolint:unparam
func (r *ExporterReconciler) reconcileStatusConditionsOnline(
	_ context.Context,
	exporter *jumpstarterdevv1alpha1.Exporter,
) (ctrl.Result, error) {
	var requeueAfter time.Duration = 0

	if exporter.Status.LastSeen.IsZero() {
		meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
			Status:             metav1.ConditionFalse,
			ObservedGeneration: exporter.Generation,
			Reason:             reasonSeen,
			Message:            "Never seen",
		})
		// Do NOT set ExporterStatusValue to Offline here.
		// Old exporters (v0.7.x) never call ReportStatus, so their status stays
		// at whatever we set here. If we set it to Offline, they can never recover
		// because the "LastSeen is recent + status is Offline" branch treats it as
		// a graceful shutdown. Leaving it empty allows the reconciler to mark them
		// Online once the Status stream connects and updates LastSeen.
		exporter.Status.StatusMessage = "Never seen"
		// marking the exporter as not yet seen, no need to requeue
	} else if time.Since(exporter.Status.LastSeen.Time) > time.Minute {
		meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
			Status:             metav1.ConditionFalse,
			ObservedGeneration: exporter.Generation,
			Reason:             reasonSeen,
			Message:            "Last seen more than 1 minute ago",
		})
		// Reset status to OFFLINE when exporter hasn't been seen recently
		if exporter.Status.ExporterStatusValue != jumpstarterdevv1alpha1.ExporterStatusOffline {
			exporter.Status.ExporterStatusValue = jumpstarterdevv1alpha1.ExporterStatusOffline
			exporter.Status.StatusMessage = "Connection lost - last seen more than 1 minute ago"
		}
		// marking the exporter offline, no need to requeue
	} else {
		// Check if exporter explicitly reported Offline status even though LastSeen is recent
		// This happens when an exporter gracefully shuts down
		if exporter.Status.ExporterStatusValue == jumpstarterdevv1alpha1.ExporterStatusOffline {
			meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
				Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
				Status:             metav1.ConditionFalse,
				ObservedGeneration: exporter.Generation,
				Reason:             "Offline",
				Message:            exporter.Status.StatusMessage,
			})
			// exporter reported offline, no need to requeue
		} else {
			meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
				Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
				Status:             metav1.ConditionTrue,
				ObservedGeneration: exporter.Generation,
				Reason:             reasonSeen,
				Message:            "Last seen less than 1 minute ago",
			})
			// marking the exporter online, requeue after 30 seconds
			requeueAfter = time.Second * 30
		}
	}

	if exporter.Status.Devices == nil {
		meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeRegistered),
			Status:             metav1.ConditionFalse,
			ObservedGeneration: exporter.Generation,
			Reason:             "Unregister",
		})
	} else {
		meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeRegistered),
			Status:             metav1.ConditionTrue,
			ObservedGeneration: exporter.Generation,
			Reason:             "Register",
		})
	}

	return ctrl.Result{
		RequeueAfter: requeueAfter,
	}, nil
}

func (r *ExporterReconciler) emitEventf(exporter *jumpstarterdevv1alpha1.Exporter, eventType, reason, msgFmt string, args ...any) {
	if r.Recorder == nil {
		return
	}
	r.Recorder.Eventf(exporter, eventType, reason, msgFmt, args...)
}

// SetupWithManager sets up the controller with the Manager.
func (r *ExporterReconciler) SetupWithManager(mgr ctrl.Manager) error {
	return ctrl.NewControllerManagedBy(mgr).
		For(&jumpstarterdevv1alpha1.Exporter{}).
		Owns(&jumpstarterdevv1alpha1.Lease{}).
		Owns(&corev1.Secret{}).
		Complete(r)
}
