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
	"strings"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
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

func TestIsDeployed_noAnnotation(t *testing.T) {
	p := New("v1.0.0", nil)
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: "test-exp"},
	}
	deployed, err := p.IsDeployed(context.Background(), exporter)
	if err != nil {
		t.Fatal(err)
	}
	if deployed {
		t.Error("IsDeployed should be false without annotation")
	}
}

func TestIsDeployed_withAnnotation(t *testing.T) {
	p := New("v1.0.0", nil)
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name: "test-exp",
			Annotations: map[string]string{
				AnnotationHost:     "lab-host-1.example.com",
				AnnotationDeployed: "true",
			},
		},
	}
	deployed, err := p.IsDeployed(context.Background(), exporter)
	if err != nil {
		t.Fatal(err)
	}
	if !deployed {
		t.Error("IsDeployed should be true once deployment is marked complete")
	}
}

// A host assignment is recorded before containers are created, so it must not
// by itself count as deployed — otherwise a partial deploy is never retried.
func TestIsDeployed_hostRecordedButNotDeployed(t *testing.T) {
	p := New("v1.0.0", nil)
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name: "test-exp",
			Annotations: map[string]string{
				AnnotationHost: "lab-host-1.example.com",
			},
		},
	}
	deployed, err := p.IsDeployed(context.Background(), exporter)
	if err != nil {
		t.Fatal(err)
	}
	if deployed {
		t.Error("IsDeployed should be false when only the host is recorded (deploy not completed)")
	}
}

// recordHostAssignment and markDeployed patch rather than update, so the
// second call must not conflict when another controller modifies the Exporter
// during the long deployInstance window between them (a plain Update would
// 409). The concurrent change must also survive.
func TestRecordAndMarkDeployed_survivesConcurrentUpdate(t *testing.T) {
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

	if err := p.recordHostAssignment(context.Background(), exporter, "lab-host-1", "ssh-secret"); err != nil {
		t.Fatalf("recordHostAssignment: %v", err)
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
	if final.Annotations[AnnotationHost] != "lab-host-1" {
		t.Errorf("host annotation = %q, want lab-host-1", final.Annotations[AnnotationHost])
	}
	if final.Annotations[AnnotationCredentialsSecret] != "ssh-secret" {
		t.Errorf("credentials annotation = %q, want ssh-secret", final.Annotations[AnnotationCredentialsSecret])
	}
	if final.Annotations[AnnotationDeployed] != "true" {
		t.Errorf("deployed annotation = %q, want true", final.Annotations[AnnotationDeployed])
	}
	if final.Labels["touched"] != "by-other-controller" {
		t.Error("concurrent label change was clobbered by the patch")
	}
}

func TestCleanup_requiresVirtualTargetClassForNonDefaultPort(t *testing.T) {
	scheme := runtime.NewScheme()
	if err := virtualtargetv1alpha1.AddToScheme(scheme); err != nil {
		t.Fatal(err)
	}

	parameters, err := json.Marshal(map[string]any{
		"host": map[string]any{
			"name": "lab-host-1.example.com",
			"user": "deploy-user",
			"port": 2222,
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	exporterSet := &virtualtargetv1alpha1.ExporterSet{
		ObjectMeta: metav1.ObjectMeta{Name: "test-exporterset", Namespace: "test-ns"},
		Spec: virtualtargetv1alpha1.ExporterSetSpec{
			VirtualTargetClassName: "test-class",
			Parameters:             &apiextensionsv1.JSON{Raw: parameters},
		},
	}
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "test-exp",
			Namespace: "test-ns",
			Annotations: map[string]string{
				AnnotationHost:              "lab-host-1.example.com",
				AnnotationCredentialsSecret: "test-ssh-secret",
			},
		},
	}

	provisioner := New("v1.0.0", fake.NewClientBuilder().WithScheme(scheme).Build())
	err = provisioner.Cleanup(context.Background(), exporterSet, exporter)
	if err == nil {
		t.Fatal("Cleanup should fail when the deployed VirtualTargetClass is deleted")
	}
	if !strings.Contains(err.Error(), "delete ExporterSet test-exporterset before its class") {
		t.Errorf("Cleanup error = %q, want missing VirtualTargetClass guidance", err)
	}
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

func TestMergeClassAndSetParameters_hostFromExporterSet(t *testing.T) {
	classRaw, err := json.Marshal(map[string]any{
		"arch": "x86_64",
		"runtime": map[string]any{
			"host_network": true,
		},
	})
	if err != nil {
		t.Fatal(err)
	}
	setRaw, err := json.Marshal(map[string]any{
		"host": map[string]any{
			"name": "172.18.0.1",
			"user": "root",
			"port": 22,
		},
	})
	if err != nil {
		t.Fatal(err)
	}

	merged := mergeClassAndSetParameters(
		&apiextensionsv1.JSON{Raw: classRaw},
		&apiextensionsv1.JSON{Raw: setRaw},
	)

	host, err := ParseHost(merged)
	if err != nil {
		t.Fatalf("ParseHost: %v", err)
	}
	if host.Name != "172.18.0.1" || host.User != "root" || host.Port != 22 {
		t.Errorf("host = %+v, want name=172.18.0.1 user=root port=22", host)
	}
	if merged["arch"] != "x86_64" {
		t.Errorf("arch = %v, want x86_64", merged["arch"])
	}
}
