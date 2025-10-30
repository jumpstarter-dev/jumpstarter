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

package e2e

import (
	"bytes"
	"fmt"
	"io"
	"os"
	"os/exec"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	appsv1 "k8s.io/api/apps/v1"
	authenticationv1 "k8s.io/api/authentication/v1"
	corev1 "k8s.io/api/core/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"
	"k8s.io/apimachinery/pkg/util/yaml"
	"sigs.k8s.io/controller-runtime/pkg/client"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
)

// namespace where the project is deployed in
const namespace = "jumpstarter-operator-system"

// serviceAccountName created for the project
const serviceAccountName = "jumpstarter-operator-controller-manager"

// metricsServiceName is the name of the metrics service of the project
const metricsServiceName = "jumpstarter-operator-controller-manager-metrics-service"

// metricsRoleBindingName is the name of the RBAC that will be created to allow get the metrics data
const metricsRoleBindingName = "jumpstarter-operator-metrics-binding"

// testNamespace is the namespace where the test will be run
const testNamespace = "jumpstarter-lab-e2e"

var _ = Describe("Manager", Ordered, func() {
	var controllerPodName string

	// After all tests have been executed, clean up by undeploying the controller, uninstalling CRDs,
	// and deleting the namespace.
	AfterAll(func() {
		By("cleaning up the curl pod for metrics")
		pod := &corev1.Pod{
			ObjectMeta: metav1.ObjectMeta{
				Name:      "curl-metrics",
				Namespace: namespace,
			},
		}
		_ = k8sClient.Delete(ctx, pod)

		By("waiting for curl pod to be deleted")
		Eventually(func(g Gomega) {
			getErr := k8sClient.Get(ctx, types.NamespacedName{
				Name:      "curl-metrics",
				Namespace: namespace,
			}, pod)
			g.Expect(apierrors.IsNotFound(getErr)).To(BeTrue())
		}, 30*time.Second).Should(Succeed())

		By("deleting the jumpstarter-lab-e2e namespace")
		ns := &corev1.Namespace{
			ObjectMeta: metav1.ObjectMeta{
				Name: testNamespace,
			},
		}
		_ = k8sClient.Delete(ctx, ns)

		By("waiting for namespace to be fully deleted")
		Eventually(func(g Gomega) {
			getErr := k8sClient.Get(ctx, types.NamespacedName{
				Name: testNamespace,
			}, ns)
			g.Expect(apierrors.IsNotFound(getErr)).To(BeTrue())
		}, 2*time.Minute).Should(Succeed())
	})

	// After each test, check for failures and collect logs, events,
	// and pod descriptions for debugging.
	AfterEach(func() {
		specReport := CurrentSpecReport()
		if specReport.Failed() {
			By("Fetching controller manager pod logs")
			req := clientset.CoreV1().Pods(namespace).GetLogs(controllerPodName, &corev1.PodLogOptions{})
			podLogs, err := req.Stream(ctx)
			if err == nil {
				defer podLogs.Close()
				buf := new(bytes.Buffer)
				_, _ = io.Copy(buf, podLogs)
				_, _ = fmt.Fprintf(GinkgoWriter, "Controller logs:\n %s", buf.String())
			} else {
				_, _ = fmt.Fprintf(GinkgoWriter, "Failed to get Controller logs: %s", err)
			}

			By("Fetching Kubernetes events")
			eventList := &corev1.EventList{}
			err = k8sClient.List(ctx, eventList, client.InNamespace(namespace))
			if err == nil {
				_, _ = fmt.Fprintf(GinkgoWriter, "Kubernetes events:\n")
				for _, event := range eventList.Items {
					_, _ = fmt.Fprintf(GinkgoWriter, "%s %s %s %s\n",
						event.LastTimestamp.Time.Format(time.RFC3339),
						event.InvolvedObject.Name,
						event.Reason,
						event.Message)
				}
			} else {
				_, _ = fmt.Fprintf(GinkgoWriter, "Failed to get Kubernetes events: %s", err)
			}

			By("Fetching curl-metrics logs")
			req = clientset.CoreV1().Pods(namespace).GetLogs("curl-metrics", &corev1.PodLogOptions{})
			metricsLogs, err := req.Stream(ctx)
			if err == nil {
				defer metricsLogs.Close()
				buf := new(bytes.Buffer)
				_, _ = io.Copy(buf, metricsLogs)
				_, _ = fmt.Fprintf(GinkgoWriter, "Metrics logs:\n %s", buf.String())
			} else {
				_, _ = fmt.Fprintf(GinkgoWriter, "Failed to get curl-metrics logs: %s", err)
			}

			By("Fetching controller manager pod description")
			pod := &corev1.Pod{}
			err = k8sClient.Get(ctx, types.NamespacedName{
				Name:      controllerPodName,
				Namespace: namespace,
			}, pod)
			if err == nil {
				fmt.Printf("Pod description:\nName: %s\nPhase: %s\nConditions: %+v\n",
					pod.Name, pod.Status.Phase, pod.Status.Conditions)
			} else {
				fmt.Println("Failed to describe controller pod")
			}
		}
	})

	SetDefaultEventuallyTimeout(2 * time.Minute)
	SetDefaultEventuallyPollingInterval(time.Second)

	Context("Manager", func() {
		It("should run successfully", func() {
			By("validating that the controller-manager pod is running as expected")
			verifyControllerUp := func(g Gomega) {
				// Get the name of the controller-manager pod
				podList := &corev1.PodList{}
				err := k8sClient.List(ctx, podList,
					client.InNamespace(namespace),
					client.MatchingLabels{"control-plane": "controller-manager"})
				g.Expect(err).NotTo(HaveOccurred(), "Failed to retrieve controller-manager pod information")

				// Filter out pods that are being deleted
				var runningPods []corev1.Pod
				for _, pod := range podList.Items {
					if pod.DeletionTimestamp.IsZero() {
						runningPods = append(runningPods, pod)
					}
				}

				g.Expect(runningPods).To(HaveLen(1), "expected 1 controller pod running")
				controllerPodName = runningPods[0].Name
				g.Expect(controllerPodName).To(ContainSubstring("controller-manager"))

				// Validate the pod's status
				g.Expect(runningPods[0].Status.Phase).To(Equal(corev1.PodRunning), "Incorrect controller-manager pod status")
			}
			Eventually(verifyControllerUp).Should(Succeed())
		})

		It("should ensure the metrics endpoint is serving metrics", func() {
			By("creating a ClusterRoleBinding for the service account to allow access to metrics")
			// Delete the ClusterRoleBinding if it exists (ignore errors)
			crb := &rbacv1.ClusterRoleBinding{
				ObjectMeta: metav1.ObjectMeta{
					Name: metricsRoleBindingName,
				},
			}
			err := k8sClient.Delete(ctx, crb)
			if err == nil {
				By("waiting for existing ClusterRoleBinding to be deleted")
				Eventually(func(g Gomega) {
					getErr := k8sClient.Get(ctx, types.NamespacedName{
						Name: metricsRoleBindingName,
					}, crb)
					g.Expect(apierrors.IsNotFound(getErr)).To(BeTrue())
				}, 30*time.Second).Should(Succeed())
			}

			// Create the ClusterRoleBinding
			crb = &rbacv1.ClusterRoleBinding{
				ObjectMeta: metav1.ObjectMeta{
					Name: metricsRoleBindingName,
				},
				RoleRef: rbacv1.RoleRef{
					APIGroup: "rbac.authorization.k8s.io",
					Kind:     "ClusterRole",
					Name:     "jumpstarter-operator-metrics-reader",
				},
				Subjects: []rbacv1.Subject{
					{
						Kind:      "ServiceAccount",
						Name:      serviceAccountName,
						Namespace: namespace,
					},
				},
			}
			err = k8sClient.Create(ctx, crb)
			Expect(err).NotTo(HaveOccurred(), "Failed to create ClusterRoleBinding")

			By("validating that the metrics service is available")
			svc := &corev1.Service{}
			err = k8sClient.Get(ctx, types.NamespacedName{
				Name:      metricsServiceName,
				Namespace: namespace,
			}, svc)
			Expect(err).NotTo(HaveOccurred(), "Metrics service should exist")

			By("getting the service account token")
			token, err := serviceAccountToken()
			Expect(err).NotTo(HaveOccurred())
			Expect(token).NotTo(BeEmpty())

			By("waiting for the metrics endpoint to be ready")
			verifyMetricsEndpointReady := func(g Gomega) {
				endpoints := &corev1.Endpoints{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      metricsServiceName,
					Namespace: namespace,
				}, endpoints)
				g.Expect(err).NotTo(HaveOccurred())

				hasPort := false
				for _, subset := range endpoints.Subsets {
					for _, port := range subset.Ports {
						if port.Port == 8443 {
							hasPort = true
							break
						}
					}
				}
				g.Expect(hasPort).To(BeTrue(), "Metrics endpoint is not ready")
			}
			Eventually(verifyMetricsEndpointReady).Should(Succeed())

			By("verifying that the controller manager is serving the metrics server")
			verifyMetricsServerStarted := func(g Gomega) {
				req := clientset.CoreV1().Pods(namespace).GetLogs(controllerPodName, &corev1.PodLogOptions{})
				podLogs, err := req.Stream(ctx)
				g.Expect(err).NotTo(HaveOccurred())
				defer podLogs.Close()
				buf := new(bytes.Buffer)
				_, _ = io.Copy(buf, podLogs)
				g.Expect(buf.String()).To(ContainSubstring("controller-runtime.metrics\tServing metrics server"),
					"Metrics server not yet started")
			}
			Eventually(verifyMetricsServerStarted).Should(Succeed())

			By("creating the curl-metrics pod to access the metrics endpoint")
			curlPod := &corev1.Pod{
				ObjectMeta: metav1.ObjectMeta{
					Name:      "curl-metrics",
					Namespace: namespace,
				},
				Spec: corev1.PodSpec{
					RestartPolicy:      corev1.RestartPolicyNever,
					ServiceAccountName: serviceAccountName,
					Containers: []corev1.Container{
						{
							Name:    "curl",
							Image:   "curlimages/curl:8.10.1",
							Command: []string{"/bin/sh", "-c"},
							Args: []string{
								fmt.Sprintf("curl -v -k -H 'Authorization: Bearer %s' https://%s.%s.svc.cluster.local:8443/metrics",
									token, metricsServiceName, namespace),
							},
							SecurityContext: &corev1.SecurityContext{
								AllowPrivilegeEscalation: func() *bool { b := false; return &b }(),
								Capabilities: &corev1.Capabilities{
									Drop: []corev1.Capability{"ALL"},
								},
								RunAsNonRoot: func() *bool { b := true; return &b }(),
								RunAsUser:    func() *int64 { i := int64(1000); return &i }(),
								SeccompProfile: &corev1.SeccompProfile{
									Type: corev1.SeccompProfileTypeRuntimeDefault,
								},
							},
						},
					},
				},
			}
			err = k8sClient.Create(ctx, curlPod)
			Expect(err).NotTo(HaveOccurred(), "Failed to create curl-metrics pod")

			By("waiting for the curl-metrics pod to complete.")
			verifyCurlUp := func(g Gomega) {
				pod := &corev1.Pod{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      "curl-metrics",
					Namespace: namespace,
				}, pod)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(pod.Status.Phase).To(Equal(corev1.PodSucceeded), "curl pod in wrong status")
			}
			Eventually(verifyCurlUp, 5*time.Minute).Should(Succeed())

			By("getting the metrics by checking curl-metrics logs")
			metricsOutput := getMetricsOutput()
			Expect(metricsOutput).To(ContainSubstring(
				"controller_runtime_reconcile_total",
			))
		})

		// +kubebuilder:scaffold:e2e-webhooks-checks

		// TODO: Customize the e2e test suite with scenarios specific to your project.
		// Consider applying sample/CR(s) and check their status and/or verifying
		// the reconciliation by using the metrics, i.e.:
		// metricsOutput := getMetricsOutput()
		// Expect(metricsOutput).To(ContainSubstring(
		//    fmt.Sprintf(`controller_runtime_reconcile_total{controller="%s",result="success"} 1`,
		//    strings.ToLower(<Kind>),
		// ))
	})

	Context("Jumpstarter operator", Ordered, func() {
		const baseDomain = "jumpstarter.127.0.0.1.nip.io"
		var dynamicTestNamespace string

		BeforeAll(func() {
			dynamicTestNamespace = CreateTestNamespace()
		})

		It("should deploy jumpstarter successfully", func() {
			By("creating a Jumpstarter custom resource")
			// Get image from environment or use default
			image := os.Getenv("IMG")
			if image == "" {
				image = "quay.io/jumpstarter-dev/jumpstarter-controller:latest"
			}

			jumpstarterYAML := fmt.Sprintf(`apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: jumpstarter
  namespace: %s
spec:
  baseDomain: %s
  useCertManager: false
  controller:
    image: %s
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
        - address: grpc.%s:8082
          nodeport:
            enabled: true
            port: 30010
    authentication:
      internal:
        prefix: "internal:"
        enabled: true
  routers:
    image: %s
    imagePullPolicy: IfNotPresent
    replicas: 1
    resources:
      requests:
        cpu: 100m
        memory: 100Mi
    grpc:
      endpoints:
        - address: router.%s:8083
          nodeport:
            enabled: true
            port: 30011
`, dynamicTestNamespace, baseDomain, image, baseDomain, image, baseDomain)

			err := applyYAML(jumpstarterYAML)
			Expect(err).NotTo(HaveOccurred(), "Failed to create Jumpstarter CR")

			By("verifying the Jumpstarter CR was created")
			verifyJumpstarterCR := func(g Gomega) {
				js := &operatorv1alpha1.Jumpstarter{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      "jumpstarter",
					Namespace: dynamicTestNamespace,
				}, js)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(js.Name).To(Equal("jumpstarter"))
			}
			Eventually(verifyJumpstarterCR).Should(Succeed())

			By("verifying the controller deployment was created")
			verifyControllerDeployment := func(g Gomega) {
				deploymentList := &corev1.PodList{}
				err := k8sClient.List(ctx, deploymentList,
					client.InNamespace(dynamicTestNamespace),
					client.MatchingLabels{"app": "jumpstarter-controller"})
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(deploymentList.Items).NotTo(BeEmpty())
			}
			Eventually(verifyControllerDeployment, 2*time.Minute).Should(Succeed())

			By("verifying the router deployment was created")
			verifyRouterDeployment := func(g Gomega) {
				deploymentList := &corev1.PodList{}
				err := k8sClient.List(ctx, deploymentList,
					client.InNamespace(dynamicTestNamespace),
					client.MatchingLabels{"app": "jumpstarter-router-0"})
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(deploymentList.Items).NotTo(BeEmpty())
			}
			Eventually(verifyRouterDeployment, 2*time.Minute).Should(Succeed())

			By("verifying the controller configmap exists and contains the expected contents")
			verifyConfigMap := func(g Gomega) {
				cm := &corev1.ConfigMap{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      "jumpstarter-controller",
					Namespace: dynamicTestNamespace,
				}, cm)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(cm.Data).To(HaveKey("config"))
				g.Expect(cm.Data).To(HaveKey("router"))

				expectedConfigYAML := `authentication:
  internal:
    prefix: 'internal:'
    tokenLifetime: 43800h0m0s
  jwt: []
  k8s: {}
grpc:
  keepalive:
    minTime: 1s
    permitWithoutStream: true
provisioning:
  enabled: false
`
				expectedRouterYAML := `default:
  endpoint: router.jumpstarter.127.0.0.1.nip.io:8083
`

				// Compare config (YAML)
				actualConfig := cm.Data["config"]
				actualRouter := cm.Data["router"]

				// Unmarshal and compare as map[string]interface{} for robustness to field ordering
				var actualConfigObj, expectedConfigObj map[string]interface{}
				err = yaml.Unmarshal([]byte(actualConfig), &actualConfigObj)
				g.Expect(err).NotTo(HaveOccurred())

				err = yaml.Unmarshal([]byte(expectedConfigYAML), &expectedConfigObj)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(actualConfigObj).To(Equal(expectedConfigObj), "config map 'config' entry did not match expected")

				var actualRouterObj, expectedRouterObj map[string]interface{}
				err = yaml.Unmarshal([]byte(actualRouter), &actualRouterObj)
				g.Expect(err).NotTo(HaveOccurred())

				err = yaml.Unmarshal([]byte(expectedRouterYAML), &expectedRouterObj)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(actualRouterObj).To(Equal(expectedRouterObj), "config map 'router' entry did not match expected")
			}
			Eventually(verifyConfigMap, 1*time.Minute).Should(Succeed())
		})

		It("should allow access to grpc endpoints", func() {
			By("checking endpoint grpc access to controller")
			waitForGRPCEndpoint("grpc.jumpstarter.127.0.0.1.nip.io:8082", 1*time.Minute)
			By("checking endpoint grpc access to router")
			waitForGRPCEndpoint("router.jumpstarter.127.0.0.1.nip.io:8083", 1*time.Minute)
		})

		It("should create new routers if the number of replicas is increased", func() {
			By("updating the Jumpstarter custom resource to increase the number of replicas")
			// Update the jumpstarter object using the k8s client
			jumpstarter := &operatorv1alpha1.Jumpstarter{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      "jumpstarter",
				Namespace: dynamicTestNamespace,
			}, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			jumpstarter.Spec.Routers.Replicas = 3
			err = k8sClient.Update(ctx, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			By("verifying the new routers deployments were created")
			allRoutersDeploymentsCreated := func(g Gomega) bool {
				deployment := &appsv1.Deployment{}

				for i := 0; i < int(jumpstarter.Spec.Routers.Replicas); i++ {
					err := k8sClient.Get(ctx, types.NamespacedName{
						Name:      fmt.Sprintf("jumpstarter-router-%d", i),
						Namespace: dynamicTestNamespace,
					}, deployment)
					//
					if err != nil {
						return false
					}
					Expect(*deployment.Spec.Replicas).To(Equal(int32(1)))
				}
				return true
			}
			Eventually(allRoutersDeploymentsCreated, 1*time.Minute).Should(BeTrue())
			By("verifying the new router services were created")
			allRoutersServicesCreated := func(g Gomega) bool {
				service := &corev1.Service{}
				for i := 0; i < int(jumpstarter.Spec.Routers.Replicas); i++ {
					err := k8sClient.Get(ctx, types.NamespacedName{
						Name:      fmt.Sprintf("jumpstarter-router-%d-np", i),
						Namespace: dynamicTestNamespace,
					}, service)
					if err != nil {
						return false
					}
					// the selector should point to the specific router
					Expect(service.Spec.Selector).To(HaveKeyWithValue("app", fmt.Sprintf("jumpstarter-router-%d", i)))
					// the service should have exactly one port that points to the router port
					Expect(service.Spec.Ports).To(HaveLen(1))
					Expect(service.Spec.Ports[0].Port).To(Equal(int32(8083)))
					Expect(service.Spec.Ports[0].TargetPort).To(Equal(intstr.FromInt(8083)))
					// and has the desired protocol and app protocol
					Expect(service.Spec.Ports[0].Protocol).To(Equal(corev1.ProtocolTCP))
					Expect(*service.Spec.Ports[0].AppProtocol).To(Equal("h2c"))
				}
				return true
			}
			Eventually(allRoutersServicesCreated, 1*time.Minute).Should(BeTrue())
		})

		It("should scale down the routers if the number of replicas is decreased", func() {
			By("updating the Jumpstarter custom resource to decrease the number of replicas")
			jumpstarter := &operatorv1alpha1.Jumpstarter{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      "jumpstarter",
				Namespace: dynamicTestNamespace,
			}, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			jumpstarter.Spec.Routers.Replicas = 1
			err = k8sClient.Update(ctx, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			By("verifying the router deployments were scaled down")
			routerDeploymentsCount := func(g Gomega) int {
				deploymentList := &appsv1.DeploymentList{}
				err := k8sClient.List(ctx, deploymentList,
					client.InNamespace(dynamicTestNamespace),
					client.MatchingLabels{"component": "router"})
				Expect(err).NotTo(HaveOccurred())
				return len(deploymentList.Items)
			}
			Eventually(routerDeploymentsCount, 1*time.Minute).Should(Equal(1))

			By("verifying the router services were scaled down")
			routerServicesCount := func(g Gomega) int {
				serviceList := &corev1.ServiceList{}
				err := k8sClient.List(ctx, serviceList,
					client.InNamespace(dynamicTestNamespace),
					client.MatchingLabels{"component": "router"})
				Expect(err).NotTo(HaveOccurred())
				return len(serviceList.Items)
			}
			Eventually(routerServicesCount, 1*time.Minute).Should(Equal(1))
		})

		AfterAll(func() {
			DeleteTestNamespace(dynamicTestNamespace)
		})
	})
})

