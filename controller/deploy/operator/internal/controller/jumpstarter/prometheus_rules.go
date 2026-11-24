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

package jumpstarter

import (
	"context"
	"fmt"
	"maps"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	"k8s.io/apimachinery/pkg/api/equality"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/controller/controllerutil"
	logf "sigs.k8s.io/controller-runtime/pkg/log"
)

const (
	prometheusRuleGroup   = "monitoring.coreos.com"
	prometheusRuleVersion = "v1"
	prometheusRuleKind    = "PrometheusRule"
	prometheusRuleCRDName = "prometheusrules.monitoring.coreos.com"

	// prometheusRuleLabel marks the generated rule set so a Prometheus
	// ruleSelector can select it.
	prometheusRuleLabel = "jumpstarter.dev/prometheus-rules"
)

// prometheusRulesEnabled reports whether starter alerts are enabled. Unset means
// disabled; the CRD default is false.
func prometheusRulesEnabled(js *operatorv1alpha1.Jumpstarter) bool {
	if js.Spec.Telemetry == nil || js.Spec.Telemetry.Metrics.PrometheusRules == nil {
		return false
	}
	return *js.Spec.Telemetry.Metrics.PrometheusRules
}

// prometheusRuleName returns the PrometheusRule name for a Jumpstarter CR.
func prometheusRuleName(js *operatorv1alpha1.Jumpstarter) string {
	return fmt.Sprintf("%s-starter-alerts", js.Name)
}

// prometheusRuleLabels returns the labels shared by the generated rules.
// User-supplied labels are applied first so they cannot override the stable
// selector label.
func prometheusRuleLabels(js *operatorv1alpha1.Jumpstarter) map[string]string {
	labels := make(map[string]string, 4)
	if js.Spec.Telemetry != nil {
		maps.Copy(labels, js.Spec.Telemetry.Metrics.RuleLabels)
	}
	labels["app.kubernetes.io/name"] = "jumpstarter"
	labels["app.kubernetes.io/instance"] = js.Name
	labels[prometheusRuleLabel] = "true"
	return labels
}

// prometheusRuleCRDAvailable reports whether monitoring.coreos.com/v1 serves
// the PrometheusRule kind.
func (r *JumpstarterReconciler) prometheusRuleCRDAvailable(ctx context.Context) (bool, error) {
	list := &unstructured.UnstructuredList{}
	list.SetGroupVersionKind(schema.GroupVersionKind{
		Group: prometheusRuleGroup, Version: prometheusRuleVersion, Kind: prometheusRuleKind + "List",
	})
	if err := r.List(ctx, list, client.Limit(1)); err != nil {
		if apimeta.IsNoMatchError(err) {
			return false, nil
		}
		return false, err
	}
	return true, nil
}

