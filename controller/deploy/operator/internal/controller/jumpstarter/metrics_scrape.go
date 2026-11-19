/*
Copyright 2025.

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

package jumpstarter

import (
	"context"
	"crypto/sha256"
	"errors"
	"fmt"
	"maps"
	"strings"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/equality"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
)

const (
	// metricsPort is the /metrics port shared by controller and router. The port
	// name (metricsPortName) is declared alongside the telemetry constants.
	metricsPort = int32(8080)

	// metricsServiceLabel marks Services that a ServiceMonitor selects.
	metricsServiceLabel = "jumpstarter.dev/metrics"

	// logScrapeLabel marks pods whose stdout logs a shipper should collect.
	logScrapeLabel = "jumpstarter.dev/log-scrape"
	// logFormatLabel declares the stdout log format for shippers.
	logFormatLabel = "jumpstarter.dev/log-format"

	serviceMonitorGroup   = "monitoring.coreos.com"
	serviceMonitorVersion = "v1"
	serviceMonitorKind    = "ServiceMonitor"
	serviceMonitorCRDName = "servicemonitors.monitoring.coreos.com"
)

// serviceMonitorEnabled reports whether Prometheus autodiscovery is enabled.
// An unset field means enabled (CRD default true).
func serviceMonitorEnabled(js *operatorv1alpha1.Jumpstarter) bool {
	if js.Spec.Telemetry == nil || js.Spec.Telemetry.Metrics.ServiceMonitor == nil {
		return true
	}
	return *js.Spec.Telemetry.Metrics.ServiceMonitor
}

// maxKubernetesNameLength is the DNS-1123 label limit shared by Service and
// ServiceMonitor names.
const maxKubernetesNameLength = 63

// metricsServiceName returns the metrics Service name for a component. The
// ServiceMonitor uses the same name, so creation and cleanup always agree.
//
// Short names are unchanged. Longer names keep a stable prefix plus a short
// hash of the full name: a Jumpstarter name may be up to 253 characters as a
// DNS subdomain, which would otherwise produce a 64+ character Service that
// Kubernetes rejects. Dots are replaced because Service names must be
// RFC 1035 labels.
func metricsServiceName(js *operatorv1alpha1.Jumpstarter, component string) string {
	name := fmt.Sprintf("%s-metrics-%s", strings.ReplaceAll(js.Name, ".", "-"), component)
	if len(name) <= maxKubernetesNameLength {
		return name
	}

	sum := sha256.Sum256([]byte(name))
	suffix := fmt.Sprintf("-%x", sum[:4])
	prefix := strings.TrimRight(name[:maxKubernetesNameLength-len(suffix)], "-")
	return prefix + suffix
}

// metricsServiceLabels returns the labels shared by metrics Services and their
// ServiceMonitors.
func metricsServiceLabels(js *operatorv1alpha1.Jumpstarter, component string) map[string]string {
	return map[string]string{
		"app.kubernetes.io/name":      "jumpstarter",
		"app.kubernetes.io/instance":  js.Name,
		"app.kubernetes.io/component": component,
		metricsServiceLabel:           "true",
	}
}

// desiredMetricsServices returns the metrics-only Services for controller and
// router. Telemetry already exposes a metrics port on its existing Service.
func desiredMetricsServices(js *operatorv1alpha1.Jumpstarter) []*corev1.Service {
	return []*corev1.Service{
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      metricsServiceName(js, "controller"),
				Namespace: js.Namespace,
				Labels:    metricsServiceLabels(js, "controller"),
			},
			Spec: corev1.ServiceSpec{
				Type: corev1.ServiceTypeClusterIP,
				Selector: map[string]string{
					"component":  "controller",
					"controller": js.Name,
				},
				Ports: []corev1.ServicePort{{
					Name:       metricsPortName,
					Port:       metricsPort,
					TargetPort: intstr.FromString(metricsPortName),
					Protocol:   corev1.ProtocolTCP,
				}},
			},
		},
		{
			ObjectMeta: metav1.ObjectMeta{
				Name:      metricsServiceName(js, "router"),
				Namespace: js.Namespace,
				Labels:    metricsServiceLabels(js, "router"),
			},
			Spec: corev1.ServiceSpec{
				Type: corev1.ServiceTypeClusterIP,
				Selector: map[string]string{
					"component": "router",
					"router":    js.Name,
				},
				Ports: []corev1.ServicePort{{
					Name:       metricsPortName,
					Port:       metricsPort,
					TargetPort: intstr.FromString(metricsPortName),
					Protocol:   corev1.ProtocolTCP,
				}},
			},
		},
	}
}

// reconcileMetricsServices creates or removes the metrics Services according to
// the serviceMonitor setting.
func (r *JumpstarterReconciler) reconcileMetricsServices(ctx context.Context, js *operatorv1alpha1.Jumpstarter) error {
	log := logf.FromContext(ctx)

	if !serviceMonitorEnabled(js) {
		var errs []error
		for _, name := range []string{metricsServiceName(js, "controller"), metricsServiceName(js, "router")} {
			if err := r.deleteObjectIfExists(ctx, js, types.NamespacedName{Name: name, Namespace: js.Namespace}, &corev1.Service{}); err != nil {
				// A foreign Service blocks its own deletion, not the other
				// component's cleanup; retry the failure on the next reconcile.
				errs = append(errs, err)
			}
		}
		return errors.Join(errs...)
	}

	for _, desired := range desiredMetricsServices(js) {
		existing := &corev1.Service{}
		err := r.Get(ctx, types.NamespacedName{Name: desired.Name, Namespace: desired.Namespace}, existing)
		if apierrors.IsNotFound(err) {
			if err := controllerutil.SetControllerReference(js, desired, r.Scheme); err != nil {
				return err
			}
			if err := r.Create(ctx, desired); err != nil {
				return fmt.Errorf("create metrics service %s: %w", desired.Name, err)
			}
			log.Info("Created metrics service", "name", desired.Name)
			continue
		}
		if err != nil {
			return err
		}
		if err := ensureControlledBy(js, existing); err != nil {
			return fmt.Errorf("refusing to update metrics service: %w", err)
		}
		if equality.Semantic.DeepEqual(existing.Labels, desired.Labels) &&
			equality.Semantic.DeepEqual(existing.Spec.Selector, desired.Spec.Selector) &&
			equality.Semantic.DeepEqual(existing.Spec.Ports, desired.Spec.Ports) {
			continue
		}
		existing.Labels = desired.Labels
		existing.Spec.Selector = desired.Spec.Selector
		existing.Spec.Ports = desired.Spec.Ports
		if err := controllerutil.SetControllerReference(js, existing, r.Scheme); err != nil {
			return err
		}
		if err := r.Update(ctx, existing); err != nil {
			return fmt.Errorf("update metrics service %s: %w", desired.Name, err)
		}
		log.Info("Updated metrics service", "name", desired.Name)
	}
	return nil
}

const (
	serviceMonitorReasonCreated       = "Created"
	serviceMonitorReasonDisabled      = "Disabled"
	serviceMonitorReasonCRDNotFound   = "CRDNotFound"
	serviceMonitorReasonNotConfigured = "NotConfigured"
	serviceMonitorReasonFailed        = "Failed"
)

// integrationResult is what one optional integration's condition reports in a
// reconcile. The accompanying error return carries any retryable failure; the
// result only describes what was observed.
type integrationResult struct {
	reason  string
	message string
}

// serviceMonitorResult is the Phase 5 name for the shared result type.
type serviceMonitorResult = integrationResult

// condition reports readiness: only the Created reason is ready; Disabled,
// CRDNotFound and Failed are informational.
func (r integrationResult) condition() (bool, string, string) {
	return r.reason == serviceMonitorReasonCreated, r.reason, r.message
}

// observabilityResult is the outcome of every optional observability
// integration attempted in one reconcile.
type observabilityResult struct {
	serviceMonitor  integrationResult
	prometheusRules integrationResult
	dashboards      integrationResult
}

// serviceMonitorCRDAvailable reports whether monitoring.coreos.com/v1 serves the
// ServiceMonitor kind. It uses a bounded List, mirroring the cert-manager CRD
// detection pattern, and treats meta.IsNoMatchError as "CRD absent".
func (r *JumpstarterReconciler) serviceMonitorCRDAvailable(ctx context.Context) (bool, error) {
	list := &unstructured.UnstructuredList{}
	list.SetGroupVersionKind(schema.GroupVersionKind{
		Group: serviceMonitorGroup, Version: serviceMonitorVersion, Kind: serviceMonitorKind + "List",
	})
	if err := r.List(ctx, list, client.Limit(1)); err != nil {
		// A cached REST mapping can outlive a removed CRD: the list then hits
		// the old endpoint and returns 404 instead of NoMatch. Treat both as
		// absent; keep authorization and transport errors.
		if apimeta.IsNoMatchError(err) || apierrors.IsNotFound(err) {
			return false, nil
		}
		return false, err
	}
	return true, nil
}

// serviceMonitorComponents returns the components that should have a
// ServiceMonitor for this CR.
func serviceMonitorComponents(js *operatorv1alpha1.Jumpstarter) []string {
	components := []string{"controller", "router"}
	if js.Spec.Telemetry != nil && js.Spec.Telemetry.Enabled && telemetryReplicas(js.Spec.Telemetry) > 0 {
		components = append(components, "telemetry")
	}
	return components
}

// serviceMonitorSelector returns the Service labels a ServiceMonitor selects.
func serviceMonitorSelector(js *operatorv1alpha1.Jumpstarter, component string) map[string]string {
	if component == "telemetry" {
		return map[string]string{
			"component":  "telemetry",
			"controller": js.Name,
		}
	}
	return metricsServiceLabels(js, component)
}

// desiredServiceMonitors returns unstructured ServiceMonitor objects for the
// running workloads.
func desiredServiceMonitors(js *operatorv1alpha1.Jumpstarter) []*unstructured.Unstructured {
	var monitors []*unstructured.Unstructured
	for _, component := range serviceMonitorComponents(js) {
		name := metricsServiceName(js, component)
		monitor := &unstructured.Unstructured{}
		monitor.SetGroupVersionKind(schema.GroupVersionKind{
			Group: serviceMonitorGroup, Version: serviceMonitorVersion, Kind: serviceMonitorKind,
		})
		monitor.SetName(name)
		monitor.SetNamespace(js.Namespace)
		// User labels are copied first so the operator labels win on a
		// collision: overriding jumpstarter.dev/metrics would leave every
		// monitor unselected by Prometheus, and an identity label must not
		// contradict the Service the monitor selects.
		labels := map[string]string{}
		if js.Spec.Telemetry != nil {
			maps.Copy(labels, js.Spec.Telemetry.Metrics.ServiceMonitorLabels)
		}
		maps.Copy(labels, metricsServiceLabels(js, component))
		monitor.SetLabels(labels)
		monitor.Object["spec"] = map[string]any{
			"namespaceSelector": map[string]any{
				"matchNames": []any{js.Namespace},
			},
			"selector": map[string]any{
				"matchLabels": toInterfaceMap(serviceMonitorSelector(js, component)),
			},
			"endpoints": []any{
				map[string]any{
					"port": metricsPortName,
					"path": "/metrics",
				},
			},
		}
		monitors = append(monitors, monitor)
	}
	return monitors
}

// toInterfaceMap converts map[string]string to map[string]any for
// unstructured objects.
func toInterfaceMap(in map[string]string) map[string]any {
	out := make(map[string]any, len(in))
	for k, v := range in {
		out[k] = v
	}
	return out
}

// reconcileServiceMonitors creates, updates, or removes ServiceMonitors.
// Removal happens when the feature is disabled or a workload is no longer
// desired (for example telemetry is disabled). The enabled path calls it only
// after the CRD probe succeeds; the disabled path calls it without probing, and
// pruning tolerates an absent API.
//
// Each monitor is applied independently and pruning always runs, so one
// conflict (for example a foreign monitor occupying a desired name) cannot
// block the other monitor or leave a stale one behind. The collected errors are
// returned for retry.
func (r *JumpstarterReconciler) reconcileServiceMonitors(ctx context.Context, js *operatorv1alpha1.Jumpstarter) error {
	var desired []*unstructured.Unstructured
	if serviceMonitorEnabled(js) {
		desired = desiredServiceMonitors(js)
	}

	// Build the full desired-name set first so pruning is correct even when an
	// apply fails below.
	desiredNames := make(map[string]struct{}, len(desired))
	for _, desiredMonitor := range desired {
		desiredNames[desiredMonitor.GetName()] = struct{}{}
	}

	var errs []error
	for _, desiredMonitor := range desired {
		if err := r.applyServiceMonitor(ctx, js, desiredMonitor); err != nil {
			errs = append(errs, err)
		}
	}

	if err := r.pruneServiceMonitors(ctx, js, desiredNames); err != nil {
		errs = append(errs, err)
	}

	return errors.Join(errs...)
}

// applyServiceMonitor creates or updates one ServiceMonitor. Per-monitor
// failures are returned to the caller so the remaining monitors still
// reconcile.
func (r *JumpstarterReconciler) applyServiceMonitor(ctx context.Context, js *operatorv1alpha1.Jumpstarter, desired *unstructured.Unstructured) error {
	log := logf.FromContext(ctx)

	existing := &unstructured.Unstructured{}
	existing.SetGroupVersionKind(desired.GroupVersionKind())
	err := r.Get(ctx, types.NamespacedName{Name: desired.GetName(), Namespace: desired.GetNamespace()}, existing)
	switch {
	case apierrors.IsNotFound(err):
		if err := controllerutil.SetControllerReference(js, desired, r.Scheme); err != nil {
			return err
		}
		if err := r.Create(ctx, desired); err != nil {
			return fmt.Errorf("create servicemonitor %s: %w", desired.GetName(), err)
		}
		log.Info("Created ServiceMonitor", "name", desired.GetName())
		return nil
	case err != nil:
		return fmt.Errorf("get servicemonitor %s: %w", desired.GetName(), err)
	}

	if err := ensureControlledBy(js, existing); err != nil {
		return fmt.Errorf("refusing to update ServiceMonitor %s: %w", desired.GetName(), err)
	}
	if equality.Semantic.DeepEqual(existing.Object["spec"], desired.Object["spec"]) &&
		equality.Semantic.DeepEqual(existing.GetLabels(), desired.GetLabels()) {
		return nil
	}
	existing.Object["spec"] = desired.Object["spec"]
	existing.SetLabels(desired.GetLabels())
	if err := controllerutil.SetControllerReference(js, existing, r.Scheme); err != nil {
		return err
	}
	if err := r.Update(ctx, existing); err != nil {
		return fmt.Errorf("update servicemonitor %s: %w", desired.GetName(), err)
	}
	log.Info("Updated ServiceMonitor", "name", desired.GetName())
	return nil
}

// pruneServiceMonitors deletes ServiceMonitors owned by this Jumpstarter CR
// that are no longer desired. Ownership is checked with the controller
// reference, not labels, because serviceMonitorLabels may override the
// operator's labels.
func (r *JumpstarterReconciler) pruneServiceMonitors(ctx context.Context, js *operatorv1alpha1.Jumpstarter, desired map[string]struct{}) error {
	log := logf.FromContext(ctx)

	monitors := &unstructured.UnstructuredList{}
	monitors.SetGroupVersionKind(schema.GroupVersionKind{
		Group: serviceMonitorGroup, Version: serviceMonitorVersion, Kind: serviceMonitorKind + "List",
	})
	if err := r.List(ctx, monitors, client.InNamespace(js.Namespace)); err != nil {
		if apimeta.IsNoMatchError(err) || apierrors.IsNotFound(err) {
			return nil
		}
		return fmt.Errorf("list servicemonitors: %w", err)
	}

	var errs []error
	for i := range monitors.Items {
		monitor := &monitors.Items[i]
		if _, ok := desired[monitor.GetName()]; ok {
			continue
		}
		if !metav1.IsControlledBy(monitor, js) {
			continue
		}

		monitor.SetGroupVersionKind(schema.GroupVersionKind{
			Group: serviceMonitorGroup, Version: serviceMonitorVersion, Kind: serviceMonitorKind,
		})
		// Delete only the object that passed the ownership check: a
		// replacement must not be removed under the same name. A conflict
		// means the object changed after the check; it is returned for retry
		// rather than swallowed, because ServiceMonitors are not watched and a
		// dropped error would wait for the resync.
		err := r.Delete(ctx, monitor, deletePreconditions(monitor))
		switch {
		case err == nil:
			log.Info("Deleted stale ServiceMonitor", "name", monitor.GetName())
		case apierrors.IsNotFound(err) || apimeta.IsNoMatchError(err):
			// Already gone, or the API is absent: nothing left to prune.
		default:
			errs = append(errs, fmt.Errorf("delete stale servicemonitor %s: %w", monitor.GetName(), err))
		}
	}

	return errors.Join(errs...)
}

// deletePreconditions pins a delete to the object that was fetched and
// ownership-checked. A conflict means the object changed; the next reconcile
// re-reads it and checks ownership again.
func deletePreconditions(obj client.Object) client.Preconditions {
	uid := obj.GetUID()
	resourceVersion := obj.GetResourceVersion()
	return client.Preconditions{UID: &uid, ResourceVersion: &resourceVersion}
}

// deleteObjectIfExists deletes an object and ignores NotFound. A conflict is
// returned so the reconcile retries with a fresh read instead of leaving the
// object behind until the resync.
func (r *JumpstarterReconciler) deleteObjectIfExists(ctx context.Context, js *operatorv1alpha1.Jumpstarter, key types.NamespacedName, obj client.Object) error {
	if err := r.Get(ctx, key, obj); err != nil {
		if apierrors.IsNotFound(err) || apimeta.IsNoMatchError(err) {
			return nil
		}
		return err
	}
	if err := ensureControlledBy(js, obj); err != nil {
		return fmt.Errorf("refusing to delete %w", err)
	}
	if err := r.Delete(ctx, obj, deletePreconditions(obj)); err != nil {
		if apierrors.IsNotFound(err) || apimeta.IsNoMatchError(err) {
			return nil
		}
		return err
	}
	return nil
}

// reconcileObservability reconciles metrics Services, ServiceMonitors and
// starter PrometheusRules. It never blocks lease operations: each optional
// integration is attempted independently, failures are collected for retry, and
// the caller reports them through status conditions instead of aborting the
// Jumpstarter reconcile.
func (r *JumpstarterReconciler) reconcileObservability(ctx context.Context, js *operatorv1alpha1.Jumpstarter) (observabilityResult, error) {
	var result observabilityResult
	var errs []error

	metricsErr := r.reconcileMetricsServices(ctx, js)
	if metricsErr != nil {
		errs = append(errs, fmt.Errorf("metrics services: %w", metricsErr))
	}

	serviceMonitor, err := r.reconcileServiceMonitorResult(ctx, js, metricsErr)
	if err != nil {
		errs = append(errs, err)
	}
	result.serviceMonitor = serviceMonitor

	prometheusRules, err := r.reconcilePrometheusRules(ctx, js)
	if err != nil {
		errs = append(errs, err)
	}
	result.prometheusRules = prometheusRules

	dashboards, err := r.reconcilePersesDashboards(ctx, js)
	if err != nil {
		errs = append(errs, err)
	}
	result.dashboards = dashboards

	return result, errors.Join(errs...)
}

// reconcileServiceMonitorResult reconciles ServiceMonitors and returns the
// condition and requeue result for this reconcile. metricsErr is the outcome of
// reconciling the Services the monitors select; a failure there means the scrape
// path is incomplete even when the monitors themselves were applied.
func (r *JumpstarterReconciler) reconcileServiceMonitorResult(ctx context.Context, js *operatorv1alpha1.Jumpstarter, metricsErr error) (integrationResult, error) {
	if !serviceMonitorEnabled(js) {
		// Prune any monitors left by a previous configuration without probing
		// for the optional CRD. A missing CRD has no monitors to prune.
		result := integrationResult{
			reason:  serviceMonitorReasonDisabled,
			message: "Prometheus autodiscovery is disabled by spec.telemetry.metrics.serviceMonitor",
		}
		if monitorErr := r.reconcileServiceMonitors(ctx, js); monitorErr != nil {
			// The prune failed, so owned monitors may still be scraped: report
			// the failure instead of a clean Disabled.
			return integrationResult{
				reason: serviceMonitorReasonFailed,
				message: fmt.Sprintf(
					"failed to remove ServiceMonitors while autodiscovery is disabled: %v",
					monitorErr,
				),
			}, fmt.Errorf("service monitors: %w", monitorErr)
		}
		if metricsErr != nil {
			return integrationResult{
				reason: serviceMonitorReasonFailed,
				message: fmt.Sprintf(
					"failed to reconcile metrics Services while autodiscovery is disabled: %v",
					metricsErr,
				),
			}, nil
		}
		return result, nil
	}

	crdAvailable, crdErr := r.serviceMonitorCRDAvailable(ctx)
	if crdErr != nil {
		// A transient API/RBAC error must not be mistaken for an absent CRD:
		// deleting the monitors here would cause churn and data loss.
		logf.FromContext(ctx).Error(crdErr, "Failed to detect ServiceMonitor CRD; leaving ServiceMonitors unchanged")
		return integrationResult{
			reason:  serviceMonitorReasonFailed,
			message: fmt.Sprintf("failed to detect ServiceMonitor CRD: %v", crdErr),
		}, fmt.Errorf("service monitor CRD detection: %w", crdErr)
	}
	if !crdAvailable {
		if metricsErr != nil {
			return integrationResult{
				reason: serviceMonitorReasonFailed,
				message: fmt.Sprintf(
					"failed to reconcile metrics Services: %v (monitoring.coreos.com/v1 ServiceMonitor CRD is not installed)",
					metricsErr,
				),
			}, nil
		}
		return integrationResult{
			reason:  serviceMonitorReasonCRDNotFound,
			message: "monitoring.coreos.com/v1 ServiceMonitor CRD is not installed",
		}, nil
	}

	monitorErr := r.reconcileServiceMonitors(ctx, js)

	// Readiness describes the whole autodiscovery path. ServiceMonitors can be
	// created while the Services they select were not reconciled, so a Service
	// failure must not report Created. The metrics error is already collected by
	// the caller, so it is not returned again here.
	switch {
	case monitorErr != nil:
		return integrationResult{
			reason:  serviceMonitorReasonFailed,
			message: fmt.Sprintf("failed to reconcile ServiceMonitors: %v", monitorErr),
		}, fmt.Errorf("service monitors: %w", monitorErr)
	case metricsErr != nil:
		return integrationResult{
			reason:  serviceMonitorReasonFailed,
			message: fmt.Sprintf("failed to reconcile metrics Services: %v", metricsErr),
		}, nil
	}

	return integrationResult{
		reason:  serviceMonitorReasonCreated,
		message: fmt.Sprintf("ServiceMonitors are configured for %d workloads", len(serviceMonitorComponents(js))),
	}, nil
}
