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
	"errors"
	"flag"
	"os"
	"os/exec"
	"path/filepath"
	"reflect"
	"strings"
	"testing"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
	"sigs.k8s.io/yaml"
)

func testJumpstarter() *operatorv1alpha1.Jumpstarter {
	return &operatorv1alpha1.Jumpstarter{
		ObjectMeta: metav1.ObjectMeta{Name: "jumpstarter", Namespace: "lab"},
	}
}

func boolPtr(v bool) *bool { return &v }

func TestServiceMonitorEnabled(t *testing.T) {
	tests := []struct {
		name string
		js   *operatorv1alpha1.Jumpstarter
		want bool
	}{
		{"nil telemetry defaults true", testJumpstarter(), true},
		{"empty metrics defaults true", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			return js
		}(), true},
		{"explicit false", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			js.Spec.Telemetry.Metrics.ServiceMonitor = boolPtr(false)
			return js
		}(), false},
		{"explicit true", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			js.Spec.Telemetry.Metrics.ServiceMonitor = boolPtr(true)
			return js
		}(), true},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := serviceMonitorEnabled(tc.js); got != tc.want {
				t.Errorf("serviceMonitorEnabled() = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestShipperType(t *testing.T) {
	tests := []struct {
		name string
		js   *operatorv1alpha1.Jumpstarter
		want string
	}{
		{"nil telemetry", testJumpstarter(), shipperTypeNone},
		{"empty type", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			return js
		}(), shipperTypeNone},
		{"promtail", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			js.Spec.Telemetry.Logging.Shipper.Type = shipperTypePromtail
			return js
		}(), shipperTypePromtail},
		{"alloy", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			js.Spec.Telemetry.Logging.Shipper.Type = shipperTypeAlloy
			return js
		}(), shipperTypeAlloy},
		{"vector", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			js.Spec.Telemetry.Logging.Shipper.Type = shipperTypeVector
			return js
		}(), shipperTypeVector},
		{"unknown falls back to none", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			js.Spec.Telemetry.Logging.Shipper.Type = "fluentbit"
			return js
		}(), shipperTypeNone},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := shipperType(tc.js); got != tc.want {
				t.Errorf("shipperType() = %q, want %q", got, tc.want)
			}
		})
	}
}

// serviceMonitorListErrorClient simulates a transient API/RBAC failure while
// detecting the optional ServiceMonitor CRD.
type serviceMonitorListErrorClient struct {
	client.Client
	listErr               error
	deletedServiceMonitor bool
}

func (c *serviceMonitorListErrorClient) List(ctx context.Context, list client.ObjectList, opts ...client.ListOption) error {
	if _, ok := list.(*unstructured.UnstructuredList); ok {
		return c.listErr
	}
	return c.Client.List(ctx, list, opts...)
}

func (c *serviceMonitorListErrorClient) Delete(ctx context.Context, obj client.Object, opts ...client.DeleteOption) error {
	if obj.GetObjectKind().GroupVersionKind().Kind == serviceMonitorKind {
		c.deletedServiceMonitor = true
	}
	return c.Client.Delete(ctx, obj, opts...)
}

func TestReconcileObservabilityDoesNotDeleteServiceMonitorsOnDetectionError(t *testing.T) {
	s := runtime.NewScheme()
	if err := corev1.AddToScheme(s); err != nil {
		t.Fatalf("add core scheme: %v", err)
	}
	if err := operatorv1alpha1.AddToScheme(s); err != nil {
		t.Fatalf("add operator scheme: %v", err)
	}

	wrapped := &serviceMonitorListErrorClient{
		Client:  fake.NewClientBuilder().WithScheme(s).Build(),
		listErr: errors.New("transient apiserver error"),
	}
	r := &JumpstarterReconciler{Client: wrapped, Scheme: s}

	err := r.reconcileObservability(context.Background(), testJumpstarter())
	if err == nil {
		t.Fatal("reconcileObservability should report the CRD detection error for retry")
	}
	if !strings.Contains(err.Error(), "service monitor CRD detection") {
		t.Fatalf("unexpected reconcileObservability error: %v", err)
	}
	if wrapped.deletedServiceMonitor {
		t.Fatal("ServiceMonitors must not be deleted when CRD detection fails")
	}
}

