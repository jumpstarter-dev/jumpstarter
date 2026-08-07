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
	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

var _ = Describe("createRouterDeployment metrics bind", func() {
	var r *JumpstarterReconciler
	var js *operatorv1alpha1.Jumpstarter

	BeforeEach(func() {
		r = &JumpstarterReconciler{}
		js = &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "jumpstarter",
				Namespace: "jumpstarter-lab",
			},
			Spec: operatorv1alpha1.JumpstarterSpec{
				Routers: operatorv1alpha1.RoutersConfig{
					Image:           "example.com/router:test",
					ImagePullPolicy: corev1.PullIfNotPresent,
					Replicas:        1,
				},
			},
		}
	})

	It("exposes metrics-bind-address=:8080 and metrics port 8080", func() {
		dep := r.createRouterDeployment(js, 0)
		Expect(dep).NotTo(BeNil())
		Expect(dep.Spec.Template.Spec.Containers).NotTo(BeEmpty())

		c := dep.Spec.Template.Spec.Containers[0]
		Expect(c.Args).To(Or(
			ContainElement("-metrics-bind-address=:8080"),
			ContainElement("--metrics-bind-address=:8080"),
		))

		var metricsPort *corev1.ContainerPort
		for i := range c.Ports {
			if c.Ports[i].Name == "metrics" {
				metricsPort = &c.Ports[i]
				break
			}
		}
		Expect(metricsPort).NotTo(BeNil(), "expected container port named metrics")
		Expect(metricsPort.ContainerPort).To(Equal(int32(8080)))
	})
})
