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
	"fmt"
	"os"
	"time"

	certmanagerv1 "github.com/cert-manager/cert-manager/pkg/apis/certmanager/v1"
	cmmeta "github.com/cert-manager/cert-manager/pkg/apis/meta/v1"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

const (
	telemetryE2EServiceName = "jumpstarter-telemetry"
	telemetryE2EPort        = 9093
	telemetryE2ECertSuffix  = "-telemetry-tls"
)

func controllerImage() string {
	if image := os.Getenv("IMG"); image != "" {
		return image
	}
	return defaultControllerImage
}

func telemetryImage() string {
	if image := os.Getenv("TELEMETRY_IMG"); image != "" {
		return image
	}
	return "quay.io/jumpstarter-dev/jumpstarter-telemetry:latest"
}

func telemetryDeploymentName(jumpstarterName string) string {
	return jumpstarterName + "-telemetry"
}

func telemetryCertName(jumpstarterName string) string {
	return jumpstarterName + telemetryE2ECertSuffix
}

func telemetryYAMLBlock() string {
	return fmt.Sprintf(`  telemetry:
    enabled: true
    image: %s
    imagePullPolicy: IfNotPresent
    resources:
      requests:
        cpu: 50m
        memory: 128Mi
`, telemetryImage())
}

var _ = Describe("Telemetry lifecycle", Ordered, func() {
	const baseDomain = "telemetry.127.0.0.1.nip.io"
	const jumpstarterName = "jumpstarter-telemetry"
	var telemetryTestNamespace string

	BeforeAll(func() {
		telemetryTestNamespace = CreateTestNamespace()
	})

	AfterAll(func() {
		DeleteTestNamespace(telemetryTestNamespace)
	})

	It("should deploy telemetry deployment and service when enabled", func() {
		image := controllerImage()

		jumpstarterYAML := fmt.Sprintf(`apiVersion: operator.jumpstarter.dev/v1alpha1
kind: Jumpstarter
metadata:
  name: %s
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
    resources:
      requests:
        cpu: 100m
        memory: 100Mi
    grpc:
      endpoints:
        - address: grpc.%s:8082
          nodeport:
            enabled: true
            port: 30080
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
            port: 30081
%s`, jumpstarterName, telemetryTestNamespace, baseDomain, image, baseDomain, image, baseDomain, telemetryYAMLBlock())

		Expect(applyYAML(jumpstarterYAML)).To(Succeed())

		Eventually(func(g Gomega) {
			dep := &appsv1.Deployment{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      telemetryDeploymentName(jumpstarterName),
				Namespace: telemetryTestNamespace,
			}, dep)
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(dep.Spec.Template.Spec.Containers).NotTo(BeEmpty())
			g.Expect(dep.Spec.Template.Spec.Containers[0].Image).To(Equal(telemetryImage()))
			verifyDeploymentHasControllerKey(g, telemetryTestNamespace, telemetryDeploymentName(jumpstarterName))
		}, 2*time.Minute).Should(Succeed())

		Eventually(func(g Gomega) {
			svc := &corev1.Service{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      telemetryE2EServiceName,
				Namespace: telemetryTestNamespace,
			}, svc)
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(svc.Spec.Ports[0].Port).To(Equal(int32(telemetryE2EPort)))
			g.Expect(svc.Spec.Selector).To(HaveKeyWithValue("app", "jumpstarter-telemetry"))
		}, 2*time.Minute).Should(Succeed())

		Eventually(func(g Gomega) {
			sa := &corev1.ServiceAccount{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      jumpstarterName + "-telemetry",
				Namespace: telemetryTestNamespace,
			}, sa)
			g.Expect(err).NotTo(HaveOccurred())
		}, 2*time.Minute).Should(Succeed())
	})

	It("should include telemetry configuration in the controller ConfigMap", func() {
		Eventually(func(g Gomega) {
			cm := &corev1.ConfigMap{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      "jumpstarter-controller",
				Namespace: telemetryTestNamespace,
			}, cm)
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(cm.Data["config"]).To(ContainSubstring("telemetry:"))
			g.Expect(cm.Data["config"]).To(ContainSubstring("enabled: true"))
			g.Expect(cm.Data["config"]).To(ContainSubstring(
				fmt.Sprintf("%s.%s.svc:%d", telemetryE2EServiceName, telemetryTestNamespace, telemetryE2EPort)))
		}, 2*time.Minute).Should(Succeed())
	})

	It("should report TelemetryDeploymentReady when the deployment is available", func() {
		waitForCondition(telemetryTestNamespace, jumpstarterName,
			operatorv1alpha1.ConditionTypeTelemetryDeploymentReady, metav1.ConditionTrue, 5*time.Minute)
	})

	It("should remove telemetry resources when telemetry is disabled", func() {
		js := &operatorv1alpha1.Jumpstarter{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name:      jumpstarterName,
			Namespace: telemetryTestNamespace,
		}, js)).To(Succeed())

		Expect(js.Spec.Telemetry).NotTo(BeNil())
		js.Spec.Telemetry.Enabled = false
		Expect(k8sClient.Update(ctx, js)).To(Succeed())

		Eventually(func(g Gomega) {
			dep := &appsv1.Deployment{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      telemetryDeploymentName(jumpstarterName),
				Namespace: telemetryTestNamespace,
			}, dep)
			g.Expect(apierrors.IsNotFound(err)).To(BeTrue())
		}, 2*time.Minute).Should(Succeed())

		Eventually(func(g Gomega) {
			svc := &corev1.Service{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      telemetryE2EServiceName,
				Namespace: telemetryTestNamespace,
			}, svc)
			g.Expect(apierrors.IsNotFound(err)).To(BeTrue())
		}, 2*time.Minute).Should(Succeed())

		Eventually(func(g Gomega) {
			cm := &corev1.ConfigMap{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      "jumpstarter-controller",
				Namespace: telemetryTestNamespace,
			}, cm)
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(cm.Data["config"]).NotTo(ContainSubstring("telemetry:"))
		}, 2*time.Minute).Should(Succeed())
	})
})

