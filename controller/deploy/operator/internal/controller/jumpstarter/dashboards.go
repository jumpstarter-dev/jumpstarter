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
	"slices"
	"strings"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
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
	persesGroup            = "perses.dev"
	persesVersion          = "v1alpha2"
	persesDashboardKind    = "PersesDashboard"
	persesDashboardCRDName = "persesdashboards.perses.dev"

	// persesOperatorMinimumVersion is the oldest perses-operator release that
	// serves perses.dev/v1alpha2 PersesDashboard. Releases up to v0.2.0 serve
	// v1alpha1 only, with the dashboard spec directly under spec and no
	// instanceSelector. Such clusters are reported as unsupported rather than
	// written with a second, incompatible shape: the CRD declares no conversion
	// webhook and upstream reconciles v1alpha2 only, so a v1alpha1 object would
	// not become a usable dashboard on any release.
	persesOperatorMinimumVersion = "v0.3.0"

	// dashboardLabel marks the generated dashboard for discovery.
	dashboardLabel = "jumpstarter.dev/dashboards"

	defaultPrometheusDatasource = "prometheus"
	defaultLokiDatasource       = "loki"
)

// dashboardsEnabled reports whether Perses dashboards are enabled. Unset means
// disabled; the CRD default is false.
func dashboardsEnabled(js *operatorv1alpha1.Jumpstarter) bool {
	if js.Spec.Telemetry == nil || js.Spec.Telemetry.Metrics.Dashboards == nil {
		return false
	}
	return *js.Spec.Telemetry.Metrics.Dashboards
}

// persesInstanceSelector returns the label selector used for the Perses
// instance and datasource CRs.
func persesInstanceSelector(js *operatorv1alpha1.Jumpstarter) map[string]string {
	if js.Spec.Telemetry == nil {
		return nil
	}
	return js.Spec.Telemetry.Metrics.PersesInstanceSelector
}

// persesDashboardName returns the dashboard name for a Jumpstarter CR.
func persesDashboardName(js *operatorv1alpha1.Jumpstarter) string {
	return fmt.Sprintf("%s-overview", js.Name)
}

// persesDashboardLabels returns the labels shared by the generated dashboard.
// User-supplied labels are applied first so they cannot override the stable
// selector label.
func persesDashboardLabels(js *operatorv1alpha1.Jumpstarter) map[string]string {
	labels := make(map[string]string, 4)
	if js.Spec.Telemetry != nil {
		maps.Copy(labels, js.Spec.Telemetry.Metrics.DashboardLabels)
	}
	labels["app.kubernetes.io/name"] = "jumpstarter"
	labels["app.kubernetes.io/instance"] = js.Name
	labels[dashboardLabel] = "true"
	return labels
}

// prometheusDatasourceName returns the Prometheus datasource name used by the
// generated dashboard and variables.
func prometheusDatasourceName(js *operatorv1alpha1.Jumpstarter) string {
	if js.Spec.Telemetry != nil && js.Spec.Telemetry.Metrics.PrometheusDatasource != "" {
		return js.Spec.Telemetry.Metrics.PrometheusDatasource
	}
	return defaultPrometheusDatasource
}

// lokiDatasourceName returns the Loki datasource name used by the generated
// log panel.
func lokiDatasourceName(js *operatorv1alpha1.Jumpstarter) string {
	if js.Spec.Telemetry != nil && js.Spec.Telemetry.Metrics.LokiDatasource != "" {
		return js.Spec.Telemetry.Metrics.LokiDatasource
	}
	return defaultLokiDatasource
}

// persesDashboardCRDAvailable reports whether perses.dev/v1alpha2 serves the
// PersesDashboard kind. When it does not, servedVersions lists the versions the
// installed persesdashboards.perses.dev CRD serves, so callers can tell an
// absent CRD from an older perses-operator release that serves v1alpha1 only.
func (r *JumpstarterReconciler) persesDashboardCRDAvailable(ctx context.Context) (bool, []string, error) {
	list := &unstructured.UnstructuredList{}
	list.SetGroupVersionKind(schema.GroupVersionKind{
		Group: persesGroup, Version: persesVersion, Kind: persesDashboardKind + "List",
	})
	if err := r.List(ctx, list, client.Limit(1)); err != nil {
		if apimeta.IsNoMatchError(err) {
			served, versionErr := r.servedPersesDashboardVersions(ctx)
			if versionErr != nil {
				return false, nil, versionErr
			}
			return false, served, nil
		}
		return false, nil, err
	}
	return true, nil, nil
}

