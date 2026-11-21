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
	"testing"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/internal/controller/jumpstarter/endpoints"
)

var testOperandImages = OperandImages{
	Controller:            "registry.example/jumpstarter-controller:1.2.3",
	ExporterSetController: "registry.example/jumpstarter-exporterset-controller:1.2.3",
}

// A Jumpstarter that names no images goes through the API server, so a CRD
// default would land in the spec before the operator sees it.
var _ = Describe("Jumpstarter without images", func() {
	ctx := context.Background()

	It("runs the operator's images and follows the operator to a new release", func() {
		ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "operand-images-"}}
		Expect(k8sClient.Create(ctx, ns)).To(Succeed())
		DeferCleanup(k8sClient.Delete, ctx, ns)

		js := &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: "js", Namespace: ns.Name},
			Spec: operatorv1alpha1.JumpstarterSpec{
				BaseDomain:  "example.com",
				CertManager: operatorv1alpha1.CertManagerConfig{Enabled: false},
				Controller: operatorv1alpha1.ControllerConfig{
					GRPC: operatorv1alpha1.GRPCConfig{
						Endpoints: []operatorv1alpha1.Endpoint{{Address: "controller"}},
					},
				},
				Routers: operatorv1alpha1.RoutersConfig{
					GRPC: operatorv1alpha1.GRPCConfig{
						Endpoints: []operatorv1alpha1.Endpoint{{Address: "router"}},
					},
				},
				ExporterSets: &operatorv1alpha1.ExporterSetsConfig{
					Provisioners: []operatorv1alpha1.ProvisionerConfig{{Name: "qemu.jumpstarter.dev"}},
				},
			},
		}
		Expect(k8sClient.Create(ctx, js)).To(Succeed())
		key := types.NamespacedName{Name: js.Name, Namespace: ns.Name}

		reconcileWith := func(images OperandImages) {
			r := &JumpstarterReconciler{
				Client:             k8sClient,
				Scheme:             k8sClient.Scheme(),
				EndpointReconciler: endpoints.NewReconciler(k8sClient, k8sClient.Scheme(), cfg),
				OperandImages:      images,
			}
			_, err := r.Reconcile(ctx, reconcile.Request{NamespacedName: key})
			Expect(err).NotTo(HaveOccurred())
		}
		expectImages := func(images OperandImages) {
			image := func(name string) string {
				dep := &appsv1.Deployment{}
				Expect(k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: ns.Name}, dep)).To(Succeed())
				return dep.Spec.Template.Spec.Containers[0].Image
			}
			Expect(image("js-controller")).To(Equal(images.Controller))
			Expect(image("js-router-0")).To(Equal(images.Controller))
			Expect(image("js-exporterset-qemu-jumpstarter-dev")).To(Equal(images.ExporterSetController))
		}

		reconcileWith(testOperandImages)
		expectImages(testOperandImages)

		// An upgraded operator comes back with its own release's images. The
		// Jumpstarter still names none, so its components follow the operator,
		// which never writes the images it picked into the spec.
		upgraded := OperandImages{
			Controller:            "registry.example/jumpstarter-controller:1.2.4",
			ExporterSetController: "registry.example/jumpstarter-exporterset-controller:1.2.4",
		}
		reconcileWith(upgraded)
		expectImages(upgraded)

		stored := &operatorv1alpha1.Jumpstarter{}
		Expect(k8sClient.Get(ctx, key, stored)).To(Succeed())
		Expect(stored.Spec).To(Equal(js.Spec))
	})
})

