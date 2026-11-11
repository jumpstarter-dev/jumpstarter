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
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"sigs.k8s.io/controller-runtime/pkg/client"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/internal/controller/jumpstarter/endpoints"
)

// Use envtest: the fake client does not enforce cluster-wide NodePort allocation.
var _ = Describe("NodePort reconciliation", func() {
	ctx := context.Background()
	var r *JumpstarterReconciler
	var js *operatorv1alpha1.Jumpstarter
	nodePort := func(port int32) operatorv1alpha1.Endpoint {
		return operatorv1alpha1.Endpoint{
			Address:  fmt.Sprintf("example.com:%d", port),
			NodePort: &operatorv1alpha1.NodePortConfig{Enabled: true, Port: port},
		}
	}
	reconcile := func() error {
		if err := r.reconcileServices(ctx, js); err != nil {
			return err
		}
		return r.reconcileTelemetryServiceStage(ctx, js)
	}
	service := func(name string) *corev1.Service {
		svc := &corev1.Service{}
		Expect(k8sClient.Get(ctx, client.ObjectKey{Namespace: js.Namespace, Name: name}, svc)).To(Succeed())
		return svc
	}

	BeforeEach(func() {
		ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "nodeports-"}}
		Expect(k8sClient.Create(ctx, ns)).To(Succeed())
		DeferCleanup(func() {
			// envtest has no garbage collector: explicitly release the ports.
			Expect(k8sClient.DeleteAllOf(ctx, &corev1.Service{}, client.InNamespace(ns.Name))).To(Succeed())
			Expect(k8sClient.Delete(ctx, ns)).To(Succeed())
		})
		js = phase3TelemetryJS(ns.Name)
		js.UID = ns.UID
		js.Spec.Routers.Replicas = new(int32(2))
		r = &JumpstarterReconciler{Client: k8sClient, Scheme: k8sClient.Scheme(), EndpointReconciler: &endpoints.Reconciler{
			Client: k8sClient, Scheme: k8sClient.Scheme(),
		}}
	})

	DescribeTable("releases allocations before endpoint updates", func(component, change string) {
		configured := &js.Spec.Telemetry.GRPC.Endpoints
		if component == "router" {
			configured = &js.Spec.Routers.GRPC.Endpoints
		}
		*configured = []operatorv1alpha1.Endpoint{nodePort(30050), nodePort(30060)}
		Expect(reconcile()).To(Succeed())
		switch change {
		case "remove":
			*configured = (*configured)[1:]
		case "swap":
			(*configured)[0], (*configured)[1] = (*configured)[1], (*configured)[0]
		case "shift":
			// Router replica 0 must claim the port currently held by replica 1.
			(*configured)[0].NodePort.Port++
		}
		Expect(reconcile()).To(Succeed())
		Expect(reconcile()).To(Succeed(), "a second reconcile must be stable")
		for i, endpoint := range *configured {
			if component == "telemetry" {
				Expect(service(fmt.Sprintf("jumpstarter-telemetry-grpc-%d-np", i)).Spec.Ports[0].NodePort).
					To(Equal(endpoint.NodePort.Port))
			} else {
				for replica := range int32(2) {
					name := fmt.Sprintf("js-router-%d", replica)
					if i > 0 {
						name += fmt.Sprintf("-%d", i)
					}
					Expect(service(name + "-np").Spec.Ports[0].NodePort).To(Equal(endpoint.NodePort.Port + replica))
				}
			}
		}
		list := &corev1.ServiceList{}
		Expect(k8sClient.List(ctx, list, client.InNamespace(js.Namespace))).To(Succeed())
		count := 0
		for _, svc := range list.Items {
			if svc.Spec.Type == corev1.ServiceTypeNodePort {
				count++
			}
		}
		want := len(*configured)
		if component == "router" {
			want *= 2
		}
		Expect(count).To(Equal(want))
	},
		Entry("telemetry removal", "telemetry", "remove"),
		Entry("telemetry reorder", "telemetry", "swap"),
		Entry("router removal", "router", "remove"),
		Entry("router reorder", "router", "swap"),
		Entry("router base port change", "router", "shift"),
	)

	It("swaps ports across controller, login, router and telemetry", func() {
		js.Spec.Routers.Replicas = new(int32(1))
		js.Spec.Controller.GRPC.Endpoints = []operatorv1alpha1.Endpoint{nodePort(30050)}
		js.Spec.Controller.Login.Endpoints = []operatorv1alpha1.Endpoint{nodePort(30051)}
		js.Spec.Routers.GRPC.Endpoints = []operatorv1alpha1.Endpoint{nodePort(30052)}
		js.Spec.Telemetry.GRPC.Endpoints = []operatorv1alpha1.Endpoint{nodePort(30053)}
		Expect(reconcile()).To(Succeed())
		js.Spec.Controller.GRPC.Endpoints[0] = nodePort(30053)
		js.Spec.Controller.Login.Endpoints[0] = nodePort(30050)
		js.Spec.Routers.GRPC.Endpoints[0] = nodePort(30051)
		js.Spec.Telemetry.GRPC.Endpoints[0] = nodePort(30052)
		Expect(reconcile()).To(Succeed())
		for name, port := range map[string]int32{
			"controller-grpc-np": 30053, "login-np": 30050,
			"js-router-0-np": 30051, "jumpstarter-telemetry-grpc-0-np": 30052,
		} {
			Expect(service(name).Spec.Ports[0].NodePort).To(Equal(port))
		}
	})

	It("preserves automatic allocations and suspended routers", func() {
		js.Spec.Controller.GRPC.Endpoints = []operatorv1alpha1.Endpoint{nodePort(0)}
		js.Spec.Routers.GRPC.Endpoints = []operatorv1alpha1.Endpoint{nodePort(30050)}
		Expect(reconcile()).To(Succeed())
		controller := service("controller-grpc-np")
		router := service("js-router-0-np")
		js.Spec.Routers.Replicas = new(int32(0))
		js.Spec.Routers.GRPC.Endpoints = nil
		Expect(reconcile()).To(Succeed())
		Expect(service("controller-grpc-np").UID).To(Equal(controller.UID))
		Expect(service("controller-grpc-np").Spec.Ports[0].NodePort).To(Equal(controller.Spec.Ports[0].NodePort))
		Expect(service("js-router-0-np").UID).To(Equal(router.UID))
	})

	It("does not delete unowned Services when releasing obsolete allocations", func() {
		js.Spec.Controller.GRPC.Endpoints = []operatorv1alpha1.Endpoint{nodePort(30050)}
		js.Spec.Controller.Login.Endpoints = []operatorv1alpha1.Endpoint{nodePort(30051)}
		Expect(reconcile()).To(Succeed())
		unowned := service("login-np")
		unowned.OwnerReferences = nil
		Expect(k8sClient.Update(ctx, unowned)).To(Succeed())
		js.Spec.Controller.GRPC.Endpoints = nil
		js.Spec.Controller.Login.Endpoints = nil
		Expect(reconcile()).To(Succeed())
		Expect(service("login-np").UID).To(Equal(unowned.UID))
		Expect(errors.IsNotFound(k8sClient.Get(ctx, client.ObjectKey{Namespace: js.Namespace, Name: "controller-grpc-np"}, &corev1.Service{}))).To(BeTrue())
		list := &corev1.ServiceList{}
		Expect(k8sClient.List(ctx, list, client.InNamespace(js.Namespace))).To(Succeed())
		Expect(list.Items).To(HaveLen(2), "only the unowned and internal telemetry Services remain")
	})

	DescribeTable("rejects conflicting configuration before deleting Services", func(kind string) {
		js.Spec.Controller.GRPC.Endpoints = []operatorv1alpha1.Endpoint{nodePort(30050)}
		js.Spec.Controller.Login.Endpoints = []operatorv1alpha1.Endpoint{nodePort(30051)}
		Expect(reconcile()).To(Succeed())
		original := service("controller-grpc-np")
		js.Spec.Controller.GRPC.Endpoints[0] = nodePort(30052)
		switch kind {
		case "controller":
			js.Spec.Controller.GRPC.Endpoints = append(js.Spec.Controller.GRPC.Endpoints, nodePort(30053))
		case "login":
			js.Spec.Controller.Login.Endpoints = append(js.Spec.Controller.Login.Endpoints, nodePort(30053))
		case "duplicate":
			js.Spec.Controller.Login.Endpoints[0] = nodePort(30052)
		}
		Expect(reconcile()).NotTo(Succeed())
		Expect(service("controller-grpc-np").UID).To(Equal(original.UID))
		Expect(service("controller-grpc-np").Spec.Ports[0].NodePort).To(Equal(int32(30050)))
	}, Entry("multiple controller NodePorts", "controller"), Entry("multiple login NodePorts", "login"), Entry("duplicate requested ports", "duplicate"))
})
