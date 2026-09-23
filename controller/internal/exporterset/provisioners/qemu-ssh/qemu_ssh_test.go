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
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestProvisionerName(t *testing.T) {
	p := New("v1.0.0", nil)
	if got := p.Name(); got != ProvisionerName {
		t.Errorf("Name() = %q, want %q", got, ProvisionerName)
	}
}

func TestRenderPod_returnsNil(t *testing.T) {
	p := New("v1.0.0", nil)
	pod, err := p.RenderPod(nil, nil, nil, nil, nil, nil)
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
	deployed, err := p.IsDeployed(nil, exporter)
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
				AnnotationHost: "lab-host-1.example.com",
			},
		},
	}
	deployed, err := p.IsDeployed(nil, exporter)
	if err != nil {
		t.Fatal(err)
	}
	if !deployed {
		t.Error("IsDeployed should be true with host annotation")
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
