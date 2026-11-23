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
	"path/filepath"
	"strings"
	"testing"
	"time"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/internal/controller/testutils"
	corev1 "k8s.io/api/core/v1"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/tools/record"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/envtest"
)

// TestPersesDashboardCRDv1alpha1Only runs in a dedicated envtest environment
// that installs a legacy perses-operator CRD serving perses.dev/v1alpha1 only
// (perses-operator <= v0.2.0). RQ1 decision: such clusters are reported as
// unsupported with an actionable message instead of being written with a second
// dashboard shape that no perses-operator release can consume.
func TestPersesDashboardCRDv1alpha1Only(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping envtest in short mode")
	}

	testEnv := &envtest.Environment{
		CRDDirectoryPaths: []string{
			filepath.Join("..", "..", "..", "config", "crd", "bases"),
			filepath.Join("..", "..", "..", "config", "test", "perses-legacy"),
		},
		ErrorIfCRDPathMissing: true,
	}
	if dir := testutils.GetFirstFoundEnvTestBinaryDir(5); dir != "" {
		testEnv.BinaryAssetsDirectory = dir
	}
	testEnv.ControlPlane.GetAPIServer().Configure().
		Set("service-cluster-ip-range", "10.0.0.0/16")

	cfg, err := testEnv.Start()
	if err != nil {
		t.Fatalf("start envtest: %v", err)
	}
	defer func() { _ = testEnv.Stop() }()

	if err := operatorv1alpha1.AddToScheme(scheme.Scheme); err != nil {
		t.Fatalf("add scheme: %v", err)
	}
	c, err := client.New(cfg, client.Options{Scheme: scheme.Scheme})
	if err != nil {
		t.Fatalf("client: %v", err)
	}

	ctx := context.Background()
	ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "legacy-perses-"}}
	if err := c.Create(ctx, ns); err != nil {
		t.Fatalf("create namespace: %v", err)
	}
	defer func() { _ = c.Delete(ctx, ns) }()

	js := &operatorv1alpha1.Jumpstarter{ObjectMeta: metav1.ObjectMeta{Name: "jumpstarter", Namespace: ns.Name}}
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	js.Spec.Telemetry.Metrics.Dashboards = new(true)
	js.Spec.Telemetry.Metrics.PersesInstanceSelector = map[string]string{
		"app.kubernetes.io/instance": "perses-sample",
	}
	if err := c.Create(ctx, js); err != nil {
		t.Fatalf("create jumpstarter: %v", err)
	}

	r := &JumpstarterReconciler{Client: c, Scheme: scheme.Scheme, Recorder: record.NewFakeRecorder(32)}

	available, served, err := r.persesDashboardCRDAvailable(ctx)
	if err != nil {
		t.Fatalf("detect CRD: %v", err)
	}
	if available {
		t.Fatal("expected perses.dev/v1alpha2 to be unavailable")
	}
	if len(served) != 1 || served[0] != "v1alpha1" {
		t.Fatalf("served versions = %v, want [v1alpha1]", served)
	}

	observability, err := r.reconcileObservability(ctx, js)
	if err != nil {
		t.Fatalf("reconcileObservability must succeed with a legacy CRD: %v", err)
	}
	if err := r.updateStatus(ctx, js, observability); err != nil {
		t.Fatalf("updateStatus: %v", err)
	}

	condition := apimeta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeDashboardsReady)
	if condition == nil {
		t.Fatal("DashboardsReady condition missing")
	}
	if condition.Status != metav1.ConditionFalse || condition.Reason != "CRDNotFound" {
		t.Fatalf("DashboardsReady = %s/%s, want False/CRDNotFound", condition.Status, condition.Reason)
	}
	for _, want := range []string{"v1alpha1", "v0.3.0", "v1alpha2"} {
		if !strings.Contains(condition.Message, want) {
			t.Fatalf("DashboardsReady message %q must mention %q", condition.Message, want)
		}
	}

	// The CRD watch re-checks when the served versions change, so the periodic
	// interval is enough while only v1alpha1 is served.
	if requeue := r.observabilityRequeueAfter(ctx, js); requeue != 30*time.Minute {
		t.Fatalf("requeue = %s, want 30m while the CRD version is unsupported", requeue)
	}

	// A v1alpha2 object must not be written: the API server does not serve it.
	dashboard := &unstructured.Unstructured{}
	dashboard.SetGroupVersionKind(persesDashboardGVK)
	err = c.Get(ctx, types.NamespacedName{Name: "jumpstarter-overview", Namespace: ns.Name}, dashboard)
	if err == nil {
		t.Fatal("expected no v1alpha2 PersesDashboard to exist")
	}
	if !apimeta.IsNoMatchError(err) {
		t.Fatalf("expected NoMatch error for the unserved version, got %v", err)
	}
}