func TestDesiredMetricsServices(t *testing.T) {
	js := testJumpstarter()
	services := desiredMetricsServices(js)
	if len(services) != 2 {
		t.Fatalf("expected 2 metrics services, got %d", len(services))
	}

	want := map[string]map[string]string{
		"jumpstarter-controller-metrics": {"component": "controller", "controller": js.Name},
		"jumpstarter-router-metrics":     {"component": "router", "router": js.Name},
	}
	for _, svc := range services {
		selector, ok := want[svc.Name]
		if !ok {
			t.Errorf("unexpected service %q", svc.Name)
			continue
		}
		if svc.Namespace != js.Namespace {
			t.Errorf("%s namespace = %q, want %q", svc.Name, svc.Namespace, js.Namespace)
		}
		if !reflect.DeepEqual(svc.Spec.Selector, selector) {
			t.Errorf("%s selector = %v, want %v", svc.Name, svc.Spec.Selector, selector)
		}
		if len(svc.Spec.Ports) != 1 {
			t.Fatalf("%s expected one port", svc.Name)
		}
		port := svc.Spec.Ports[0]
		if port.Name != metricsPortName || port.Port != metricsPort || port.TargetPort.String() != metricsPortName {
			t.Errorf("%s port = %+v, want name/port/target %s/%d/%s", svc.Name, port, metricsPortName, metricsPort, metricsPortName)
		}
		if svc.Labels[metricsServiceLabel] != "true" {
			t.Errorf("%s missing %s label", svc.Name, metricsServiceLabel)
		}
	}
}

func TestDesiredServiceMonitors(t *testing.T) {
	js := testJumpstarter()
	if got := len(desiredServiceMonitors(js)); got != 2 {
		t.Fatalf("without telemetry expected 2 monitors, got %d", got)
	}

	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{Enabled: true}
	if got := len(desiredServiceMonitors(js)); got != 3 {
		t.Fatalf("with telemetry expected 3 monitors, got %d", got)
	}

	for _, monitor := range desiredServiceMonitors(js) {
		if monitor.GetNamespace() != js.Namespace {
			t.Errorf("%s namespace = %q", monitor.GetName(), monitor.GetNamespace())
		}
		if monitor.GetKind() != serviceMonitorKind || monitor.GetAPIVersion() != serviceMonitorGroup+"/"+serviceMonitorVersion {
			t.Errorf("%s wrong GVK %s/%s", monitor.GetName(), monitor.GetAPIVersion(), monitor.GetKind())
		}
		spec, ok := monitor.Object["spec"].(map[string]interface{})
		if !ok {
			t.Fatalf("%s missing spec", monitor.GetName())
		}
		endpoints, ok := spec["endpoints"].([]interface{})
		if !ok || len(endpoints) != 1 {
			t.Fatalf("%s endpoints = %v", monitor.GetName(), spec["endpoints"])
		}
		endpoint := endpoints[0].(map[string]interface{})
		if endpoint["port"] != metricsPortName {
			t.Errorf("%s endpoint port = %v, want %s", monitor.GetName(), endpoint["port"], metricsPortName)
		}
		if spec["namespaceSelector"].(map[string]interface{})["matchNames"].([]interface{})[0] != js.Namespace {
			t.Errorf("%s namespaceSelector mismatch", monitor.GetName())
		}
	}

	telemetry := desiredServiceMonitors(js)[2]
	selector := telemetry.Object["spec"].(map[string]interface{})["selector"].(map[string]interface{})["matchLabels"].(map[string]interface{})
	if selector["component"] != "telemetry" || selector["controller"] != js.Name {
		t.Errorf("telemetry selector = %v", selector)
	}
}

func TestDesiredServiceMonitorsAddsConfiguredLabels(t *testing.T) {
	js := testJumpstarter()
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	js.Spec.Telemetry.Metrics.ServiceMonitorLabels = map[string]string{
		"release": "kube-prometheus-stack",
	}

	for _, monitor := range desiredServiceMonitors(js) {
		if got := monitor.GetLabels()["release"]; got != "kube-prometheus-stack" {
			t.Errorf("%s release label = %q, want kube-prometheus-stack", monitor.GetName(), got)
		}
		if monitor.GetLabels()[metricsServiceLabel] != "true" {
			t.Errorf("%s lost the metrics label: %v", monitor.GetName(), monitor.GetLabels())
		}
	}
}

