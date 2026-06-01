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
	"fmt"
	"math"
	"os"
	"strconv"
	"strings"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/intstr"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/deploy/operator/api/v1alpha1"
)

// These tests verify the router deployment configuration that prevents the
// failure patterns described in issue #28: updating the router kills existing
// connections because the deployment lacks proper rollout strategy, health
// probes, and termination grace period.
var _ = Describe("Router graceful shutdown regression (issue #28)", Ordered, Pending, func() {
	const baseDomain = "graceful.127.0.0.1.nip.io"
	const jumpstarterName = "jumpstarter-graceful"
	var gracefulTestNamespace string

	BeforeAll(func() {
		gracefulTestNamespace = CreateTestNamespace()

		By("creating a Jumpstarter CR to produce a router deployment")
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
`, jumpstarterName, gracefulTestNamespace, baseDomain, image, baseDomain, image, baseDomain)

		err := applyYAML(jumpstarterYAML)
		Expect(err).NotTo(HaveOccurred(), "Failed to create Jumpstarter CR")

		By("waiting for the router deployment to exist")
		Eventually(func(g Gomega) {
			deployment := &appsv1.Deployment{}
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name:      fmt.Sprintf("%s-router-0", jumpstarterName),
				Namespace: gracefulTestNamespace,
			}, deployment)
			g.Expect(err).NotTo(HaveOccurred())
		}, 2*time.Minute).Should(Succeed())
	})

	getRouterDeployment := func() *appsv1.Deployment {
		deployment := &appsv1.Deployment{}
		err := k8sClient.Get(ctx, types.NamespacedName{
			Name:      fmt.Sprintf("%s-router-0", jumpstarterName),
			Namespace: gracefulTestNamespace,
		}, deployment)
		Expect(err).NotTo(HaveOccurred())
		return deployment
	}

	getRouterContainer := func(deployment *appsv1.Deployment) corev1.Container {
		Expect(deployment.Spec.Template.Spec.Containers).NotTo(BeEmpty(),
			"router deployment should have at least one container")
		return deployment.Spec.Template.Spec.Containers[0]
	}

	Context("Rollout strategy prevents connection loss during updates", func() {
		It("should use RollingUpdate strategy type", func() {
			deployment := getRouterDeployment()
			Expect(deployment.Spec.Strategy.Type).To(
				Equal(appsv1.RollingUpdateDeploymentStrategyType),
				"router deployment must use RollingUpdate strategy to avoid downtime")
		})

		It("should set maxUnavailable to 0 so the old router stays running until the new one is ready", func() {
			deployment := getRouterDeployment()
			Expect(deployment.Spec.Strategy.RollingUpdate).NotTo(BeNil(),
				"RollingUpdate configuration must be specified")
			Expect(deployment.Spec.Strategy.RollingUpdate.MaxUnavailable).NotTo(BeNil(),
				"maxUnavailable must be explicitly set")

			maxUnavailable := deployment.Spec.Strategy.RollingUpdate.MaxUnavailable
			Expect(maxUnavailable).To(Equal(&intstr.IntOrString{Type: intstr.Int, IntVal: 0}),
				"maxUnavailable must be 0 so existing router pods are not terminated "+
					"before their replacement is ready -- otherwise active connections are dropped")
		})

		It("should set maxSurge to at least 1 so the new router starts before the old one stops", func() {
			deployment := getRouterDeployment()
			Expect(deployment.Spec.Strategy.RollingUpdate).NotTo(BeNil(),
				"RollingUpdate configuration must be specified")
			Expect(deployment.Spec.Strategy.RollingUpdate.MaxSurge).NotTo(BeNil(),
				"maxSurge must be explicitly set")

			maxSurge := deployment.Spec.Strategy.RollingUpdate.MaxSurge
			if maxSurge.Type == intstr.Int {
				Expect(maxSurge.IntVal).To(BeNumerically(">=", 1),
					"maxSurge must be >= 1 so a new router pod is created before the old one is terminated")
			} else {
				pctStr := strings.TrimSuffix(maxSurge.StrVal, "%")
				pct, err := strconv.ParseFloat(pctStr, 64)
				Expect(err).NotTo(HaveOccurred(), "maxSurge percentage must be a valid number")

				replicas := int32(1)
				if deployment.Spec.Replicas != nil {
					replicas = *deployment.Spec.Replicas
				}
				effectiveSurge := int(math.Ceil(float64(replicas) * pct / 100.0))
				Expect(effectiveSurge).To(BeNumerically(">=", 1),
					"maxSurge percentage must resolve to at least 1 pod of surge "+
						"(got %q with %d replicas = %d effective surge)",
					maxSurge.StrVal, replicas, effectiveSurge)
			}
		})
	})

	Context("Health probes allow the cluster to route traffic correctly", func() {
		It("should have a readiness probe so the cluster stops sending new connections to a stopping router", func() {
			deployment := getRouterDeployment()
			container := getRouterContainer(deployment)
			Expect(container.ReadinessProbe).NotTo(BeNil(),
				"router container must have a readiness probe -- without it the cluster "+
					"cannot detect when a router is shutting down and will continue sending "+
					"new connections to it")
		})

		It("should have a liveness probe so the cluster can detect and restart a stuck router", func() {
			deployment := getRouterDeployment()
			container := getRouterContainer(deployment)
			Expect(container.LivenessProbe).NotTo(BeNil(),
				"router container must have a liveness probe -- without it a hung router "+
					"will never be restarted, silently dropping connections")
		})
	})

	Context("Connection draining mechanism", func() {
		It("should have a preStop hook to initiate connection draining", func() {
			deployment := getRouterDeployment()
			container := getRouterContainer(deployment)
			Expect(container.Lifecycle).NotTo(BeNil(),
				"lifecycle hooks must be configured for graceful shutdown")
			Expect(container.Lifecycle.PreStop).NotTo(BeNil(),
				"preStop hook is needed to drain connections before the container "+
					"receives SIGTERM -- without it the router stops accepting new "+
					"connections abruptly when killed")
		})
	})

	Context("Termination grace period allows existing connections to drain", func() {
		It("should have terminationGracePeriodSeconds long enough for connections to finish", func() {
			deployment := getRouterDeployment()
			gracePeriod := deployment.Spec.Template.Spec.TerminationGracePeriodSeconds
			Expect(gracePeriod).NotTo(BeNil(),
				"terminationGracePeriodSeconds must be set explicitly")

			// Issue #28 recommends 1-2 hours (3600-7200 seconds) to allow
			// long-running lease connections to complete before the router is killed.
			Expect(*gracePeriod).To(BeNumerically(">=", 3600),
				"terminationGracePeriodSeconds must be at least 3600 (1 hour) to allow "+
					"existing long-running connections to complete before the router is "+
					"forcibly terminated -- 30 seconds is insufficient for lease-bound sessions")
		})
	})

	Context("Parity with controller deployment health configuration", func() {
		It("should configure health probes on the same port and paths as the controller", func() {
			By("fetching the controller deployment for comparison")
			controllerDeployment := &appsv1.Deployment{}
			Eventually(func(g Gomega) {
				err := k8sClient.Get(ctx, types.NamespacedName{
					Name:      fmt.Sprintf("%s-controller", jumpstarterName),
					Namespace: gracefulTestNamespace,
				}, controllerDeployment)
				g.Expect(err).NotTo(HaveOccurred())
			}, 2*time.Minute).Should(Succeed())

			controllerContainer := controllerDeployment.Spec.Template.Spec.Containers[0]
			Expect(controllerContainer.ReadinessProbe).NotTo(BeNil(),
				"controller should have readiness probe for comparison baseline")
			Expect(controllerContainer.LivenessProbe).NotTo(BeNil(),
				"controller should have liveness probe for comparison baseline")

			By("verifying the router uses the same health configuration as the controller")
			routerDeployment := getRouterDeployment()
			routerContainer := getRouterContainer(routerDeployment)

			Expect(routerContainer.ReadinessProbe).NotTo(BeNil(),
				"router must have a readiness probe like the controller does")
			Expect(routerContainer.LivenessProbe).NotTo(BeNil(),
				"router must have a liveness probe like the controller does")

			if controllerContainer.ReadinessProbe.HTTPGet != nil {
				Expect(routerContainer.ReadinessProbe.HTTPGet).NotTo(BeNil(),
					"router readiness probe should use HTTPGet like the controller")
				Expect(routerContainer.ReadinessProbe.HTTPGet.Path).To(
					Equal(controllerContainer.ReadinessProbe.HTTPGet.Path),
					"router readiness probe path should match controller readiness probe path")
			}

			if controllerContainer.LivenessProbe.HTTPGet != nil {
				Expect(routerContainer.LivenessProbe.HTTPGet).NotTo(BeNil(),
					"router liveness probe should use HTTPGet like the controller")
				Expect(routerContainer.LivenessProbe.HTTPGet.Path).To(
					Equal(controllerContainer.LivenessProbe.HTTPGet.Path),
					"router liveness probe path should match controller liveness probe path")
			}

			routerHealthPort := int32(0)
			for _, port := range routerContainer.Ports {
				if port.Name == "health" {
					routerHealthPort = port.ContainerPort
					break
				}
			}
			Expect(routerHealthPort).NotTo(BeZero(),
				"router should expose a health port for probes to target")
		})
	})

	AfterAll(func() {
		By("cleaning up the Jumpstarter CR")
		jumpstarter := &operatorv1alpha1.Jumpstarter{}
		err := k8sClient.Get(ctx, types.NamespacedName{
			Name:      jumpstarterName,
			Namespace: gracefulTestNamespace,
		}, jumpstarter)
		if err == nil {
			Expect(k8sClient.Delete(ctx, jumpstarter)).To(Succeed())
			Eventually(func(g Gomega) {
				getErr := k8sClient.Get(ctx, types.NamespacedName{
					Name:      jumpstarterName,
					Namespace: gracefulTestNamespace,
				}, jumpstarter)
				g.Expect(apierrors.IsNotFound(getErr)).To(BeTrue())
			}, 2*time.Minute).Should(Succeed())
		}

		DeleteTestNamespace(gracefulTestNamespace)
	})
})
