/*
Copyright 2026.

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

package e2e

import (
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/test/utils"
)

var observabilityServiceMonitorGVK = schema.GroupVersionKind{
	Group: "monitoring.coreos.com", Version: "v1", Kind: "ServiceMonitor",
}

var _ = Describe("Observability integration", Ordered, func() {
	const crName = "jumpstarter"

	It("creates metrics Services, a shipper ConfigMap, and ServiceMonitors when available", func() {
		namespace := CreateTestNamespace()
		DeferCleanup(func() { DeleteTestNamespace(namespace) })

		By("creating a Jumpstarter CR with the promtail shipper enabled")
		js := &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: namespace},
		}
		js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
		js.Spec.Telemetry.Logging.Shipper.Type = "promtail"
		Expect(k8sClient.Create(ctx, js)).To(Succeed())

		By("waiting for the controller metrics Service")
		Eventually(func(g Gomega) {
			svc := &corev1.Service{}
			g.Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: crName + "-controller-metrics", Namespace: namespace,
			}, svc)).To(Succeed())
			g.Expect(svc.Spec.Ports).NotTo(BeEmpty())
		}, 2*time.Minute, 2*time.Second).Should(Succeed())

		By("waiting for the shipper ConfigMap")
		Eventually(func(g Gomega) {
			cm := &corev1.ConfigMap{}
			g.Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: crName + "-log-shipper", Namespace: namespace,
			}, cm)).To(Succeed())
			g.Expect(cm.Data).To(HaveKey("promtail.yaml"))
			g.Expect(cm.Data).To(HaveKey("README.md"))
		}, 2*time.Minute, 2*time.Second).Should(Succeed())

		if utils.IsPrometheusCRDsInstalled() {
			By("waiting for the controller ServiceMonitor")
			Eventually(func(g Gomega) {
				sm := &unstructured.Unstructured{}
				sm.SetGroupVersionKind(observabilityServiceMonitorGVK)
				g.Expect(k8sClient.Get(ctx, types.NamespacedName{
					Name: crName + "-controller-metrics", Namespace: namespace,
				}, sm)).To(Succeed())
			}, 2*time.Minute, 2*time.Second).Should(Succeed())
		} else {
			By("verifying safe degradation without the Prometheus Operator CRDs")
			waitForCondition(namespace, crName,
				operatorv1alpha1.ConditionTypeServiceMonitorReady,
				metav1.ConditionFalse, 2*time.Minute)
		}
	})
})
