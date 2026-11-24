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

package controller

import (
	"context"
	"fmt"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
	k8serrors "k8s.io/apimachinery/pkg/api/errors"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
)

func forward(name string, endpoints ...string) jumpstarterdevv1alpha1.LeaseForward {
	f := jumpstarterdevv1alpha1.LeaseForward{Name: name}
	for i := 0; i+1 < len(endpoints); i += 2 {
		f.Endpoints = append(f.Endpoints, jumpstarterdevv1alpha1.ForwardEndpoint{
			Member: endpoints[i], Port: endpoints[i+1],
		})
	}
	return f
}

// The API schema of the forwards of JEP-0016: the admission rules of
// Lease.spec.forwards and LeaseTemplate.spec.forwards, and the ports of
// Exporter.status.devices. No controller runs here.
var _ = Describe("Forward API rules", func() {
	const leaseName = "fw-lease"
	const templateName = "fw-template"
	const exporterName = "fw-exporter"
	label := map[string]string{"fw": "x"}
	var ctx context.Context

	// forwardLease is a lease with the members headunit, peer and logger.
	forwardLease := func(forwards ...jumpstarterdevv1alpha1.LeaseForward) *jumpstarterdevv1alpha1.Lease {
		lease := memberLease(leaseName,
			selectorMember("headunit", label), selectorMember("peer", label), selectorMember("logger", label))
		lease.Spec.Forwards = forwards
		return lease
	}
	forwardTemplate := func(forwards ...jumpstarterdevv1alpha1.LeaseForward) *jumpstarterdevv1alpha1.LeaseTemplate {
		return &jumpstarterdevv1alpha1.LeaseTemplate{
			ObjectMeta: metav1.ObjectMeta{Name: templateName, Namespace: "default"},
			Spec: jumpstarterdevv1alpha1.LeaseTemplateSpec{
				Members: []jumpstarterdevv1alpha1.LeaseMember{
					selectorMember("headunit", label), selectorMember("peer", label),
				},
				Forwards: forwards,
			},
		}
	}
	expectInvalid := func(err error, message string) {
		GinkgoHelper()
		Expect(err).To(HaveOccurred())
		Expect(k8serrors.IsInvalid(err)).To(BeTrue(), "error: %v", err)
		Expect(err.Error()).To(ContainSubstring(message))
	}

	BeforeEach(func() {
		ctx = context.Background()
	})
	AfterEach(func() {
		deleteLeases(ctx, leaseName)
		_ = k8sClient.Delete(ctx, forwardTemplate())
		_ = k8sClient.Delete(ctx, mxExporter(exporterName, "", nil))
	})

	When("a lease has forwards", func() {
		It("accepts forwards between its members", func() {
			lease := forwardLease(
				forward("bt", "headunit", "rootcanal", "peer", "controller"),
				forward("console", "headunit", "console", "peer", "console", "logger", "console"),
			)
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())

			stored := &jumpstarterdevv1alpha1.Lease{}
			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: leaseName, Namespace: "default"}, stored)).To(Succeed())
			Expect(stored.HasForwards()).To(BeTrue())
			Expect(stored.ForwardNames()).To(Equal([]string{"bt", "console"}))
			Expect(stored.Forward("bt").Endpoint("peer").Port).To(Equal("controller"))
			Expect(stored.Forward("console").Peers("headunit")).To(HaveLen(2))
			Expect(stored.ForwardsOfMember("logger")).To(HaveLen(1))
			Expect(stored.Forward("bt").PathInitiator()).To(Equal("headunit"))
		})

		It("accepts the largest forward list inside the CEL cost limit", func() {
			lease := memberLease(leaseName)
			for i := range 8 {
				lease.Spec.Members = append(lease.Spec.Members, selectorMember(fmt.Sprintf("member-%d", i), label))
			}
			for i := range 16 {
				f := jumpstarterdevv1alpha1.LeaseForward{Name: fmt.Sprintf("forward-%d", i)}
				for j := range 8 {
					f.Endpoints = append(f.Endpoints, jumpstarterdevv1alpha1.ForwardEndpoint{
						Member: fmt.Sprintf("member-%d", j), Port: fmt.Sprintf("port-%d", i),
					})
				}
				lease.Spec.Forwards = append(lease.Spec.Forwards, f)
			}
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			// The update runs the transition rule over the full lists.
			lease.Spec.Release = true
			Expect(k8sClient.Update(ctx, lease)).To(Succeed())
		})

		It("stores the forward status", func() {
			lease := forwardLease(forward("bt", "headunit", "rootcanal", "peer", "controller"))
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			Expect(lease.SetForwardStatus("bt", jumpstarterdevv1alpha1.LeaseForwardStatePending, "")).To(BeTrue())
			Expect(lease.SetForwardStatus("bt", jumpstarterdevv1alpha1.LeaseForwardStatePending, "")).To(BeFalse())
			Expect(k8sClient.Status().Update(ctx, lease)).To(Succeed())

			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: leaseName, Namespace: "default"}, lease)).To(Succeed())
			Expect(lease.ForwardStatus("bt").State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStatePending))
			Expect(lease.HasForwardInState(jumpstarterdevv1alpha1.LeaseForwardStatePending)).To(BeTrue())

			lease.Status.Forwards[0].State = "Unknown"
			expectInvalid(k8sClient.Status().Update(ctx, lease), "Unsupported value")
		})

		It("rejects an endpoint on a role that the lease does not have", func() {
			err := k8sClient.Create(ctx, forwardLease(forward("bt", "headunit", "rootcanal", "phone", "controller")))
			expectInvalid(err, "forward endpoints must reference declared members")
		})

		It("rejects forwards in a lease without members", func() {
			lease := scalarRefLease(leaseName, "some-exporter")
			lease.Spec.Forwards = []jumpstarterdevv1alpha1.LeaseForward{
				forward("bt", "headunit", "rootcanal", "peer", "controller"),
			}
			expectInvalid(k8sClient.Create(ctx, lease), "forward endpoints must reference declared members")
		})

		It("rejects a forward that has the name of a member", func() {
			err := k8sClient.Create(ctx, forwardLease(forward("peer", "headunit", "rootcanal", "peer", "controller")))
			expectInvalid(err, "a forward and a member cannot have the same name")
		})

		It("rejects two forwards with the same name", func() {
			err := k8sClient.Create(ctx, forwardLease(
				forward("bt", "headunit", "rootcanal", "peer", "controller"),
				forward("bt", "headunit", "console", "logger", "console"),
			))
			expectInvalid(err, "Duplicate value")
		})

		It("rejects two endpoints on the same member", func() {
			err := k8sClient.Create(ctx, forwardLease(forward("bt", "headunit", "rootcanal", "headunit", "controller")))
			expectInvalid(err, "Duplicate value")
		})

		It("rejects a forward with fewer than two endpoints", func() {
			err := k8sClient.Create(ctx, forwardLease(forward("bt", "headunit", "rootcanal")))
			expectInvalid(err, "should have at least 2 items")
		})

		It("rejects a forward with more than eight endpoints", func() {
			f := jumpstarterdevv1alpha1.LeaseForward{Name: "bt"}
			for i := range 9 {
				f.Endpoints = append(f.Endpoints, jumpstarterdevv1alpha1.ForwardEndpoint{
					Member: fmt.Sprintf("member-%d", i), Port: "port",
				})
			}
			expectInvalid(k8sClient.Create(ctx, forwardLease(f)), "must have at most 8 items")
		})

		It("rejects more than sixteen forwards", func() {
			var forwards []jumpstarterdevv1alpha1.LeaseForward
			for i := range 17 {
				forwards = append(forwards, forward(fmt.Sprintf("forward-%d", i), "headunit", "a", "peer", "b"))
			}
			expectInvalid(k8sClient.Create(ctx, forwardLease(forwards...)), "must have at most 16 items")
		})

		It("rejects names that are not DNS labels", func() {
			err := k8sClient.Create(ctx, forwardLease(forward("Bt_Link", "headunit", "rootcanal", "peer", "controller")))
			expectInvalid(err, "spec.forwards[0].name")
			err = k8sClient.Create(ctx, forwardLease(forward("bt", "headunit", "127.0.0.1:7300", "peer", "controller")))
			expectInvalid(err, "spec.forwards[0].endpoints[0].port")
			err = k8sClient.Create(ctx, forwardLease(forward("bt", "headunit", "", "peer", "controller")))
			expectInvalid(err, "spec.forwards[0].endpoints[0].port")
		})

		It("rejects a change of the forwards after creation", func() {
			lease := forwardLease(forward("bt", "headunit", "rootcanal", "peer", "controller"))
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())

			changed := lease.DeepCopy()
			changed.Spec.Forwards[0].Endpoints[1].Port = "other"
			expectInvalid(k8sClient.Update(ctx, changed), "forwards are immutable after creation")

			added := lease.DeepCopy()
			added.Spec.Forwards = append(added.Spec.Forwards, forward("console", "headunit", "console", "logger", "console"))
			expectInvalid(k8sClient.Update(ctx, added), "forwards are immutable after creation")

			removed := lease.DeepCopy()
			removed.Spec.Forwards = nil
			expectInvalid(k8sClient.Update(ctx, removed), "forwards are immutable after creation")

			// Other fields can still change.
			lease.Spec.Release = true
			Expect(k8sClient.Update(ctx, lease)).To(Succeed())
		})

		It("rejects forwards that are added to a lease after creation", func() {
			lease := forwardLease()
			Expect(k8sClient.Create(ctx, lease)).To(Succeed())
			lease.Spec.Forwards = []jumpstarterdevv1alpha1.LeaseForward{
				forward("bt", "headunit", "rootcanal", "peer", "controller"),
			}
			expectInvalid(k8sClient.Update(ctx, lease), "forwards are immutable after creation")
		})
	})

	When("a lease template has forwards", func() {
		It("accepts forwards between its members", func() {
			template := forwardTemplate(forward("bt", "headunit", "rootcanal", "peer", "controller"))
			Expect(k8sClient.Create(ctx, template)).To(Succeed())
			// A template can change: its forwards are not immutable.
			template.Spec.Forwards[0].Endpoints[1].Port = "other"
			Expect(k8sClient.Update(ctx, template)).To(Succeed())
		})

		It("rejects an endpoint on a role that the template does not have", func() {
			err := k8sClient.Create(ctx, forwardTemplate(forward("bt", "headunit", "rootcanal", "phone", "controller")))
			expectInvalid(err, "forward endpoints must reference declared members")
		})

		It("rejects a forward that has the name of a member", func() {
			err := k8sClient.Create(ctx, forwardTemplate(forward("headunit", "headunit", "rootcanal", "peer", "controller")))
			expectInvalid(err, "a forward and a member cannot have the same name")
		})
	})

	When("an exporter reports ports", func() {
		It("stores the ports of its devices and rejects values outside the enums", func() {
			exporter := mxExporter(exporterName, "", nil)
			Expect(k8sClient.Create(ctx, exporter)).To(Succeed())
			exporter.Status.Devices = []jumpstarterdevv1alpha1.Device{{
				Uuid: "11111111-1111-1111-1111-111111111111",
				Ports: []jumpstarterdevv1alpha1.DevicePort{
					{Name: "rootcanal", Direction: jumpstarterdevv1alpha1.PortDirectionProvides, Protocol: "hci-h4"},
					{
						Name:       "someip-sd",
						Direction:  jumpstarterdevv1alpha1.PortDirectionConnects,
						Attachment: jumpstarterdevv1alpha1.PortAttachmentSocket,
						Transport:  jumpstarterdevv1alpha1.PortTransportDatagram,
					},
				},
			}}
			Expect(k8sClient.Status().Update(ctx, exporter)).To(Succeed())

			Expect(k8sClient.Get(ctx, types.NamespacedName{Name: exporterName, Namespace: "default"}, exporter)).To(Succeed())
			Expect(exporter.Ports()).To(HaveLen(2))
			Expect(exporter.Port("rootcanal").IsForwardable()).To(BeTrue())
			Expect(exporter.Port("rootcanal").EffectiveTransport()).To(Equal(jumpstarterdevv1alpha1.PortTransportStream))
			Expect(exporter.Port("someip-sd").IsForwardable()).To(BeFalse())
			Expect(exporter.Port("absent")).To(BeNil())

			exporter.Status.Devices[0].Ports[0].Direction = ""
			expectInvalid(k8sClient.Status().Update(ctx, exporter), "Unsupported value")
		})
	})
})
