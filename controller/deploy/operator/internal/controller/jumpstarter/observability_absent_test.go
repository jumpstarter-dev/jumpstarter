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
	"path/filepath"
	"testing"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/internal/controller/testutils"
	corev1 "k8s.io/api/core/v1"
	apimeta "k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes/scheme"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/envtest"
)

// TestServiceMonitorCRDAbsent runs in a dedicated envtest environment that does
// not install the ServiceMonitor CRD, proving the cluster-without-Prometheus-Operator
// path reconciles successfully.
func TestServiceMonitorCRDAbsent(t *testing.T) {
	if testing.Short() {
		t.Skip("skipping envtest in short mode")
	}

	testEnv := &envtest.Environment{
		CRDDirectoryPaths:     []string{filepath.Join("..", "..", "..", "config", "crd", "bases")},
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
	ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "absent-"}}
	if err := c.Create(ctx, ns); err != nil {
		t.Fatalf("create namespace: %v", err)
	}
	defer func() { _ = c.Delete(ctx, ns) }()

	js := &operatorv1alpha1.Jumpstarter{ObjectMeta: metav1.ObjectMeta{Name: "jumpstarter", Namespace: ns.Name}}
	if err := c.Create(ctx, js); err != nil {
		t.Fatalf("create jumpstarter: %v", err)
	}

	r := &JumpstarterReconciler{Client: c, Scheme: scheme.Scheme}

	available, err := r.serviceMonitorCRDAvailable(ctx)
	if err != nil {
		t.Fatalf("detect CRD: %v", err)
	}
	if available {
		t.Fatal("expected ServiceMonitor CRD to be absent")
	}

	if err := r.reconcileObservability(ctx, js); err != nil {
		t.Fatalf("reconcileObservability must succeed without the CRD: %v", err)
	}

	svc := &corev1.Service{}
	if err := c.Get(ctx, types.NamespacedName{Name: "jumpstarter-controller-metrics", Namespace: ns.Name}, svc); err != nil {
		t.Fatalf("metrics Service should still be created: %v", err)
	}

	sm := &unstructured.Unstructured{}
	sm.SetGroupVersionKind(serviceMonitorGVK)
	err = c.Get(ctx, types.NamespacedName{Name: "jumpstarter-controller-metrics", Namespace: ns.Name}, sm)
	if err == nil {
		t.Fatal("expected no ServiceMonitor to exist")
	}
	if !apimeta.IsNoMatchError(err) {
		t.Fatalf("expected NoMatch error for absent CRD, got %v", err)
	}
}
