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
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"strings"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"k8s.io/apimachinery/pkg/types"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

const (
	prometheusTargetsE2E  = "PROMETHEUS_K8S_E2E"
	prometheusNamespace   = "prometheus-jumpstarter-e2e"
	prometheusOperatorURL = "https://github.com/prometheus-operator/prometheus-operator/" +
		"releases/download/v0.94.2/bundle.yaml"
	prometheusCRDsURL = "https://github.com/prometheus-operator/prometheus-operator/" +
		"releases/download/v0.94.2/stripped-down-crds.yaml"
	prometheusServiceAccount = "prometheus-jumpstarter-e2e"
	prometheusCRName         = "jumpstarter-e2e"
)

var prometheusGVK = schema.GroupVersionKind{
	Group: "monitoring.coreos.com", Version: "v1", Kind: "Prometheus",
}

type prometheusTargetsResponse struct {
	Status string `json:"status"`
	Data   struct {
		ActiveTargets []prometheusTarget `json:"activeTargets"`
	} `json:"data"`
}

type prometheusTarget struct {
	Labels     map[string]string `json:"labels"`
	ScrapePool string            `json:"scrapePool"`
	ScrapeURL  string            `json:"scrapeUrl"`
	Health     string            `json:"health"`
	LastError  string            `json:"lastError"`
	LastScrape time.Time         `json:"lastScrape"`
}

// Ensure the selector contract used by the test is the same default emitted by
// the operator. Keep this label in sync with desiredServiceMonitors.
const jumpstarterMetricsLabel = "jumpstarter.dev/metrics"

var _ = Describe("Prometheus target health", Ordered, func() {
	var (
		testNamespace string
		promAvailable bool
	)

	BeforeAll(func() {
		if os.Getenv(prometheusTargetsE2E) != "1" {
			Skip("set PROMETHEUS_K8S_E2E=1 to run the Prometheus target-health e2e test")
		}

		ensurePrometheusOperator()
		createPrometheusRBAC()
		testNamespace = CreateTestNamespace()
		createPrometheusInstance(testNamespace)
		waitForPrometheusReady()
		promAvailable = true
	})

	AfterAll(func() {
		if !promAvailable {
			return
		}
		if testNamespace != "" {
			DeleteTestNamespace(testNamespace)
		}
		cleanupPrometheusInstance()
	})

	It("discovers healthy controller and router targets without manual scrape config", func() {
		By("creating a default Jumpstarter CR")
		js := &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: "jumpstarter", Namespace: testNamespace},
		}
		js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
		Expect(k8sClient.Create(ctx, js)).To(Succeed())

		By("waiting for controller and router metrics Services")
		for _, name := range []string{"jumpstarter-controller-metrics", "jumpstarter-router-metrics"} {
			Eventually(func(g Gomega) {
				svc := &corev1.Service{}
				g.Expect(k8sClient.Get(ctx, types.NamespacedName{Name: name, Namespace: testNamespace}, svc)).To(Succeed())
				g.Expect(svc.Spec.Ports).NotTo(BeEmpty())
			}, 2*time.Minute, 2*time.Second).Should(Succeed())
		}

		By("waiting for ServiceMonitorReady=Created")
		waitForCondition(testNamespace, "jumpstarter",
			operatorv1alpha1.ConditionTypeServiceMonitorReady,
			metav1.ConditionTrue, 2*time.Minute)

		By("asserting Prometheus selected both ServiceMonitors without additional scrape config")
		Eventually(func(g Gomega) {
			result, err := getPrometheusTargets()
			g.Expect(err).NotTo(HaveOccurred())
			targets := activeJumpstarterTargets(result, testNamespace)
			g.Expect(targets).NotTo(BeEmpty())
			for _, target := range targets {
				g.Expect(target.Health).To(Equal("up"))
				g.Expect(target.LastError).To(BeEmpty())
				g.Expect(target.LastScrape).NotTo(BeZero())
				g.Expect(time.Since(target.LastScrape)).To(BeNumerically("<", 30*time.Second))
			}
		}, 90*time.Second, 2*time.Second).Should(Succeed())

		targetResult, err := getPrometheusTargets()
		Expect(err).NotTo(HaveOccurred())
		targets := activeJumpstarterTargets(targetResult, testNamespace)
		Expect(targets).NotTo(BeEmpty())

		seen := map[string]bool{}
		for _, target := range targets {
			Expect(target.Health).To(Equal("up"))
			Expect(target.LastError).To(BeEmpty())
			Expect(target.LastScrape).NotTo(BeZero())
			Expect(time.Since(target.LastScrape)).To(BeNumerically("<", 30*time.Second))
			Expect(target.ScrapeURL).To(ContainSubstring("/metrics"))
			if strings.Contains(target.ScrapePool, "controller-metrics") {
				seen["controller"] = true
			}
			if strings.Contains(target.ScrapePool, "router-metrics") {
				seen["router"] = true
			}
		}
		Expect(seen).To(HaveKey("controller"))
		Expect(seen).To(HaveKey("router"))

		By("asserting the Prometheus instance has no manual Jumpstarter scrape config")
		prom := &unstructured.Unstructured{}
		prom.SetGroupVersionKind(prometheusGVK)
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: prometheusCRName, Namespace: prometheusNamespace,
		}, prom)).To(Succeed())
		spec, found, err := unstructured.NestedMap(prom.Object, "spec")
		Expect(err).NotTo(HaveOccurred())
		Expect(found).To(BeTrue())
		Expect(spec).NotTo(HaveKey("additionalScrapeConfigs"))
	})
})

