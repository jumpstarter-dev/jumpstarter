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
	"bytes"
	"crypto/sha256"
	"encoding/hex"
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
	// Checksums pin the v0.94.2 release assets so the test never applies an
	// unverified manifest from the network.
	prometheusOperatorSHA256 = "045754c91469cccb27d7832168a5b5f0448188bcb3513f74a13c76c9079d654b"
	prometheusCRDsSHA256     = "e02e48c760e86aa1b5888205d1670c7baa42abe86211f8408b7fb18651203ede"
	prometheusServiceAccount = "prometheus-jumpstarter-e2e"
	prometheusCRName         = "jumpstarter-e2e"

	// Discovery includes Prometheus Operator config reload and target
	// discovery, which Jumpstarter does not control. Scrape freshness is
	// measured separately once a target has been discovered.
	prometheusScrapeInterval         = 5 * time.Second
	prometheusTargetDiscoveryTimeout = 120 * time.Second
	prometheusScrapeFreshness        = 2 * prometheusScrapeInterval
)

var prometheusGVK = schema.GroupVersionKind{
	Group: "monitoring.coreos.com", Version: "v1", Kind: "Prometheus",
}

var prometheusRuleGVK = schema.GroupVersionKind{
	Group: "monitoring.coreos.com", Version: "v1", Kind: "PrometheusRule",
}

type prometheusTargetsResponse struct {
	Status string `json:"status"`
	Data   struct {
		ActiveTargets []prometheusTarget `json:"activeTargets"`
	} `json:"data"`
}

type prometheusRulesResponse struct {
	Status string `json:"status"`
	Data   struct {
		Groups []struct {
			Name  string `json:"name"`
			Rules []struct {
				Name   string `json:"name"`
				Health string `json:"health"`
			} `json:"rules"`
		} `json:"groups"`
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
const jumpstarterPrometheusRulesLabel = "jumpstarter.dev/prometheus-rules"

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
		js.Spec.Telemetry.Metrics.PrometheusRules = new(true)
		Expect(k8sClient.Create(ctx, js)).To(Succeed())

		By("waiting for controller and router metrics Services")
		for _, name := range []string{"jumpstarter-metrics-controller", "jumpstarter-metrics-router"} {
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

		By("asserting Prometheus selected healthy controller and router targets without extra scrape config")
		// Both pools must be inside the retry: Prometheus discovers the two
		// ServiceMonitors independently, so a target set with only the controller
		// (or only the router) is a wait state, not a result.
		Eventually(func(g Gomega) {
			result, err := getPrometheusTargets()
			g.Expect(err).NotTo(HaveOccurred())
			expectHealthyJumpstarterTargets(g, activeJumpstarterTargets(result, testNamespace))
		}, prometheusTargetDiscoveryTimeout, 2*time.Second).Should(Succeed())

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

		By("waiting for PrometheusRulesReady=Created")
		waitForCondition(testNamespace, "jumpstarter",
			operatorv1alpha1.ConditionTypePrometheusRulesReady,
			metav1.ConditionTrue, 2*time.Minute)

		By("asserting the starter PrometheusRule exists and is selected by Prometheus")
		rule := &unstructured.Unstructured{}
		rule.SetGroupVersionKind(prometheusRuleGVK)
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: "jumpstarter-starter-alerts", Namespace: testNamespace,
		}, rule)).To(Succeed())

		Eventually(func(g Gomega) {
			result, err := getPrometheusRules()
			g.Expect(err).NotTo(HaveOccurred())
			names := map[string]bool{}
			for _, group := range result.Data.Groups {
				for _, rule := range group.Rules {
					names[rule.Name] = true
				}
			}
			g.Expect(names).To(HaveKey("JumpstarterOperationFailureRate"))
			g.Expect(names).To(HaveKey("JumpstarterExporterIdle"))
			g.Expect(names).To(HaveKey("JumpstarterTelemetryDropped"))
		}, prometheusTargetDiscoveryTimeout, 2*time.Second).Should(Succeed())
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
	if exec.Command("kubectl", "get", "crd", "prometheuses.monitoring.coreos.com").Run() != nil ||
		exec.Command("kubectl", "get", "crd", "servicemonitors.monitoring.coreos.com").Run() != nil {
		// Apply the full CRD asset when any required CRD is missing: a partial
		// earlier apply must be repaired, and the operator asset below filters
		// CRDs out.
		kubectlApplyStdin(fetchManifest(crdsURL, prometheusCRDsURL, prometheusCRDsSHA256))
	}

	raw := fetchManifest(url, prometheusOperatorURL, prometheusOperatorSHA256)
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

// fetchManifest downloads a manifest and verifies its checksum when the
// built-in URL is used. An environment-supplied URL is a local override and is
// not pinned.
func fetchManifest(url, defaultURL, wantSum string) []byte {
	raw, err := exec.Command("curl", "-fsSL", url).Output() //nolint:gosec // test-controlled URL
	ExpectWithOffset(1, err).NotTo(HaveOccurred())
	if url == defaultURL {
		sum := sha256.Sum256(raw)
		ExpectWithOffset(1, hex.EncodeToString(sum[:])).To(Equal(wantSum),
			"sha256 mismatch for %s", url)
	}
	return raw
}