func TestRenderShipper(t *testing.T) {
	js := testJumpstarter()
	tests := []struct {
		name    string
		key     string
		markers []string
	}{
		{"promtail", "promtail.yaml", []string{"kubernetes_sd_configs", "jumpstarter_dev_log_scrape", "component", "exporter"}},
		{"alloy", "alloy.alloy", []string{"discovery.kubernetes", "jumpstarter_dev_log_scrape", "selectors", "spec.nodeName", "stage.labels", "stage.label_keep", "component", "exporter", "namespace"}},
		{"vector", "vector.yaml", []string{"kubernetes_logs", "jumpstarter.dev/log-scrape=true", "labels:", "component", "exporter"}},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			key, content := renderShipper(js, tc.name)
			if key != tc.key {
				t.Errorf("key = %q, want %q", key, tc.key)
			}
			for _, marker := range tc.markers {
				if !strings.Contains(content, marker) {
					t.Errorf("rendering missing %q", marker)
				}
			}
			lower := strings.ToLower(content)
			for _, forbidden := range []string{"password", "username", "token", "secret"} {
				if strings.Contains(lower, forbidden) {
					t.Errorf("rendering contains forbidden credential marker %q", forbidden)
				}
			}
			if !strings.Contains(content, js.Namespace) {
				t.Errorf("rendering missing namespace %q", js.Namespace)
			}
			if !strings.Contains(content, "{{LOKI_URL}}") {
				t.Errorf("rendering missing placeholder")
			}
		})
	}
}

func TestRenderShipperDoesNotPromotePodLabels(t *testing.T) {
	js := testJumpstarter()
	for _, shipper := range []string{shipperTypePromtail, shipperTypeAlloy, shipperTypeVector} {
		_, content := renderShipper(js, shipper)
		for _, forbidden := range []string{
			"target_label: pod",
			"target_label: container",
			`target_label  = "pod"`,
			`target_label  = "container"`,
			`pod: "{{ kubernetes.pod_name }}"`,
		} {
			if strings.Contains(content, forbidden) {
				t.Errorf("%s promotes a high-cardinality pod/container label: %q", shipper, forbidden)
			}
		}
		if !strings.Contains(content, "namespace") {
			t.Errorf("%s should keep the namespace stream label", shipper)
		}
	}
}

var updateAlloyGolden = flag.Bool("update-alloy-golden", false, "update testdata/logshipper/alloy.alloy.golden")

func TestRenderAlloyGolden(t *testing.T) {
	js := testJumpstarter()
	_, got := renderShipper(js, shipperTypeAlloy)
	golden := filepath.Join("testdata", "logshipper", "alloy.alloy.golden")

	if *updateAlloyGolden {
		if err := os.MkdirAll(filepath.Dir(golden), 0o755); err != nil {
			t.Fatalf("create golden directory: %v", err)
		}
		if err := os.WriteFile(golden, []byte(got), 0o644); err != nil {
			t.Fatalf("write golden file: %v", err)
		}
	}

	want, err := os.ReadFile(golden)
	if err != nil {
		t.Fatalf("read golden file: %v", err)
	}
	if string(want) != got {
		t.Fatalf("alloy rendering differs from %s; rerun with -update-alloy-golden", golden)
	}
}

func TestAlloyRenderingValidates(t *testing.T) {
	alloyBin := os.Getenv("ALLOY_BIN")
	if alloyBin == "" {
		var err error
		alloyBin, err = exec.LookPath("alloy")
		if err != nil {
			t.Skip("set ALLOY_BIN or install alloy to run Alloy validation")
		}
	}

	js := testJumpstarter()
	_, content := renderShipper(js, shipperTypeAlloy)
	path := filepath.Join(t.TempDir(), "alloy.alloy")
	if err := os.WriteFile(path, []byte(content), 0o644); err != nil {
		t.Fatalf("write alloy config: %v", err)
	}

	for _, args := range [][]string{
		{"validate", path},
		{"fmt", "--test", path},
	} {
		cmd := exec.Command(alloyBin, args...)
		if out, err := cmd.CombinedOutput(); err != nil {
			t.Fatalf("alloy %s failed: %v\n%s", strings.Join(args, " "), err, out)
		}
	}
}

func TestRenderShipperUsesConfiguredLokiURL(t *testing.T) {
	js := testJumpstarter()
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{Loki: operatorv1alpha1.TelemetryLokiConfig{URL: "https://loki.example/push"}}
	_, content := renderShipper(js, shipperTypePromtail)
	if !strings.Contains(content, "https://loki.example/push") {
		t.Fatal("expected configured Loki URL in rendering")
	}
	if strings.Contains(content, "{{LOKI_URL}}") {
		t.Fatal("placeholder should be replaced when URL is configured")
	}
}