// servedPersesDashboardVersions returns the served versions recorded in the
// installed persesdashboards.perses.dev CRD, sorted, or nil when the CRD is
// absent.
func (r *JumpstarterReconciler) servedPersesDashboardVersions(ctx context.Context) ([]string, error) {
	crd := &apiextensionsv1.CustomResourceDefinition{}
	err := r.Get(ctx, types.NamespacedName{Name: persesDashboardCRDName}, crd)
	if apierrors.IsNotFound(err) {
		return nil, nil
	}
	if err != nil {
		return nil, fmt.Errorf("get %s: %w", persesDashboardCRDName, err)
	}

	served := make([]string, 0, len(crd.Spec.Versions))
	for _, version := range crd.Spec.Versions {
		if version.Served {
			served = append(served, version.Name)
		}
	}
	slices.Sort(served)
	return served, nil
}

// dashboardCRDUnavailableMessage explains why the PersesDashboard API cannot be
// used. A legacy perses-operator that serves v1alpha1 only is named explicitly
// so the fix (upgrade the operator) is obvious from the condition message.
func dashboardCRDUnavailableMessage(servedVersions []string) string {
	generic := fmt.Sprintf(
		"perses.dev/%s PersesDashboard CRD is not installed; install perses-operator %s or newer",
		persesVersion, persesOperatorMinimumVersion)

	if len(servedVersions) == 0 || slices.Contains(servedVersions, persesVersion) {
		return generic
	}

	return fmt.Sprintf(
		"persesdashboards.perses.dev serves only %s; perses-operator %s or newer (perses.dev/%s) is required",
		strings.Join(servedVersions, ", "), persesOperatorMinimumVersion, persesVersion)
}

// desiredPersesDashboard returns the overview dashboard for a Jumpstarter CR.
// The datasource selector lives inside each query plugin spec and references
// the configured global/project datasource by name; no dashboard-level
// datasources map is emitted.
func desiredPersesDashboard(js *operatorv1alpha1.Jumpstarter) *unstructured.Unstructured {
	dashboard := &unstructured.Unstructured{}
	dashboard.SetGroupVersionKind(schema.GroupVersionKind{
		Group: persesGroup, Version: persesVersion, Kind: persesDashboardKind,
	})
	dashboard.SetName(persesDashboardName(js))
	dashboard.SetNamespace(js.Namespace)
	dashboard.SetLabels(persesDashboardLabels(js))

	prometheusDatasource := prometheusDatasourceName(js)
	lokiDatasource := lokiDatasourceName(js)

	panels := map[string]any{}
	panelKeys := make([]string, 0, 10)
	addPanel := func(key, title, plugin string, queries ...any) {
		panels[key] = dashboardPanel(title, plugin, queries...)
		panelKeys = append(panelKeys, key)
	}

	addPanel("operationFailureRate", "Operation failure rate", "TimeSeriesChart",
		prometheusQuery(`sum by (exporter, operation) (
  rate(jumpstarter_operations_total{result="failure"}[5m])
)
/
sum by (exporter, operation) (
  rate(jumpstarter_operations_total[5m])
)`, prometheusDatasource))
	addPanel("operationDurationP95", "Operation duration p95", "TimeSeriesChart",
		prometheusQuery(`histogram_quantile(0.95,
  sum by (operation, le) (
    rate(jumpstarter_operation_duration_seconds_bucket[5m])
  )
)`, prometheusDatasource))
	addPanel("operationErrors", "Errors by type", "TimeSeriesChart",
		prometheusQuery(`sum by (exporter, error_type) (
  increase(jumpstarter_operation_errors_total[5m])
)`, prometheusDatasource))
	addPanel("activeSessions", "Active sessions", "TimeSeriesChart",
		prometheusQuery(`sum by (exporter) (jumpstarter_active_sessions)`, prometheusDatasource))
	addPanel("streamThroughput", "Stream throughput", "TimeSeriesChart",
		prometheusQuery(`sum by (exporter, direction) (
  rate(jumpstarter_stream_bytes_total[5m])
)`, prometheusDatasource))
	addPanel("leaseAcquisitions", "Lease acquisitions", "TimeSeriesChart",
		prometheusQuery(`sum by (result) (
  rate(jumpstarter_lease_acquisitions_total[5m])
)`, prometheusDatasource))
	addPanel("topExporters", "Top exporters by operations", "TimeSeriesChart",
		prometheusQuery(`topk(5, sum by (exporter) (
  rate(jumpstarter_operations_total[1h])
))`, prometheusDatasource))
	addPanel("telemetryDrops", "Telemetry drops", "TimeSeriesChart",
		prometheusQuery(`increase(jumpstarter_telemetry_dropped_total[5m])`, prometheusDatasource))
	addPanel("scrapeTimeouts", "Scrape timeouts", "TimeSeriesChart",
		prometheusQuery(`topk(10, sum by (exporter) (
  increase(jumpstarter_scrape_timeouts_total[30m])
))`, prometheusDatasource))
	addPanel("leaseLogs", "Lease logs", "LogsTable",
		lokiQuery(`{component="exporter"} | json | lease_id="$lease_id"`, lokiDatasource))

	variables := []any{
		prometheusListVariable("exporter", "exporter", prometheusDatasource),
		prometheusListVariable("driver_type", "driver_type", prometheusDatasource),
		textVariable("lease_id"),
	}

	dashboard.Object["spec"] = map[string]any{
		"instanceSelector": map[string]any{
			"matchLabels": toInterfaceMap(persesInstanceSelector(js)),
		},
		"config": map[string]any{
			"display": map[string]any{
				"name":        "Jumpstarter Overview",
				"description": "Controller, router, exporter, and telemetry health.",
			},
			"duration":        "1h",
			"refreshInterval": "30s",
			"variables":       variables,
			"panels":          panels,
			"layouts": []any{
				dashboardLayout(panelKeys, 2, 6, 4),
			},
		},
	}
	return dashboard
}

