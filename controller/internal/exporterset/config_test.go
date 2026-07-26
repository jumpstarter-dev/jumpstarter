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

package exporterset

import (
	"encoding/json"
	"strings"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestRenderExporterConfigYAML_minimal(t *testing.T) {
	exp := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{
			Name:      "my-exporter",
			Namespace: nsDefault,
		},
	}

	got, err := renderExporterConfigYAML(exp, "tok123", "", nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	assertContains(t, got, "apiVersion: jumpstarter.dev/v1alpha1")
	assertContains(t, got, "kind: ExporterConfig")
	assertContains(t, got, "name: my-exporter")
	assertContains(t, got, "namespace: default")
	assertContains(t, got, "token: tok123")
	assertNotContains(t, got, "tls:")
	assertNotContains(t, got, "export:")
}

func TestRenderExporterConfigYAML_withCABundle(t *testing.T) {
	exp := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: "exp", Namespace: nsDefault},
	}

	got, err := renderExporterConfigYAML(exp, "tok", "-----BEGIN CERTIFICATE-----\nABC\n", nil)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	assertContains(t, got, "tls:")
	assertContains(t, got, "ca:")
	assertContains(t, got, "BEGIN CERTIFICATE")
}

func TestRenderExporterConfigYAML_withDrivers_namedExplicit(t *testing.T) {
	exp := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: "exp", Namespace: nsDefault},
	}

	drivers := []virtualtargetv1alpha1.DriverConfig{
		{Name: "power", Type: "jumpstarter_driver_power.driver.QemuPower"},
		{Name: "serial", Type: "jumpstarter_driver_serial.driver.QemuSerial"},
	}

	got, err := renderExporterConfigYAML(exp, "tok", "", drivers)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	assertContains(t, got, "export:")
	assertContains(t, got, "power:")
	assertContains(t, got, "serial:")
	assertContains(t, got, "QemuPower")
	assertContains(t, got, "QemuSerial")
}

func TestRenderExporterConfigYAML_withDrivers_derivedName(t *testing.T) {
	exp := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: "exp", Namespace: nsDefault},
	}

	drivers := []virtualtargetv1alpha1.DriverConfig{
		{Type: "jumpstarter_driver_power.driver.QemuPower"},
	}

	got, err := renderExporterConfigYAML(exp, "tok", "", drivers)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	// Name derived: lower-case last segment = "qemupower"
	assertContains(t, got, "qemupower:")
}

func TestRenderExporterConfigYAML_withDriverConfig(t *testing.T) {
	exp := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: "exp", Namespace: nsDefault},
	}

	raw, _ := json.Marshal(map[string]interface{}{"port": 22, "host": "192.168.1.1"})
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "network",
			Type:   "jumpstarter_driver_network.driver.TcpNetwork",
			Config: &apiextensionsv1.JSON{Raw: raw},
		},
	}

	got, err := renderExporterConfigYAML(exp, "tok", "", drivers)
	if err != nil {
		t.Fatalf("unexpected error: %v", err)
	}

	assertContains(t, got, "network:")
	assertContains(t, got, "TcpNetwork")
	assertContains(t, got, "port:")
	assertContains(t, got, "host:")
}

func TestDriverKey_explicit(t *testing.T) {
	d := virtualtargetv1alpha1.DriverConfig{Name: "power", Type: "some.Type"}
	if got := driverKey(d, 0); got != "power" {
		t.Errorf("got %q, want %q", got, "power")
	}
}

func TestDriverKey_derived(t *testing.T) {
	d := virtualtargetv1alpha1.DriverConfig{Type: "a.b.c.MyDriver"}
	if got := driverKey(d, 0); got != "mydriver" {
		t.Errorf("got %q, want %q", got, "mydriver")
	}
}

func TestDriverKey_fallback(t *testing.T) {
	d := virtualtargetv1alpha1.DriverConfig{Type: ""}
	if got := driverKey(d, 3); got != "driver3" {
		t.Errorf("got %q, want %q", got, "driver3")
	}
}

func TestInjectConfigVolume_mountsExporterContainer(t *testing.T) {
	pod := &corev1.Pod{
		Spec: corev1.PodSpec{
			InitContainers: []corev1.Container{{Name: "exporter"}},
		},
	}

	ok := injectConfigVolume(pod, "my-exp")
	if !ok {
		t.Fatal("expected injectConfigVolume to return true")
	}
	if len(pod.Spec.Volumes) == 0 {
		t.Fatal("expected volume to be added")
	}
	if len(pod.Spec.InitContainers[0].VolumeMounts) == 0 {
		t.Fatal("expected volume mount to be added to exporter container")
	}
	if pod.Spec.InitContainers[0].VolumeMounts[0].MountPath != configMountPath {
		t.Errorf("unexpected mount path: %s", pod.Spec.InitContainers[0].VolumeMounts[0].MountPath)
	}
}

func TestInjectConfigVolume_returnsFalseWhenContainerMissing(t *testing.T) {
	pod := &corev1.Pod{
		Spec: corev1.PodSpec{
			InitContainers: []corev1.Container{{Name: "not-exporter"}},
		},
	}

	ok := injectConfigVolume(pod, "my-exp")
	if ok {
		t.Error("expected injectConfigVolume to return false when exporter container absent")
	}
	// Volume is still added even when mount can't be set.
	if len(pod.Spec.Volumes) == 0 {
		t.Error("expected volume to be added even when mount fails")
	}
}

func assertContains(t *testing.T, s, sub string) {
	t.Helper()
	if !strings.Contains(s, sub) {
		t.Errorf("expected output to contain %q\ngot:\n%s", sub, s)
	}
}

func assertNotContains(t *testing.T, s, sub string) {
	t.Helper()
	if strings.Contains(s, sub) {
		t.Errorf("expected output NOT to contain %q\ngot:\n%s", sub, s)
	}
}

func TestRenderExporterConfigYAML_duplicateDriverKey_returnsError(t *testing.T) {
	exp := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: "exp-1", Namespace: "default"},
	}
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{Name: "power", Type: "jumpstarter_driver_power.driver.QemuPower"},
		{Name: "power", Type: "jumpstarter_driver_power.driver.QemuPower2"},
	}
	_, err := renderExporterConfigYAML(exp, "tok", "", drivers)
	if err == nil {
		t.Fatal("expected error for duplicate driver key, got nil")
	}
}

func TestRenderExporterConfigYAML_derivedDuplicateKey_returnsError(t *testing.T) {
	exp := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: "exp-1", Namespace: "default"},
	}
	// Both types end in ".Serial" — derived key "serial" would collide.
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{Type: "vendor_a.driver.Serial"},
		{Type: "vendor_b.driver.Serial"},
	}
	_, err := renderExporterConfigYAML(exp, "tok", "", drivers)
	if err == nil {
		t.Fatal("expected error for derived duplicate key, got nil")
	}
}