func TestBuildLogShipperConfigMap(t *testing.T) {
	js := testJumpstarter()
	if cm := buildLogShipperConfigMap(js); cm != nil {
		t.Fatalf("expected nil ConfigMap when shipper is none, got %v", cm.Data)
	}

	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	js.Spec.Telemetry.Logging.Shipper.Type = shipperTypePromtail
	cm := buildLogShipperConfigMap(js)
	if cm == nil {
		t.Fatal("expected ConfigMap")
	}
	if cm.Name != "jumpstarter-log-shipper" || cm.Namespace != js.Namespace {
		t.Errorf("ConfigMap identity = %s/%s", cm.Namespace, cm.Name)
	}
	if len(cm.Data) != 2 {
		t.Fatalf("expected 2 keys, got %d: %v", len(cm.Data), cm.Data)
	}
	if _, ok := cm.Data["promtail.yaml"]; !ok {
		t.Error("missing promtail.yaml")
	}
	if _, ok := cm.Data["README.md"]; !ok {
		t.Error("missing README.md")
	}
	if cm.Labels["jumpstarter.dev/log-shipper"] != shipperTypePromtail {
		t.Errorf("labels = %v", cm.Labels)
	}
}

func TestAlloyConfigMapReadmeMentionsValidatedCommit(t *testing.T) {
	js := testJumpstarter()
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	js.Spec.Telemetry.Logging.Shipper.Type = shipperTypeAlloy

	cm := buildLogShipperConfigMap(js)
	if cm == nil {
		t.Fatal("expected Alloy ConfigMap")
	}
	readme := cm.Data["README.md"]
	for _, want := range []string{
		alloyValidatedCommit,
		"alloy validate",
		"alloy fmt --test",
		"single-replica Deployment",
	} {
		if !strings.Contains(readme, want) {
			t.Errorf("Alloy README missing %q", want)
		}
	}
}

func TestPodLabelsDoNotChangeSelector(t *testing.T) {
	js := testJumpstarter()
	r := &JumpstarterReconciler{}

	controller := r.createControllerDeployment(js, "hash", "")
	wantControllerSelector := map[string]string{
		"component":  "controller",
		"app":        "jumpstarter-controller",
		"controller": js.Name,
	}
	if !reflect.DeepEqual(controller.Spec.Selector.MatchLabels, wantControllerSelector) {
		t.Errorf("controller selector changed: %v", controller.Spec.Selector.MatchLabels)
	}
	if controller.Spec.Template.Labels[logScrapeLabel] != "true" || controller.Spec.Template.Labels[logFormatLabel] != "json" {
		t.Errorf("controller pod template missing discovery labels: %v", controller.Spec.Template.Labels)
	}

	router := r.createRouterDeployment(js, 0, "")
	wantRouterSelector := map[string]string{
		"component":    "router",
		"app":          js.Name + "-router-0",
		"router":       js.Name,
		"router-index": "0",
	}
	if !reflect.DeepEqual(router.Spec.Selector.MatchLabels, wantRouterSelector) {
		t.Errorf("router selector changed: %v", router.Spec.Selector.MatchLabels)
	}
	if router.Spec.Template.Labels[logScrapeLabel] != "true" || router.Spec.Template.Labels[logFormatLabel] != "json" {
		t.Errorf("router pod template missing discovery labels: %v", router.Spec.Template.Labels)
	}
}

func TestSnippetsParse(t *testing.T) {
	js := testJumpstarter()

	t.Run("promtail parses as YAML", func(t *testing.T) {
		_, content := renderShipper(js, shipperTypePromtail)
		var parsed map[string]interface{}
		if err := yaml.Unmarshal([]byte(content), &parsed); err != nil {
			t.Fatalf("promtail rendering is not valid YAML: %v", err)
		}
		if _, ok := parsed["scrape_configs"]; !ok {
			t.Error("promtail rendering missing scrape_configs")
		}
	})

	t.Run("vector parses as YAML", func(t *testing.T) {
		_, content := renderShipper(js, shipperTypeVector)
		var parsed map[string]interface{}
		if err := yaml.Unmarshal([]byte(content), &parsed); err != nil {
			t.Fatalf("vector rendering is not valid YAML: %v", err)
		}
		for _, key := range []string{"sources", "transforms", "sinks"} {
			if _, ok := parsed[key]; !ok {
				t.Errorf("vector rendering missing %s", key)
			}
		}
	})

	t.Run("alloy has balanced blocks", func(t *testing.T) {
		_, content := renderShipper(js, shipperTypeAlloy)
		if strings.Count(content, "{") != strings.Count(content, "}") {
			t.Error("alloy rendering has unbalanced braces")
		}
		for _, marker := range []string{"loki.source.kubernetes", "loki.process", "loki.write"} {
			if !strings.Contains(content, marker) {
				t.Errorf("alloy rendering missing %s", marker)
			}
		}
	})
}
