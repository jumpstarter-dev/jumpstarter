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

package service

import (
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/durationpb"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/auth"
	clientsvcv1 "github.com/jumpstarter-dev/jumpstarter/controller/internal/service/client/v1"
)

// These specs run against a real API server, so the CRD rules for members,
// constraints and templates apply to what the services write.
var _ = Describe("Member-form leases", func() {
	const (
		testNamespace = "default"
		clientName    = "member-it-client"
	)

	var created []client.Object

	create := func(obj client.Object) {
		GinkgoHelper()
		Expect(k8sClient.Create(ctx, obj)).To(Succeed())
		created = append(created, obj)
	}

	AfterEach(func() {
		for _, obj := range created {
			_ = k8sClient.Delete(ctx, obj)
		}
		created = nil
		Expect(k8sClient.DeleteAllOf(ctx, &jumpstarterdevv1alpha1.Lease{}, client.InNamespace(testNamespace))).To(Succeed())
	})

	selector := func(deviceType string) *metav1.LabelSelector {
		return &metav1.LabelSelector{MatchLabels: map[string]string{"device-type": deviceType}}
	}

	Context("ClientService.CreateLease", func() {
		var svc *clientsvcv1.ClientService

		BeforeEach(func() {
			create(&jumpstarterdevv1alpha1.Client{ObjectMeta: metav1.ObjectMeta{Name: clientName, Namespace: testNamespace}})
			create(&jumpstarterdevv1alpha1.Client{ObjectMeta: metav1.ObjectMeta{Name: "member-it-support", Namespace: testNamespace}})
			create(&jumpstarterdevv1alpha1.LeaseTemplate{
				ObjectMeta: metav1.ObjectMeta{Name: "member-it-ivi", Namespace: testNamespace},
				Spec: jumpstarterdevv1alpha1.LeaseTemplateSpec{
					Description: "head unit, phone and ECU",
					Members: []jumpstarterdevv1alpha1.LeaseMember{
						{Name: "headunit", Selector: selector("aaos-headunit")},
						{Name: "phone", Selector: selector("phone"), Optional: true},
						{Name: "ecu", ExporterRef: &corev1.LocalObjectReference{Name: "ecu-11"}},
					},
					Constraints: []jumpstarterdevv1alpha1.LeaseConstraint{
						{Members: []string{"headunit", "ecu"}, SameLabel: "bus"},
						{Members: []string{"headunit", "phone"}, SameLabel: "rf-domain"},
					},
					Defaults: jumpstarterdevv1alpha1.LeaseTemplateDefaults{
						Duration:   &metav1.Duration{Duration: 2 * time.Hour},
						SharedWith: []string{"member-it-support", clientName},
					},
				},
			})
			a := auth.NewAuth(k8sClient,
				&passingAuthenticator{userName: "test-user"},
				passingAuthorizer{},
				&clientAttributesGetter{namespace: testNamespace, name: clientName},
			)
			svc = clientsvcv1.NewClientService(k8sClient, *a, 10, nil, nil, nil)
		})

		It("creates the lease that a template request expands to", func() {
			result, err := svc.CreateLease(ctx, &cpb.CreateLeaseRequest{
				Parent:  "namespaces/" + testNamespace,
				LeaseId: "member-it-template",
				Lease: &cpb.Lease{Template: &cpb.LeaseTemplateUse{
					Template:        "member-it-ivi",
					ExcludedMembers: []string{"phone"},
					MemberSelectors: map[string]string{"headunit": "soc=sa8775p,rev!=a"},
				}},
			})
			Expect(err).NotTo(HaveOccurred())
			Expect(result.Members).To(HaveLen(3))
			Expect(result.Template.Template).To(Equal("namespaces/default/leasetemplates/member-it-ivi"))

			var lease jumpstarterdevv1alpha1.Lease
			Expect(k8sClient.Get(ctx, types.NamespacedName{Namespace: testNamespace, Name: "member-it-template"}, &lease)).To(Succeed())
			var template jumpstarterdevv1alpha1.LeaseTemplate
			Expect(k8sClient.Get(ctx, types.NamespacedName{Namespace: testNamespace, Name: "member-it-ivi"}, &template)).To(Succeed())

			Expect(lease.Spec.TemplateRef).To(Equal(&jumpstarterdevv1alpha1.LeaseTemplateReference{
				Name: "member-it-ivi", Generation: template.Generation,
			}))
			Expect(template.Generation).To(BeNumerically(">", 0))
			Expect(result.Template.Generation).To(Equal(template.Generation))
			Expect(lease.Spec.Duration.Duration).To(Equal(2 * time.Hour))
			Expect(lease.Spec.SharedWith).To(Equal([]string{"member-it-support"}))
			Expect(lease.Spec.Members).To(HaveLen(3))
			Expect(lease.Spec.Members[0].Selector.MatchLabels).To(Equal(map[string]string{
				"device-type": "aaos-headunit", "soc": "sa8775p",
			}))
			Expect(lease.Spec.Members[0].Selector.MatchExpressions).To(HaveLen(1))
			Expect(lease.Spec.Members[1].Excluded).To(BeTrue())
			Expect(lease.Spec.Members[2].ExporterRef.Name).To(Equal("ecu-11"))
			Expect(lease.Spec.Constraints).To(Equal(template.Spec.Constraints))
			Expect(lease.Status.ExporterRef).To(BeNil())
		})

		It("creates a lease from members of the request", func() {
			_, err := svc.CreateLease(ctx, &cpb.CreateLeaseRequest{
				Parent:  "namespaces/" + testNamespace,
				LeaseId: "member-it-adhoc",
				Lease: &cpb.Lease{
					Duration: durationpb.New(45 * time.Minute),
					Members: []*cpb.LeaseMember{
						{Name: "phone", Selection: &cpb.LeaseMember_Selector{Selector: "device-type=phone"}},
						{Name: "headunit", Selection: &cpb.LeaseMember_Selector{Selector: "device-type=headunit"}},
					},
					Constraints: []*cpb.LeaseConstraint{
						{MemberNames: []string{"phone", "headunit"}, Rule: &cpb.LeaseConstraint_SameLabel{SameLabel: "rf-domain"}},
					},
				},
			})
			Expect(err).NotTo(HaveOccurred())

			var lease jumpstarterdevv1alpha1.Lease
			Expect(k8sClient.Get(ctx, types.NamespacedName{Namespace: testNamespace, Name: "member-it-adhoc"}, &lease)).To(Succeed())
			Expect(lease.MemberNames()).To(Equal([]string{"phone", "headunit"}))
			Expect(lease.Spec.TemplateRef).To(BeNil())
		})

		It("reports a member that the CRD rejects as an invalid argument", func() {
			_, err := svc.CreateLease(ctx, &cpb.CreateLeaseRequest{
				Parent:  "namespaces/" + testNamespace,
				LeaseId: "member-it-invalid",
				Lease: &cpb.Lease{
					Duration: durationpb.New(time.Hour),
					Members: []*cpb.LeaseMember{
						{Name: "Not_A_DNS_Label", Selection: &cpb.LeaseMember_Selector{Selector: "device-type=phone"}},
					},
				},
			})
			Expect(status.Code(err)).To(Equal(codes.InvalidArgument))

			var lease jumpstarterdevv1alpha1.Lease
			err = k8sClient.Get(ctx, types.NamespacedName{Namespace: testNamespace, Name: "member-it-invalid"}, &lease)
			Expect(client.IgnoreNotFound(err)).To(Succeed())
			Expect(err).To(HaveOccurred())
		})

		It("lists the templates of the namespace", func() {
			response, err := svc.ListLeaseTemplates(ctx, &cpb.ListLeaseTemplatesRequest{Parent: "namespaces/" + testNamespace})
			Expect(err).NotTo(HaveOccurred())
			Expect(response.LeaseTemplates).To(HaveLen(1))
			Expect(response.LeaseTemplates[0].Name).To(Equal("namespaces/default/leasetemplates/member-it-ivi"))
			Expect(response.LeaseTemplates[0].Members).To(HaveLen(3))
		})
	})

	Context("release by an exporter", func() {
		It("marks the member lost and keeps the lease", func() {
			lease := &jumpstarterdevv1alpha1.Lease{
				ObjectMeta: metav1.ObjectMeta{Name: "member-it-release", Namespace: testNamespace},
				Spec: jumpstarterdevv1alpha1.LeaseSpec{
					ClientRef: corev1.LocalObjectReference{Name: clientName},
					Duration:  &metav1.Duration{Duration: time.Hour},
					Members: []jumpstarterdevv1alpha1.LeaseMember{
						{Name: "headunit", Selector: selector("headunit")},
						{Name: "phone", Selector: selector("phone")},
					},
				},
			}
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			lease.Status.Members = []jumpstarterdevv1alpha1.LeaseMemberStatus{
				{Name: "headunit", State: jumpstarterdevv1alpha1.LeaseMemberStateBound, ExporterRef: &corev1.LocalObjectReference{Name: "member-it-hu"}},
				{Name: "phone", State: jumpstarterdevv1alpha1.LeaseMemberStateBound, ExporterRef: &corev1.LocalObjectReference{Name: "member-it-phone"}},
			}
			Expect(k8sClient.Status().Update(ctx, lease)).To(Succeed())

			exporter := &jumpstarterdevv1alpha1.Exporter{ObjectMeta: metav1.ObjectMeta{Name: "member-it-phone", Namespace: testNamespace}}
			create(exporter)
			exporter.Status.LeaseRef = &corev1.LocalObjectReference{Name: lease.Name}
			Expect(k8sClient.Status().Update(ctx, exporter)).To(Succeed())

			svc := &ControllerService{Client: k8sClient}
			Expect(svc.handleExporterLeaseRelease(ctx, exporter)).To(Succeed())

			var updated jumpstarterdevv1alpha1.Lease
			Expect(k8sClient.Get(ctx, client.ObjectKeyFromObject(lease), &updated)).To(Succeed())
			Expect(updated.Spec.Release).To(BeFalse())
			Expect(updated.Status.Ended).To(BeFalse())
			phone := updated.MemberStatus("phone")
			Expect(phone.State).To(Equal(jumpstarterdevv1alpha1.LeaseMemberStateLost))
			Expect(phone.Reason).To(Equal(jumpstarterdevv1alpha1.LeaseMemberReasonExporterReleased))
			Expect(phone.ExporterRef.Name).To(Equal("member-it-phone"))
			Expect(updated.MemberStatus("headunit").State).To(Equal(jumpstarterdevv1alpha1.LeaseMemberStateBound))

			// A second release, through ReleaseLease, finds the member lost already.
			resp, err := svc.releaseLeaseAsExporter(ctx, exporter, &pb.ReleaseLeaseRequest{Name: lease.Name})
			Expect(err).NotTo(HaveOccurred())
			Expect(resp).NotTo(BeNil())

			other := &jumpstarterdevv1alpha1.Exporter{ObjectMeta: metav1.ObjectMeta{Name: "member-it-other", Namespace: testNamespace}}
			_, err = svc.releaseLeaseAsExporter(ctx, other, &pb.ReleaseLeaseRequest{Name: lease.Name})
			Expect(status.Code(err)).To(Equal(codes.FailedPrecondition))
		})
	})
})
