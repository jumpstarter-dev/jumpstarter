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
	"slices"
	"strings"
	"testing"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
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
	if result.reason != serviceMonitorReasonNotConfigured {
		t.Fatalf("reason = %q, want %s", result.reason, serviceMonitorReasonNotConfigured)
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
	if result.reason != serviceMonitorReasonDisabled {
		t.Fatalf("reason = %q, want %s", result.reason, serviceMonitorReasonDisabled)
	}
}

// A failed prune while dashboards are disabled must not be reported as a clean
// Disabled condition: owned dashboards may still be served.
func TestReconcilePersesDashboardsReportsPruneFailureWhenDisabled(t *testing.T) {
	s := runtime.NewScheme()
	if err := operatorv1alpha1.AddToScheme(s); err != nil {
		t.Fatalf("add operator scheme: %v", err)
	}

	js := testJumpstarter()
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	js.Spec.Telemetry.Metrics.Dashboards = new(false)

	owned := &unstructured.Unstructured{}
	owned.SetGroupVersionKind(persesDashboardGVK)
	owned.SetName("jumpstarter-overview")
	owned.SetNamespace(js.Namespace)
	owned.SetOwnerReferences([]metav1.OwnerReference{
		*metav1.NewControllerRef(js, operatorv1alpha1.GroupVersion.WithKind("Jumpstarter")),
	})

	base := fake.NewClientBuilder().WithScheme(s).WithObjects(owned).Build()
	r := &JumpstarterReconciler{Client: &deleteConflictClient{Client: base}, Scheme: s}

	result, err := r.reconcilePersesDashboards(context.Background(), js)
	if err == nil {
		t.Fatal("reconcilePersesDashboards should report the prune failure for retry")
	}
	if result.reason != serviceMonitorReasonFailed {
		t.Fatalf("reason = %q, want %s", result.reason, serviceMonitorReasonFailed)
	}
	if !strings.Contains(result.message, "dashboards") || !strings.Contains(result.message, "disabled") {
		t.Fatalf("message %q must name the cleanup failure and the disabled context", result.message)
	}
}

func TestDashboardCRDUnavailableMessage(t *testing.T) {
	tests := []struct {
		name   string
		served []string
		want   []string
		absent []string
	}{
		{
			name:   "crd absent",
			served: nil,
			want:   []string{"not installed", "v0.3.0", "perses.dev/v1alpha2"},
		},
		{
			name:   "legacy v1alpha1 only",
			served: []string{"v1alpha1"},
			want:   []string{"serves only v1alpha1", "v0.3.0", "perses.dev/v1alpha2"},
			absent: []string{"not installed"},
		},
		{
			name:   "v1alpha2 served but undiscoverable",
			served: []string{"v1alpha1", "v1alpha2"},
			want:   []string{"not installed", "v0.3.0"},
			absent: []string{"serves only"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			message := dashboardCRDUnavailableMessage(tt.served)
			for _, want := range tt.want {
				if !strings.Contains(message, want) {
					t.Errorf("message %q must contain %q", message, want)
				}
			}
			for _, absent := range tt.absent {
				if strings.Contains(message, absent) {
					t.Errorf("message %q must not contain %q", message, absent)
				}
			}
		})
	}
}

// noMatchListClient returns a NoMatch error for unstructured lists, emulating a
// cluster whose RESTMapper does not serve perses.dev/v1alpha2.
type noMatchListClient struct {
	client.Client
}

func (c *noMatchListClient) List(ctx context.Context, list client.ObjectList, opts ...client.ListOption) error {
	if _, ok := list.(*unstructured.UnstructuredList); ok {
		return &apimeta.NoKindMatchError{
			GroupKind: schema.GroupKind{Group: persesGroup, Kind: persesDashboardKind + "List"},
		}
	}
	return c.Client.List(ctx, list, opts...)
}

func TestServedPersesDashboardVersions(t *testing.T) {
	newCRD := func(versions ...apiextensionsv1.CustomResourceDefinitionVersion) *apiextensionsv1.CustomResourceDefinition {
		return &apiextensionsv1.CustomResourceDefinition{
			ObjectMeta: metav1.ObjectMeta{Name: persesDashboardCRDName},
			Spec:       apiextensionsv1.CustomResourceDefinitionSpec{Versions: versions},
		}
	}
	served := func(name string, isServed bool) apiextensionsv1.CustomResourceDefinitionVersion {
		return apiextensionsv1.CustomResourceDefinitionVersion{Name: name, Served: isServed}
	}

	tests := []struct {
		name   string
		crd    *apiextensionsv1.CustomResourceDefinition
		want   []string
		absent bool
	}{
		{name: "crd absent", want: nil, absent: true},
		{
			name: "legacy v1alpha1 only",
			crd:  newCRD(served("v1alpha1", true)),
			want: []string{"v1alpha1"},
		},
		{
			name: "v1alpha1 deprecated but served",
			crd:  newCRD(served("v1alpha2", true), served("v1alpha1", true)),
			want: []string{"v1alpha1", "v1alpha2"},
		},
		{
			name: "unserved versions are ignored",
			crd:  newCRD(served("v1alpha1", false), served("v1alpha2", true)),
			want: []string{"v1alpha2"},
		},
	}

	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			s := runtime.NewScheme()
			if err := apiextensionsv1.AddToScheme(s); err != nil {
				t.Fatalf("add scheme: %v", err)
			}
			builder := fake.NewClientBuilder().WithScheme(s)
			if tt.crd != nil {
				builder = builder.WithObjects(tt.crd)
			}
			fakeClient := builder.Build()
			r := &JumpstarterReconciler{Client: fakeClient, Scheme: s}

			got, err := r.servedPersesDashboardVersions(context.Background())
			if err != nil {
				t.Fatalf("servedPersesDashboardVersions() error = %v", err)
			}
			if !slices.Equal(got, tt.want) {
				t.Fatalf("servedPersesDashboardVersions() = %v, want %v", got, tt.want)
			}

			probe := &JumpstarterReconciler{
				Client: &noMatchListClient{Client: fakeClient},
				Scheme: s,
			}
			available, versions, err := probe.persesDashboardCRDAvailable(context.Background())
			if err != nil {
				t.Fatalf("persesDashboardCRDAvailable() error = %v", err)
			}
			if available {
				t.Fatal("persesDashboardCRDAvailable() = true, want false")
			}
			if !slices.Equal(versions, tt.want) {
				t.Fatalf("persesDashboardCRDAvailable() versions = %v, want %v", versions, tt.want)
			}
		})
	}
}