// serviceAccountToken returns a token for the specified service account in the given namespace.
// It uses the Kubernetes TokenRequest API to generate a token by directly calling the API.
func serviceAccountToken() (string, error) {
	var token string
	verifyTokenCreation := func(g Gomega) {
		// Create a token request for the service account
		tokenRequest := &authenticationv1.TokenRequest{
			Spec: authenticationv1.TokenRequestSpec{
				ExpirationSeconds: func() *int64 { i := int64(3600); return &i }(),
			},
		}

		// Use the clientset to create the token
		result, err := clientset.CoreV1().ServiceAccounts(namespace).CreateToken(
			ctx,
			serviceAccountName,
			tokenRequest,
			metav1.CreateOptions{},
		)
		g.Expect(err).NotTo(HaveOccurred())
		g.Expect(result.Status.Token).NotTo(BeEmpty())

		token = result.Status.Token
	}
	Eventually(verifyTokenCreation).Should(Succeed())

	return token, nil
}

// getMetricsOutput retrieves and returns the logs from the curl pod used to access the metrics endpoint.
func getMetricsOutput() string {
	By("getting the curl-metrics logs")
	req := clientset.CoreV1().Pods(namespace).GetLogs("curl-metrics", &corev1.PodLogOptions{})
	podLogs, err := req.Stream(ctx)
	Expect(err).NotTo(HaveOccurred(), "Failed to retrieve logs from curl pod")
	defer podLogs.Close()

	buf := new(bytes.Buffer)
	_, _ = io.Copy(buf, podLogs)
	metricsOutput := buf.String()

	Expect(metricsOutput).To(ContainSubstring("< HTTP/1.1 200 OK"))
	return metricsOutput
}