func ensurePrometheusOperator() {
	By("ensuring the Prometheus Operator is installed")
	if exec.Command("kubectl", "get", "deployment", "prometheus-operator", "-n", "default").Run() == nil {
		return
	}
	url := os.Getenv("PROMETHEUS_OPERATOR_MANIFEST")
	if url == "" {
		url = prometheusOperatorURL
	}
	crdsURL := os.Getenv("PROMETHEUS_CRDS_MANIFEST")
	if crdsURL == "" {
		crdsURL = prometheusCRDsURL
	}
	if exec.Command("kubectl", "get", "crd", "prometheuses.monitoring.coreos.com").Run() != nil {
		cmd := exec.Command( //nolint:gosec // test-controlled URL
			"kubectl", "apply", "--server-side", "--force-conflicts", "-f", crdsURL,
		)
		out, err := cmd.CombinedOutput()
		Expect(err).NotTo(HaveOccurred(), string(out))
	}

	raw, err := exec.Command("curl", "-fsSL", url).Output() //nolint:gosec // test-controlled URL
	Expect(err).NotTo(HaveOccurred())
	docs := strings.Split(string(raw), "\n---\n")
	var operatorDocs []string
	for _, doc := range docs {
		if strings.Contains(doc, "\nkind: CustomResourceDefinition") {
			continue
		}
		if strings.TrimSpace(doc) != "" {
			operatorDocs = append(operatorDocs, doc)
		}
	}
	manifest, err := os.CreateTemp("", "prometheus-operator-*.yaml")
	Expect(err).NotTo(HaveOccurred())
	defer func() { _ = os.Remove(manifest.Name()) }()
	_, err = manifest.WriteString(strings.Join(operatorDocs, "\n---\n"))
	Expect(err).NotTo(HaveOccurred())
	Expect(manifest.Close()).To(Succeed())

	cmd := exec.Command( //nolint:gosec // test-controlled temp path
		"kubectl", "apply", "--server-side", "--force-conflicts", "-f", manifest.Name(),
	)
	out, err := cmd.CombinedOutput()
	Expect(err).NotTo(HaveOccurred(), string(out))
	Eventually(func(g Gomega) {
		var deployment = &unstructured.Unstructured{}
		deployment.SetAPIVersion("apps/v1")
		deployment.SetKind("Deployment")
		g.Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: "prometheus-operator", Namespace: "default",
		}, deployment)).To(Succeed())
	}, 2*time.Minute, 2*time.Second).Should(Succeed())
}

func createPrometheusRBAC() {
	By("creating Prometheus RBAC")
	ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: prometheusNamespace}}
	if err := k8sClient.Create(ctx, ns); err != nil {
		Expect(apierrors.IsAlreadyExists(err)).To(BeTrue())
	}

	sa := &corev1.ServiceAccount{ObjectMeta: metav1.ObjectMeta{
		Name: prometheusServiceAccount, Namespace: prometheusNamespace,
	}}
	_ = k8sClient.Delete(ctx, sa)
	Expect(k8sClient.Create(ctx, sa)).To(Succeed())

	role := &rbacv1.ClusterRole{
		ObjectMeta: metav1.ObjectMeta{Name: prometheusServiceAccount},
		Rules: []rbacv1.PolicyRule{{
			APIGroups: []string{""},
			Resources: []string{"services", "endpoints", "pods", "namespaces", "configmaps"},
			Verbs:     []string{"get", "list", "watch"},
		}},
	}
	_ = k8sClient.Delete(ctx, role)
	Expect(k8sClient.Create(ctx, role)).To(Succeed())

	binding := &rbacv1.ClusterRoleBinding{
		ObjectMeta: metav1.ObjectMeta{Name: prometheusServiceAccount},
		RoleRef:    rbacv1.RoleRef{APIGroup: rbacv1.GroupName, Kind: "ClusterRole", Name: prometheusServiceAccount},
		Subjects: []rbacv1.Subject{{
			Kind:      rbacv1.ServiceAccountKind,
			Name:      prometheusServiceAccount,
			Namespace: prometheusNamespace,
		}},
	}
	_ = k8sClient.Delete(ctx, binding)
	Expect(k8sClient.Create(ctx, binding)).To(Succeed())
}

