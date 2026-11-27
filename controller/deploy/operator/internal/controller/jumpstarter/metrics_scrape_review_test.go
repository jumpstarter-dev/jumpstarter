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
	"errors"
	"strings"
	"testing"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

// Regression tests for the review findings on PR #1268.

func newObservabilityScheme(t *testing.T) *runtime.Scheme {
	t.Helper()

	s := runtime.NewScheme()
	if err := corev1.AddToScheme(s); err != nil {
		t.Fatalf("add core scheme: %v", err)
	}
	if err := operatorv1alpha1.AddToScheme(s); err != nil {
		t.Fatalf("add operator scheme: %v", err)
	}
	return s
}

func serviceMonitorObject(name, namespace string) *unstructured.Unstructured {
	obj := &unstructured.Unstructured{}
	obj.SetGroupVersionKind(schema.GroupVersionKind{
		Group: serviceMonitorGroup, Version: serviceMonitorVersion, Kind: serviceMonitorKind,
	})
	obj.SetName(name)
	obj.SetNamespace(namespace)
	return obj
}

func TestMetricsServiceNameBounded(t *testing.T) {
	short := testJumpstarter()
	if got, want := metricsServiceName(short, "controller"), "jumpstarter-metrics-controller"; got != want {
		t.Fatalf("short name = %q, want %q", got, want)
	}

	long := testJumpstarter()
	long.Name = strings.Repeat("a", 45)
	first := metricsServiceName(long, "controller")
	if len(first) > maxKubernetesNameLength {
		t.Fatalf("bounded name %q is %d chars, want <= %d", first, len(first), maxKubernetesNameLength)
	}
	if first != metricsServiceName(long, "controller") {
		t.Fatal("bounded name must be deterministic")
	}

	other := testJumpstarter()
	other.Name = strings.Repeat("a", 44) + "b"
	if first == metricsServiceName(other, "controller") {
		t.Fatal("different names must not collide after bounding")
	}

	dotted := testJumpstarter()
	dotted.Name = "jumpstarter.lab"
	if got := metricsServiceName(dotted, "router"); strings.Contains(got, ".") {
		t.Fatalf("name %q must not contain dots: Service names are RFC 1035 labels", got)
	}
}

// A foreign ServiceMonitor must not stop the other monitor from reconciling.
func TestReconcileServiceMonitorsContinuesAfterOwnershipConflict(t *testing.T) {
	s := newObservabilityScheme(t)
	js := testJumpstarter()

	foreign := serviceMonitorObject(metricsServiceName(js, "controller"), js.Namespace)
	c := fake.NewClientBuilder().WithScheme(s).WithObjects(foreign).Build()
	r := &JumpstarterReconciler{Client: c, Scheme: s}

	err := r.reconcileServiceMonitors(context.Background(), js)
	if err == nil {
		t.Fatal("expected the ownership refusal to be reported")
	}
	if !strings.Contains(err.Error(), "refusing to update ServiceMonitor") {
		t.Fatalf("unexpected error: %v", err)
	}

	router := serviceMonitorObject("", "")
	router.SetGroupVersionKind(schema.GroupVersionKind{
		Group: serviceMonitorGroup, Version: serviceMonitorVersion, Kind: serviceMonitorKind,
	})
	key := types.NamespacedName{Name: metricsServiceName(js, "router"), Namespace: js.Namespace}
	if err := c.Get(context.Background(), key, router); err != nil {
		t.Fatalf("router ServiceMonitor must still be created: %v", err)
	}

	untouched := serviceMonitorObject("", "")
	untouched.SetGroupVersionKind(foreign.GroupVersionKind())
	controllerKey := types.NamespacedName{Name: foreign.GetName(), Namespace: js.Namespace}
	if err := c.Get(context.Background(), controllerKey, untouched); err != nil {
		t.Fatalf("foreign ServiceMonitor must be preserved: %v", err)
	}
	if len(untouched.GetOwnerReferences()) != 0 {
		t.Fatalf("foreign ServiceMonitor must not gain an owner reference: %v", untouched.GetOwnerReferences())
	}
}