// applyYAML applies a YAML string to the Kubernetes cluster using the client.
// This function parses the YAML and creates/updates the resource using server-side apply.
// It supports any Kubernetes resource type.
func applyYAML(yamlContent string) error {
	// Decode YAML to unstructured object
	decoder := yaml.NewYAMLOrJSONDecoder(bytes.NewReader([]byte(yamlContent)), 4096)
	obj := &unstructured.Unstructured{}

	err := decoder.Decode(obj)
	if err != nil {
		return fmt.Errorf("failed to decode YAML: %w", err)
	}

	// Try to create the object first
	err = k8sClient.Create(ctx, obj)
	if err != nil {
		// If it already exists, update it
		if apierrors.IsAlreadyExists(err) {
			// Get the existing object to get the resource version
			existing := &unstructured.Unstructured{}
			existing.SetGroupVersionKind(obj.GroupVersionKind())
			err = k8sClient.Get(ctx, types.NamespacedName{
				Name:      obj.GetName(),
				Namespace: obj.GetNamespace(),
			}, existing)
			if err != nil {
				return fmt.Errorf("failed to get existing resource: %w", err)
			}

			// Set the resource version for update
			obj.SetResourceVersion(existing.GetResourceVersion())
			err = k8sClient.Update(ctx, obj)
			if err != nil {
				return fmt.Errorf("failed to update resource: %w", err)
			}
		} else {
			return fmt.Errorf("failed to create resource: %w", err)
		}
	}

	return nil
}

