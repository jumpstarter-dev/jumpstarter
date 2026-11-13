/*
Copyright 2026. The Jumpstarter Authors.

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

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

var _ = Describe("Endpoint port admission", func() {
	ctx := context.Background()
	var js *operatorv1alpha1.Jumpstarter
	BeforeEach(func() {
		ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "endpoint-ports-"}}
		Expect(k8sClient.Create(ctx, ns)).To(Succeed())
		DeferCleanup(k8sClient.Delete, ctx, ns)
		js = phase3TelemetryJS(ns.Name)
	})

	DescribeTable("validates address ports for every endpoint type", func(address string, valid bool) {
		endpoints := []operatorv1alpha1.Endpoint{{Address: address}}
		js.Spec.Controller.GRPC.Endpoints = endpoints
		js.Spec.Controller.Login.Endpoints = endpoints
		js.Spec.Controller.RestAPI.Endpoints = endpoints
		js.Spec.Routers.GRPC.Endpoints = endpoints
		js.Spec.Telemetry.GRPC.Endpoints = endpoints
		err := k8sClient.Create(ctx, js)
		if valid {
			Expect(err).NotTo(HaveOccurred())
			DeferCleanup(k8sClient.Delete, ctx, js)
			return
		}
		Expect(errors.IsInvalid(err)).To(BeTrue())
		var fields []string
		for _, cause := range err.(*errors.StatusError).ErrStatus.Details.Causes {
			fields = append(fields, cause.Field)
		}
		Expect(fields).To(ContainElements(
			"spec.controller.grpc.endpoints[0].address",
			"spec.controller.login.endpoints[0].address",
			"spec.controller.restApi.endpoints[0].address",
			"spec.routers.grpc.endpoints[0].address",
			"spec.telemetry.grpc.endpoints[0].address",
		))
	},
		Entry("no port", "telemetry.example.com", true),
		Entry("minimum", "telemetry.example.com:1", true),
		Entry("maximum", "telemetry.example.com:65535", true),
		Entry("leading zeros", "telemetry.example.com:00443", true),
		Entry("replica substitution", "router-$(replica).example.com:443", true),
		Entry("IPv4", "192.0.2.1:443", true),
		Entry("IPv6 without port", "[2001:db8::1]", true),
		Entry("IPv6 with port", "[2001:db8::1]:443", true),
		Entry("zero", "telemetry.example.com:0", false),
		Entry("zero with padding", "telemetry.example.com:000", false),
		Entry("above range", "telemetry.example.com:65536", false),
		Entry("negative", "telemetry.example.com:-1", false),
		Entry("nonnumeric", "telemetry.example.com:https", false),
		Entry("empty port", "telemetry.example.com:", false),
		Entry("IPv4 zero", "192.0.2.1:0", false),
		Entry("IPv6 zero", "[2001:db8::1]:0", false),
		Entry("int32 wraparound", "telemetry.example.com:4294967739", false),
		Entry("overflow", "telemetry.example.com:999999999999999999999999", false),
	)

	It("allows omitted Service ports but rejects explicit zero", func() {
		js.Spec.Telemetry.GRPC.Endpoints = []operatorv1alpha1.Endpoint{{
			Address:      "telemetry.example.com",
			NodePort:     &operatorv1alpha1.NodePortConfig{Enabled: true},
			LoadBalancer: &operatorv1alpha1.LoadBalancerConfig{Enabled: true},
		}}
		Expect(k8sClient.Create(ctx, js)).To(Succeed())
		DeferCleanup(k8sClient.Delete, ctx, js)

		// Go's omitempty drops zero; use unstructured data to send explicit zeros.
		data, err := runtime.DefaultUnstructuredConverter.ToUnstructured(js)
		Expect(err).NotTo(HaveOccurred())
		configured, found, err := unstructured.NestedSlice(data, "spec", "telemetry", "grpc", "endpoints")
		Expect(err).NotTo(HaveOccurred())
		Expect(found).To(BeTrue())
		for _, method := range []string{"nodeport", "loadBalancer"} {
			configured[0].(map[string]any)[method].(map[string]any)["port"] = int64(0)
		}
		Expect(unstructured.SetNestedSlice(data, configured, "spec", "telemetry", "grpc", "endpoints")).To(Succeed())
		obj := &unstructured.Unstructured{Object: data}
		obj.SetAPIVersion(operatorv1alpha1.GroupVersion.String())
		obj.SetKind("Jumpstarter")
		err = k8sClient.Update(ctx, obj)
		Expect(errors.IsInvalid(err)).To(BeTrue())
		Expect(err.Error()).To(ContainSubstring("spec.telemetry.grpc.endpoints[0].nodeport.port"))
		Expect(err.Error()).To(ContainSubstring("spec.telemetry.grpc.endpoints[0].loadBalancer.port"))
	})
})