// serviceCreateErrorClient fails metrics Service creation after the CRD probe
// has succeeded.
type serviceCreateErrorClient struct {
	client.Client
}

func (c *serviceCreateErrorClient) Create(ctx context.Context, obj client.Object, opts ...client.CreateOption) error {
	if _, ok := obj.(*corev1.Service); ok {
		return errors.New("injected metrics Service create failure")
	}
	return c.Client.Create(ctx, obj, opts...)
}

// A metrics Service failure must not publish ServiceMonitorReady=True.
func TestReconcileObservabilityReportsMetricsServiceFailure(t *testing.T) {
	s := newObservabilityScheme(t)
	js := testJumpstarter()

	wrapped := &serviceCreateErrorClient{Client: fake.NewClientBuilder().WithScheme(s).Build()}
	r := &JumpstarterReconciler{Client: wrapped, Scheme: s}

	result, err := r.reconcileObservability(context.Background(), js)
	if err == nil {
		t.Fatal("reconcileObservability should report the metrics Service failure for retry")
	}
	if !strings.Contains(err.Error(), "injected metrics Service create failure") {
		t.Fatalf("unexpected error: %v", err)
	}
	ready, reason, message := result.serviceMonitor.condition()
	if ready || reason != serviceMonitorReasonFailed {
		t.Fatalf("condition = %v/%q, want false/%s", ready, reason, serviceMonitorReasonFailed)
	}
	if !strings.Contains(message, "metrics Services") {
		t.Fatalf("message %q must name the metrics Services failure", message)
	}
}

// A failed ServiceMonitor prune while autodiscovery is disabled must not be
// reported as a clean Disabled condition: owned monitors may still be scraped.
func TestReconcileObservabilityReportsServiceMonitorCleanupFailureWhenDisabled(t *testing.T) {
	s := newObservabilityScheme(t)
	js := testJumpstarter()
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	js.Spec.Telemetry.Metrics.ServiceMonitor = new(false)

	owned := &unstructured.Unstructured{}
	owned.SetGroupVersionKind(serviceMonitorGVK)
	owned.SetName(metricsServiceName(js, "controller"))
	owned.SetNamespace(js.Namespace)
	owned.SetOwnerReferences([]metav1.OwnerReference{
		*metav1.NewControllerRef(js, operatorv1alpha1.GroupVersion.WithKind("Jumpstarter")),
	})

	base := fake.NewClientBuilder().WithScheme(s).WithObjects(owned).Build()
	r := &JumpstarterReconciler{Client: &deleteConflictClient{Client: base}, Scheme: s}

	result, err := r.reconcileObservability(context.Background(), js)
	if err == nil {
		t.Fatal("reconcileObservability should report the ServiceMonitor cleanup failure for retry")
	}
	ready, reason, message := result.serviceMonitor.condition()
	if ready || reason != serviceMonitorReasonFailed {
		t.Fatalf("condition = %v/%q, want false/%s", ready, reason, serviceMonitorReasonFailed)
	}
	if !strings.Contains(message, "ServiceMonitors") || !strings.Contains(message, "disabled") {
		t.Fatalf("message %q must name the cleanup failure and the disabled context", message)
	}
}