// waitForGRPCEndpoint waits for a gRPC endpoint to be ready by attempting to list services using grpcurl.
// It uses Eventually from Gomega to poll the endpoint until it responds or times out.
// Args:
//   - endpoint: the gRPC endpoint address (e.g., "grpc.jumpstarter.127.0.0.1.nip.io:8082")
//   - timeout: maximum time to wait for the endpoint to be ready (default is used from Eventually if not specified)
func waitForGRPCEndpoint(endpoint string, timeout time.Duration) {
	By(fmt.Sprintf("waiting for gRPC endpoint %s to be ready", endpoint))

	// Get grpcurl path from environment or use default
	grpcurlPath := os.Getenv("GRPCURL")
	if grpcurlPath == "" {
		grpcurlPath = "../../../../bin/grpcurl" // installed on the base jumpstarter-controller project
	}

	// exec grpcurl -h to verify it is available
	cmd := exec.Command(grpcurlPath, "-h")
	err := cmd.Run()
	Expect(err).NotTo(HaveOccurred(), "grpcurl is not available")

	checkEndpoint := func(g Gomega) {
		cmd := exec.Command(grpcurlPath, "-insecure", endpoint, "list")
		err := cmd.Run()
		g.Expect(err).NotTo(HaveOccurred(), fmt.Sprintf("gRPC endpoint %s is not ready", endpoint))
	}

	Eventually(checkEndpoint, timeout, 2*time.Second).Should(Succeed())
}
