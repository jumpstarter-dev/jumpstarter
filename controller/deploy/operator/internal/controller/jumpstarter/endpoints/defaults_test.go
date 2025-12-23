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

package endpoints

import (
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
)

var _ = Describe("ApplyEndpointDefaults", func() {
	Context("when baseDomain is empty", func() {
		It("should skip endpoint generation", func() {
			spec := &operatorv1alpha1.JumpstarterSpec{BaseDomain: ""}

			ApplyEndpointDefaults(spec, true, true)

			Expect(spec.Controller.GRPC.Endpoints).To(BeEmpty())
			Expect(spec.Routers.GRPC.Endpoints).To(BeEmpty())
		})
	})

	Context("when baseDomain is set and no endpoints exist", func() {
		It("should generate controller endpoint with Route when available", func() {
			spec := &operatorv1alpha1.JumpstarterSpec{BaseDomain: "example.com"}

			ApplyEndpointDefaults(spec, true, true)

			Expect(spec.Controller.GRPC.Endpoints).To(HaveLen(1))
			Expect(spec.Controller.GRPC.Endpoints[0].Address).To(Equal("grpc.example.com"))
			Expect(spec.Controller.GRPC.Endpoints[0].Route).NotTo(BeNil())
			Expect(spec.Controller.GRPC.Endpoints[0].Route.Enabled).To(BeTrue())
		})

		It("should generate controller endpoint with Ingress when Route unavailable", func() {
			spec := &operatorv1alpha1.JumpstarterSpec{BaseDomain: "example.com"}

			ApplyEndpointDefaults(spec, false, true)

			Expect(spec.Controller.GRPC.Endpoints).To(HaveLen(1))
			Expect(spec.Controller.GRPC.Endpoints[0].Ingress).NotTo(BeNil())
			Expect(spec.Controller.GRPC.Endpoints[0].Ingress.Enabled).To(BeTrue())
		})

		It("should fallback to ClusterIP when neither Route nor Ingress available", func() {
			spec := &operatorv1alpha1.JumpstarterSpec{BaseDomain: "example.com"}

			ApplyEndpointDefaults(spec, false, false)

			Expect(spec.Controller.GRPC.Endpoints).To(HaveLen(1))
			Expect(spec.Controller.GRPC.Endpoints[0].ClusterIP).NotTo(BeNil())
			Expect(spec.Controller.GRPC.Endpoints[0].ClusterIP.Enabled).To(BeTrue())
		})

		It("should generate router endpoint with $(replica) placeholder", func() {
			spec := &operatorv1alpha1.JumpstarterSpec{BaseDomain: "example.com"}

			ApplyEndpointDefaults(spec, true, true)

			Expect(spec.Routers.GRPC.Endpoints).To(HaveLen(1))
			Expect(spec.Routers.GRPC.Endpoints[0].Address).To(Equal("router-$(replica).example.com"))
			Expect(spec.Routers.GRPC.Endpoints[0].Route).NotTo(BeNil())
			Expect(spec.Routers.GRPC.Endpoints[0].Route.Enabled).To(BeTrue())
		})
	})

	Context("when endpoints already exist", func() {
		It("should not override existing endpoints", func() {
			spec := &operatorv1alpha1.JumpstarterSpec{
				BaseDomain: "example.com",
				Controller: operatorv1alpha1.ControllerConfig{
					GRPC: operatorv1alpha1.GRPCConfig{
						Endpoints: []operatorv1alpha1.Endpoint{
							{Address: "custom.example.com", ClusterIP: &operatorv1alpha1.ClusterIPConfig{Enabled: true}},
						},
					},
				},
			}

			ApplyEndpointDefaults(spec, true, true)

			Expect(spec.Controller.GRPC.Endpoints).To(HaveLen(1))
			Expect(spec.Controller.GRPC.Endpoints[0].Address).To(Equal("custom.example.com"))
		})

		It("should ensure existing endpoints have a service type enabled", func() {
			spec := &operatorv1alpha1.JumpstarterSpec{
				BaseDomain: "example.com",
				Controller: operatorv1alpha1.ControllerConfig{
					GRPC: operatorv1alpha1.GRPCConfig{
						Endpoints: []operatorv1alpha1.Endpoint{
							{Address: "custom.example.com"}, // No service type
						},
					},
				},
			}

			ApplyEndpointDefaults(spec, true, true)

			// Should auto-select Route since it's available
			Expect(spec.Controller.GRPC.Endpoints[0].Route).NotTo(BeNil())
			Expect(spec.Controller.GRPC.Endpoints[0].Route.Enabled).To(BeTrue())
		})
	})
})

var _ = Describe("ensureEndpointServiceType", func() {
	Context("when endpoint already has a service type enabled", func() {
		It("should not modify the endpoint", func() {
			endpoint := &operatorv1alpha1.Endpoint{
				NodePort: &operatorv1alpha1.NodePortConfig{Enabled: true},
			}

			ensureEndpointServiceType(endpoint, true, true)

			// Should remain NodePort, not changed to Route
			Expect(endpoint.NodePort.Enabled).To(BeTrue())
			Expect(endpoint.Route).To(BeNil())
		})
	})

	Context("when no service type is enabled", func() {
		It("should auto-select Route when available", func() {
			endpoint := &operatorv1alpha1.Endpoint{}

			ensureEndpointServiceType(endpoint, true, true)

			Expect(endpoint.Route).NotTo(BeNil())
			Expect(endpoint.Route.Enabled).To(BeTrue())
		})

		It("should auto-select Ingress when Route unavailable", func() {
			endpoint := &operatorv1alpha1.Endpoint{}

			ensureEndpointServiceType(endpoint, false, true)

			Expect(endpoint.Ingress).NotTo(BeNil())
			Expect(endpoint.Ingress.Enabled).To(BeTrue())
		})

		It("should fallback to ClusterIP when neither available", func() {
			endpoint := &operatorv1alpha1.Endpoint{}

			ensureEndpointServiceType(endpoint, false, false)

			Expect(endpoint.ClusterIP).NotTo(BeNil())
			Expect(endpoint.ClusterIP.Enabled).To(BeTrue())
		})
	})
})