func TestOperandImagesApplyDefaults(t *testing.T) {
	const custom = "registry.example/custom:v9"

	tests := []struct {
		name string
		spec operatorv1alpha1.JumpstarterSpec
		want operatorv1alpha1.JumpstarterSpec
	}{
		{
			// Stored by the API server for every Jumpstarter created
			// without an image while the CRD defaulted to it (up to v0.9.0).
			name: "legacy controller default",
			spec: operatorv1alpha1.JumpstarterSpec{
				Controller: operatorv1alpha1.ControllerConfig{Image: legacyControllerImageDefault},
				Routers:    operatorv1alpha1.RoutersConfig{Image: legacyControllerImageDefault},
			},
			want: operatorv1alpha1.JumpstarterSpec{
				Controller: operatorv1alpha1.ControllerConfig{Image: testOperandImages.Controller},
				Routers:    operatorv1alpha1.RoutersConfig{Image: testOperandImages.Controller},
			},
		},
		{
			name: "telemetry and exportersets without images",
			spec: operatorv1alpha1.JumpstarterSpec{
				Controller:   operatorv1alpha1.ControllerConfig{Image: custom},
				Routers:      operatorv1alpha1.RoutersConfig{Image: custom},
				Telemetry:    &operatorv1alpha1.TelemetryConfig{},
				ExporterSets: &operatorv1alpha1.ExporterSetsConfig{},
			},
			want: operatorv1alpha1.JumpstarterSpec{
				Controller:   operatorv1alpha1.ControllerConfig{Image: custom},
				Routers:      operatorv1alpha1.RoutersConfig{Image: custom},
				Telemetry:    &operatorv1alpha1.TelemetryConfig{Image: testOperandImages.Controller},
				ExporterSets: &operatorv1alpha1.ExporterSetsConfig{Image: testOperandImages.ExporterSetController},
			},
		},
		{
			name: "explicit images",
			spec: operatorv1alpha1.JumpstarterSpec{
				Controller:   operatorv1alpha1.ControllerConfig{Image: custom},
				Routers:      operatorv1alpha1.RoutersConfig{Image: custom},
				Telemetry:    &operatorv1alpha1.TelemetryConfig{Image: custom},
				ExporterSets: &operatorv1alpha1.ExporterSetsConfig{Image: custom},
			},
			want: operatorv1alpha1.JumpstarterSpec{
				Controller:   operatorv1alpha1.ControllerConfig{Image: custom},
				Routers:      operatorv1alpha1.RoutersConfig{Image: custom},
				Telemetry:    &operatorv1alpha1.TelemetryConfig{Image: custom},
				ExporterSets: &operatorv1alpha1.ExporterSetsConfig{Image: custom},
			},
		},
		{
			// Filling in an image must not switch on a component.
			name: "no telemetry or exportersets",
			spec: operatorv1alpha1.JumpstarterSpec{},
			want: operatorv1alpha1.JumpstarterSpec{
				Controller: operatorv1alpha1.ControllerConfig{Image: testOperandImages.Controller},
				Routers:    operatorv1alpha1.RoutersConfig{Image: testOperandImages.Controller},
			},
		},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := NewWithT(t)
			spec := tt.spec
			testOperandImages.applyDefaults(&spec)
			g.Expect(spec).To(Equal(tt.want))
		})
	}
}

func TestOperandImagesFromEnv(t *testing.T) {
	tests := []struct {
		name            string
		controller      string
		exporterSet     string
		wantErrContains string
	}{
		{name: "both set", controller: "c:1", exporterSet: "e:1"},
		{name: "controller missing", exporterSet: "e:1", wantErrContains: controllerImageEnv},
		{name: "exporterset missing", controller: "c:1", wantErrContains: exporterSetControllerImageEnv},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			g := NewWithT(t)
			t.Setenv(controllerImageEnv, tt.controller)
			t.Setenv(exporterSetControllerImageEnv, tt.exporterSet)

			images, err := OperandImagesFromEnv()
			if tt.wantErrContains != "" {
				g.Expect(err).To(MatchError(ContainSubstring(tt.wantErrContains)))
				return
			}
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(images).To(Equal(OperandImages{Controller: "c:1", ExporterSetController: "e:1"}))
		})
	}
}
