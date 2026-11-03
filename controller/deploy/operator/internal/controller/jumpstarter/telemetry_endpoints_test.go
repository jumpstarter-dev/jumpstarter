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

	certmanagerv1 "github.com/cert-manager/cert-manager/pkg/apis/certmanager/v1"
	cmmeta "github.com/cert-manager/cert-manager/pkg/apis/meta/v1"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	routev1 "github.com/openshift/api/route/v1"
	corev1 "k8s.io/api/core/v1"
	networkingv1 "k8s.io/api/networking/v1"
	"k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/internal/controller/jumpstarter/endpoints"
)

var _ = Describe("Telemetry endpoints", func() {
	ctx := context.Background()
	var r *JumpstarterReconciler
	var js *operatorv1alpha1.Jumpstarter
	const serviceName = "jumpstarter-telemetry-grpc-0"

	BeforeEach(func() {
		scheme := runtime.NewScheme()
		Expect(corev1.AddToScheme(scheme)).To(Succeed())
		Expect(networkingv1.AddToScheme(scheme)).To(Succeed())
		Expect(routev1.AddToScheme(scheme)).To(Succeed())
		Expect(certmanagerv1.AddToScheme(scheme)).To(Succeed())
		Expect(operatorv1alpha1.AddToScheme(scheme)).To(Succeed())
		js = phase3TelemetryJS("lab")
		js.UID = "telemetry-endpoint-test"
		js.Spec.BaseDomain = "example.com"
		c := fake.NewClientBuilder().WithScheme(scheme).WithObjects(js).Build()
		r = &JumpstarterReconciler{Client: c, Scheme: scheme, EndpointReconciler: &endpoints.Reconciler{
			Client: c, Scheme: scheme, IngressAvailable: true, RouteAvailable: true,
		}}
	})

	DescribeTable("generates a public endpoint using platform defaults", func(routes, ingress bool) {
		endpoints.ApplyEndpointDefaults(&js.Spec, routes, ingress)
		ep := js.Spec.Telemetry.GRPC.Endpoints[0]
		Expect(ep.Address).To(Equal("telemetry.example.com"))
		Expect(telemetryEndpointFor(js)).To(Equal("telemetry.example.com:443"))
		if routes {
			Expect(ep.Route.Enabled).To(BeTrue())
		} else if ingress {
			Expect(ep.Ingress.Enabled).To(BeTrue())
		} else {
			Expect(ep.ClusterIP.Enabled).To(BeTrue())
		}
	}, Entry("OpenShift", true, true), Entry("Kubernetes ingress", false, true), Entry("ClusterIP fallback", false, false))

	It("does not enable telemetry implicitly", func() {
		js.Spec.Telemetry.Enabled = false
		endpoints.ApplyEndpointDefaults(&js.Spec, true, true)
		Expect(js.Spec.Telemetry.GRPC.Endpoints).To(BeEmpty())
	})

	It("honors an explicit endpoint without a baseDomain and uses it for discovery and TLS", func() {
		js.Spec.BaseDomain = ""
		js.Spec.Telemetry.GRPC.Endpoints = []operatorv1alpha1.Endpoint{{
			Address: "telemetry.example.net:9093", NodePort: &operatorv1alpha1.NodePortConfig{Enabled: true, Port: 30015},
		}}
		endpoints.ApplyEndpointDefaults(&js.Spec, true, true)
		Expect(js.Spec.Telemetry.GRPC.Endpoints[0].Route).To(BeNil())
		cfg, err := r.buildConfig(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(cfg.Telemetry.Endpoint).To(Equal("telemetry.example.net:9093"))
		container := createTelemetryDeployment(js, "", telemetryLokiPod{}).Spec.Template.Spec.Containers[0]
		Expect(envValue(container, "GRPC_TELEMETRY_ENDPOINT")).To(Equal(cfg.Telemetry.Endpoint))
		Expect(r.collectTelemetryDNSNames(js, false)).To(Equal([]string{"telemetry.example.net"}))
	})

	It("uses the first endpoint but includes every public address in certificates", func() {
		js.Spec.Telemetry.GRPC.Endpoints = []operatorv1alpha1.Endpoint{
			{Address: "telemetry.example.net:443"}, {Address: "alternate.example.net:8443"},
		}
		Expect(telemetryEndpointFor(js)).To(Equal("telemetry.example.net:443"))
		Expect(r.collectTelemetryDNSNames(js, false)).To(ConsistOf("telemetry.example.net", "alternate.example.net"))
		Expect(r.collectTelemetryDNSNames(js, true)).To(ContainElement("jumpstarter-telemetry.lab.svc"))
	})

	It("issues an external-issuer certificate for the public hostname, without internal names", func() {
		endpoints.ApplyEndpointDefaults(&js.Spec, true, true)
		js.Spec.CertManager.Server = &operatorv1alpha1.ServerCertConfig{
			IssuerRef: &operatorv1alpha1.IssuerReference{Name: "public-ca", Kind: "ClusterIssuer"},
		}
		Expect(r.reconcileTelemetryCertificate(ctx, js, cmmeta.ObjectReference{Name: "public-ca", Kind: "ClusterIssuer"})).To(Succeed())
		cert := &certmanagerv1.Certificate{}
		Expect(r.Get(ctx, client.ObjectKey{Name: GetTelemetryCertSecretName(js), Namespace: js.Namespace}, cert)).To(Succeed())
		Expect(cert.Spec.DNSNames).To(Equal([]string{"telemetry.example.com"}))
	})

	DescribeTable("creates only gRPC exposure", func(ep operatorv1alpha1.Endpoint, suffix string, serviceType corev1.ServiceType, externalPort int32) {
		ep.Address = "telemetry.example.com:443"
		js.Spec.Telemetry.GRPC.Endpoints = []operatorv1alpha1.Endpoint{ep}
		Expect(r.reconcileTelemetryServiceStage(ctx, js)).To(Succeed())
		service := &corev1.Service{}
		Expect(r.Get(ctx, client.ObjectKey{Name: serviceName + suffix, Namespace: js.Namespace}, service)).To(Succeed())
		Expect(service.Spec.Type).To(Equal(serviceType))
		Expect(service.Spec.Ports).To(HaveLen(1))
		Expect(service.Spec.Ports[0].Port).To(Equal(externalPort))
		Expect(service.Spec.Ports[0].TargetPort.IntValue()).To(Equal(telemetryPort))
		Expect(service.Spec.Selector).To(Equal(telemetryLabels(js)))
		if ep.NodePort != nil {
			Expect(service.Spec.Ports[0].NodePort).To(Equal(ep.NodePort.Port))
			Expect(service.Annotations).To(HaveKeyWithValue("example.com/test", "nodeport"))
		}
		if ep.Ingress != nil {
			ing := &networkingv1.Ingress{}
			Expect(r.Get(ctx, client.ObjectKey{Name: serviceName + "-ing", Namespace: js.Namespace}, ing)).To(Succeed())
			Expect(ing.Spec.Rules[0].Host).To(Equal("telemetry.example.com"))
			Expect(ing.Spec.Rules[0].HTTP.Paths[0].Backend.Service.Port.Number).To(Equal(int32(telemetryPort)))
			Expect(ing.Spec.IngressClassName).To(HaveValue(Equal("nginx")))
			Expect(ing.Annotations).To(HaveKeyWithValue("nginx.ingress.kubernetes.io/ssl-passthrough", "true"))
		}
		if ep.Route != nil {
			route := &routev1.Route{}
			Expect(r.Get(ctx, client.ObjectKey{Name: serviceName + "-route", Namespace: js.Namespace}, route)).To(Succeed())
			Expect(route.Spec.Host).To(Equal("telemetry.example.com"))
			Expect(route.Spec.TLS.Termination).To(Equal(routev1.TLSTerminationPassthrough))
			Expect(route.Spec.To.Name).To(Equal(serviceName))
		}
		Expect(r.Get(ctx, client.ObjectKey{Name: telemetryServiceName, Namespace: js.Namespace}, service)).To(Succeed())
		Expect(service.Spec.Type).To(Equal(corev1.ServiceTypeClusterIP))
		Expect(service.Spec.Ports).To(HaveLen(2), "internal scraping must remain available")
	},
		Entry("NodePort", operatorv1alpha1.Endpoint{NodePort: &operatorv1alpha1.NodePortConfig{Enabled: true, Port: 30015, Annotations: map[string]string{"example.com/test": "nodeport"}}}, "-np", corev1.ServiceTypeNodePort, int32(9093)),
		Entry("LoadBalancer", operatorv1alpha1.Endpoint{LoadBalancer: &operatorv1alpha1.LoadBalancerConfig{Enabled: true, Port: 443}}, "-lb", corev1.ServiceTypeLoadBalancer, int32(443)),
		Entry("Ingress", operatorv1alpha1.Endpoint{Ingress: &operatorv1alpha1.IngressConfig{Enabled: true, Class: "nginx"}}, "", corev1.ServiceTypeClusterIP, int32(9093)),
		Entry("Route", operatorv1alpha1.Endpoint{Route: &operatorv1alpha1.RouteConfig{Enabled: true}}, "", corev1.ServiceTypeClusterIP, int32(9093)),
		Entry("ClusterIP", operatorv1alpha1.Endpoint{ClusterIP: &operatorv1alpha1.ClusterIPConfig{Enabled: true}}, "", corev1.ServiceTypeClusterIP, int32(9093)),
	)

	It("updates NodePorts and cleans up removed endpoints and networking methods", func() {
		js.Spec.Telemetry.GRPC.Endpoints = []operatorv1alpha1.Endpoint{
			{Address: "telemetry.example.com:30015", NodePort: &operatorv1alpha1.NodePortConfig{Enabled: true, Port: 30015}},
			{Address: "alternate.example.com", Ingress: &operatorv1alpha1.IngressConfig{Enabled: true}, Route: &operatorv1alpha1.RouteConfig{Enabled: true}},
		}
		Expect(r.reconcileTelemetryServiceStage(ctx, js)).To(Succeed())
		js.Spec.Telemetry.GRPC.Endpoints = js.Spec.Telemetry.GRPC.Endpoints[:1]
		js.Spec.Telemetry.GRPC.Endpoints[0].NodePort.Port = 30016
		Expect(r.reconcileTelemetryServiceStage(ctx, js)).To(Succeed())
		service := &corev1.Service{}
		Expect(r.Get(ctx, client.ObjectKey{Name: serviceName + "-np", Namespace: js.Namespace}, service)).To(Succeed())
		Expect(service.Spec.Ports[0].NodePort).To(Equal(int32(30016)))
		Expect(errors.IsNotFound(r.Get(ctx, client.ObjectKey{Name: "jumpstarter-telemetry-grpc-1", Namespace: js.Namespace}, &corev1.Service{}))).To(BeTrue())
		Expect(errors.IsNotFound(r.Get(ctx, client.ObjectKey{Name: "jumpstarter-telemetry-grpc-1-ing", Namespace: js.Namespace}, &networkingv1.Ingress{}))).To(BeTrue())
		Expect(errors.IsNotFound(r.Get(ctx, client.ObjectKey{Name: "jumpstarter-telemetry-grpc-1-route", Namespace: js.Namespace}, &routev1.Route{}))).To(BeTrue())

		// User labels must not prevent cleanup, and unowned Services must survive it.
		service.Labels = map[string]string{"app": "custom"}
		Expect(r.Update(ctx, service)).To(Succeed())
		unowned := &corev1.Service{ObjectMeta: metav1.ObjectMeta{Name: "jumpstarter-telemetry-grpc-user", Namespace: js.Namespace}}
		Expect(r.Create(ctx, unowned)).To(Succeed())
		js.Spec.Telemetry.Enabled = false
		Expect(r.reconcileTelemetryServiceStage(ctx, js)).To(Succeed())
		Expect(errors.IsNotFound(r.Get(ctx, client.ObjectKey{Name: serviceName + "-np", Namespace: js.Namespace}, &corev1.Service{}))).To(BeTrue())
		Expect(r.Get(ctx, client.ObjectKeyFromObject(unowned), unowned)).To(Succeed())
	})

	It("rejects a telemetry endpoint without an advertised address in the CRD", func() {
		ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{GenerateName: "telemetry-endpoint-"}}
		Expect(k8sClient.Create(ctx, ns)).To(Succeed())
		DeferCleanup(k8sClient.Delete, ctx, ns)
		js.Namespace, js.UID, js.ResourceVersion = ns.Name, "", ""
		js.Spec.Telemetry.GRPC.Endpoints = []operatorv1alpha1.Endpoint{{NodePort: &operatorv1alpha1.NodePortConfig{Enabled: true, Port: 30015}}}
		Expect(k8sClient.Create(ctx, js)).To(MatchError(ContainSubstring("telemetry endpoints require an address")))
	})

	It("suspends advertisement without removing exposure Services", func() {
		endpoints.ApplyEndpointDefaults(&js.Spec, true, true)
		Expect(r.reconcileTelemetryServiceStage(ctx, js)).To(Succeed())
		js.Spec.Telemetry.Replicas = new(int32(0))
		cfg, err := r.buildConfig(ctx, js)
		Expect(err).NotTo(HaveOccurred())
		Expect(cfg.Telemetry).To(BeNil())
		Expect(r.reconcileTelemetryServiceStage(ctx, js)).To(Succeed())
		Expect(r.Get(ctx, client.ObjectKey{Name: serviceName, Namespace: js.Namespace}, &corev1.Service{})).To(Succeed())
	})

	It("keeps legacy internal discovery when no endpoints or baseDomain are configured", func() {
		js.Spec.BaseDomain = ""
		endpoints.ApplyEndpointDefaults(&js.Spec, false, false)
		Expect(telemetryEndpointFor(js)).To(Equal("jumpstarter-telemetry.lab.svc:9093"))
		js.Spec.CertManager.Server = &operatorv1alpha1.ServerCertConfig{IssuerRef: &operatorv1alpha1.IssuerReference{Name: "public-ca"}}
		Expect(r.reconcileTelemetryCertificate(ctx, js, cmmeta.ObjectReference{Name: "public-ca"})).To(MatchError(ContainSubstring("telemetry requires a public")))
	})

})
