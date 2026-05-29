/*
Copyright 2025. The Jumpstarter Authors.

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
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/resource"
)

var _ = Describe("defaultControllerResources", func() {
	It("should return defaults when spec is empty", func() {
		result := defaultControllerResources(corev1.ResourceRequirements{})

		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("200m")))
		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("512Mi")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("1")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("1Gi")))
	})

	It("should return user-specified resources when requests are set", func() {
		custom := corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU: resource.MustParse("500m"),
			},
		}

		result := defaultControllerResources(custom)

		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("500m")))
		Expect(result.Limits).To(BeNil())
	})

	It("should return user-specified resources when limits are set", func() {
		custom := corev1.ResourceRequirements{
			Limits: corev1.ResourceList{
				corev1.ResourceMemory: resource.MustParse("2Gi"),
			},
		}

		result := defaultControllerResources(custom)

		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("2Gi")))
		Expect(result.Requests).To(BeNil())
	})

	It("should return user-specified resources when both requests and limits are set", func() {
		custom := corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("300m"),
				corev1.ResourceMemory: resource.MustParse("1Gi"),
			},
			Limits: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("2"),
				corev1.ResourceMemory: resource.MustParse("4Gi"),
			},
		}

		result := defaultControllerResources(custom)

		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("300m")))
		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("1Gi")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("2")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("4Gi")))
	})

	It("should preserve claims-only input without applying defaults", func() {
		custom := corev1.ResourceRequirements{
			Claims: []corev1.ResourceClaim{
				{Name: "gpu"},
			},
		}

		result := defaultControllerResources(custom)

		Expect(result.Claims).To(HaveLen(1))
		Expect(result.Claims[0].Name).To(Equal("gpu"))
		Expect(result.Requests).To(BeNil())
		Expect(result.Limits).To(BeNil())
	})
})

var _ = Describe("defaultRouterResources", func() {
	It("should return defaults when spec is empty", func() {
		result := defaultRouterResources(corev1.ResourceRequirements{})

		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("100m")))
		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("256Mi")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("1")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("512Mi")))
	})

	It("should return user-specified resources when requests are set", func() {
		custom := corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU: resource.MustParse("250m"),
			},
		}

		result := defaultRouterResources(custom)

		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("250m")))
		Expect(result.Limits).To(BeNil())
	})

	It("should return user-specified resources when limits are set", func() {
		custom := corev1.ResourceRequirements{
			Limits: corev1.ResourceList{
				corev1.ResourceMemory: resource.MustParse("1Gi"),
			},
		}

		result := defaultRouterResources(custom)

		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("1Gi")))
		Expect(result.Requests).To(BeNil())
	})

	It("should return user-specified resources when both requests and limits are set", func() {
		custom := corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("200m"),
				corev1.ResourceMemory: resource.MustParse("512Mi"),
			},
			Limits: corev1.ResourceList{
				corev1.ResourceCPU:    resource.MustParse("2"),
				corev1.ResourceMemory: resource.MustParse("2Gi"),
			},
		}

		result := defaultRouterResources(custom)

		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("200m")))
		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("512Mi")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("2")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("2Gi")))
	})

	It("should preserve claims-only input without applying defaults", func() {
		custom := corev1.ResourceRequirements{
			Claims: []corev1.ResourceClaim{
				{Name: "gpu"},
			},
		}

		result := defaultRouterResources(custom)

		Expect(result.Claims).To(HaveLen(1))
		Expect(result.Claims[0].Name).To(Equal("gpu"))
		Expect(result.Requests).To(BeNil())
		Expect(result.Limits).To(BeNil())
	})
})