// kubectlApplyStdin applies an in-memory manifest so kubectl does not re-fetch
// a download that was already verified.
func kubectlApplyStdin(raw []byte) {
	cmd := exec.Command("kubectl", "apply", "--server-side", "--force-conflicts", "-f", "-")
	cmd.Stdin = bytes.NewReader(raw)
	out, err := cmd.CombinedOutput()
	ExpectWithOffset(1, err).NotTo(HaveOccurred(), string(out))
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
	prom := &unstructured.Unstructured{Object: map[string]any{
		"apiVersion": "monitoring.coreos.com/v1",
		"kind":       "Prometheus",
		"metadata": map[string]any{
			"name":      prometheusCRName,
			"namespace": prometheusNamespace,
		},
		"spec": map[string]any{
			"serviceAccountName": prometheusServiceAccount,
			"replicas":           int64(1),
			"scrapeInterval":     prometheusScrapeInterval.String(),
			"evaluationInterval": prometheusScrapeInterval.String(),
			"retention":          "1h",
			"serviceMonitorSelector": map[string]any{
				"matchLabels": map[string]any{jumpstarterMetricsLabel: "true"},
			},
			"serviceMonitorNamespaceSelector": map[string]any{
				"matchLabels": map[string]any{
					"kubernetes.io/metadata.name": testNamespace,
				},
			},
			"ruleSelector": map[string]any{
				"matchLabels": map[string]any{jumpstarterPrometheusRulesLabel: "true"},
			},
			"ruleNamespaceSelector": map[string]any{
				"matchLabels": map[string]any{
					"kubernetes.io/metadata.name": testNamespace,
				},
			},
			"podMonitorSelector": map[string]any{},
			"probeSelector":      map[string]any{},
			"resources": map[string]any{
				"requests": map[string]any{"cpu": "50m", "memory": "128Mi"},
				"limits":   map[string]any{"memory": "512Mi"},
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
	// Set the name and namespace: a Delete on an object with only a GVK fails
	// with "resource name may not be empty" and would leave the fixture behind.
	prom := &unstructured.Unstructured{}
	prom.SetGroupVersionKind(prometheusGVK)
	prom.SetName(prometheusCRName)
	prom.SetNamespace(prometheusNamespace)
	_ = k8sClient.Delete(ctx, prom)
	// Wait for the CR to go so the operator removes the StatefulSet before the
	// namespace starts terminating.
	Eventually(func(g Gomega) {
		err := k8sClient.Get(ctx, types.NamespacedName{
			Name: prometheusCRName, Namespace: prometheusNamespace,
		}, prom)
		g.Expect(apierrors.IsNotFound(err)).To(BeTrue(), "Prometheus fixture should be deleted: %v", err)
	}, time.Minute, time.Second).Should(Succeed())

	_ = k8sClient.Delete(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: prometheusNamespace}})
	// A terminating namespace makes the next run's ServiceAccount creation fail,
	// so do not return until it is gone.
	Eventually(func(g Gomega) {
		err := k8sClient.Get(ctx, types.NamespacedName{Name: prometheusNamespace}, &corev1.Namespace{})
		g.Expect(apierrors.IsNotFound(err)).To(BeTrue(), "Prometheus fixture namespace should be deleted: %v", err)
	}, 2*time.Minute, 2*time.Second).Should(Succeed())

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

// expectHealthyJumpstarterTargets asserts that every discovered Jumpstarter
// target is scraping successfully, and that both the controller and router
// scrape pools are represented. Run it inside Eventually: a single discovered
// pool is a wait state, so it must not end the retry.
func expectHealthyJumpstarterTargets(g Gomega, targets []prometheusTarget) {
	g.Expect(targets).NotTo(BeEmpty())

	seen := map[string]bool{}
	for _, target := range targets {
		g.Expect(target.Health).To(Equal("up"))
		g.Expect(target.LastError).To(BeEmpty())
		g.Expect(target.LastScrape).NotTo(BeZero())
		g.Expect(time.Since(target.LastScrape)).To(BeNumerically("<=", prometheusScrapeFreshness))
		g.Expect(target.ScrapeURL).To(ContainSubstring("/metrics"))
		if strings.Contains(target.ScrapePool, "metrics-controller") {
			seen["controller"] = true
		}
		if strings.Contains(target.ScrapePool, "metrics-router") {
			seen["router"] = true
		}
	}
	g.Expect(seen).To(HaveKey("controller"))
	g.Expect(seen).To(HaveKey("router"))
}

func getPrometheusRules() (prometheusRulesResponse, error) {
	raw, err := clientset.CoreV1().Services(prometheusNamespace).ProxyGet(
		"http", "prometheus-operated", "9090", "/api/v1/rules", nil,
	).DoRaw(ctx)
	if err != nil {
		return prometheusRulesResponse{}, err
	}
	var result prometheusRulesResponse
	if err := json.Unmarshal(raw, &result); err != nil {
		return result, err
	}
	if result.Status != "success" {
		return result, fmt.Errorf("Prometheus rules status %q", result.Status)
	}
	return result, nil
}

func activeJumpstarterTargets(result prometheusTargetsResponse, namespace string) []prometheusTarget {
	var targets []prometheusTarget
	for _, target := range result.Data.ActiveTargets {
		if target.Labels["namespace"] != namespace {
			continue
		}
		if strings.Contains(target.ScrapePool, "metrics-controller") ||
			strings.Contains(target.ScrapePool, "metrics-router") {
			targets = append(targets, target)
		}
	}
	return targets
}
