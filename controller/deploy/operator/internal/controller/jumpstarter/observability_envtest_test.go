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
	"strings"

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
		_, err := r.reconcileObservability(ctx, js)
		Expect(err).NotTo(HaveOccurred())

		for _, name := range []string{"jumpstarter-metrics-controller", "jumpstarter-metrics-router"} {
			svc := &corev1.Service{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace.Name}, svc)).To(Succeed(),
				"expected metrics service %s", name)
		}

		sm := &unstructured.Unstructured{}
		sm.SetGroupVersionKind(serviceMonitorGVK)
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "jumpstarter-metrics-controller", Namespace: namespace.Name}, sm)).To(Succeed())
		resourceVersion := sm.GetResourceVersion()

		observability, err := r.reconcileObservability(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "jumpstarter-metrics-controller", Namespace: namespace.Name}, sm)).To(Succeed())
		Expect(sm.GetResourceVersion()).To(Equal(resourceVersion), "steady-state reconcile must not write")

		ready, reason, _ := observability.serviceMonitor.condition()
		Expect(ready).To(BeTrue())
		Expect(reason).To(Equal("Created"))
	})

	It("creates starter PrometheusRules and prunes them when disabled", func() {
		js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
		js.Spec.Telemetry.Metrics.PrometheusRules = new(true)
		Expect(k8sClient.Update(ctx, js)).To(Succeed())

		observability, err := r.reconcileObservability(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		ready, reason, message := observability.prometheusRules.condition()
		Expect(ready).To(BeTrue())
		Expect(reason).To(Equal("Created"))
		Expect(message).To(ContainSubstring("configured"))

		rule := &unstructured.Unstructured{}
		rule.SetGroupVersionKind(prometheusRuleGVK)
		ruleKey := types.NamespacedName{Name: "jumpstarter-starter-alerts", Namespace: namespace.Name}
		Expect(k8sClient.Get(ctx, ruleKey, rule)).To(Succeed())
		spec, ok := rule.Object["spec"].(map[string]any)
		Expect(ok).To(BeTrue())
		groups, ok := spec["groups"].([]any)
		Expect(ok).To(BeTrue())
		Expect(groups).To(HaveLen(2))

		resourceVersion := rule.GetResourceVersion()
		_, err = r.reconcileObservability(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(k8sClient.Get(ctx, ruleKey, rule)).To(Succeed())
		Expect(rule.GetResourceVersion()).To(Equal(resourceVersion), "steady-state reconcile must not write")

		foreign := &unstructured.Unstructured{}
		foreign.SetGroupVersionKind(prometheusRuleGVK)
		foreign.SetName("foreign-rules")
		foreign.SetNamespace(namespace.Name)
		Expect(k8sClient.Create(ctx, foreign)).To(Succeed())

		js.Spec.Telemetry.Metrics.PrometheusRules = new(false)
		Expect(k8sClient.Update(ctx, js)).To(Succeed())
		observability, err = r.reconcileObservability(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		ready, reason, _ = observability.prometheusRules.condition()
		Expect(ready).To(BeFalse())
		Expect(reason).To(Equal("Disabled"))

		err = k8sClient.Get(ctx, ruleKey, rule)
		Expect(apierrors.IsNotFound(err)).To(BeTrue(), "owned rule should be pruned: %v", err)
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: "foreign-rules", Namespace: namespace.Name}, foreign)).To(Succeed())
	})

	It("prunes stale ServiceMonitors without deleting foreign monitors", func() {
		js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{Enabled: true}
		Expect(k8sClient.Update(ctx, js)).To(Succeed())
		_, err := r.reconcileObservability(ctx, js)
		Expect(err).NotTo(HaveOccurred())

		for _, name := range []string{
			"jumpstarter-metrics-controller",
			"jumpstarter-metrics-router",
			"jumpstarter-metrics-telemetry",
		} {
			sm := &unstructured.Unstructured{}
			sm.SetGroupVersionKind(serviceMonitorGVK)
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace.Name}, sm)).To(Succeed(),
				"expected ServiceMonitor %s", name)
		}

		// This monitor has matching labels but no owner reference. Pruning by
		// label would delete it; pruning by controller ownership must not.
		foreign := &unstructured.Unstructured{}
		foreign.SetGroupVersionKind(serviceMonitorGVK)
		foreign.SetName("foreign-monitor")
		foreign.SetNamespace(namespace.Name)
		foreign.SetLabels(map[string]string{
			metricsServiceLabel:          "true",
			"app.kubernetes.io/name":     "jumpstarter",
			"app.kubernetes.io/instance": js.Name,
		})
		Expect(k8sClient.Create(ctx, foreign)).To(Succeed())

		js.Spec.Telemetry.Enabled = false
		Expect(k8sClient.Update(ctx, js)).To(Succeed())
		observability, err := r.reconcileObservability(ctx, js)
		Expect(err).NotTo(HaveOccurred())

		telemetry := &unstructured.Unstructured{}
		telemetry.SetGroupVersionKind(serviceMonitorGVK)
		err = k8sClient.Get(ctx, types.NamespacedName{
			Name: "jumpstarter-metrics-telemetry", Namespace: namespace.Name,
		}, telemetry)
		Expect(apierrors.IsNotFound(err)).To(BeTrue(), "telemetry ServiceMonitor should be pruned: %v", err)

		for _, name := range []string{
			"jumpstarter-metrics-controller",
			"jumpstarter-metrics-router",
			"foreign-monitor",
		} {
			sm := &unstructured.Unstructured{}
			sm.SetGroupVersionKind(serviceMonitorGVK)
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace.Name}, sm)).To(Succeed(),
				"expected ServiceMonitor %s to remain", name)
		}

		ready, reason, message := observability.serviceMonitor.condition()
		Expect(ready).To(BeTrue())
		Expect(reason).To(Equal("Created"))
		Expect(message).To(ContainSubstring("2 workloads"))

		js.Spec.Telemetry.Metrics.ServiceMonitor = new(false)
		Expect(k8sClient.Update(ctx, js)).To(Succeed())
		observability, err = r.reconcileObservability(ctx, js)
		Expect(err).NotTo(HaveOccurred())

		for _, name := range []string{
			"jumpstarter-metrics-controller",
			"jumpstarter-metrics-router",
			"jumpstarter-metrics-telemetry",
		} {
			sm := &unstructured.Unstructured{}
			sm.SetGroupVersionKind(serviceMonitorGVK)
			err := k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: namespace.Name}, sm)
			Expect(apierrors.IsNotFound(err)).To(BeTrue(), "ServiceMonitor %s should be pruned: %v", name, err)
		}

		sm := &unstructured.Unstructured{}
		sm.SetGroupVersionKind(serviceMonitorGVK)
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: "foreign-monitor", Namespace: namespace.Name,
		}, sm)).To(Succeed(), "foreign ServiceMonitor should remain")

		ready, reason, _ = observability.serviceMonitor.condition()
		Expect(ready).To(BeFalse())
		Expect(reason).To(Equal("Disabled"))
	})

	It("keeps the router metrics Service outside the router service prefix", func() {
		_, err := r.reconcileObservability(ctx, js)
		Expect(err).NotTo(HaveOccurred())

		// cleanupExcessRouterServices deletes the services this CR controls whose
		// names start with "<name>-router-". The metrics Service is named
		// "<name>-metrics-router", deliberately outside that prefix, so name-based
		// pruning cannot reach it. The prefix assertion makes the guard
		// intentional: a rename back inside the prefix fails here as well as in
		// the survival check below.
		metricsService := "jumpstarter-metrics-router"
		Expect(strings.HasPrefix(metricsService, routerServiceNamePrefix(js))).To(BeFalse(),
			"the metrics Service must stay outside the router prefix")
		Expect(r.cleanupExcessRouterServices(ctx, js)).To(Succeed())

		svc := &corev1.Service{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: metricsService, Namespace: namespace.Name,
		}, svc)).To(Succeed())
	})

})