// dashboardPanel builds one Perses panel.
func dashboardPanel(title, pluginKind string, queries ...any) map[string]any {
	return map[string]any{
		"kind": "Panel",
		"spec": map[string]any{
			"display": map[string]any{"name": title},
			"plugin":  map[string]any{"kind": pluginKind, "spec": map[string]any{}},
			"queries": queries,
		},
	}
}

// prometheusQuery builds a Prometheus time-series query with a datasource
// selector.
func prometheusQuery(query, datasource string) map[string]any {
	return map[string]any{
		"kind": "TimeSeriesQuery",
		"spec": map[string]any{
			"plugin": map[string]any{
				"kind": "PrometheusTimeSeriesQuery",
				"spec": map[string]any{
					"datasource": map[string]any{
						"kind": "PrometheusDatasource",
						"name": datasource,
					},
					"query": query,
				},
			},
		},
	}
}

// lokiQuery builds a Loki log query with a datasource selector.
func lokiQuery(query, datasource string) map[string]any {
	return map[string]any{
		"kind": "LogQuery",
		"spec": map[string]any{
			"plugin": map[string]any{
				"kind": "LokiLogQuery",
				"spec": map[string]any{
					"datasource": map[string]any{
						"kind": "LokiDatasource",
						"name": datasource,
					},
					"query": query,
				},
			},
		},
	}
}

// prometheusListVariable builds a list variable backed by a Prometheus
// datasource.
func prometheusListVariable(name, labelName, datasource string) map[string]any {
	return map[string]any{
		"kind": "ListVariable",
		"spec": map[string]any{
			"name":          name,
			"allowMultiple": true,
			"allowAllValue": true,
			"plugin": map[string]any{
				"kind": "PrometheusLabelValuesVariable",
				"spec": map[string]any{
					"datasource": map[string]any{
						"kind": "PrometheusDatasource",
						"name": datasource,
					},
					"labelName": labelName,
				},
			},
		},
	}
}

// textVariable builds a free-text variable.
func textVariable(name string) map[string]any {
	return map[string]any{
		"kind": "TextVariable",
		"spec": map[string]any{"name": name, "value": ""},
	}
}

// dashboardLayout builds a grid layout referencing every panel.
func dashboardLayout(keys []string, perRow, width, height int) map[string]any {
	items := make([]any, 0, len(keys))
	for i, key := range keys {
		items = append(items, map[string]any{
			"x":      int64((i % perRow) * width),
			"y":      int64((i / perRow) * height),
			"width":  int64(width),
			"height": int64(height),
			"content": map[string]any{
				"$ref": "#/spec/panels/" + key,
			},
		})
	}
	return map[string]any{
		"kind": "Grid",
		"spec": map[string]any{
			"display": map[string]any{"title": "Jumpstarter"},
			"items":   items,
		},
	}
}