// desiredPrometheusRules returns the starter alert rules for a Jumpstarter CR.
func desiredPrometheusRules(js *operatorv1alpha1.Jumpstarter) *unstructured.Unstructured {
	rules := &unstructured.Unstructured{}
	rules.SetGroupVersionKind(schema.GroupVersionKind{
		Group: prometheusRuleGroup, Version: prometheusRuleVersion, Kind: prometheusRuleKind,
	})
	rules.SetName(prometheusRuleName(js))
	rules.SetNamespace(js.Namespace)
	rules.SetLabels(prometheusRuleLabels(js))
	rules.Object["spec"] = map[string]any{
		"groups": []any{
			map[string]any{
				"name": "jumpstarter-operations",
				"rules": []any{
					alertRule(
						"JumpstarterOperationFailureRate",
						`sum by (exporter, operation) (rate(jumpstarter_operations_total{result="failure"}[15m]))
/
sum by (exporter, operation) (rate(jumpstarter_operations_total[15m])) > 0.2`,
						"15m",
						"warning",
						"Jumpstarter operation failure rate is above 20%",
						"Exporter {{ $labels.exporter }} reports more than 20% failed {{ $labels.operation }} operations over 15 minutes.",
					),
					alertRule(
						"JumpstarterOperationDurationHigh",
						`histogram_quantile(0.95, sum by (operation, le) (rate(jumpstarter_operation_duration_seconds_bucket[15m]))) > 60`,
						"15m",
						"warning",
						"Jumpstarter operation p95 duration is above 60 seconds",
						"Operation {{ $labels.operation }} has a p95 duration above 60 seconds over 15 minutes.",
					),
					alertRule(
						"JumpstarterOperationErrors",
						`sum by (exporter, error_type) (increase(jumpstarter_operation_errors_total[5m])) > 0`,
						"15m",
						"warning",
						"Jumpstarter operation errors are recurring",
						"Exporter {{ $labels.exporter }} is reporting recurring {{ $labels.error_type }} errors.",
					),
					alertRule(
						"JumpstarterExporterIdle",
						`max by (exporter) (jumpstarter_active_sessions) == 0
and
max by (exporter) (increase(jumpstarter_operations_total[1h])) > 0`,
						"30m",
						"warning",
						"Jumpstarter exporter has no active sessions after recent activity",
						"Exporter {{ $labels.exporter }} had operations in the last hour but no active session for 30 minutes; check the exporter or its scheduling.",
					),
					alertRule(
						"JumpstarterLeaseAcquisitionFailureRate",
						`sum(rate(jumpstarter_lease_acquisitions_total{result="failure"}[15m]))
/
sum(rate(jumpstarter_lease_acquisitions_total[15m])) > 0.1`,
						"15m",
						"warning",
						"Jumpstarter lease acquisition failure rate is above 10%",
						"More than 10% of lease acquisition attempts failed over 15 minutes.",
					),
				},
			},
			map[string]any{
				"name": "jumpstarter-observability",
				"rules": []any{
					alertRule(
						"JumpstarterTelemetryDropped",
						`increase(jumpstarter_telemetry_dropped_total[5m]) > 0`,
						"5m",
						"critical",
						"Jumpstarter telemetry is dropping log entries",
						"The telemetry Loki push queue dropped entries; check telemetry backpressure and Loki availability.",
					),
					alertRule(
						"JumpstarterScrapeTimeouts",
						`sum by (exporter) (increase(jumpstarter_scrape_timeouts_total[15m])) > 3`,
						"15m",
						"critical",
						"Jumpstarter exporter scrape timeouts are repeating",
						"Exporter {{ $labels.exporter }} timed out more than three times during metrics fan-out over 15 minutes.",
					),
				},
			},
		},
	}
	return rules
}

// alertRule builds one Prometheus alerting rule.
func alertRule(name, expr, forDuration, severity, summary, description string) map[string]any {
	return map[string]any{
		"alert": name,
		"expr":  expr,
		"for":   forDuration,
		"labels": map[string]any{
			"severity": severity,
		},
		"annotations": map[string]any{
			"summary":     summary,
			"description": description,
			"runbook_url": "https://jumpstarter.dev/docs/getting-started/configuration/observability",
		},
	}
}

