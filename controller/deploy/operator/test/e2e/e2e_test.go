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

	certmanagerv1 "github.com/cert-manager/cert-manager/pkg/apis/certmanager/v1"
	cmmeta "github.com/cert-manager/cert-manager/pkg/apis/meta/v1"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	appsv1 "k8s.io/api/apps/v1"
	authenticationv1 "k8s.io/api/authentication/v1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	rbacv1 "k8s.io/api/rbac/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
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

// defaultControllerImage is the default image for the controller if IMG env is not set
const defaultControllerImage = "quay.io/jumpstarter-dev/jumpstarter-controller:latest"

var _ = Describe("Manager", Ordered, ContinueOnFailure, func() {
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
						event.LastTimestamp.Format(time.RFC3339),
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

			// Dump cert-manager related resources for debugging
			dumpCertManagerResourcesOnFailure()
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
				image = defaultControllerImage
			}

			jumpstarterYAML := fmt.Sprintf(`apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: jumpstarter
  namespace: %s
spec:
  baseDomain: %s
  useCertManager: false
  authentication:
    internal:
      prefix: "internal:"
      enabled: true
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

		It("should setup ingress for the controller and router for ingress mode", func() {
			By("updating the Jumpstarter custom resource to enable ingress mode")
			jumpstarter := &operatorv1alpha1.Jumpstarter{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      "jumpstarter",
				Namespace: dynamicTestNamespace,
			}, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			jumpstarter.Spec.Controller.GRPC.Endpoints = []operatorv1alpha1.Endpoint{
				{
					Address: "grpc.jumpstarter.127.0.0.1.nip.io:5443",
					Ingress: &operatorv1alpha1.IngressConfig{
						Enabled: true,
						Class:   "nginx",
					},
				},
			}
			jumpstarter.Spec.Routers.GRPC.Endpoints = []operatorv1alpha1.Endpoint{
				{
					Address: "router.jumpstarter.127.0.0.1.nip.io:5443",
					Ingress: &operatorv1alpha1.IngressConfig{
						Enabled: true,
						Class:   "nginx",
					},
				},
			}
			err = k8sClient.Update(ctx, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			By("verifying the ingress for the controller was created")
			verifyIngressForController := func(g Gomega) bool {
				ingress := &networkingv1.Ingress{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      "controller-grpc-ing",
					Namespace: dynamicTestNamespace,
				}, ingress)
				if err != nil {
					return false
				}
				Expect(ingress.Spec.Rules).To(HaveLen(1))
				Expect(ingress.Spec.Rules[0].Host).To(Equal("grpc.jumpstarter.127.0.0.1.nip.io"))
				Expect(ingress.Spec.Rules[0].HTTP.Paths).To(HaveLen(1))
				Expect(ingress.Spec.Rules[0].HTTP.Paths[0].Path).To(Equal("/"))
				Expect(*ingress.Spec.Rules[0].HTTP.Paths[0].PathType).To(Equal(networkingv1.PathTypePrefix))
				return true
			}
			Eventually(verifyIngressForController, 1*time.Minute).Should(BeTrue())

			By("verifying the ingress for the router was created")
			verifyIngressForRouter := func(g Gomega) bool {
				ingress := &networkingv1.Ingress{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      "jumpstarter-router-0-ing",
					Namespace: dynamicTestNamespace,
				}, ingress)
				if err != nil {
					return false
				}
				Expect(ingress.Spec.Rules).To(HaveLen(1))
				Expect(ingress.Spec.Rules[0].Host).To(Equal("router.jumpstarter.127.0.0.1.nip.io"))
				Expect(ingress.Spec.Rules[0].HTTP.Paths).To(HaveLen(1))
				Expect(ingress.Spec.Rules[0].HTTP.Paths[0].Path).To(Equal("/"))
				Expect(*ingress.Spec.Rules[0].HTTP.Paths[0].PathType).To(Equal(networkingv1.PathTypePrefix))
				return true
			}
			Eventually(verifyIngressForRouter, 1*time.Minute).Should(BeTrue())
		})

		It("should contain the right router configuration in the configmap", func() {
			By("checking the configmap contains the right router configuration")
			Eventually(func(g Gomega) string {
				configmap := &corev1.ConfigMap{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      "jumpstarter-controller",
					Namespace: dynamicTestNamespace,
				}, configmap)
				g.Expect(err).NotTo(HaveOccurred())
				return configmap.Data["router"]
			}, 1*time.Minute).Should(ContainSubstring("router.jumpstarter.127.0.0.1.nip.io:5443"))
		})

		It("should update provisioning config when autoProvisioning is enabled", func() {
			By("updating the Jumpstarter CR to enable auto provisioning")
			jumpstarter := &operatorv1alpha1.Jumpstarter{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      "jumpstarter",
				Namespace: dynamicTestNamespace,
			}, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			jumpstarter.Spec.Authentication.AutoProvisioning.Enabled = true
			err = k8sClient.Update(ctx, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			By("verifying the ConfigMap contains provisioning.enabled: true")
			Eventually(func(g Gomega) {
				configmap := &corev1.ConfigMap{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      "jumpstarter-controller",
					Namespace: dynamicTestNamespace,
				}, configmap)
				g.Expect(err).NotTo(HaveOccurred())

				var configObj map[string]interface{}
				err = yaml.Unmarshal([]byte(configmap.Data["config"]), &configObj)
				g.Expect(err).NotTo(HaveOccurred())

				provisioning, ok := configObj["provisioning"].(map[string]interface{})
				g.Expect(ok).To(BeTrue())
				g.Expect(provisioning["enabled"]).To(Equal(true))
			}, 1*time.Minute).Should(Succeed())
		})

		It("should allow access to ingress grpc endpoints", func() {
			// TODO: fix ingress in kind (not working for helm either)
			Skip("nginx ingress not working in kind")

			By("checking endpoint grpc access to controller")
			waitForGRPCEndpoint("grpc.jumpstarter.127.0.0.1.nip.io:5443", 1*time.Minute)
			By("checking endpoint grpc access to router")
			waitForGRPCEndpoint("router.jumpstarter.127.0.0.1.nip.io:5443", 1*time.Minute)
		})

		AfterAll(func() {
			DeleteTestNamespace(dynamicTestNamespace)
		})
	})

	Context("Login endpoint TLS configuration", Ordered, func() {
		const baseDomain = "login-tls.127.0.0.1.nip.io"
		const jumpstarterName = "jumpstarter-login-tls"
		const loginTLSSecretName = "my-custom-login-tls-secret"
		var loginTLSTestNamespace string

		BeforeAll(func() {
			loginTLSTestNamespace = CreateTestNamespace()
		})

		It("should create login ingress with explicit TLS secret", func() {
			By("creating a TLS secret for the login endpoint")
			// Create a dummy TLS secret (in real scenarios this would be a valid cert)
			tlsSecret := &corev1.Secret{
				ObjectMeta: metav1.ObjectMeta{
					Name:      loginTLSSecretName,
					Namespace: loginTLSTestNamespace,
				},
				Type: corev1.SecretTypeTLS,
				Data: map[string][]byte{
					"tls.crt": []byte("dummy-cert"),
					"tls.key": []byte("dummy-key"),
				},
			}
			err := k8sClient.Create(ctx, tlsSecret)
			Expect(err).NotTo(HaveOccurred(), "Failed to create TLS secret")

			By("creating a Jumpstarter CR with login endpoint and explicit TLS secret")
			image := os.Getenv("IMG")
			if image == "" {
				image = defaultControllerImage
			}

			jumpstarterYAML := fmt.Sprintf(`apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: %s
  namespace: %s
spec:
  baseDomain: %s
  authentication:
    internal:
      prefix: "internal:"
      enabled: true
  controller:
    image: %s
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
        - address: grpc.%s:8082
          nodeport:
            enabled: true
            port: 30040
    login:
      tls:
        secretName: %s
      endpoints:
        - address: login.%s
          ingress:
            enabled: true
            class: nginx
  routers:
    image: %s
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
        - address: router.%s:8083
          nodeport:
            enabled: true
            port: 30041
`, jumpstarterName, loginTLSTestNamespace, baseDomain, image, baseDomain, loginTLSSecretName, baseDomain, image, baseDomain)

			err = applyYAML(jumpstarterYAML)
			Expect(err).NotTo(HaveOccurred(), "Failed to create Jumpstarter CR with login TLS config")

			By("verifying the Jumpstarter CR was created with login TLS config")
			Eventually(func(g Gomega) {
				js := &operatorv1alpha1.Jumpstarter{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      jumpstarterName,
					Namespace: loginTLSTestNamespace,
				}, js)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(js.Spec.Controller.Login.TLS).NotTo(BeNil())
				g.Expect(js.Spec.Controller.Login.TLS.SecretName).To(Equal(loginTLSSecretName))
			}, 30*time.Second).Should(Succeed())
		})

		It("should create login service", func() {
			By("verifying the login service was created")
			Eventually(func(g Gomega) {
				svc := &corev1.Service{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      "login",
					Namespace: loginTLSTestNamespace,
				}, svc)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(svc.Spec.Ports).To(HaveLen(1))
				g.Expect(svc.Spec.Ports[0].Port).To(Equal(int32(8086)))
			}, 1*time.Minute).Should(Succeed())
		})

		It("should create login ingress with the explicit TLS secret", func() {
			By("verifying the login ingress was created with correct TLS secret")
			Eventually(func(g Gomega) {
				ingress := &networkingv1.Ingress{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      "login-ing",
					Namespace: loginTLSTestNamespace,
				}, ingress)
				g.Expect(err).NotTo(HaveOccurred())

				// Verify the ingress rules
				g.Expect(ingress.Spec.Rules).To(HaveLen(1))
				g.Expect(ingress.Spec.Rules[0].Host).To(Equal("login." + baseDomain))

				// Verify the TLS configuration uses the explicit secret
				g.Expect(ingress.Spec.TLS).To(HaveLen(1))
				g.Expect(ingress.Spec.TLS[0].SecretName).To(Equal(loginTLSSecretName),
					"Login ingress should use the explicitly configured TLS secret")
				g.Expect(ingress.Spec.TLS[0].Hosts).To(ContainElement("login." + baseDomain))
			}, 1*time.Minute).Should(Succeed())
		})

		It("should update login ingress TLS secret when config changes", func() {
			By("updating the Jumpstarter CR with a different TLS secret name")
			newSecretName := "updated-login-tls-secret"

			// Create the new TLS secret
			newTLSSecret := &corev1.Secret{
				ObjectMeta: metav1.ObjectMeta{
					Name:      newSecretName,
					Namespace: loginTLSTestNamespace,
				},
				Type: corev1.SecretTypeTLS,
				Data: map[string][]byte{
					"tls.crt": []byte("dummy-cert-2"),
					"tls.key": []byte("dummy-key-2"),
				},
			}
			err := k8sClient.Create(ctx, newTLSSecret)
			Expect(err).NotTo(HaveOccurred(), "Failed to create new TLS secret")

			// Update the Jumpstarter CR
			jumpstarter := &operatorv1alpha1.Jumpstarter{}
			err = k8sClient.Get(ctx, types.NamespacedName{
				Name:      jumpstarterName,
				Namespace: loginTLSTestNamespace,
			}, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			jumpstarter.Spec.Controller.Login.TLS.SecretName = newSecretName
			err = k8sClient.Update(ctx, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			By("verifying the login ingress TLS secret was updated")
			Eventually(func(g Gomega) {
				ingress := &networkingv1.Ingress{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      "login-ing",
					Namespace: loginTLSTestNamespace,
				}, ingress)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(ingress.Spec.TLS).To(HaveLen(1))
				g.Expect(ingress.Spec.TLS[0].SecretName).To(Equal(newSecretName),
					"Login ingress should use the updated TLS secret")
			}, 1*time.Minute).Should(Succeed())
		})

		AfterAll(func() {
			DeleteTestNamespace(loginTLSTestNamespace)
		})
	})

	Context("Login endpoint with cert-manager default TLS", Ordered, func() {
		const baseDomain = "login-cm.127.0.0.1.nip.io"
		const jumpstarterName = "jumpstarter-login-cm"
		var loginCMTestNamespace string

		BeforeAll(func() {
			loginCMTestNamespace = CreateTestNamespace()
		})

		It("should create login ingress with default TLS secret when cert-manager is enabled", func() {
			By("creating a Jumpstarter CR with login endpoint and cert-manager enabled")
			image := os.Getenv("IMG")
			if image == "" {
				image = defaultControllerImage
			}

			jumpstarterYAML := fmt.Sprintf(`apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: %s
  namespace: %s
spec:
  baseDomain: %s
  certManager:
    enabled: true
    server:
      selfSigned:
        enabled: true
  authentication:
    internal:
      prefix: "internal:"
      enabled: true
  controller:
    image: %s
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
        - address: grpc.%s:8082
          nodeport:
            enabled: true
            port: 30050
    login:
      endpoints:
        - address: login.%s
          ingress:
            enabled: true
            class: nginx
  routers:
    image: %s
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
        - address: router.%s:8083
          nodeport:
            enabled: true
            port: 30051
`, jumpstarterName, loginCMTestNamespace, baseDomain, image, baseDomain, baseDomain, image, baseDomain)

			err := applyYAML(jumpstarterYAML)
			Expect(err).NotTo(HaveOccurred(), "Failed to create Jumpstarter CR with cert-manager")

			By("verifying the Jumpstarter CR was created")
			Eventually(func(g Gomega) {
				js := &operatorv1alpha1.Jumpstarter{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      jumpstarterName,
					Namespace: loginCMTestNamespace,
				}, js)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(js.Spec.CertManager.Enabled).To(BeTrue())
			}, 30*time.Second).Should(Succeed())
		})

		It("should create login ingress with default TLS secret name", func() {
			By("verifying the login ingress was created with default TLS secret naming")
			Eventually(func(g Gomega) {
				ingress := &networkingv1.Ingress{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      "login-ing",
					Namespace: loginCMTestNamespace,
				}, ingress)
				g.Expect(err).NotTo(HaveOccurred())

				// Verify the ingress rules
				g.Expect(ingress.Spec.Rules).To(HaveLen(1))
				g.Expect(ingress.Spec.Rules[0].Host).To(Equal("login." + baseDomain))

				// When cert-manager is enabled and no explicit TLS secret is set,
				// the default naming convention should be used: serviceName + "-tls"
				g.Expect(ingress.Spec.TLS).To(HaveLen(1))
				g.Expect(ingress.Spec.TLS[0].SecretName).To(Equal("login-tls"),
					"Login ingress should use default TLS secret name (login-tls) when cert-manager is enabled")
			}, 1*time.Minute).Should(Succeed())
		})

		AfterAll(func() {
			DeleteTestNamespace(loginCMTestNamespace)
		})
	})

	Context("Login endpoint without TLS secret", Ordered, func() {
		const baseDomain = "login-notls.127.0.0.1.nip.io"
		const jumpstarterName = "jumpstarter-login-notls"
		var loginNoTLSTestNamespace string

		BeforeAll(func() {
			loginNoTLSTestNamespace = CreateTestNamespace()
		})

		It("should create login ingress with empty TLS secret when cert-manager is disabled", func() {
			By("creating a Jumpstarter CR with login endpoint and no TLS config")
			image := os.Getenv("IMG")
			if image == "" {
				image = defaultControllerImage
			}

			jumpstarterYAML := fmt.Sprintf(`apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: %s
  namespace: %s
spec:
  baseDomain: %s
  authentication:
    internal:
      prefix: "internal:"
      enabled: true
  controller:
    image: %s
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
        - address: grpc.%s:8082
          nodeport:
            enabled: true
            port: 30060
    login:
      endpoints:
        - address: login.%s
          ingress:
            enabled: true
            class: nginx
  routers:
    image: %s
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
        - address: router.%s:8083
          nodeport:
            enabled: true
            port: 30061
`, jumpstarterName, loginNoTLSTestNamespace, baseDomain, image, baseDomain, baseDomain, image, baseDomain)

			err := applyYAML(jumpstarterYAML)
			Expect(err).NotTo(HaveOccurred(), "Failed to create Jumpstarter CR without TLS config")

			By("verifying the Jumpstarter CR was created")
			Eventually(func(g Gomega) {
				js := &operatorv1alpha1.Jumpstarter{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      jumpstarterName,
					Namespace: loginNoTLSTestNamespace,
				}, js)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(js.Spec.CertManager.Enabled).To(BeFalse())
				g.Expect(js.Spec.Controller.Login.TLS).To(BeNil())
			}, 30*time.Second).Should(Succeed())
		})

		It("should create login ingress with empty TLS secret name", func() {
			By("verifying the login ingress was created with empty TLS secret")
			Eventually(func(g Gomega) {
				ingress := &networkingv1.Ingress{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      "login-ing",
					Namespace: loginNoTLSTestNamespace,
				}, ingress)
				g.Expect(err).NotTo(HaveOccurred())

				// Verify the ingress rules
				g.Expect(ingress.Spec.Rules).To(HaveLen(1))
				g.Expect(ingress.Spec.Rules[0].Host).To(Equal("login." + baseDomain))

				// When cert-manager is disabled and no explicit TLS secret is set,
				// the TLS secret name should be empty (let ingress controller handle it)
				g.Expect(ingress.Spec.TLS).To(HaveLen(1))
				g.Expect(ingress.Spec.TLS[0].SecretName).To(BeEmpty(),
					"Login ingress should have empty TLS secret name when no TLS config and cert-manager disabled")
			}, 1*time.Minute).Should(Succeed())
		})

		AfterAll(func() {
			DeleteTestNamespace(loginNoTLSTestNamespace)
		})
	})

	Context("cert-manager self-signed mode", Ordered, func() {
		const baseDomain = "certmanager.127.0.0.1.nip.io"
		const jumpstarterName = "jumpstarter-certmanager"
		var certManagerTestNamespace string

		BeforeAll(func() {
			certManagerTestNamespace = CreateTestNamespace()
		})

		It("should deploy jumpstarter with self-signed cert-manager", func() {
			By("creating a Jumpstarter custom resource with cert-manager enabled")
			image := os.Getenv("IMG")
			if image == "" {
				image = defaultControllerImage
			}

			jumpstarterYAML := fmt.Sprintf(`apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: %s
  namespace: %s
spec:
  baseDomain: %s
  certManager:
    enabled: true
    server:
      selfSigned:
        enabled: true
  controller:
    image: %s
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
        - address: grpc.%s:8082
          nodeport:
            enabled: true
            port: 30020
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
            port: 30021
`, jumpstarterName, certManagerTestNamespace, baseDomain, image, baseDomain, image, baseDomain)

			err := applyYAML(jumpstarterYAML)
			Expect(err).NotTo(HaveOccurred(), "Failed to create Jumpstarter CR with cert-manager")

			By("verifying the Jumpstarter CR was created")
			Eventually(func(g Gomega) {
				js := &operatorv1alpha1.Jumpstarter{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      jumpstarterName,
					Namespace: certManagerTestNamespace,
				}, js)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(js.Spec.CertManager.Enabled).To(BeTrue())
			}, 30*time.Second).Should(Succeed())
		})

		It("should create the self-signed issuer", func() {
			By("verifying the self-signed issuer was created")
			issuerName := jumpstarterName + "-selfsigned-issuer"
			Eventually(func(g Gomega) {
				issuer := &certmanagerv1.Issuer{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      issuerName,
					Namespace: certManagerTestNamespace,
				}, issuer)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(issuer.Spec.SelfSigned).NotTo(BeNil())
			}, 1*time.Minute).Should(Succeed())

			waitForIssuerReady(certManagerTestNamespace, issuerName, 2*time.Minute)
		})

		It("should create the CA certificate", func() {
			By("verifying the CA certificate was created")
			caCertName := jumpstarterName + "-ca"
			Eventually(func(g Gomega) {
				cert := &certmanagerv1.Certificate{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      caCertName,
					Namespace: certManagerTestNamespace,
				}, cert)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(cert.Spec.IsCA).To(BeTrue())
			}, 1*time.Minute).Should(Succeed())

			waitForCertificateReady(certManagerTestNamespace, caCertName, 2*time.Minute)
		})

		It("should create the CA issuer", func() {
			By("verifying the CA issuer was created")
			caIssuerName := jumpstarterName + "-ca-issuer"
			Eventually(func(g Gomega) {
				issuer := &certmanagerv1.Issuer{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      caIssuerName,
					Namespace: certManagerTestNamespace,
				}, issuer)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(issuer.Spec.CA).NotTo(BeNil())
			}, 1*time.Minute).Should(Succeed())

			waitForIssuerReady(certManagerTestNamespace, caIssuerName, 2*time.Minute)
		})

		It("should create the controller TLS certificate", func() {
			By("verifying the controller certificate was created")
			controllerCertName := jumpstarterName + "-controller-tls"
			Eventually(func(g Gomega) {
				cert := &certmanagerv1.Certificate{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      controllerCertName,
					Namespace: certManagerTestNamespace,
				}, cert)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(cert.Spec.IsCA).To(BeFalse())
			}, 1*time.Minute).Should(Succeed())

			waitForCertificateReady(certManagerTestNamespace, controllerCertName, 2*time.Minute)

			By("verifying the controller TLS secret exists")
			verifyTLSSecret(certManagerTestNamespace, controllerCertName)
		})

		It("should create the router TLS certificate", func() {
			By("verifying the router certificate was created")
			routerCertName := fmt.Sprintf("%s-router-0-tls", jumpstarterName)
			Eventually(func(g Gomega) {
				cert := &certmanagerv1.Certificate{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      routerCertName,
					Namespace: certManagerTestNamespace,
				}, cert)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(cert.Spec.IsCA).To(BeFalse())
			}, 1*time.Minute).Should(Succeed())

			waitForCertificateReady(certManagerTestNamespace, routerCertName, 2*time.Minute)

			By("verifying the router TLS secret exists")
			verifyTLSSecret(certManagerTestNamespace, routerCertName)
		})

		It("should create the CA ConfigMap with the CA certificate", func() {
			By("verifying the CA ConfigMap was created with the correct CA certificate")
			caConfigMapName := "jumpstarter-service-ca-cert"
			caSecretName := jumpstarterName + "-ca"

			Eventually(func(g Gomega) {
				// Get the CA ConfigMap
				cm := &corev1.ConfigMap{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      caConfigMapName,
					Namespace: certManagerTestNamespace,
				}, cm)
				g.Expect(err).NotTo(HaveOccurred())

				// Get the CA secret to compare
				caSecret := &corev1.Secret{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      caSecretName,
					Namespace: certManagerTestNamespace,
				}, caSecret)
				g.Expect(err).NotTo(HaveOccurred())

				// Verify the CA ConfigMap contains the CA certificate from the secret
				g.Expect(cm.Data).To(HaveKey("ca.crt"))
				g.Expect(cm.Data["ca.crt"]).NotTo(BeEmpty(), "CA ConfigMap should contain the CA certificate")
				g.Expect(cm.Data["ca.crt"]).To(Equal(string(caSecret.Data["tls.crt"])),
					"CA ConfigMap should contain the same certificate as the CA secret")
			}, 2*time.Minute).Should(Succeed())
		})

		It("should mount TLS certificates in controller deployment", func() {
			By("waiting for controller deployment to be available with TLS mount")
			controllerDeploymentName := jumpstarterName + "-controller"
			Eventually(func(g Gomega) {
				verifyDeploymentHasTLSMount(g, certManagerTestNamespace, controllerDeploymentName)
			}, 2*time.Minute, 2*time.Second).Should(Succeed())
		})

		It("should mount TLS certificates in router deployment", func() {
			By("waiting for router deployment to be available with TLS mount")
			routerDeploymentName := jumpstarterName + "-router-0"
			Eventually(func(g Gomega) {
				verifyDeploymentHasTLSMount(g, certManagerTestNamespace, routerDeploymentName)
			}, 2*time.Minute, 2*time.Second).Should(Succeed())
		})

		It("should report CertManagerAvailable condition as True", func() {
			waitForCondition(certManagerTestNamespace, jumpstarterName,
				operatorv1alpha1.ConditionTypeCertManagerAvailable, metav1.ConditionTrue, 1*time.Minute)
		})

		It("should report IssuerReady condition as True", func() {
			waitForCondition(certManagerTestNamespace, jumpstarterName,
				operatorv1alpha1.ConditionTypeIssuerReady, metav1.ConditionTrue, 2*time.Minute)
		})

		It("should report ControllerCertificateReady condition as True", func() {
			waitForCondition(certManagerTestNamespace, jumpstarterName,
				operatorv1alpha1.ConditionTypeControllerCertificateReady, metav1.ConditionTrue, 2*time.Minute)
		})

		It("should report RouterCertificatesReady condition as True", func() {
			waitForCondition(certManagerTestNamespace, jumpstarterName,
				operatorv1alpha1.ConditionTypeRouterCertificatesReady, metav1.ConditionTrue, 2*time.Minute)
		})

		It("should report ControllerDeploymentReady condition as True", func() {
			// Deployment readiness can take longer due to image pulls and pod scheduling
			waitForCondition(certManagerTestNamespace, jumpstarterName,
				operatorv1alpha1.ConditionTypeControllerDeploymentReady, metav1.ConditionTrue, 5*time.Minute)
		})

		It("should report RouterDeploymentsReady condition as True", func() {
			// Deployment readiness can take longer due to image pulls and pod scheduling
			waitForCondition(certManagerTestNamespace, jumpstarterName,
				operatorv1alpha1.ConditionTypeRouterDeploymentsReady, metav1.ConditionTrue, 5*time.Minute)
		})

		It("should report Ready condition as True when all components are ready", func() {
			waitForCondition(certManagerTestNamespace, jumpstarterName,
				operatorv1alpha1.ConditionTypeReady, metav1.ConditionTrue, 5*time.Minute)

			By("verifying all conditions are present and True")
			conditions := getJumpstarterConditions(certManagerTestNamespace, jumpstarterName)
			expectedConditions := []string{
				operatorv1alpha1.ConditionTypeCertManagerAvailable,
				operatorv1alpha1.ConditionTypeIssuerReady,
				operatorv1alpha1.ConditionTypeControllerCertificateReady,
				operatorv1alpha1.ConditionTypeRouterCertificatesReady,
				operatorv1alpha1.ConditionTypeControllerDeploymentReady,
				operatorv1alpha1.ConditionTypeRouterDeploymentsReady,
				operatorv1alpha1.ConditionTypeReady,
			}

			for _, condType := range expectedConditions {
				cond := meta.FindStatusCondition(conditions, condType)
				Expect(cond).NotTo(BeNil(), fmt.Sprintf("condition %s not found", condType))
				Expect(cond.Status).To(Equal(metav1.ConditionTrue),
					fmt.Sprintf("condition %s is not True: %s", condType, cond.Message))
			}
		})

		It("should preserve certificates when cert-manager is disabled and re-enabled", func() {
			By("starting with cert-manager enabled and verifying TLS is configured")
			controllerDeploymentName := jumpstarterName + "-controller"
			routerDeploymentName := jumpstarterName + "-router-0"
			controllerCertName := jumpstarterName + "-controller-tls"
			routerCertName := fmt.Sprintf("%s-router-0-tls", jumpstarterName)
			issuerNames := []string{
				jumpstarterName + "-selfsigned-issuer",
				jumpstarterName + "-ca-issuer",
			}

			// Verify initial state has TLS configured
			Eventually(func(g Gomega) {
				verifyDeploymentHasTLSMount(g, certManagerTestNamespace, controllerDeploymentName)
				verifyDeploymentHasTLSMount(g, certManagerTestNamespace, routerDeploymentName)
			}, 1*time.Minute, 2*time.Second).Should(Succeed())

			By("disabling cert-manager")
			jumpstarter := &operatorv1alpha1.Jumpstarter{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      jumpstarterName,
				Namespace: certManagerTestNamespace,
			}, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			jumpstarter.Spec.CertManager.Enabled = false
			err = k8sClient.Update(ctx, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			By("waiting for and verifying deployments are reconciled WITHOUT TLS configuration")
			Eventually(func(g Gomega) {
				verifyDeploymentHasNoTLSMount(g, certManagerTestNamespace, controllerDeploymentName)
				verifyDeploymentHasNoTLSMount(g, certManagerTestNamespace, routerDeploymentName)
			}, 2*time.Minute, 2*time.Second).Should(Succeed())

			By("verifying Certificate and Issuer resources still exist (not deleted)")
			cert := &certmanagerv1.Certificate{}
			err = k8sClient.Get(ctx, types.NamespacedName{
				Name:      controllerCertName,
				Namespace: certManagerTestNamespace,
			}, cert)
			Expect(err).NotTo(HaveOccurred(), "Controller certificate should still exist")

			err = k8sClient.Get(ctx, types.NamespacedName{
				Name:      routerCertName,
				Namespace: certManagerTestNamespace,
			}, cert)
			Expect(err).NotTo(HaveOccurred(), "Router certificate should still exist")

			for _, issuerName := range issuerNames {
				issuer := &certmanagerv1.Issuer{}
				err = k8sClient.Get(ctx, types.NamespacedName{
					Name:      issuerName,
					Namespace: certManagerTestNamespace,
				}, issuer)
				Expect(err).NotTo(HaveOccurred(), fmt.Sprintf("Issuer %s should still exist", issuerName))
			}

			By("re-enabling cert-manager")
			err = k8sClient.Get(ctx, types.NamespacedName{
				Name:      jumpstarterName,
				Namespace: certManagerTestNamespace,
			}, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			jumpstarter.Spec.CertManager.Enabled = true
			err = k8sClient.Update(ctx, jumpstarter)
			Expect(err).NotTo(HaveOccurred())

			By("verifying deployments are reconciled WITH TLS configuration again")
			Eventually(func(g Gomega) {
				verifyDeploymentHasTLSMount(g, certManagerTestNamespace, controllerDeploymentName)
				verifyDeploymentHasTLSMount(g, certManagerTestNamespace, routerDeploymentName)
			}, 2*time.Minute, 2*time.Second).Should(Succeed())

			By("verifying system is ready with certificates")
			waitForCondition(certManagerTestNamespace, jumpstarterName,
				operatorv1alpha1.ConditionTypeReady, metav1.ConditionTrue, 3*time.Minute)
		})

		AfterAll(func() {
			DeleteTestNamespace(certManagerTestNamespace)
		})
	})

	Context("cert-manager external issuer mode", Ordered, func() {
		const baseDomain = "external-issuer.127.0.0.1.nip.io"
		const jumpstarterName = "jumpstarter-external"
		const clusterIssuerName = "test-cluster-issuer"
		var externalIssuerTestNamespace string

		BeforeAll(func() {
			externalIssuerTestNamespace = CreateTestNamespace()

			By("creating a self-signed ClusterIssuer for testing")
			err := createSelfSignedClusterIssuer(clusterIssuerName)
			Expect(err).NotTo(HaveOccurred())
			waitForClusterIssuerReady(clusterIssuerName, 2*time.Minute)
		})

		It("should deploy jumpstarter with external issuer reference", func() {
			By("creating a Jumpstarter custom resource with external issuer")
			image := os.Getenv("IMG")
			if image == "" {
				image = defaultControllerImage
			}

			jumpstarterYAML := fmt.Sprintf(`apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: %s
  namespace: %s
spec:
  baseDomain: %s
  certManager:
    enabled: true
    server:
      issuerRef:
        name: %s
        kind: ClusterIssuer
  controller:
    image: %s
    imagePullPolicy: IfNotPresent
    replicas: 1
    grpc:
      endpoints:
        - address: grpc.%s:8082
          nodeport:
            enabled: true
            port: 30030
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
            port: 30031
`, jumpstarterName, externalIssuerTestNamespace, baseDomain, clusterIssuerName, image, baseDomain, image, baseDomain)

			err := applyYAML(jumpstarterYAML)
			Expect(err).NotTo(HaveOccurred(), "Failed to create Jumpstarter CR with external issuer")

			By("verifying the Jumpstarter CR was created")
			Eventually(func(g Gomega) {
				js := &operatorv1alpha1.Jumpstarter{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      jumpstarterName,
					Namespace: externalIssuerTestNamespace,
				}, js)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(js.Spec.CertManager.Enabled).To(BeTrue())
				g.Expect(js.Spec.CertManager.Server).NotTo(BeNil())
				g.Expect(js.Spec.CertManager.Server.IssuerRef).NotTo(BeNil())
				g.Expect(js.Spec.CertManager.Server.IssuerRef.Name).To(Equal(clusterIssuerName))
			}, 30*time.Second).Should(Succeed())
		})

		It("should NOT create a self-signed issuer when using external issuer", func() {
			By("verifying no self-signed issuer was created")
			selfSignedIssuerName := jumpstarterName + "-selfsigned-issuer"
			Consistently(func(g Gomega) {
				issuer := &certmanagerv1.Issuer{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      selfSignedIssuerName,
					Namespace: externalIssuerTestNamespace,
				}, issuer)
				g.Expect(apierrors.IsNotFound(err)).To(BeTrue(),
					"Self-signed issuer should not exist when using external issuer")
			}, 10*time.Second, time.Second).Should(Succeed())
		})

		It("should create controller certificate referencing the external issuer", func() {
			By("verifying the controller certificate was created")
			controllerCertName := jumpstarterName + "-controller-tls"
			Eventually(func(g Gomega) {
				cert := &certmanagerv1.Certificate{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      controllerCertName,
					Namespace: externalIssuerTestNamespace,
				}, cert)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(cert.Spec.IssuerRef.Name).To(Equal(clusterIssuerName))
				g.Expect(cert.Spec.IssuerRef.Kind).To(Equal("ClusterIssuer"))
			}, 1*time.Minute).Should(Succeed())

			waitForCertificateReady(externalIssuerTestNamespace, controllerCertName, 2*time.Minute)

			By("verifying the controller TLS secret exists")
			verifyTLSSecret(externalIssuerTestNamespace, controllerCertName)
		})

		It("should create router certificate referencing the external issuer", func() {
			By("verifying the router certificate was created")
			routerCertName := fmt.Sprintf("%s-router-0-tls", jumpstarterName)
			Eventually(func(g Gomega) {
				cert := &certmanagerv1.Certificate{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      routerCertName,
					Namespace: externalIssuerTestNamespace,
				}, cert)
				g.Expect(err).NotTo(HaveOccurred())
				g.Expect(cert.Spec.IssuerRef.Name).To(Equal(clusterIssuerName))
				g.Expect(cert.Spec.IssuerRef.Kind).To(Equal("ClusterIssuer"))
			}, 1*time.Minute).Should(Succeed())

			waitForCertificateReady(externalIssuerTestNamespace, routerCertName, 2*time.Minute)

			By("verifying the router TLS secret exists")
			verifyTLSSecret(externalIssuerTestNamespace, routerCertName)
		})

		It("should create an empty CA ConfigMap for external issuer without CABundle", func() {
			By("verifying the CA ConfigMap was created but is empty (external issuer without CABundle)")
			// Fixed name for discoverability by jmp admin cli
			caConfigMapName := "jumpstarter-service-ca-cert"

			Eventually(func(g Gomega) {
				cm := &corev1.ConfigMap{}
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      caConfigMapName,
					Namespace: externalIssuerTestNamespace,
				}, cm)
				g.Expect(err).NotTo(HaveOccurred())

				// Verify the CA ConfigMap exists but ca.crt is empty (publicly trusted CA)
				g.Expect(cm.Data).To(HaveKey("ca.crt"))
				g.Expect(cm.Data["ca.crt"]).To(BeEmpty(),
					"CA ConfigMap should be empty for external issuer without CABundle")
			}, 1*time.Minute).Should(Succeed())
		})

		AfterAll(func() {
			DeleteTestNamespace(externalIssuerTestNamespace)
			_ = deleteSelfSignedClusterIssuer(clusterIssuerName)
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
	// Tests run from controller/deploy/operator/ directory, grpcurl is at controller/bin/
	grpcurlPath := os.Getenv("GRPCURL")
	if grpcurlPath == "" {
		grpcurlPath = "../../bin/grpcurl" // installed on the base jumpstarter-controller project
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

// verifyCondition checks if a Jumpstarter resource has a specific condition with the expected status.
// Returns true if the condition exists and has the expected status.
func verifyCondition(js *operatorv1alpha1.Jumpstarter, condType string, expectedStatus metav1.ConditionStatus) bool {
	cond := meta.FindStatusCondition(js.Status.Conditions, condType)
	if cond == nil {
		return false
	}
	return cond.Status == expectedStatus
}

// waitForCondition waits for a Jumpstarter resource to have a specific condition with the expected status.
// It polls the resource until the condition is met or the timeout is reached.
func waitForCondition(namespace, name, condType string, expectedStatus metav1.ConditionStatus, timeout time.Duration) {
	By(fmt.Sprintf("waiting for condition %s to be %s", condType, expectedStatus))

	checkCondition := func(g Gomega) {
		js := &operatorv1alpha1.Jumpstarter{}
		err := k8sClient.Get(ctx, types.NamespacedName{
			Name:      name,
			Namespace: namespace,
		}, js)
		g.Expect(err).NotTo(HaveOccurred())
		g.Expect(verifyCondition(js, condType, expectedStatus)).To(BeTrue(),
			fmt.Sprintf("condition %s is not %s", condType, expectedStatus))
	}

	Eventually(checkCondition, timeout, 2*time.Second).Should(Succeed())
}

// getJumpstarterConditions retrieves and returns all conditions from a Jumpstarter resource.
func getJumpstarterConditions(namespace, name string) []metav1.Condition {
	js := &operatorv1alpha1.Jumpstarter{}
	err := k8sClient.Get(ctx, types.NamespacedName{
		Name:      name,
		Namespace: namespace,
	}, js)
	Expect(err).NotTo(HaveOccurred())
	return js.Status.Conditions
}

// createSelfSignedClusterIssuer creates a self-signed ClusterIssuer for testing external issuer mode.
func createSelfSignedClusterIssuer(name string) error {
	issuer := &certmanagerv1.ClusterIssuer{
		ObjectMeta: metav1.ObjectMeta{
			Name: name,
		},
		Spec: certmanagerv1.IssuerSpec{
			IssuerConfig: certmanagerv1.IssuerConfig{
				SelfSigned: &certmanagerv1.SelfSignedIssuer{},
			},
		},
	}

	err := k8sClient.Create(ctx, issuer)
	if err != nil && !apierrors.IsAlreadyExists(err) {
		return err
	}
	return nil
}

// deleteSelfSignedClusterIssuer deletes a ClusterIssuer.
func deleteSelfSignedClusterIssuer(name string) error {
	issuer := &certmanagerv1.ClusterIssuer{
		ObjectMeta: metav1.ObjectMeta{
			Name: name,
		},
	}
	return client.IgnoreNotFound(k8sClient.Delete(ctx, issuer))
}

// waitForIssuerReady waits for an Issuer to have a Ready condition.
func waitForIssuerReady(namespace, name string, timeout time.Duration) {
	By(fmt.Sprintf("waiting for Issuer %s to be ready", name))

	checkReady := func(g Gomega) {
		issuer := &certmanagerv1.Issuer{}
		err := k8sClient.Get(ctx, types.NamespacedName{
			Name:      name,
			Namespace: namespace,
		}, issuer)
		g.Expect(err).NotTo(HaveOccurred())

		// Check for Ready condition
		for _, cond := range issuer.Status.Conditions {
			if cond.Type == certmanagerv1.IssuerConditionReady {
				g.Expect(cond.Status).To(Equal(cmmeta.ConditionTrue),
					fmt.Sprintf("Issuer %s is not ready: %s", name, cond.Message))
				return
			}
		}
		g.Expect(false).To(BeTrue(), fmt.Sprintf("Issuer %s has no Ready condition", name))
	}

	Eventually(checkReady, timeout, 2*time.Second).Should(Succeed())
}

// waitForClusterIssuerReady waits for a ClusterIssuer to have a Ready condition.
func waitForClusterIssuerReady(name string, timeout time.Duration) {
	By(fmt.Sprintf("waiting for ClusterIssuer %s to be ready", name))

	checkReady := func(g Gomega) {
		issuer := &certmanagerv1.ClusterIssuer{}
		err := k8sClient.Get(ctx, types.NamespacedName{
			Name: name,
		}, issuer)
		g.Expect(err).NotTo(HaveOccurred())

		// Check for Ready condition
		for _, cond := range issuer.Status.Conditions {
			if cond.Type == certmanagerv1.IssuerConditionReady {
				g.Expect(cond.Status).To(Equal(cmmeta.ConditionTrue),
					fmt.Sprintf("ClusterIssuer %s is not ready: %s", name, cond.Message))
				return
			}
		}
		g.Expect(false).To(BeTrue(), fmt.Sprintf("ClusterIssuer %s has no Ready condition", name))
	}

	Eventually(checkReady, timeout, 2*time.Second).Should(Succeed())
}

// waitForCertificateReady waits for a Certificate to have a Ready condition.
func waitForCertificateReady(namespace, name string, timeout time.Duration) {
	By(fmt.Sprintf("waiting for Certificate %s to be ready", name))

	checkReady := func(g Gomega) {
		cert := &certmanagerv1.Certificate{}
		err := k8sClient.Get(ctx, types.NamespacedName{
			Name:      name,
			Namespace: namespace,
		}, cert)
		g.Expect(err).NotTo(HaveOccurred())

		// Check for Ready condition
		for _, cond := range cert.Status.Conditions {
			if cond.Type == certmanagerv1.CertificateConditionReady {
				g.Expect(cond.Status).To(Equal(cmmeta.ConditionTrue),
					fmt.Sprintf("Certificate %s is not ready: %s", name, cond.Message))
				return
			}
		}
		g.Expect(false).To(BeTrue(), fmt.Sprintf("Certificate %s has no Ready condition", name))
	}

	Eventually(checkReady, timeout, 2*time.Second).Should(Succeed())
}

// verifyTLSSecret checks that a TLS secret exists and has the expected keys.
func verifyTLSSecret(namespace, name string) {
	By(fmt.Sprintf("verifying TLS secret %s exists", name))

	secret := &corev1.Secret{}
	err := k8sClient.Get(ctx, types.NamespacedName{
		Name:      name,
		Namespace: namespace,
	}, secret)
	Expect(err).NotTo(HaveOccurred(), fmt.Sprintf("TLS secret %s not found", name))
	Expect(secret.Data).To(HaveKey("tls.crt"), fmt.Sprintf("TLS secret %s missing tls.crt", name))
	Expect(secret.Data).To(HaveKey("tls.key"), fmt.Sprintf("TLS secret %s missing tls.key", name))
}

// verifyDeploymentHasTLSMount checks that a deployment has the TLS volume mount and env vars.
// This is used with Gomega assertions to verify the deployment has been reconciled with TLS.
func verifyDeploymentHasTLSMount(g Gomega, namespace, name string) {
	deployment := &appsv1.Deployment{}
	err := k8sClient.Get(ctx, types.NamespacedName{
		Name:      name,
		Namespace: namespace,
	}, deployment)
	g.Expect(err).NotTo(HaveOccurred())

	// Check for tls-certs volume
	hasVolume := false
	for _, vol := range deployment.Spec.Template.Spec.Volumes {
		if vol.Name == "tls-certs" {
			hasVolume = true
			break
		}
	}
	g.Expect(hasVolume).To(BeTrue(), fmt.Sprintf("deployment %s missing tls-certs volume", name))

	// Check for volume mount in the first container
	g.Expect(deployment.Spec.Template.Spec.Containers).NotTo(BeEmpty())
	container := deployment.Spec.Template.Spec.Containers[0]

	hasMount := false
	for _, mount := range container.VolumeMounts {
		if mount.Name == "tls-certs" && mount.MountPath == "/tls" {
			hasMount = true
			break
		}
	}
	g.Expect(hasMount).To(BeTrue(), fmt.Sprintf("deployment %s missing /tls volume mount", name))

	// Check for EXTERNAL_CERT_PEM and EXTERNAL_KEY_PEM env vars
	hasCertEnv := false
	hasKeyEnv := false
	for _, env := range container.Env {
		if env.Name == "EXTERNAL_CERT_PEM" && env.Value == "/tls/tls.crt" {
			hasCertEnv = true
		}
		if env.Name == "EXTERNAL_KEY_PEM" && env.Value == "/tls/tls.key" {
			hasKeyEnv = true
		}
	}
	g.Expect(hasCertEnv).To(BeTrue(), fmt.Sprintf("deployment %s missing EXTERNAL_CERT_PEM env var", name))
	g.Expect(hasKeyEnv).To(BeTrue(), fmt.Sprintf("deployment %s missing EXTERNAL_KEY_PEM env var", name))
}

// verifyDeploymentHasNoTLSMount checks that a deployment does NOT have TLS configuration.
// This is used with Gomega assertions to verify the deployment has been reconciled without TLS.
func verifyDeploymentHasNoTLSMount(g Gomega, namespace, name string) {
	deployment := &appsv1.Deployment{}
	err := k8sClient.Get(ctx, types.NamespacedName{
		Name:      name,
		Namespace: namespace,
	}, deployment)
	g.Expect(err).NotTo(HaveOccurred())

	// Check that tls-certs volume is NOT present
	for _, vol := range deployment.Spec.Template.Spec.Volumes {
		g.Expect(vol.Name).NotTo(Equal("tls-certs"),
			fmt.Sprintf("deployment %s should not have tls-certs volume", name))
	}

	// Check for volume mount in the first container
	g.Expect(deployment.Spec.Template.Spec.Containers).NotTo(BeEmpty())
	container := deployment.Spec.Template.Spec.Containers[0]

	// Check that /tls volume mount is NOT present
	for _, mount := range container.VolumeMounts {
		if mount.Name == "tls-certs" {
			g.Expect(mount.MountPath).NotTo(Equal("/tls"),
				fmt.Sprintf("deployment %s should not have /tls volume mount", name))
		}
	}

	// Check that EXTERNAL_CERT_PEM and EXTERNAL_KEY_PEM env vars are NOT present
	for _, env := range container.Env {
		g.Expect(env.Name).NotTo(Equal("EXTERNAL_CERT_PEM"),
			fmt.Sprintf("deployment %s should not have EXTERNAL_CERT_PEM env var", name))
		g.Expect(env.Name).NotTo(Equal("EXTERNAL_KEY_PEM"),
			fmt.Sprintf("deployment %s should not have EXTERNAL_KEY_PEM env var", name))
	}
}

// dumpCertManagerResourcesOnFailure dumps cert-manager and Jumpstarter resources for debugging test failures.
func dumpCertManagerResourcesOnFailure() {
	By("Dumping cert-manager resources for debugging")

	// List all Jumpstarter resources across all namespaces
	jsList := &operatorv1alpha1.JumpstarterList{}
	if err := k8sClient.List(ctx, jsList); err == nil {
		_, _ = fmt.Fprintf(GinkgoWriter, "\n=== Jumpstarter Resources ===\n")
		for _, js := range jsList.Items {
			_, _ = fmt.Fprintf(GinkgoWriter, "Jumpstarter: %s/%s\n", js.Namespace, js.Name)
			_, _ = fmt.Fprintf(GinkgoWriter, "  CertManager.Enabled: %v\n", js.Spec.CertManager.Enabled)
			_, _ = fmt.Fprintf(GinkgoWriter, "  Conditions:\n")
			for _, cond := range js.Status.Conditions {
				_, _ = fmt.Fprintf(GinkgoWriter, "    - %s: %s (%s: %s)\n",
					cond.Type, cond.Status, cond.Reason, cond.Message)
			}
		}
	}

	// List all Issuers across all namespaces
	issuerList := &certmanagerv1.IssuerList{}
	if err := k8sClient.List(ctx, issuerList); err == nil {
		_, _ = fmt.Fprintf(GinkgoWriter, "\n=== cert-manager Issuers ===\n")
		for _, issuer := range issuerList.Items {
			_, _ = fmt.Fprintf(GinkgoWriter, "Issuer: %s/%s\n", issuer.Namespace, issuer.Name)
			for _, cond := range issuer.Status.Conditions {
				_, _ = fmt.Fprintf(GinkgoWriter, "  - %s: %s (%s: %s)\n",
					cond.Type, cond.Status, cond.Reason, cond.Message)
			}
		}
	}

	// List all ClusterIssuers
	clusterIssuerList := &certmanagerv1.ClusterIssuerList{}
	if err := k8sClient.List(ctx, clusterIssuerList); err == nil {
		_, _ = fmt.Fprintf(GinkgoWriter, "\n=== cert-manager ClusterIssuers ===\n")
		for _, issuer := range clusterIssuerList.Items {
			_, _ = fmt.Fprintf(GinkgoWriter, "ClusterIssuer: %s\n", issuer.Name)
			for _, cond := range issuer.Status.Conditions {
				_, _ = fmt.Fprintf(GinkgoWriter, "  - %s: %s (%s: %s)\n",
					cond.Type, cond.Status, cond.Reason, cond.Message)
			}
		}
	}

	// List all Certificates across all namespaces
	certList := &certmanagerv1.CertificateList{}
	if err := k8sClient.List(ctx, certList); err == nil {
		_, _ = fmt.Fprintf(GinkgoWriter, "\n=== cert-manager Certificates ===\n")
		for _, cert := range certList.Items {
			_, _ = fmt.Fprintf(GinkgoWriter, "Certificate: %s/%s (Secret: %s)\n",
				cert.Namespace, cert.Name, cert.Spec.SecretName)
			_, _ = fmt.Fprintf(GinkgoWriter, "  IssuerRef: %s/%s\n", cert.Spec.IssuerRef.Kind, cert.Spec.IssuerRef.Name)
			for _, cond := range cert.Status.Conditions {
				_, _ = fmt.Fprintf(GinkgoWriter, "  - %s: %s (%s: %s)\n",
					cond.Type, cond.Status, cond.Reason, cond.Message)
			}
		}
	}
}