// A foreign metrics Service must not stop cleanup of the owned one.
func TestReconcileMetricsServicesContinuesCleanupAfterForeignService(t *testing.T) {
	s := newObservabilityScheme(t)
	js := testJumpstarter()
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	js.Spec.Telemetry.Metrics.ServiceMonitor = new(false)

	foreign := &corev1.Service{ObjectMeta: metav1.ObjectMeta{
		Name:      metricsServiceName(js, "controller"),
		Namespace: js.Namespace,
	}}
	owned := &corev1.Service{ObjectMeta: metav1.ObjectMeta{
		Name:            metricsServiceName(js, "router"),
		Namespace:       js.Namespace,
		OwnerReferences: []metav1.OwnerReference{*metav1.NewControllerRef(js, operatorv1alpha1.GroupVersion.WithKind("Jumpstarter"))},
	}}

	c := fake.NewClientBuilder().WithScheme(s).WithObjects(foreign, owned).Build()
	r := &JumpstarterReconciler{Client: c, Scheme: s}

	err := r.reconcileMetricsServices(context.Background(), js)
	if err == nil || !strings.Contains(err.Error(), "refusing to delete") {
		t.Fatalf("expected the ownership refusal to be reported, got %v", err)
	}

	routerKey := types.NamespacedName{Name: metricsServiceName(js, "router"), Namespace: js.Namespace}
	if err := c.Get(context.Background(), routerKey, &corev1.Service{}); !apierrors.IsNotFound(err) {
		t.Fatalf("owned router Service must be deleted, got %v", err)
	}

	controllerKey := types.NamespacedName{Name: metricsServiceName(js, "controller"), Namespace: js.Namespace}
	if err := c.Get(context.Background(), controllerKey, &corev1.Service{}); err != nil {
		t.Fatalf("foreign Service must survive: %v", err)
	}
}

// A removed CRD can surface as 404 from a cached mapping instead of NoMatch.
type serviceMonitorListNotFoundClient struct {
	client.Client
}

func (c *serviceMonitorListNotFoundClient) List(ctx context.Context, list client.ObjectList, opts ...client.ListOption) error {
	if monitors, ok := list.(*unstructured.UnstructuredList); ok &&
		monitors.GroupVersionKind().Kind == serviceMonitorKind+"List" {
		return apierrors.NewNotFound(schema.GroupResource{
			Group: serviceMonitorGroup, Resource: "servicemonitors",
		}, "")
	}
	return c.Client.List(ctx, list, opts...)
}

func TestReconcileObservabilityTreatsListNotFoundAsAbsentCRD(t *testing.T) {
	s := newObservabilityScheme(t)
	js := testJumpstarter()

	wrapped := &serviceMonitorListNotFoundClient{Client: fake.NewClientBuilder().WithScheme(s).Build()}
	r := &JumpstarterReconciler{Client: wrapped, Scheme: s}

	result, err := r.reconcileObservability(context.Background(), js)
	if err != nil {
		t.Fatalf("reconcileObservability must not fail when the API is gone: %v", err)
	}
	if result.serviceMonitor.reason != serviceMonitorReasonCRDNotFound {
		t.Fatalf("reason = %q, want %s", result.serviceMonitor.reason, serviceMonitorReasonCRDNotFound)
	}
}

// deleteRecorderClient captures the delete options passed to Delete.
type deleteRecorderClient struct {
	client.Client
	preconditions *metav1.Preconditions
}

func (c *deleteRecorderClient) Delete(ctx context.Context, obj client.Object, opts ...client.DeleteOption) error {
	options := &client.DeleteOptions{}
	options.ApplyOptions(opts)
	c.preconditions = options.Preconditions
	return c.Client.Delete(ctx, obj, opts...)
}