var _ = Describe("Telemetry cert-manager integration", Ordered, func() {
	const baseDomain = "telemetry-tls.127.0.0.1.nip.io"
	const jumpstarterName = "jumpstarter-telemetry-tls"
	var telemetryTLSTestNamespace string

	BeforeAll(func() {
		telemetryTLSTestNamespace = CreateTestNamespace()
	})

	AfterAll(func() {
		DeleteTestNamespace(telemetryTLSTestNamespace)
	})

	It("should deploy jumpstarter with telemetry and cert-manager enabled", func() {
		image := controllerImage()

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
    resources:
      requests:
        cpu: 100m
        memory: 100Mi
    grpc:
      endpoints:
        - address: grpc.%s:8082
          nodeport:
            enabled: true
            port: 30082
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
            port: 30083
%s`, jumpstarterName, telemetryTLSTestNamespace, baseDomain, image, baseDomain, image, baseDomain, telemetryYAMLBlock())

		Expect(applyYAML(jumpstarterYAML)).To(Succeed())
	})

	It("should create the telemetry TLS certificate", func() {
		certName := telemetryCertName(jumpstarterName)
		Eventually(func(g Gomega) {
			cert := &certmanagerv1.Certificate{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      certName,
				Namespace: telemetryTLSTestNamespace,
			}, cert)
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(cert.Spec.IsCA).To(BeFalse())
		}, 2*time.Minute).Should(Succeed())

		Eventually(func(g Gomega) {
			cert := &certmanagerv1.Certificate{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      certName,
				Namespace: telemetryTLSTestNamespace,
			}, cert)
			g.Expect(err).NotTo(HaveOccurred())
			for _, cond := range cert.Status.Conditions {
				if cond.Type == certmanagerv1.CertificateConditionReady {
					g.Expect(cond.Status).To(Equal(cmmeta.ConditionTrue),
						fmt.Sprintf("Certificate %s is not ready: %s", certName, cond.Message))
					return
				}
			}
			g.Expect(false).To(BeTrue(), fmt.Sprintf("Certificate %s has no Ready condition", certName))
		}, 2*time.Minute, 2*time.Second).Should(Succeed())
		verifyTLSSecret(telemetryTLSTestNamespace, certName)
	})

	It("should mount TLS certificates in telemetry deployment", func() {
		deploymentName := telemetryDeploymentName(jumpstarterName)
		Eventually(func(g Gomega) {
			verifyDeploymentHasTLSMount(g, telemetryTLSTestNamespace, deploymentName)
			verifyDeploymentHasControllerKey(g, telemetryTLSTestNamespace, deploymentName)
		}, 2*time.Minute, 2*time.Second).Should(Succeed())
	})

	It("should include telemetry CA certificate in the controller ConfigMap", func() {
		Eventually(func(g Gomega) {
			cm := &corev1.ConfigMap{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      "jumpstarter-controller",
				Namespace: telemetryTLSTestNamespace,
			}, cm)
			g.Expect(err).NotTo(HaveOccurred())
			g.Expect(cm.Data["config"]).To(ContainSubstring("telemetry:"))
			g.Expect(cm.Data["config"]).To(ContainSubstring("certificate:"))
			g.Expect(cm.Data["config"]).To(ContainSubstring("BEGIN CERTIFICATE"))
		}, 2*time.Minute).Should(Succeed())
	})

	It("should report TelemetryDeploymentReady when telemetry is available", func() {
		waitForCondition(telemetryTLSTestNamespace, jumpstarterName,
			operatorv1alpha1.ConditionTypeTelemetryDeploymentReady, metav1.ConditionTrue, 5*time.Minute)
	})

	It("should delete the telemetry Certificate when telemetry is disabled", func() {
		js := &operatorv1alpha1.Jumpstarter{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name:      jumpstarterName,
			Namespace: telemetryTLSTestNamespace,
		}, js)).To(Succeed())

		Expect(js.Spec.Telemetry).NotTo(BeNil())
		js.Spec.Telemetry.Enabled = false
		Expect(k8sClient.Update(ctx, js)).To(Succeed())

		certName := telemetryCertName(jumpstarterName)
		Eventually(func(g Gomega) {
			cert := &certmanagerv1.Certificate{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      certName,
				Namespace: telemetryTLSTestNamespace,
			}, cert)
			g.Expect(apierrors.IsNotFound(err)).To(BeTrue(),
				"telemetry Certificate should be deleted when telemetry is disabled")
		}, 2*time.Minute).Should(Succeed())

		Eventually(func(g Gomega) {
			dep := &appsv1.Deployment{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      telemetryDeploymentName(jumpstarterName),
				Namespace: telemetryTLSTestNamespace,
			}, dep)
			g.Expect(apierrors.IsNotFound(err)).To(BeTrue())
		}, 2*time.Minute).Should(Succeed())

		Eventually(func(g Gomega) {
			js := &operatorv1alpha1.Jumpstarter{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      jumpstarterName,
				Namespace: telemetryTLSTestNamespace,
			}, js)
			g.Expect(err).NotTo(HaveOccurred())
			cond := meta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeTelemetryDeploymentReady)
			g.Expect(cond).To(BeNil())
		}, 2*time.Minute).Should(Succeed())
	})
})
