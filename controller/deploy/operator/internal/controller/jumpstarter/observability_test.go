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
	"reflect"
	"strings"
	"testing"
	"time"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func testJumpstarter() *operatorv1alpha1.Jumpstarter {
	return &operatorv1alpha1.Jumpstarter{
		ObjectMeta: metav1.ObjectMeta{Name: "jumpstarter", Namespace: "lab"},
	}
}

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
			js.Spec.Telemetry.Metrics.ServiceMonitor = new(false)
			return js
		}(), false},
		{"explicit true", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			js.Spec.Telemetry.Metrics.ServiceMonitor = new(true)
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

	result, err := r.reconcileObservability(context.Background(), testJumpstarter())
	if err == nil {
		t.Fatal("reconcileObservability should report the CRD detection error for retry")
	}
	if !strings.Contains(err.Error(), "service monitor CRD detection") {
		t.Fatalf("unexpected reconcileObservability error: %v", err)
	}
	ready, reason, message := result.serviceMonitor.condition()
	if ready || reason != serviceMonitorReasonFailed || message == "" {
		t.Fatalf("condition = %v/%q/%q, want false/%s with a message", ready, reason, message, serviceMonitorReasonFailed)
	}
	if wrapped.deletedServiceMonitor {
		t.Fatal("ServiceMonitors must not be deleted when CRD detection fails")
	}
}

// serviceMonitorApplyErrorClient simulates an API failure while creating a
// ServiceMonitor, after the CRD probe has already succeeded.
type serviceMonitorApplyErrorClient struct {
	client.Client
}

func (c *serviceMonitorApplyErrorClient) List(ctx context.Context, list client.ObjectList, opts ...client.ListOption) error {
	if _, ok := list.(*unstructured.UnstructuredList); ok {
		// Pretend the optional CRD is served so reconciliation reaches the
		// apply step.
		return nil
	}
	return c.Client.List(ctx, list, opts...)
}

func (c *serviceMonitorApplyErrorClient) Create(ctx context.Context, obj client.Object, opts ...client.CreateOption) error {
	if monitor, ok := obj.(*unstructured.Unstructured); ok && monitor.GetKind() == serviceMonitorKind {
		return errors.New("injected ServiceMonitor create failure")
	}
	return c.Client.Create(ctx, obj, opts...)
}

func TestReconcileObservabilityReportsApplyFailureInCondition(t *testing.T) {
	s := runtime.NewScheme()
	if err := corev1.AddToScheme(s); err != nil {
		t.Fatalf("add core scheme: %v", err)
	}
	if err := operatorv1alpha1.AddToScheme(s); err != nil {
		t.Fatalf("add operator scheme: %v", err)
	}

	wrapped := &serviceMonitorApplyErrorClient{
		Client: fake.NewClientBuilder().WithScheme(s).Build(),
	}
	r := &JumpstarterReconciler{Client: wrapped, Scheme: s}

	result, err := r.reconcileObservability(context.Background(), testJumpstarter())
	if err == nil {
		t.Fatal("reconcileObservability should report the ServiceMonitor apply error for retry")
	}
	if !strings.Contains(err.Error(), "injected ServiceMonitor create failure") {
		t.Fatalf("unexpected reconcileObservability error: %v", err)
	}
	ready, reason, message := result.serviceMonitor.condition()
	if ready || reason != serviceMonitorReasonFailed || message == "" {
		t.Fatalf("condition = %v/%q/%q, want false/%s with a message", ready, reason, message, serviceMonitorReasonFailed)
	}
}

