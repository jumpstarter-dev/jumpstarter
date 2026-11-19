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

package jumpstarter

import (
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/client-go/kubernetes/scheme"
	"k8s.io/client-go/tools/record"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

var _ = Describe("Perses dashboard reconciliation", func() {
	var (
		namespace *corev1.Namespace
		js        *operatorv1alpha1.Jumpstarter
		r         *JumpstarterReconciler
	)

	BeforeEach(func() {
		namespace = &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "dash-"}}
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

	It("creates, updates, and prunes the overview dashboard", func() {
		js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
		js.Spec.Telemetry.Metrics.Dashboards = new(true)
		js.Spec.Telemetry.Metrics.PersesInstanceSelector = map[string]string{
			"app.kubernetes.io/instance": "perses-sample",
		}
		js.Spec.Telemetry.Metrics.DashboardLabels = map[string]string{"team": "lab"}
		Expect(k8sClient.Update(ctx, js)).To(Succeed())

		observability, err := r.reconcileObservability(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		ready, reason, _ := observability.dashboards.condition()
		Expect(ready).To(BeTrue())
		Expect(reason).To(Equal("Created"))

		key := types.NamespacedName{Name: "jumpstarter-overview", Namespace: namespace.Name}
		dashboard := &unstructured.Unstructured{}
		dashboard.SetGroupVersionKind(persesDashboardGVK)
		Expect(k8sClient.Get(ctx, key, dashboard)).To(Succeed())
		Expect(dashboard.GetLabels()).To(HaveKeyWithValue("team", "lab"))

		spec, ok := dashboard.Object["spec"].(map[string]any)
		Expect(ok).To(BeTrue())
		config, ok := spec["config"].(map[string]any)
		Expect(ok).To(BeTrue())
		Expect(config).To(HaveKey("panels"))
		Expect(config).To(HaveKey("layouts"))
		Expect(config).NotTo(HaveKey("datasources"))

		resourceVersion := dashboard.GetResourceVersion()
		_, err = r.reconcileObservability(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(k8sClient.Get(ctx, key, dashboard)).To(Succeed())
		Expect(dashboard.GetResourceVersion()).To(Equal(resourceVersion), "steady-state reconcile must not write")

		foreign := desiredPersesDashboard(js)
		foreign.SetName("foreign-dashboard")
		foreign.SetNamespace(namespace.Name)
		foreign.SetOwnerReferences(nil)
		Expect(k8sClient.Create(ctx, foreign)).To(Succeed())

		js.Spec.Telemetry.Metrics.Dashboards = new(false)
		Expect(k8sClient.Update(ctx, js)).To(Succeed())
		observability, err = r.reconcileObservability(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		ready, reason, _ = observability.dashboards.condition()
		Expect(ready).To(BeFalse())
		Expect(reason).To(Equal("Disabled"))

		err = k8sClient.Get(ctx, key, dashboard)
		Expect(apierrors.IsNotFound(err)).To(BeTrue(), "owned dashboard should be pruned: %v", err)
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: "foreign-dashboard", Namespace: namespace.Name,
		}, foreign)).To(Succeed())
	})
})