func TestDeleteObjectIfExistsPinsDeleteToObservedObject(t *testing.T) {
	s := newObservabilityScheme(t)
	js := testJumpstarter()

	owned := &corev1.Service{ObjectMeta: metav1.ObjectMeta{
		Name:            metricsServiceName(js, "controller"),
		Namespace:       js.Namespace,
		UID:             types.UID("uid-1"),
		ResourceVersion: "42",
		OwnerReferences: []metav1.OwnerReference{*metav1.NewControllerRef(js, operatorv1alpha1.GroupVersion.WithKind("Jumpstarter"))},
	}}

	wrapped := &deleteRecorderClient{Client: fake.NewClientBuilder().WithScheme(s).WithObjects(owned).Build()}
	r := &JumpstarterReconciler{Client: wrapped, Scheme: s}

	key := types.NamespacedName{Name: owned.Name, Namespace: owned.Namespace}
	if err := r.deleteObjectIfExists(context.Background(), js, key, &corev1.Service{}); err != nil {
		t.Fatalf("deleteObjectIfExists: %v", err)
	}

	if wrapped.preconditions == nil {
		t.Fatal("delete must carry preconditions")
	}
	if wrapped.preconditions.UID == nil || *wrapped.preconditions.UID != owned.UID {
		t.Fatalf("UID precondition = %v, want %s", wrapped.preconditions.UID, owned.UID)
	}
	if wrapped.preconditions.ResourceVersion == nil || *wrapped.preconditions.ResourceVersion != owned.ResourceVersion {
		t.Fatalf("resourceVersion precondition = %v, want %s", wrapped.preconditions.ResourceVersion, owned.ResourceVersion)
	}
}

// deleteConflictClient fails every delete with a Conflict, emulating an object
// that changed after the ownership check.
type deleteConflictClient struct {
	client.Client
}

func (c *deleteConflictClient) Delete(ctx context.Context, obj client.Object, opts ...client.DeleteOption) error {
	return apierrors.NewConflict(
		schema.GroupResource{Group: "test", Resource: "objects"},
		obj.GetName(),
		errors.New("object changed after the ownership check"),
	)
}

// A conflict must be returned for retry, not swallowed: ServiceMonitors are not
// watched, so a dropped error would wait for the resync.
func TestDeleteObjectIfExistsReturnsConflict(t *testing.T) {
	s := newObservabilityScheme(t)
	js := testJumpstarter()

	owned := &corev1.Service{ObjectMeta: metav1.ObjectMeta{
		Name:            metricsServiceName(js, "controller"),
		Namespace:       js.Namespace,
		UID:             types.UID("uid-1"),
		ResourceVersion: "42",
		OwnerReferences: []metav1.OwnerReference{*metav1.NewControllerRef(js, operatorv1alpha1.GroupVersion.WithKind("Jumpstarter"))},
	}}

	wrapped := &deleteConflictClient{Client: fake.NewClientBuilder().WithScheme(s).WithObjects(owned).Build()}
	r := &JumpstarterReconciler{Client: wrapped, Scheme: s}

	key := types.NamespacedName{Name: owned.Name, Namespace: owned.Namespace}
	err := r.deleteObjectIfExists(context.Background(), js, key, &corev1.Service{})
	if err == nil || !apierrors.IsConflict(err) {
		t.Fatalf("deleteObjectIfExists() error = %v, want a conflict", err)
	}

	if err := wrapped.Get(context.Background(), key, &corev1.Service{}); err != nil {
		t.Fatalf("the Service must survive a conflicting delete: %v", err)
	}
}

func TestPruneServiceMonitorsReturnsConflict(t *testing.T) {
	s := newObservabilityScheme(t)
	js := testJumpstarter()

	stale := serviceMonitorObject(metricsServiceName(js, "controller"), js.Namespace)
	stale.SetOwnerReferences([]metav1.OwnerReference{
		*metav1.NewControllerRef(js, operatorv1alpha1.GroupVersion.WithKind("Jumpstarter")),
	})

	wrapped := &deleteConflictClient{Client: fake.NewClientBuilder().WithScheme(s).WithObjects(stale).Build()}
	r := &JumpstarterReconciler{Client: wrapped, Scheme: s}

	err := r.pruneServiceMonitors(context.Background(), js, map[string]struct{}{})
	if err == nil || !apierrors.IsConflict(err) {
		t.Fatalf("pruneServiceMonitors() error = %v, want a conflict", err)
	}

	key := types.NamespacedName{Name: stale.GetName(), Namespace: stale.GetNamespace()}
	remaining := serviceMonitorObject("", "")
	remaining.SetGroupVersionKind(schema.GroupVersionKind{
		Group: serviceMonitorGroup, Version: serviceMonitorVersion, Kind: serviceMonitorKind,
	})
	if err := wrapped.Get(context.Background(), key, remaining); err != nil {
		t.Fatalf("the ServiceMonitor must survive a conflicting delete: %v", err)
	}
}

