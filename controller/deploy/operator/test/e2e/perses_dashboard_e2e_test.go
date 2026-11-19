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
	"os"
	"os/exec"
	"path/filepath"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

var _ = Describe("Perses dashboard integration", Ordered, func() {
	const (
		persesDashboardCRDName = "persesdashboards.perses.dev"
		persesDashboardE2E     = "PERSES_K8S_E2E"
	)

	var (
		namespace       string
		installedByTest bool
	)

	BeforeAll(func() {
		if os.Getenv(persesDashboardE2E) != "1" {
			Skip("set PERSES_K8S_E2E=1 to run the Perses dashboard e2e test")
		}

		if exec.Command("kubectl", "get", "crd", persesDashboardCRDName).Run() != nil {
			crdPath := filepath.Join("..", "..", "config", "test", "perses", "persesdashboard-crd.yaml")
			out, err := exec.Command("kubectl", "apply", "-f", crdPath).CombinedOutput()
			Expect(err).NotTo(HaveOccurred(), string(out))
			installedByTest = true
		}

		Eventually(func(g Gomega) {
			g.Expect(exec.Command("kubectl", "get", "crd", persesDashboardCRDName).Run()).To(Succeed())
		}, time.Minute, time.Second).Should(Succeed())
	})

	AfterAll(func() {
		if installedByTest {
			_ = exec.Command("kubectl", "delete", "crd", persesDashboardCRDName, "--ignore-not-found").Run()
		}
	})

	It("creates the overview dashboard with the expected instance selector and config", func() {
		namespace = CreateTestNamespace()
		DeferCleanup(func() {
			DeleteTestNamespace(namespace)
		})

		js := &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: "jumpstarter", Namespace: namespace},
		}
		js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
		js.Spec.Telemetry.Metrics.Dashboards = new(true)
		js.Spec.Telemetry.Metrics.PersesInstanceSelector = map[string]string{
			"app.kubernetes.io/instance": "perses-sample",
		}
		js.Spec.Telemetry.Metrics.DashboardLabels = map[string]string{"team": "lab"}
		Expect(k8sClient.Create(ctx, js)).To(Succeed())

		waitForCondition(namespace, "jumpstarter",
			operatorv1alpha1.ConditionTypeDashboardsReady,
			metav1.ConditionTrue, 2*time.Minute)

		dashboard := &unstructured.Unstructured{}
		dashboard.SetGroupVersionKind(schema.GroupVersionKind{
			Group: "perses.dev", Version: "v1alpha2", Kind: "PersesDashboard",
		})
		key := types.NamespacedName{Name: "jumpstarter-overview", Namespace: namespace}

		Eventually(func(g Gomega) {
			g.Expect(k8sClient.Get(ctx, key, dashboard)).To(Succeed())
			g.Expect(dashboard.GetLabels()).To(HaveKeyWithValue("team", "lab"))

			selector, found, err := unstructured.NestedStringMap(
				dashboard.Object, "spec", "instanceSelector", "matchLabels")
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(found).To(BeTrue())
			g.Expect(selector).To(HaveKeyWithValue("app.kubernetes.io/instance", "perses-sample"))

			config, found, err := unstructured.NestedMap(dashboard.Object, "spec", "config")
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(found).To(BeTrue())
			g.Expect(config).To(HaveKey("panels"))
			g.Expect(config).To(HaveKey("layouts"))
			g.Expect(config).NotTo(HaveKey("datasources"))
		}, 2*time.Minute, 2*time.Second).Should(Succeed())
	})
})