func TestServiceMonitorResultCondition(t *testing.T) {
	tests := []struct {
		name       string
		result     serviceMonitorResult
		wantReady  bool
		wantReason string
	}{
		{
			name:       "active is ready",
			result:     serviceMonitorResult{reason: serviceMonitorReasonCreated},
			wantReady:  true,
			wantReason: serviceMonitorReasonCreated,
		},
		{
			name:       "disabled is not ready",
			result:     serviceMonitorResult{reason: serviceMonitorReasonDisabled},
			wantReady:  false,
			wantReason: serviceMonitorReasonDisabled,
		},
		{
			name:       "waiting for CRD is not ready",
			result:     serviceMonitorResult{reason: serviceMonitorReasonCRDNotFound},
			wantReady:  false,
			wantReason: serviceMonitorReasonCRDNotFound,
		},
		{
			name:       "probe failure is not ready",
			result:     serviceMonitorResult{reason: serviceMonitorReasonFailed},
			wantReady:  false,
			wantReason: serviceMonitorReasonFailed,
		},
		{
			name:       "apply failure is not ready",
			result:     serviceMonitorResult{reason: serviceMonitorReasonFailed},
			wantReady:  false,
			wantReason: serviceMonitorReasonFailed,
		},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			ready, reason, _ := tc.result.condition()
			if ready != tc.wantReady || reason != tc.wantReason {
				t.Fatalf("condition() = %v/%q, want %v/%q", ready, reason, tc.wantReady, tc.wantReason)
			}
		})
	}
}

func TestDeleteObjectIfExistsRejectsForeignObject(t *testing.T) {
	s := runtime.NewScheme()
	if err := corev1.AddToScheme(s); err != nil {
		t.Fatalf("add core scheme: %v", err)
	}
	foreign := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{Name: "jumpstarter-metrics-controller", Namespace: "lab"},
	}
	c := fake.NewClientBuilder().WithScheme(s).WithObjects(foreign).Build()
	r := &JumpstarterReconciler{Client: c, Scheme: s}

	err := r.deleteObjectIfExists(context.Background(), testJumpstarter(),
		types.NamespacedName{Name: foreign.Name, Namespace: foreign.Namespace}, &corev1.Service{})
	if err == nil {
		t.Fatal("expected an ownership error for a foreign Service")
	}
	if !strings.Contains(err.Error(), "not controlled") {
		t.Fatalf("unexpected ownership error: %v", err)
	}
	if err := c.Get(context.Background(), types.NamespacedName{Name: foreign.Name, Namespace: foreign.Namespace}, &corev1.Service{}); err != nil {
		t.Fatalf("foreign Service must remain: %v", err)
	}
}

func TestReconcileMetricsServicesRejectsForeignService(t *testing.T) {
	s := runtime.NewScheme()
	if err := corev1.AddToScheme(s); err != nil {
		t.Fatalf("add core scheme: %v", err)
	}
	foreign := &corev1.Service{
		ObjectMeta: metav1.ObjectMeta{Name: "jumpstarter-metrics-controller", Namespace: "lab"},
	}
	c := fake.NewClientBuilder().WithScheme(s).WithObjects(foreign).Build()
	r := &JumpstarterReconciler{Client: c, Scheme: s}

	err := r.reconcileMetricsServices(context.Background(), testJumpstarter())
	if err == nil {
		t.Fatal("expected an ownership error for a foreign metrics Service")
	}
	if !strings.Contains(err.Error(), "refusing to update metrics service") {
		t.Fatalf("unexpected ownership error: %v", err)
	}
}

func TestDesiredMetricsServices(t *testing.T) {
	js := testJumpstarter()
	services := desiredMetricsServices(js)
	if len(services) != 2 {
		t.Fatalf("expected 2 metrics services, got %d", len(services))
	}

	want := map[string]map[string]string{
		"jumpstarter-metrics-controller": {"component": "controller", "controller": js.Name},
		"jumpstarter-metrics-router":     {"component": "router", "router": js.Name},
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
		spec, ok := monitor.Object["spec"].(map[string]any)
		if !ok {
			t.Fatalf("%s missing spec", monitor.GetName())
		}
		endpoints, ok := spec["endpoints"].([]any)
		if !ok || len(endpoints) != 1 {
			t.Fatalf("%s endpoints = %v", monitor.GetName(), spec["endpoints"])
		}
		endpoint := endpoints[0].(map[string]any)
		if endpoint["port"] != metricsPortName {
			t.Errorf("%s endpoint port = %v, want %s", monitor.GetName(), endpoint["port"], metricsPortName)
		}
		if spec["namespaceSelector"].(map[string]any)["matchNames"].([]any)[0] != js.Namespace {
			t.Errorf("%s namespaceSelector mismatch", monitor.GetName())
		}
	}

	telemetry := desiredServiceMonitors(js)[2]
	selector := telemetry.Object["spec"].(map[string]any)["selector"].(map[string]any)["matchLabels"].(map[string]any)
	if selector["component"] != "telemetry" || selector["controller"] != js.Name {
		t.Errorf("telemetry selector = %v", selector)
	}
}

