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
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/tools/record"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

var serviceMonitorGVK = schema.GroupVersionKind{
	Group: serviceMonitorGroup, Version: serviceMonitorVersion, Kind: serviceMonitorKind,
}

var _ = Describe("Observability reconciliation", func() {
	var (
		namespace *corev1.Namespace
		js        *operatorv1alpha1.Jumpstarter
		r         *JumpstarterReconciler
	)

	BeforeEach(func() {
		namespace = &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "obs-"}}
		Expect(k8sClient.Create(ctx, namespace)).To(Succeed())

		js = &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: "jumpstarter", Namespace: namespace.Name},
		}
		Expect(k8sClient.Create(ctx, js)).To(Succeed())

		r = &JumpstarterReconciler{
			Client:   k8sClient,
			Scheme:   scheme.Scheme,
			Recorder: record.NewFakeRecorder(32),
		}
	})

	AfterEach(func() {
		Expect(k8sClient.Delete(ctx, namespace)).To(Succeed())
	})

	It("creates metrics Services and ServiceMonitors, and is idempotent", func() {
		Expect(r.reconcileObservability(ctx, js)).To(Succeed())

		for _, name := range []string{"jumpstarter-controller-metrics", "jumpstarter-router-metrics"} {
			svc := &corev1.Service{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace.Name}, svc)).To(Succeed(),
				"expected metrics service %s", name)
		}

		sm := &unstructured.Unstructured{}
		sm.SetGroupVersionKind(serviceMonitorGVK)
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "jumpstarter-controller-metrics", Namespace: namespace.Name}, sm)).To(Succeed())
		resourceVersion := sm.GetResourceVersion()

		Expect(r.reconcileObservability(ctx, js)).To(Succeed())
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "jumpstarter-controller-metrics", Namespace: namespace.Name}, sm)).To(Succeed())
		Expect(sm.GetResourceVersion()).To(Equal(resourceVersion), "steady-state reconcile must not write")

		ready, reason, _ := r.serviceMonitorCondition(ctx, js)
		Expect(ready).To(BeTrue())
		Expect(reason).To(Equal("Created"))
	})

	It("keeps the router metrics Service during router service cleanup", func() {
		Expect(r.reconcileObservability(ctx, js)).To(Succeed())

		// The endpoint reconciler prunes services matching the "<name>-router-"
		// prefix; the metrics Service uses that prefix and must survive.
		Expect(r.cleanupExcessRouterServices(ctx, js)).To(Succeed())

		svc := &corev1.Service{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: "jumpstarter-router-metrics", Namespace: namespace.Name,
		}, svc)).To(Succeed())
	})

	It("manages the shipper ConfigMap lifecycle", func() {
		js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
		js.Spec.Telemetry.Logging.Shipper.Type = shipperTypePromtail
		Expect(k8sClient.Update(ctx, js)).To(Succeed())
		Expect(r.reconcileObservability(ctx, js)).To(Succeed())

		cm := &corev1.ConfigMap{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "jumpstarter-log-shipper", Namespace: namespace.Name}, cm)).To(Succeed())
		Expect(cm.Data).To(HaveKey("promtail.yaml"))
		Expect(cm.Data).To(HaveKey("README.md"))

		js.Spec.Telemetry.Logging.Shipper.Type = shipperTypeVector
		Expect(k8sClient.Update(ctx, js)).To(Succeed())
		Expect(r.reconcileObservability(ctx, js)).To(Succeed())
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "jumpstarter-log-shipper", Namespace: namespace.Name}, cm)).To(Succeed())
		Expect(cm.Data).To(HaveKey("vector.yaml"))
		Expect(cm.Data).NotTo(HaveKey("promtail.yaml"))

		js.Spec.Telemetry.Logging.Shipper.Type = shipperTypeNone
		Expect(k8sClient.Update(ctx, js)).To(Succeed())
		Expect(r.reconcileObservability(ctx, js)).To(Succeed())
		err := k8sClient.Get(ctx, types.NamespacedName{Name: "jumpstarter-log-shipper", Namespace: namespace.Name}, cm)
		Expect(apierrors.IsNotFound(err)).To(BeTrue())
	})
})