func waitForPrometheusReady() {
	By("waiting for the Prometheus Operator and Prometheus API")
	Eventually(func(g Gomega) {
		pods := &corev1.PodList{}
		g.Expect(k8sClient.List(ctx, pods)).To(Succeed())
		var operatorReady, prometheusReady bool
		for _, pod := range pods.Items {
			if pod.Namespace == "default" &&
				strings.Contains(pod.Name, "prometheus-operator") &&
				pod.Status.Phase == corev1.PodRunning {
				operatorReady = true
			}
			if pod.Namespace == prometheusNamespace &&
				strings.Contains(pod.Name, "prometheus-"+prometheusCRName) &&
				pod.Status.Phase == corev1.PodRunning {
				prometheusReady = true
			}
		}
		g.Expect(operatorReady).To(BeTrue())
		g.Expect(prometheusReady).To(BeTrue())
	}, 2*time.Minute, 2*time.Second).Should(Succeed())
}

func createPrometheusInstance(testNamespace string) {
	By("creating a minimal Prometheus instance")
	prom := &unstructured.Unstructured{Object: map[string]interface{}{
		"apiVersion": "monitoring.coreos.com/v1",
		"kind":       "Prometheus",
		"metadata": map[string]interface{}{
			"name":      prometheusCRName,
			"namespace": prometheusNamespace,
		},
		"spec": map[string]interface{}{
			"serviceAccountName": prometheusServiceAccount,
			"replicas":           int64(1),
			"scrapeInterval":     "5s",
			"evaluationInterval": "5s",
			"retention":          "1h",
			"serviceMonitorSelector": map[string]interface{}{
				"matchLabels": map[string]interface{}{jumpstarterMetricsLabel: "true"},
			},
			"serviceMonitorNamespaceSelector": map[string]interface{}{
				"matchLabels": map[string]interface{}{
					"kubernetes.io/metadata.name": testNamespace,
				},
			},
			"podMonitorSelector": map[string]interface{}{},
			"probeSelector":      map[string]interface{}{},
			"resources": map[string]interface{}{
				"requests": map[string]interface{}{"cpu": "50m", "memory": "128Mi"},
				"limits":   map[string]interface{}{"memory": "512Mi"},
			},
		},
	}}
	Expect(k8sClient.Create(ctx, prom)).To(Succeed())
	Eventually(func(g Gomega) {
		svc := &corev1.Service{}
		g.Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: "prometheus-operated", Namespace: prometheusNamespace,
		}, svc)).To(Succeed())
	}, 2*time.Minute, 2*time.Second).Should(Succeed())
}

func cleanupPrometheusInstance() {
	By("cleaning up the Prometheus fixture")
	prom := &unstructured.Unstructured{}
	prom.SetGroupVersionKind(prometheusGVK)
	_ = k8sClient.Delete(ctx, prom)
	_ = k8sClient.Delete(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: prometheusNamespace}})
	_ = k8sClient.Delete(ctx, &rbacv1.ClusterRoleBinding{ObjectMeta: metav1.ObjectMeta{Name: prometheusServiceAccount}})
	_ = k8sClient.Delete(ctx, &rbacv1.ClusterRole{ObjectMeta: metav1.ObjectMeta{Name: prometheusServiceAccount}})
}

func getPrometheusTargets() (prometheusTargetsResponse, error) {
	raw, err := clientset.CoreV1().Services(prometheusNamespace).ProxyGet(
		"http", "prometheus-operated", "9090", "/api/v1/targets", map[string]string{"state": "active"},
	).DoRaw(ctx)
	if err != nil {
		return prometheusTargetsResponse{}, err
	}
	var result prometheusTargetsResponse
	if err := json.Unmarshal(raw, &result); err != nil {
		return result, err
	}
	if result.Status != "success" {
		return result, fmt.Errorf("Prometheus targets status %q", result.Status)
	}
	return result, nil
}

func activeJumpstarterTargets(result prometheusTargetsResponse, namespace string) []prometheusTarget {
	var targets []prometheusTarget
	for _, target := range result.Data.ActiveTargets {
		if target.Labels["namespace"] != namespace {
			continue
		}
		if strings.Contains(target.ScrapePool, "controller-metrics") ||
			strings.Contains(target.ScrapePool, "router-metrics") {
			targets = append(targets, target)
		}
	}
	return targets
}
