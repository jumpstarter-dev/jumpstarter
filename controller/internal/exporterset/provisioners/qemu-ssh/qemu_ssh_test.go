/*
Copyright 2026 The Jumpstarter Authors

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

package qemussh

import (
	"context"
	"encoding/json"
	"errors"
	"strings"
	"testing"
	"time"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/exporterset"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestProvisionerName(t *testing.T) {
	p := New("v1.0.0", nil)
	if got := p.Name(); got != ProvisionerName {
		t.Errorf("Name() = %q, want %q", got, ProvisionerName)
	}
}

func TestRenderPod_returnsNil(t *testing.T) {
	p := New("v1.0.0", nil)
	pod, err := p.RenderPod(context.Background(), nil, nil, nil, nil, nil)
	if err != nil {
		t.Fatal(err)
	}
	if pod != nil {
		t.Errorf("RenderPod() should return nil for off-cluster provisioner")
	}
}

func TestDeployedAt_noAnnotation(t *testing.T) {
	p := New("v1.0.0", nil)
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: "test-exp"},
	}
	at, deployed := p.DeployedAt(exporter)
	if deployed {
		t.Error("deployed should be false without annotation")
	}
	if !at.IsZero() {
		t.Errorf("at = %v, want zero", at)
	}
}

func TestDeployedAt_withTimestamp(t *testing.T) {
	p := New("v1.0.0", nil)
	when := time.Now().UTC().Truncate(time.Second)
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name: "test-exp",
			Annotations: map[string]string{
				AnnotationDeployedAt: when.Format(time.RFC3339),
			},
		},
	}
	at, deployed := p.DeployedAt(exporter)
	if !deployed {
		t.Error("deployed should be true with deployed-at annotation")
	}
	if !at.Equal(when) {
		t.Errorf("at = %v, want %v", at, when)
	}
}

// The connection is recorded before containers are created, so it must not by
// itself count as deployed — otherwise a partial deploy is never retried.
func TestDeployedAt_connectionRecordedButNotDeployed(t *testing.T) {
	p := New("v1.0.0", nil)
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name: "test-exp",
			Annotations: map[string]string{
				AnnotationConnection: `{"name":"lab-host-1.example.com","port":22,"user":"root","secret":"ssh-key"}`,
			},
		},
	}
	_, deployed := p.DeployedAt(exporter)
	if deployed {
		t.Error("deployed should be false when only the connection is recorded (deploy not completed)")
	}
}

func TestDeployedAt_malformedTimestamp(t *testing.T) {
	p := New("v1.0.0", nil)
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name: "test-exp",
			Annotations: map[string]string{
				AnnotationDeployedAt: "not-a-time",
			},
		},
	}
	at, deployed := p.DeployedAt(exporter)
	if !deployed {
		t.Error("deployed should be true (annotation present, even if malformed)")
	}
	if !at.IsZero() {
		t.Errorf("at = %v, want zero (malformed timestamp)", at)
	}
}

// recordConnection and markDeployed patch rather than update, so the
// second call must not conflict when another controller modifies the Exporter
// during the long deployInstance window between them (a plain Update would
// 409). The concurrent change must also survive.
func TestRecordConnectionAndMarkDeployed_survivesConcurrentUpdate(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := jumpstarterdevv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}

	key := client.ObjectKey{Name: "test-exp", Namespace: "test-ns"}
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: key.Name, Namespace: key.Namespace},
	}
	c := fake.NewClientBuilder().WithScheme(scheme).WithObjects(exporter).Build()
	p := New("v1.0.0", c)

	connCfg := ConnectionConfig{
		HostConfig: HostConfig{Host: "lab-host-1", Port: 2222, User: "deploy"},
		Secret:     "ssh-secret",
	}
	if err := p.recordConnection(context.Background(), exporter, connCfg); err != nil {
		t.Fatalf("recordConnection: %v", err)
	}

	// Simulate another controller updating the Exporter during deployInstance,
	// bumping the server resourceVersion so the object Deploy holds is stale.
	var concurrent jumpstarterdevv1alpha1.Exporter
	if err := c.Get(context.Background(), key, &concurrent); err != nil {
		t.Fatal(err)
	}
	concurrent.Labels = map[string]string{"touched": "by-other-controller"}
	if err := c.Update(context.Background(), &concurrent); err != nil {
		t.Fatalf("concurrent update: %v", err)
	}

	if err := p.markDeployed(context.Background(), exporter); err != nil {
		t.Fatalf("markDeployed after concurrent update: %v", err)
	}

	var final jumpstarterdevv1alpha1.Exporter
	if err := c.Get(context.Background(), key, &final); err != nil {
		t.Fatal(err)
	}
	gotConn, present, parseErr := connectionFromExporter(&final)
	if parseErr != nil {
		t.Fatalf("connectionFromExporter() err = %v", parseErr)
	}
	if !present {
		t.Fatal("connection annotation should be set after recordConnection")
	}
	if gotConn != connCfg {
		t.Errorf("connection = %+v, want %+v", gotConn, connCfg)
	}
	if final.Annotations[AnnotationDeployedAt] == "" {
		t.Error("deployed-at annotation should be set")
	}
	if final.Labels["touched"] != "by-other-controller" {
		t.Error("concurrent label change was clobbered by the patch")
	}
}

// Cleanup uses the connection recorded at Deploy time, so it no longer needs the
// VirtualTargetClass — the ExporterSet and its class can be deleted in any
// order. When the recorded Secret is gone, Cleanup returns an
// OrphanedResourcesError so the reconciler warns about the leftover resources
// without wedging the finalizer.
func TestCleanup_reportsOrphanedResourcesOnMissingSecret(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := virtualtargetv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := jumpstarterdevv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := corev1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}

	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "test-exporterset", Namespace: "test-ns"},
		Spec: virtualtargetv1alpha1.ExporterSetSpec{
			VirtualTargetClassName: "test-class",
		},
	}

	connRaw, err := json.Marshal(ConnectionConfig{
		HostConfig: HostConfig{Host: "lab-host-1.example.com", Port: 2222, User: "deploy-user"},
		Secret:     "test-ssh-secret",
	})
	if err != nil {
		t.Fatal(err)
	}
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name:        "test-exp",
			Namespace:   "test-ns",
			Annotations: map[string]string{AnnotationConnection: string(connRaw)},
		},
	}

	// No VirtualTargetClass and no Secret in the cluster: the missing Secret is a
	// skip-with-warning, reported as OrphanedResourcesError rather than failure.
	provisioner := New("v1.0.0", fake.NewClientBuilder().WithScheme(scheme).Build())
	err = provisioner.Cleanup(context.Background(), exporterSet, exporter)

	var orphaned *exporterset.OrphanedResourcesError
	if !errors.As(err, &orphaned) {
		t.Fatalf("Cleanup() = %v, want OrphanedResourcesError when the Secret is missing", err)
	}
	if !strings.Contains(orphaned.Message, "lab-host-1.example.com") {
		t.Errorf("message = %q, want it to name the host", orphaned.Message)
	}
}

// A transient (non-NotFound) Secret read error must fail Cleanup so it retries
// rather than orphaning on a blip.
func TestCleanup_failsOnTransientSecretError(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := virtualtargetv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}
	if err := jumpstarterdevv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}

	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "test-exporterset", Namespace: "test-ns"},
	}
	connRaw, err := json.Marshal(ConnectionConfig{
		HostConfig: HostConfig{Host: "lab-host-1.example.com", Port: 22, User: "root"},
		Secret:     "test-ssh-secret",
	})
	if err != nil {
		t.Fatal(err)
	}
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name:        "test-exp",
			Namespace:   "test-ns",
			Annotations: map[string]string{AnnotationConnection: string(connRaw)},
		},
	}

	c := &secretGetErrClient{
		Client: fake.NewClientBuilder().WithScheme(scheme).Build(),
		err:    apierrors.NewServiceUnavailable("kube-apiserver down"),
	}
	provisioner := New("v1.0.0", c)
	err = provisioner.Cleanup(context.Background(), exporterSet, exporter)
	if err == nil {
		t.Fatal("Cleanup should fail on a transient Secret read error so it retries")
	}
	var orphaned *exporterset.OrphanedResourcesError
	if errors.As(err, &orphaned) {
		t.Fatalf("Cleanup() = %v; a transient error must fail (retry), not report orphans", err)
	}
}

// secretGetErrClient returns a fixed error on Secret Get, simulating a transient
// API failure that must not be mistaken for NotFound.
type secretGetErrClient struct {
	client.Client
	err error
}

func (c *secretGetErrClient) Get(
	ctx context.Context, key client.ObjectKey, obj client.Object, opts ...client.GetOption,
) error {
	if _, ok := obj.(*corev1.Secret); ok {
		return c.err
	}
	return c.Client.Get(ctx, key, obj, opts...)
}

func TestResolveImage_latest(t *testing.T) {
	cases := []struct {
		name    string
		version string
		image   string
		want    string
	}{
		{
			name:    "version replaces latest",
			version: "v1.2.3",
			image:   "quay.io/jumpstarter-dev/jumpstarter:latest",
			want:    "quay.io/jumpstarter-dev/jumpstarter:1.2.3",
		},
		{
			name:    "dev version keeps latest",
			version: "dev",
			image:   "quay.io/jumpstarter-dev/jumpstarter:latest",
			want:    "quay.io/jumpstarter-dev/jumpstarter:latest",
		},
		{
			name:    "empty version keeps latest",
			version: "",
			image:   "quay.io/jumpstarter-dev/jumpstarter:latest",
			want:    "quay.io/jumpstarter-dev/jumpstarter:latest",
		},
		{
			name:    "git describe version keeps latest",
			version: "1.2.3-4-gabcdef",
			image:   "quay.io/jumpstarter-dev/jumpstarter:latest",
			want:    "quay.io/jumpstarter-dev/jumpstarter:latest",
		},
		{
			name:    "non-latest tag preserved",
			version: "v1.2.3",
			image:   "quay.io/jumpstarter-dev/jumpstarter:custom",
			want:    "quay.io/jumpstarter-dev/jumpstarter:custom",
		},
		{
			name:    "version without v prefix",
			version: "1.2.3",
			image:   "quay.io/jumpstarter-dev/jumpstarter:latest",
			want:    "quay.io/jumpstarter-dev/jumpstarter:1.2.3",
		},
	}

	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			got := resolveImage(tc.version, tc.image)
			if got != tc.want {
				t.Errorf("resolveImage(%q, %q) = %q, want %q",
					tc.version, tc.image, got, tc.want)
			}
		})
	}
}

func TestResolveImages_overrides(t *testing.T) {
	p := New("v1.0.0", nil)

	t.Run("nil images uses defaults", func(t *testing.T) {
		exp, rt := p.resolveImages(nil)
		if exp != "quay.io/jumpstarter-dev/jumpstarter:1.0.0" {
			t.Errorf("exporter image = %q", exp)
		}
		if rt != "quay.io/jumpstarter-dev/virtual/qemu-runtime:1.0.0" {
			t.Errorf("runtime image = %q", rt)
		}
	})

	t.Run("exporter override", func(t *testing.T) {
		exp, rt := p.resolveImages(&virtualtargetv1alpha1.ImageOverrides{
			Exporter: &virtualtargetv1alpha1.ImageSpec{
				Image: "custom-exporter:v2",
			},
		})
		if exp != "custom-exporter:v2" {
			t.Errorf("exporter image = %q", exp)
		}
		if rt != "quay.io/jumpstarter-dev/virtual/qemu-runtime:1.0.0" {
			t.Errorf("runtime image = %q", rt)
		}
	})

	t.Run("runtime override", func(t *testing.T) {
		exp, rt := p.resolveImages(&virtualtargetv1alpha1.ImageOverrides{
			Runtime: &virtualtargetv1alpha1.ImageSpec{
				Image: "custom-runtime:v3",
			},
		})
		if exp != "quay.io/jumpstarter-dev/jumpstarter:1.0.0" {
			t.Errorf("exporter image = %q", exp)
		}
		if rt != "custom-runtime:v3" {
			t.Errorf("runtime image = %q", rt)
		}
	})
}

func TestBuildExportMap_basic(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "qemu",
			Type:   qemuDriverType,
			Config: mustJSON(map[string]any{"arch": "x86_64"}),
		},
		{
			Name: "ssh",
			Ref:  "qemu.ssh",
		},
	}

	exportMap, err := buildExportMap(drivers)
	if err != nil {
		t.Fatal(err)
	}

	if len(exportMap) != 2 {
		t.Fatalf("export map len = %d, want 2", len(exportMap))
	}

	qemu := exportMap["qemu"]
	if qemu.Type != qemuDriverType {
		t.Errorf("qemu.type = %q", qemu.Type)
	}

	sshDriver := exportMap["ssh"]
	if sshDriver.Ref != "qemu.ssh" {
		t.Errorf("ssh.ref = %q", sshDriver.Ref)
	}
}

func TestBuildExportMap_duplicateKey(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{Name: "qemu", Type: qemuDriverType},
		{Name: "qemu", Type: tcpDriverType},
	}

	_, err := buildExportMap(drivers)
	if err == nil {
		t.Fatal("expected error for duplicate key")
	}
}

// ParseHost applies defaults for user/port; the recorded ConnectionConfig
// carries the resolved values so Cleanup never re-parses parameters.
