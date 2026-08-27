/*
Copyright 2026. The Jumpstarter Authors.

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
	"context"
	"fmt"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/api/meta"
	"k8s.io/apimachinery/pkg/api/resource"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/internal/controller/jumpstarter/endpoints"
)

var _ = Describe("Router Lifecycle", func() {
	const crName = "test-router"

	var crNamespace string
	ctx := context.Background()

	makeJumpstarterSpec := func(replicas *int32) operatorv1alpha1.JumpstarterSpec {
		return operatorv1alpha1.JumpstarterSpec{
			BaseDomain: "example.com",
			CertManager: operatorv1alpha1.CertManagerConfig{
				Enabled: false,
			},
			Controller: operatorv1alpha1.ControllerConfig{
				Image:    "quay.io/jumpstarter/jumpstarter:latest",
				Replicas: ptr.To(int32(1)),
				GRPC: operatorv1alpha1.GRPCConfig{
					Endpoints: []operatorv1alpha1.Endpoint{{Address: "controller"}},
				},
			},
			Routers: operatorv1alpha1.RoutersConfig{
				Image:           "quay.io/jumpstarter/jumpstarter:latest",
				ImagePullPolicy: corev1.PullIfNotPresent,
				Replicas:        replicas,
				GRPC: operatorv1alpha1.GRPCConfig{
					Endpoints: []operatorv1alpha1.Endpoint{{Address: "router"}},
				},
			},
		}
	}

	newReconciler := func() *JumpstarterReconciler {
		return &JumpstarterReconciler{
			Client:             k8sClient,
			Scheme:             k8sClient.Scheme(),
			EndpointReconciler: endpoints.NewReconciler(k8sClient, k8sClient.Scheme(), cfg),
		}
	}

	doReconcile := func() {
		_, err := newReconciler().Reconcile(ctx, reconcile.Request{
			NamespacedName: types.NamespacedName{Name: crName, Namespace: crNamespace},
		})
		Expect(err).NotTo(HaveOccurred())
	}

	routerDeploymentName := func(index int) string {
		return fmt.Sprintf("%s-router-%d", crName, index)
	}

	routerServiceName := func(index int) string {
		return fmt.Sprintf("%s-router-%d", crName, index)
	}

	BeforeEach(func() {
		ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "router-test-"}}
		Expect(k8sClient.Create(ctx, ns)).To(Succeed())
		crNamespace = ns.Name
	})

	AfterEach(func() {
		_ = k8sClient.Delete(ctx, &corev1.Namespace{
			ObjectMeta: metav1.ObjectMeta{Name: crNamespace},
		})
	})

	It("creates a router Deployment and Service for replicas=1", func() {
		By("creating a Jumpstarter CR with 1 router replica")
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       makeJumpstarterSpec(ptr.To(int32(1))),
		})).To(Succeed())

		doReconcile()

		By("verifying the router Deployment exists")
		dep := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: routerDeploymentName(0), Namespace: crNamespace,
		}, dep)).To(Succeed())

		By("verifying the router Service exists")
		svc := &corev1.Service{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: routerServiceName(0), Namespace: crNamespace,
		}, svc)).To(Succeed())
		Expect(svc.Spec.Type).To(Equal(corev1.ServiceTypeClusterIP))
		Expect(svc.Spec.Ports).To(HaveLen(1))
		Expect(svc.Spec.Ports[0].Port).To(Equal(int32(8083)))
	})

	It("sets correct labels on the router Deployment", func() {
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       makeJumpstarterSpec(ptr.To(int32(1))),
		})).To(Succeed())

		doReconcile()

		dep := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: routerDeploymentName(0), Namespace: crNamespace,
		}, dep)).To(Succeed())

		Expect(dep.Labels).To(HaveKeyWithValue("component", "router"))
		Expect(dep.Labels).To(HaveKeyWithValue("app", fmt.Sprintf("%s-router-0", crName)))
		Expect(dep.Labels).To(HaveKeyWithValue("router", crName))
		Expect(dep.Labels).To(HaveKeyWithValue("router-index", "0"))
	})

	It("uses the image and imagePullPolicy from the spec", func() {
		spec := makeJumpstarterSpec(ptr.To(int32(1)))
		spec.Routers.Image = "quay.io/jumpstarter/jumpstarter:v1.2.3"
		spec.Routers.ImagePullPolicy = corev1.PullAlways
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       spec,
		})).To(Succeed())

		doReconcile()

		dep := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: routerDeploymentName(0), Namespace: crNamespace,
		}, dep)).To(Succeed())

		container := dep.Spec.Template.Spec.Containers[0]
		Expect(container.Image).To(Equal("quay.io/jumpstarter/jumpstarter:v1.2.3"))
		Expect(container.ImagePullPolicy).To(Equal(corev1.PullAlways))
	})

	It("sets required environment variables on the router container", func() {
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       makeJumpstarterSpec(ptr.To(int32(1))),
		})).To(Succeed())

		doReconcile()

		dep := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: routerDeploymentName(0), Namespace: crNamespace,
		}, dep)).To(Succeed())

		envNames := make([]string, 0)
		for _, e := range dep.Spec.Template.Spec.Containers[0].Env {
			envNames = append(envNames, e.Name)
		}
		Expect(envNames).To(ContainElements("GRPC_ROUTER_ENDPOINT", "ROUTER_KEY", "NAMESPACE"))
	})

	It("applies default resource requests and limits when none are specified", func() {
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       makeJumpstarterSpec(ptr.To(int32(1))),
		})).To(Succeed())

		doReconcile()

		dep := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: routerDeploymentName(0), Namespace: crNamespace,
		}, dep)).To(Succeed())

		res := dep.Spec.Template.Spec.Containers[0].Resources
		Expect(res.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("100m")))
		Expect(res.Requests).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("256Mi")))
		Expect(res.Limits).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("1")))
		Expect(res.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("512Mi")))
	})

	It("applies security context: RunAsNonRoot and drop ALL capabilities", func() {
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       makeJumpstarterSpec(ptr.To(int32(1))),
		})).To(Succeed())

		doReconcile()

		dep := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: routerDeploymentName(0), Namespace: crNamespace,
		}, dep)).To(Succeed())

		podSC := dep.Spec.Template.Spec.SecurityContext
		Expect(podSC).NotTo(BeNil())
		Expect(podSC.RunAsNonRoot).NotTo(BeNil())
		Expect(*podSC.RunAsNonRoot).To(BeTrue())

		containerSC := dep.Spec.Template.Spec.Containers[0].SecurityContext
		Expect(containerSC).NotTo(BeNil())
		Expect(containerSC.AllowPrivilegeEscalation).NotTo(BeNil())
		Expect(*containerSC.AllowPrivilegeEscalation).To(BeFalse())
		Expect(containerSC.Capabilities).NotTo(BeNil())
		Expect(containerSC.Capabilities.Drop).To(ContainElement(corev1.Capability("ALL")))
	})

	It("creates one Deployment and Service per replica when replicas=3", func() {
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       makeJumpstarterSpec(ptr.To(int32(3))),
		})).To(Succeed())

		doReconcile()

		for i := 0; i < 3; i++ {
			dep := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: routerDeploymentName(i), Namespace: crNamespace,
			}, dep)).To(Succeed(), "Deployment for router-%d should exist", i)
			Expect(dep.Labels).To(HaveKeyWithValue("router-index", fmt.Sprintf("%d", i)))

			svc := &corev1.Service{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: routerServiceName(i), Namespace: crNamespace,
			}, svc)).To(Succeed(), "Service for router-%d should exist", i)
		}
	})

	It("scales up: adds new Deployments and Services when replicas increases", func() {
		By("creating with 1 router replica")
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       makeJumpstarterSpec(ptr.To(int32(1))),
		})).To(Succeed())
		doReconcile()

		By("scaling up to 3 replicas")
		js := &operatorv1alpha1.Jumpstarter{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
		js.Spec.Routers.Replicas = ptr.To(int32(3))
		Expect(k8sClient.Update(ctx, js)).To(Succeed())
		doReconcile()

		By("verifying all 3 Deployments and Services exist")
		for i := 0; i < 3; i++ {
			dep := &appsv1.Deployment{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: routerDeploymentName(i), Namespace: crNamespace,
			}, dep)).To(Succeed(), "Deployment router-%d should exist after scale-up", i)

			svc := &corev1.Service{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{
				Name: routerServiceName(i), Namespace: crNamespace,
			}, svc)).To(Succeed(), "Service router-%d should exist after scale-up", i)
		}
	})

	It("scales down: deletes excess Deployments and Services when replicas decreases", func() {
		By("creating with 3 router replicas")
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       makeJumpstarterSpec(ptr.To(int32(3))),
		})).To(Succeed())
		doReconcile()

		By("scaling down to 1 replica")
		js := &operatorv1alpha1.Jumpstarter{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
		js.Spec.Routers.Replicas = ptr.To(int32(1))
		Expect(k8sClient.Update(ctx, js)).To(Succeed())
		doReconcile()

		By("verifying router-0 still exists")
		dep := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: routerDeploymentName(0), Namespace: crNamespace,
		}, dep)).To(Succeed())

		By("verifying router-1 and router-2 Deployments are deleted")
		for i := 1; i < 3; i++ {
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name: routerDeploymentName(i), Namespace: crNamespace,
			}, dep)
			Expect(errors.IsNotFound(err)).To(BeTrue(), "Deployment router-%d should be deleted", i)
		}

		By("verifying router-1 and router-2 Services are deleted")
		svc := &corev1.Service{}
		for i := 1; i < 3; i++ {
			err := k8sClient.Get(ctx, types.NamespacedName{
				Name: routerServiceName(i), Namespace: crNamespace,
			}, svc)
			Expect(errors.IsNotFound(err)).To(BeTrue(), "Service router-%d should be deleted", i)
		}
	})

	It("updates the Deployment when the image changes", func() {
		By("creating with initial image")
		spec := makeJumpstarterSpec(ptr.To(int32(1)))
		spec.Routers.Image = "quay.io/jumpstarter/jumpstarter:v1"
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       spec,
		})).To(Succeed())
		doReconcile()

		dep := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: routerDeploymentName(0), Namespace: crNamespace,
		}, dep)).To(Succeed())
		Expect(dep.Spec.Template.Spec.Containers[0].Image).To(Equal("quay.io/jumpstarter/jumpstarter:v1"))

		By("updating the image in the CR")
		js := &operatorv1alpha1.Jumpstarter{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
		js.Spec.Routers.Image = "quay.io/jumpstarter/jumpstarter:v2"
		Expect(k8sClient.Update(ctx, js)).To(Succeed())
		doReconcile()

		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: routerDeploymentName(0), Namespace: crNamespace,
		}, dep)).To(Succeed())
		Expect(dep.Spec.Template.Spec.Containers[0].Image).To(Equal("quay.io/jumpstarter/jumpstarter:v2"))
	})

	It("reports RouterDeploymentsReady=False when Deployment is not Available yet", func() {
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       makeJumpstarterSpec(ptr.To(int32(1))),
		})).To(Succeed())

		doReconcile()

		js := &operatorv1alpha1.Jumpstarter{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
		cond := meta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeRouterDeploymentsReady)
		Expect(cond).NotTo(BeNil(), "RouterDeploymentsReady condition should be set")
		Expect(cond.Status).To(Equal(metav1.ConditionFalse),
			"should be False while Deployment is not Available")
	})

	It("reports RouterDeploymentsReady=True when all Deployments become Available", func() {
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       makeJumpstarterSpec(ptr.To(int32(1))),
		})).To(Succeed())

		doReconcile()

		By("marking the router Deployment as Available")
		dep := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: routerDeploymentName(0), Namespace: crNamespace,
		}, dep)).To(Succeed())
		dep.Status.Conditions = []appsv1.DeploymentCondition{
			{Type: appsv1.DeploymentAvailable, Status: corev1.ConditionTrue, Reason: "MinimumReplicasAvailable"},
		}
		Expect(k8sClient.Status().Update(ctx, dep)).To(Succeed())

		doReconcile()

		js := &operatorv1alpha1.Jumpstarter{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
		cond := meta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeRouterDeploymentsReady)
		Expect(cond).NotTo(BeNil())
		Expect(cond.Status).To(Equal(metav1.ConditionTrue))
	})

	It("reports RouterDeploymentsReady=False when any replica Deployment is not Available", func() {
		By("creating with 2 replicas")
		Expect(k8sClient.Create(ctx, &operatorv1alpha1.Jumpstarter{
			ObjectMeta: metav1.ObjectMeta{Name: crName, Namespace: crNamespace},
			Spec:       makeJumpstarterSpec(ptr.To(int32(2))),
		})).To(Succeed())

		doReconcile()

		By("marking only router-0 as Available (router-1 is not)")
		dep := &appsv1.Deployment{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{
			Name: routerDeploymentName(0), Namespace: crNamespace,
		}, dep)).To(Succeed())
		dep.Status.Conditions = []appsv1.DeploymentCondition{
			{Type: appsv1.DeploymentAvailable, Status: corev1.ConditionTrue, Reason: "MinimumReplicasAvailable"},
		}
		Expect(k8sClient.Status().Update(ctx, dep)).To(Succeed())

		doReconcile()

		js := &operatorv1alpha1.Jumpstarter{}
		Expect(k8sClient.Get(ctx, types.NamespacedName{Name: crName, Namespace: crNamespace}, js)).To(Succeed())
		cond := meta.FindStatusCondition(js.Status.Conditions, operatorv1alpha1.ConditionTypeRouterDeploymentsReady)
		Expect(cond).NotTo(BeNil())
		Expect(cond.Status).To(Equal(metav1.ConditionFalse),
			"overall condition should be False when any replica is not Available")
	})
})

var _ = Describe("defaultRouterResources", func() {
	It("returns defaults when spec is empty", func() {
		result := defaultRouterResources(corev1.ResourceRequirements{})
		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("100m")))
		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("256Mi")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("1")))
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("512Mi")))
	})

	It("returns user-specified resources unchanged when requests are set", func() {
		custom := corev1.ResourceRequirements{
			Requests: corev1.ResourceList{
				corev1.ResourceCPU: resource.MustParse("200m"),
			},
		}
		result := defaultRouterResources(custom)
		Expect(result.Requests).To(HaveKeyWithValue(corev1.ResourceCPU, resource.MustParse("200m")))
		Expect(result.Limits).To(BeNil())
	})

	It("returns user-specified resources unchanged when limits are set", func() {
		custom := corev1.ResourceRequirements{
			Limits: corev1.ResourceList{
				corev1.ResourceMemory: resource.MustParse("1Gi"),
			},
		}
		result := defaultRouterResources(custom)
		Expect(result.Limits).To(HaveKeyWithValue(corev1.ResourceMemory, resource.MustParse("1Gi")))
		Expect(result.Requests).To(BeNil())
	})
})