// reconcilePrometheusRules creates, updates, or removes the starter alert rules.
func (r *JumpstarterReconciler) reconcilePrometheusRules(ctx context.Context, js *operatorv1alpha1.Jumpstarter) (integrationResult, error) {
	if !prometheusRulesEnabled(js) {
		result := integrationResult{
			reason:  serviceMonitorReasonDisabled,
			message: "starter PrometheusRules are disabled by spec.telemetry.metrics.prometheusRules",
		}
		if err := r.prunePrometheusRules(ctx, js, nil); err != nil {
			// The prune failed, so owned rules may still be evaluated: report the
			// failure instead of a clean Disabled.
			return integrationResult{
				reason: serviceMonitorReasonFailed,
				message: fmt.Sprintf(
					"failed to remove starter PrometheusRules while they are disabled: %v",
					err,
				),
			}, fmt.Errorf("prometheus rules: %w", err)
		}
		return result, nil
	}

	available, err := r.prometheusRuleCRDAvailable(ctx)
	if err != nil {
		logf.FromContext(ctx).Error(err, "Failed to detect PrometheusRule CRD; leaving rules unchanged")
		return integrationResult{
			reason:  serviceMonitorReasonFailed,
			message: fmt.Sprintf("failed to detect PrometheusRule CRD: %v", err),
		}, fmt.Errorf("prometheus rule CRD detection: %w", err)
	}
	if !available {
		return integrationResult{
			reason:  serviceMonitorReasonCRDNotFound,
			message: "monitoring.coreos.com/v1 PrometheusRule CRD is not installed",
		}, nil
	}

	desired := desiredPrometheusRules(js)
	existing := &unstructured.Unstructured{}
	existing.SetGroupVersionKind(desired.GroupVersionKind())
	err = r.Get(ctx, types.NamespacedName{Name: desired.GetName(), Namespace: desired.GetNamespace()}, existing)
	switch {
	case apierrors.IsNotFound(err):
		if err := controllerutil.SetControllerReference(js, desired, r.Scheme); err != nil {
			return applyFailedRuleResult(err), fmt.Errorf("set owner reference: %w", err)
		}
		if err := r.Create(ctx, desired); err != nil {
			return applyFailedRuleResult(err), fmt.Errorf("create prometheusrule %s: %w", desired.GetName(), err)
		}
		logf.FromContext(ctx).Info("Created PrometheusRule", "name", desired.GetName())
	case err != nil:
		return applyFailedRuleResult(err), fmt.Errorf("get prometheusrule %s: %w", desired.GetName(), err)
	default:
		if err := ensureControlledBy(js, existing); err != nil {
			return applyFailedRuleResult(err), fmt.Errorf("refusing to update prometheusrule %s: %w", desired.GetName(), err)
		}
		if equality.Semantic.DeepEqual(existing.Object["spec"], desired.Object["spec"]) &&
			equality.Semantic.DeepEqual(existing.GetLabels(), desired.GetLabels()) {
			break
		}
		existing.Object["spec"] = desired.Object["spec"]
		existing.SetLabels(desired.GetLabels())
		if err := controllerutil.SetControllerReference(js, existing, r.Scheme); err != nil {
			return applyFailedRuleResult(err), fmt.Errorf("set owner reference: %w", err)
		}
		if err := r.Update(ctx, existing); err != nil {
			return applyFailedRuleResult(err), fmt.Errorf("update prometheusrule %s: %w", desired.GetName(), err)
		}
		logf.FromContext(ctx).Info("Updated PrometheusRule", "name", desired.GetName())
	}

	desiredNames := map[string]struct{}{desired.GetName(): {}}
	if err := r.prunePrometheusRules(ctx, js, desiredNames); err != nil {
		return applyFailedRuleResult(err), fmt.Errorf("prune prometheus rules: %w", err)
	}
	return activeRuleResult(), nil
}

func activeRuleResult() integrationResult {
	return integrationResult{
		reason:  serviceMonitorReasonCreated,
		message: "starter PrometheusRules are configured",
	}
}

func applyFailedRuleResult(err error) integrationResult {
	return integrationResult{
		reason:  serviceMonitorReasonFailed,
		message: fmt.Sprintf("failed to reconcile PrometheusRules: %v", err),
	}
}

// prunePrometheusRules deletes rules owned by this Jumpstarter CR that are not
// desired. A nil desired set prunes every owned rule (feature disabled).
func (r *JumpstarterReconciler) prunePrometheusRules(ctx context.Context, js *operatorv1alpha1.Jumpstarter, desired map[string]struct{}) error {
	list := &unstructured.UnstructuredList{}
	list.SetGroupVersionKind(schema.GroupVersionKind{
		Group: prometheusRuleGroup, Version: prometheusRuleVersion, Kind: prometheusRuleKind + "List",
	})
	if err := r.List(ctx, list, client.InNamespace(js.Namespace)); err != nil {
		if apimeta.IsNoMatchError(err) {
			return nil
		}
		return fmt.Errorf("list prometheusrules: %w", err)
	}

	for i := range list.Items {
		rule := &list.Items[i]
		if _, ok := desired[rule.GetName()]; ok {
			continue
		}
		if !metav1.IsControlledBy(rule, js) {
			continue
		}
		if err := r.Delete(ctx, rule); err != nil && !apierrors.IsNotFound(err) && !apimeta.IsNoMatchError(err) {
			return fmt.Errorf("delete stale prometheusrule %s: %w", rule.GetName(), err)
		}
		logf.FromContext(ctx).Info("Deleted stale PrometheusRule", "name", rule.GetName())
	}
	return nil
}