// absentCRDServiceErrorClient fails metrics Service creation and reports the
// ServiceMonitor API as absent, so both failures are present at once.
type absentCRDServiceErrorClient struct {
	client.Client
}

func (c *absentCRDServiceErrorClient) List(ctx context.Context, list client.ObjectList, opts ...client.ListOption) error {
	if monitors, ok := list.(*unstructured.UnstructuredList); ok &&
		monitors.GroupVersionKind().Kind == serviceMonitorKind+"List" {
		return apierrors.NewNotFound(schema.GroupResource{
			Group: serviceMonitorGroup, Resource: "servicemonitors",
		}, "")
	}
	return c.Client.List(ctx, list, opts...)
}

func (c *absentCRDServiceErrorClient) Create(ctx context.Context, obj client.Object, opts ...client.CreateOption) error {
	if _, ok := obj.(*corev1.Service); ok {
		return errors.New("injected metrics Service create failure")
	}
	return c.Client.Create(ctx, obj, opts...)
}

// A metrics Service failure must be visible in status even when the
// ServiceMonitor CRD is absent.
func TestReconcileObservabilityReportsMetricsServiceFailureWhenCRDAbsent(t *testing.T) {
	s := newObservabilityScheme(t)
	js := testJumpstarter()

	wrapped := &absentCRDServiceErrorClient{Client: fake.NewClientBuilder().WithScheme(s).Build()}
	r := &JumpstarterReconciler{Client: wrapped, Scheme: s}

	result, err := r.reconcileObservability(context.Background(), js)
	if err == nil {
		t.Fatal("reconcileObservability should report the metrics Service failure for retry")
	}
	ready, reason, message := result.serviceMonitor.condition()
	if ready || reason != serviceMonitorReasonFailed {
		t.Fatalf("condition = %v/%q, want false/%s", ready, reason, serviceMonitorReasonFailed)
	}
	if !strings.Contains(message, "metrics Services") || !strings.Contains(message, "not installed") {
		t.Fatalf("message %q must name the metrics failure and the absent CRD", message)
	}
}

// serviceGetErrorClient fails every Service read, which is how the disabled
// path attempts cleanup.
type serviceGetErrorClient struct {
	client.Client
}

func (c *serviceGetErrorClient) Get(ctx context.Context, key client.ObjectKey, obj client.Object, opts ...client.GetOption) error {
	if _, ok := obj.(*corev1.Service); ok {
		return errors.New("injected metrics Service get failure")
	}
	return c.Client.Get(ctx, key, obj, opts...)
}

// A broken metrics Service must also surface while autodiscovery is disabled.
func TestReconcileObservabilityReportsMetricsServiceFailureWhenDisabled(t *testing.T) {
	s := newObservabilityScheme(t)
	js := testJumpstarter()
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	js.Spec.Telemetry.Metrics.ServiceMonitor = new(false)

	wrapped := &serviceGetErrorClient{Client: fake.NewClientBuilder().WithScheme(s).Build()}
	r := &JumpstarterReconciler{Client: wrapped, Scheme: s}

	result, err := r.reconcileObservability(context.Background(), js)
	if err == nil {
		t.Fatal("reconcileObservability should report the metrics Service failure for retry")
	}
	ready, reason, message := result.serviceMonitor.condition()
	if ready || reason != serviceMonitorReasonFailed {
		t.Fatalf("condition = %v/%q, want false/%s", ready, reason, serviceMonitorReasonFailed)
	}
	if !strings.Contains(message, "disabled") {
		t.Fatalf("message %q must keep the disabled context", message)
	}
}
