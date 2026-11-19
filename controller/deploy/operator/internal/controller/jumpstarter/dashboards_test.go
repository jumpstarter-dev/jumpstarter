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
	"strings"
	"testing"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

var persesDashboardGVK = schema.GroupVersionKind{
	Group: persesGroup, Version: persesVersion, Kind: persesDashboardKind,
}

func TestDashboardsEnabled(t *testing.T) {
	tests := []struct {
		name string
		js   *operatorv1alpha1.Jumpstarter
		want bool
	}{
		{"nil telemetry", testJumpstarter(), false},
		{"unset telemetry metrics", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			return js
		}(), false},
		{"explicit true", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			js.Spec.Telemetry.Metrics.Dashboards = new(true)
			return js
		}(), true},
		{"explicit false", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			js.Spec.Telemetry.Metrics.Dashboards = new(false)
			return js
		}(), false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := dashboardsEnabled(tc.js); got != tc.want {
				t.Fatalf("dashboardsEnabled() = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestDesiredPersesDashboard(t *testing.T) {
	js := testJumpstarter()
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	js.Spec.Telemetry.Metrics.DashboardLabels = map[string]string{
		"perses.dev/instance": "perses-sample",
		dashboardLabel:        "false",
	}
	js.Spec.Telemetry.Metrics.PersesInstanceSelector = map[string]string{
		"app.kubernetes.io/instance": "perses-sample",
	}
	js.Spec.Telemetry.Metrics.PrometheusDatasource = "prom"
	js.Spec.Telemetry.Metrics.LokiDatasource = "logs"

	dashboard := desiredPersesDashboard(js)
	if got, want := dashboard.GetName(), "jumpstarter-overview"; got != want {
		t.Errorf("name = %q, want %q", got, want)
	}
	if got, want := dashboard.GetNamespace(), "lab"; got != want {
		t.Errorf("namespace = %q, want %q", got, want)
	}
	if dashboard.GroupVersionKind() != persesDashboardGVK {
		t.Errorf("GVK = %v, want %v", dashboard.GroupVersionKind(), persesDashboardGVK)
	}

	labels := dashboard.GetLabels()
	if labels[dashboardLabel] != "true" {
		t.Errorf("%s = %q, want true", dashboardLabel, labels[dashboardLabel])
	}
	if labels["perses.dev/instance"] != "perses-sample" {
		t.Errorf("perses instance label = %q, want perses-sample", labels["perses.dev/instance"])
	}

	spec, ok := dashboard.Object["spec"].(map[string]any)
	if !ok {
		t.Fatalf("spec is %T, want map[string]any", dashboard.Object["spec"])
	}
	selector, ok := spec["instanceSelector"].(map[string]any)
	if !ok {
		t.Fatalf("instanceSelector is %T, want map[string]any", spec["instanceSelector"])
	}
	matchLabels, ok := selector["matchLabels"].(map[string]any)
	if !ok || matchLabels["app.kubernetes.io/instance"] != "perses-sample" {
		t.Fatalf("instanceSelector.matchLabels = %#v", selector["matchLabels"])
	}

	config, ok := spec["config"].(map[string]any)
	if !ok {
		t.Fatalf("config is %T, want map[string]any", spec["config"])
	}
	if _, ok := config["datasources"]; ok {
		t.Error("dashboard config must not contain a datasources map")
	}
	panels, ok := config["panels"].(map[string]any)
	if !ok || len(panels) != 10 {
		t.Fatalf("panels = %#v, want 10 panels", config["panels"])
	}
	variables, ok := config["variables"].([]any)
	if !ok || len(variables) != 3 {
		t.Fatalf("variables = %#v, want 3 variables", config["variables"])
	}
	layouts, ok := config["layouts"].([]any)
	if !ok || len(layouts) != 1 {
		t.Fatalf("layouts = %#v, want 1 layout", config["layouts"])
	}

	assertPrometheusPanel(t, panels, "operationFailureRate", "prom")
	assertLokiPanel(t, panels, "leaseLogs", "logs")
	assertLayoutReferences(t, layouts[0], panels)
}

func assertPrometheusPanel(t *testing.T, panels map[string]any, key, datasource string) {
	t.Helper()
	panel, ok := panels[key].(map[string]any)
	if !ok {
		t.Fatalf("panel %q missing", key)
	}
	panelSpec, ok := panel["spec"].(map[string]any)
	if !ok {
		t.Fatalf("panel %q spec is %T", key, panel["spec"])
	}
	queries, ok := panelSpec["queries"].([]any)
	if !ok || len(queries) == 0 {
		t.Fatalf("panel %q has no queries", key)
	}
	query, ok := queries[0].(map[string]any)
	if !ok {
		t.Fatalf("panel %q query is %T", key, queries[0])
	}
	if query["kind"] != "TimeSeriesQuery" {
		t.Errorf("panel %q query kind = %v, want TimeSeriesQuery", key, query["kind"])
	}
	qspec, ok := query["spec"].(map[string]any)
	if !ok {
		t.Fatalf("panel %q query spec is %T", key, query["spec"])
	}
	plugin, ok := qspec["plugin"].(map[string]any)
	if !ok {
		t.Fatalf("panel %q query plugin is %T", key, qspec["plugin"])
	}
	if plugin["kind"] != "PrometheusTimeSeriesQuery" {
		t.Errorf("panel %q plugin kind = %v, want PrometheusTimeSeriesQuery", key, plugin["kind"])
	}
	pspec, ok := plugin["spec"].(map[string]any)
	if !ok {
		t.Fatalf("panel %q plugin spec is %T", key, plugin["spec"])
	}
	ds, ok := pspec["datasource"].(map[string]any)
	if !ok {
		t.Fatalf("panel %q datasource is %T", key, pspec["datasource"])
	}
	if ds["kind"] != "PrometheusDatasource" || ds["name"] != datasource {
		t.Errorf("panel %q datasource = %#v", key, ds)
	}
	if expr, _ := pspec["query"].(string); !strings.Contains(expr, "jumpstarter_") {
		t.Errorf("panel %q query = %q", key, expr)
	}
}

func assertLokiPanel(t *testing.T, panels map[string]any, key, datasource string) {
	t.Helper()
	panel, ok := panels[key].(map[string]any)
	if !ok {
		t.Fatalf("panel %q missing", key)
	}
	panelSpec := panel["spec"].(map[string]any)
	queries := panelSpec["queries"].([]any)
	query := queries[0].(map[string]any)
	if query["kind"] != "LogQuery" {
		t.Errorf("panel %q query kind = %v, want LogQuery", key, query["kind"])
	}
	qspec := query["spec"].(map[string]any)
	plugin := qspec["plugin"].(map[string]any)
	if plugin["kind"] != "LokiLogQuery" {
		t.Errorf("panel %q plugin kind = %v, want LokiLogQuery", key, plugin["kind"])
	}
	pspec := plugin["spec"].(map[string]any)
	ds := pspec["datasource"].(map[string]any)
	if ds["kind"] != "LokiDatasource" || ds["name"] != datasource {
		t.Errorf("panel %q datasource = %#v", key, ds)
	}
}

func assertLayoutReferences(t *testing.T, layout any, panels map[string]any) {
	t.Helper()
	layoutMap, ok := layout.(map[string]any)
	if !ok {
		t.Fatalf("layout is %T", layout)
	}
	lspec, ok := layoutMap["spec"].(map[string]any)
	if !ok {
		t.Fatalf("layout spec is %T", layoutMap["spec"])
	}
	items, ok := lspec["items"].([]any)
	if !ok || len(items) != len(panels) {
		t.Fatalf("layout items = %#v, want one per panel", lspec["items"])
	}
	for _, item := range items {
		content, ok := item.(map[string]any)["content"].(map[string]any)
		if !ok {
			t.Fatalf("layout item content = %#v", item)
		}
		ref, _ := content["$ref"].(string)
		key := strings.TrimPrefix(ref, "#/spec/panels/")
		if _, ok := panels[key]; !ok {
			t.Errorf("layout references unknown panel %q", key)
		}
	}
}

func TestReconcilePersesDashboardsNotConfigured(t *testing.T) {
	js := testJumpstarter()
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	js.Spec.Telemetry.Metrics.Dashboards = new(true)

	r := &JumpstarterReconciler{}
	result, err := r.reconcilePersesDashboards(context.Background(), js)
	if err != nil {
		t.Fatalf("reconcilePersesDashboards() error = %v", err)
	}
	if result.phase != integrationNotConfigured || result.reason != serviceMonitorReasonNotConfigured {
		t.Fatalf("result = %v/%q, want %v/%s", result.phase, result.reason, integrationNotConfigured, serviceMonitorReasonNotConfigured)
	}
}

// unstructuredListClient returns an empty result for unstructured lists so the
// disabled dashboard prune path does not need a registered CRD in the fake
// client.
type unstructuredListClient struct {
	client.Client
}

func (c *unstructuredListClient) List(ctx context.Context, list client.ObjectList, opts ...client.ListOption) error {
	if _, ok := list.(*unstructured.UnstructuredList); ok {
		return nil
	}
	return c.Client.List(ctx, list, opts...)
}

func TestReconcilePersesDashboardsDisabled(t *testing.T) {
	s := runtime.NewScheme()
	wrapped := &unstructuredListClient{Client: fake.NewClientBuilder().WithScheme(s).Build()}
	r := &JumpstarterReconciler{Client: wrapped, Scheme: s}

	result, err := r.reconcilePersesDashboards(context.Background(), testJumpstarter())
	if err != nil {
		t.Fatalf("reconcilePersesDashboards() error = %v", err)
	}
	if result.phase != integrationDisabled || result.reason != serviceMonitorReasonDisabled {
		t.Fatalf("result = %v/%q, want %v/%s", result.phase, result.reason, integrationDisabled, serviceMonitorReasonDisabled)
	}
}