// reconcilePersesDashboards creates, updates, or removes the overview
// dashboard.
func (r *JumpstarterReconciler) reconcilePersesDashboards(ctx context.Context, js *operatorv1alpha1.Jumpstarter) (integrationResult, error) {
	if !dashboardsEnabled(js) {
		result := integrationResult{
			reason:  serviceMonitorReasonDisabled,
			message: "Perses dashboards are disabled by spec.telemetry.metrics.dashboards",
		}
		if err := r.prunePersesDashboards(ctx, js, nil); err != nil {
			// The prune failed, so owned dashboards may still be served: report
			// the failure instead of a clean Disabled.
			return integrationResult{
				reason: serviceMonitorReasonFailed,
				message: fmt.Sprintf(
					"failed to remove Perses dashboards while they are disabled: %v",
					err,
				),
			}, fmt.Errorf("perses dashboards: %w", err)
		}
		return result, nil
	}

	if len(persesInstanceSelector(js)) == 0 {
		return integrationResult{
			reason:  serviceMonitorReasonNotConfigured,
			message: "dashboards are enabled but spec.telemetry.metrics.persesInstanceSelector is empty",
		}, nil
	}

	available, servedVersions, err := r.persesDashboardCRDAvailable(ctx)
	if err != nil {
		logf.FromContext(ctx).Error(err, "Failed to detect PersesDashboard CRD; leaving dashboards unchanged")
		return integrationResult{
			reason:  serviceMonitorReasonFailed,
			message: fmt.Sprintf("failed to detect PersesDashboard CRD: %v", err),
		}, fmt.Errorf("perses dashboard CRD detection: %w", err)
	}
	if !available {
		return integrationResult{
			reason:  serviceMonitorReasonCRDNotFound,
			message: dashboardCRDUnavailableMessage(servedVersions),
		}, nil
	}

	desired := desiredPersesDashboard(js)
	existing := &unstructured.Unstructured{}
	existing.SetGroupVersionKind(desired.GroupVersionKind())
	err = r.Get(ctx, types.NamespacedName{Name: desired.GetName(), Namespace: desired.GetNamespace()}, existing)
	switch {
	case apierrors.IsNotFound(err):
		if err := controllerutil.SetControllerReference(js, desired, r.Scheme); err != nil {
			return applyFailedDashboardResult(err), fmt.Errorf("set owner reference: %w", err)
		}
		if err := r.Create(ctx, desired); err != nil {
			return applyFailedDashboardResult(err), fmt.Errorf("create PersesDashboard %s: %w", desired.GetName(), err)
		}
		logf.FromContext(ctx).Info("Created PersesDashboard", "name", desired.GetName())
	case err != nil:
		return applyFailedDashboardResult(err), fmt.Errorf("get PersesDashboard %s: %w", desired.GetName(), err)
	default:
		if err := ensureControlledBy(js, existing); err != nil {
			return applyFailedDashboardResult(err), fmt.Errorf("refusing to update PersesDashboard %s: %w", desired.GetName(), err)
		}
		if equality.Semantic.DeepEqual(existing.Object["spec"], desired.Object["spec"]) &&
			equality.Semantic.DeepEqual(existing.GetLabels(), desired.GetLabels()) {
			break
		}
		existing.Object["spec"] = desired.Object["spec"]
		existing.SetLabels(desired.GetLabels())
		if err := controllerutil.SetControllerReference(js, existing, r.Scheme); err != nil {
			return applyFailedDashboardResult(err), fmt.Errorf("set owner reference: %w", err)
		}
		if err := r.Update(ctx, existing); err != nil {
			return applyFailedDashboardResult(err), fmt.Errorf("update PersesDashboard %s: %w", desired.GetName(), err)
		}
		logf.FromContext(ctx).Info("Updated PersesDashboard", "name", desired.GetName())
	}

	desiredNames := map[string]struct{}{desired.GetName(): {}}
	if err := r.prunePersesDashboards(ctx, js, desiredNames); err != nil {
		return applyFailedDashboardResult(err), fmt.Errorf("prune Perses dashboards: %w", err)
	}
	return activeDashboardResult(), nil
}

func activeDashboardResult() integrationResult {
	return integrationResult{
		reason:  serviceMonitorReasonCreated,
		message: "Perses dashboard is configured",
	}
}

func applyFailedDashboardResult(err error) integrationResult {
	return integrationResult{
		reason:  serviceMonitorReasonFailed,
		message: fmt.Sprintf("failed to reconcile Perses dashboards: %v", err),
	}
}

// prunePersesDashboards deletes dashboards owned by this Jumpstarter CR that
// are not desired. A nil desired set prunes every owned dashboard.
func (r *JumpstarterReconciler) prunePersesDashboards(ctx context.Context, js *operatorv1alpha1.Jumpstarter, desired map[string]struct{}) error {
	list := &unstructured.UnstructuredList{}
	list.SetGroupVersionKind(schema.GroupVersionKind{
		Group: persesGroup, Version: persesVersion, Kind: persesDashboardKind + "List",
	})
	if err := r.List(ctx, list, client.InNamespace(js.Namespace)); err != nil {
		if apimeta.IsNoMatchError(err) {
			return nil
		}
		return fmt.Errorf("list PersesDashboards: %w", err)
	}

	for i := range list.Items {
		dashboard := &list.Items[i]
		if _, ok := desired[dashboard.GetName()]; ok {
			continue
		}
		if !metav1.IsControlledBy(dashboard, js) {
			continue
		}
		if err := r.Delete(ctx, dashboard); err != nil && !apierrors.IsNotFound(err) && !apimeta.IsNoMatchError(err) {
			return fmt.Errorf("delete stale PersesDashboard %s: %w", dashboard.GetName(), err)
		}
		logf.FromContext(ctx).Info("Deleted stale PersesDashboard", "name", dashboard.GetName())
	}
	return nil
}