func TestDesiredServiceMonitorsExcludesDisabledOrZeroReplicaTelemetry(t *testing.T) {
	tests := []struct {
		name string
		spec *operatorv1alpha1.TelemetryConfig
	}{
		{"nil telemetry", nil},
		{"telemetry disabled", &operatorv1alpha1.TelemetryConfig{Enabled: false}},
		{"telemetry zero replicas", func() *operatorv1alpha1.TelemetryConfig {
			zero := int32(0)
			return &operatorv1alpha1.TelemetryConfig{Enabled: true, Replicas: &zero}
		}()},
	}

	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			js := testJumpstarter()
			js.Spec.Telemetry = tc.spec
			if got := len(desiredServiceMonitors(js)); got != 2 {
				t.Fatalf("expected 2 monitors, got %d", got)
			}
		})
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

func TestDesiredServiceMonitorsOperatorLabelsWin(t *testing.T) {
	js := testJumpstarter()
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	js.Spec.Telemetry.Metrics.ServiceMonitorLabels = map[string]string{
		"release":                     "kube-prometheus-stack",
		metricsServiceLabel:           "false",
		"app.kubernetes.io/name":      "not-jumpstarter",
		"app.kubernetes.io/component": "controller",
	}

	for _, monitor := range desiredServiceMonitors(js) {
		labels := monitor.GetLabels()
		if got := labels["release"]; got != "kube-prometheus-stack" {
			t.Errorf("%s release label = %q, want the user label kept", monitor.GetName(), got)
		}
		if got := labels[metricsServiceLabel]; got != "true" {
			t.Errorf("%s %s = %q, want the operator value true", monitor.GetName(), metricsServiceLabel, got)
		}
		if got := labels["app.kubernetes.io/name"]; got != "jumpstarter" {
			t.Errorf("%s app.kubernetes.io/name = %q, want the operator value", monitor.GetName(), got)
		}
		wantComponent := "controller"
		if strings.Contains(monitor.GetName(), "-router") {
			wantComponent = "router"
		}
		if got := labels["app.kubernetes.io/component"]; got != wantComponent {
			t.Errorf("%s app.kubernetes.io/component = %q, want %q", monitor.GetName(), got, wantComponent)
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

func TestObservabilityRequeueAfterTelemetryCAWins(t *testing.T) {
	// A missing telemetry CA is retried every 30s. Nothing else shortens the
	// periodic interval: a missing CRD is picked up by the CRD watch.
	s := runtime.NewScheme()
	if err := corev1.AddToScheme(s); err != nil {
		t.Fatalf("add core scheme: %v", err)
	}
	if err := operatorv1alpha1.AddToScheme(s); err != nil {
		t.Fatalf("add operator scheme: %v", err)
	}

	js := testJumpstarter()
	js.Spec.CertManager.Enabled = true
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{Enabled: true}

	// No CA secret exists in the fake client, so telemetryCANeedsRequeue is true.
	r := &JumpstarterReconciler{Client: fake.NewClientBuilder().WithScheme(s).Build(), Scheme: s}

	got := r.observabilityRequeueAfter(context.Background(), js)
	if got != telemetryCARequeueInterval {
		t.Fatalf("requeue = %s, want %s", got, telemetryCARequeueInterval)
	}
}

func TestObservabilityRequeueAfterDefault(t *testing.T) {
	// Without a pending telemetry CA the operator reconciles periodically. A
	// missing ServiceMonitor CRD does not shorten this: the CRD watch reacts to
	// the CRD being served, and every other change to the CRD is a watch event.
	js := testJumpstarter()
	r := &JumpstarterReconciler{}

	if got := r.observabilityRequeueAfter(context.Background(), js); got != 30*time.Minute {
		t.Fatalf("requeue = %s, want 30m", got)
	}
}
