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
	"context"
	"os"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/durationpb"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/types"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/reconcile"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/config"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/controller"
	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/auth"
	clientsvcv1 "github.com/jumpstarter-dev/jumpstarter/controller/internal/service/client/v1"
)

// One forward from the request to the end of the lease, with the client
// service, the controller service and the lease reconciler on a real API
// server: the CRD rules apply to each write, and the watch of Listen is a
// real watch.
var _ = Describe("Lease forwards", func() {
	const (
		leaseName  = "fwd-it-lease"
		clientName = "fwd-it-client"
		headunit   = "fwd-it-hu"
		phone      = "fwd-it-phone"
	)
	leaseKey := types.NamespacedName{Namespace: memberNamespace, Name: leaseName}

	var created []client.Object
	var cancel context.CancelFunc

	getForwardLease := func() *jumpstarterdevv1alpha1.Lease {
		GinkgoHelper()
		var lease jumpstarterdevv1alpha1.Lease
		Expect(k8sClient.Get(ctx, leaseKey, &lease)).To(Succeed())
		return &lease
	}
	reconcileLease := func() {
		GinkgoHelper()
		r := &controller.LeaseReconciler{
			Client: k8sClient, APIReader: k8sClient, Scheme: k8sClient.Scheme(),
			StartedAt: time.Now().Add(-time.Hour),
		}
		_, err := r.Reconcile(ctx, reconcile.Request{NamespacedName: leaseKey})
		Expect(err).NotTo(HaveOccurred())
	}
	// onlineExporter creates an exporter that is registered, online and
	// Available, and that reports these ports.
	onlineExporter := func(name string, ports ...jumpstarterdevv1alpha1.DevicePort) {
		GinkgoHelper()
		exporter := &jumpstarterdevv1alpha1.Exporter{ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: memberNamespace}}
		Expect(k8sClient.Create(ctx, exporter)).To(Succeed())
		created = append(created, exporter)
		for _, t := range []jumpstarterdevv1alpha1.ExporterConditionType{
			jumpstarterdevv1alpha1.ExporterConditionTypeRegistered, jumpstarterdevv1alpha1.ExporterConditionTypeOnline,
		} {
			meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
				Type: string(t), Status: metav1.ConditionTrue, Reason: "Test",
			})
		}
		exporter.Status.LastSeen = metav1.Now()
		exporter.Status.ExporterStatusValue = jumpstarterdevv1alpha1.ExporterStatusAvailable
		exporter.Status.Devices = []jumpstarterdevv1alpha1.Device{{Uuid: name, Ports: ports}}
		Expect(k8sClient.Status().Update(ctx, exporter)).To(Succeed())
	}
	// serveLease does what the exporter does when it gets the lease: it has
	// the lease in its status, and its beforeLease hook is complete.
	serveLease := func(name string) {
		GinkgoHelper()
		var exporter jumpstarterdevv1alpha1.Exporter
		Expect(k8sClient.Get(ctx, types.NamespacedName{Namespace: memberNamespace, Name: name}, &exporter)).To(Succeed())
		exporter.Status.LeaseRef = &corev1.LocalObjectReference{Name: leaseName}
		exporter.Status.ExporterStatusValue = jumpstarterdevv1alpha1.ExporterStatusLeaseReady
		Expect(k8sClient.Status().Update(ctx, &exporter)).To(Succeed())
	}
	newService := func() *ControllerService {
		return &ControllerService{
			Client: k8sClient,
			Authn:  &passingAuthenticator{userName: "test-user"},
			Authz:  passingAuthorizer{},
			Attr:   callerAttributesGetter{},
			Router: config.Router{"default": {Endpoint: "router.example.com:443"}},
		}
	}
	listen := func(listenCtx context.Context, svc *ControllerService, exporter string) *recordingListenStream {
		stream := &recordingListenStream{ctx: asExporter(listenCtx, exporter), sent: make(chan *pb.ListenResponse, 16)}
		go func() {
			defer GinkgoRecover()
			_ = svc.Listen(&pb.ListenRequest{LeaseName: leaseName}, stream)
		}()
		return stream
	}
	report := func(svc *ControllerService, exporter string, state pb.ForwardEndState, sequence uint64) {
		GinkgoHelper()
		_, err := svc.ReportForward(asExporter(ctx, exporter), &pb.ReportForwardRequest{
			LeaseUid: string(getForwardLease().UID), ForwardName: "bt", State: state, Sequence: sequence,
		})
		Expect(err).NotTo(HaveOccurred())
	}
	forwardState := func() jumpstarterdevv1alpha1.LeaseForwardState {
		GinkgoHelper()
		forwardStatus := getForwardLease().ForwardStatus("bt")
		Expect(forwardStatus).NotTo(BeNil())
		return forwardStatus.State
	}

	BeforeEach(func() {
		_ = k8sClient.Create(ctx, &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: memberNamespace}})
		Expect(os.Setenv("ROUTER_KEY", "test-router-key")).To(Succeed())
		_, cancel = context.WithCancel(ctx)
	})
	AfterEach(func() {
		cancel()
		for _, obj := range created {
			_ = k8sClient.Delete(ctx, obj)
		}
		created = nil
		Expect(k8sClient.DeleteAllOf(ctx, &jumpstarterdevv1alpha1.Lease{}, client.InNamespace(memberNamespace))).To(Succeed())
		Expect(k8sClient.DeleteAllOf(ctx, &jumpstarterdevv1alpha1.ExporterClaim{}, client.InNamespace(memberNamespace))).To(Succeed())
	})

	It("sets up a forward, opens a connection, keeps its state across a controller restart, and tears it down", func() {
		jclient := &jumpstarterdevv1alpha1.Client{ObjectMeta: metav1.ObjectMeta{Name: clientName, Namespace: memberNamespace}}
		Expect(k8sClient.Create(ctx, jclient)).To(Succeed())
		created = append(created, jclient)
		onlineExporter(headunit, jumpstarterdevv1alpha1.DevicePort{
			Name: "rootcanal", Direction: jumpstarterdevv1alpha1.PortDirectionProvides, Protocol: "hci-h4"})
		onlineExporter(phone, jumpstarterdevv1alpha1.DevicePort{
			Name: "controller", Direction: jumpstarterdevv1alpha1.PortDirectionRequires})

		// The request: ad-hoc members with a forward.
		clientService := clientsvcv1.NewClientService(k8sClient, *auth.NewAuth(k8sClient,
			&passingAuthenticator{userName: "test-user"}, passingAuthorizer{},
			&clientAttributesGetter{namespace: memberNamespace, name: clientName},
		), 10, nil, nil, nil)
		forwards := []*cpb.LeaseForward{{Name: "bt", Endpoints: []*cpb.ForwardEndpoint{
			{MemberName: "headunit", PortName: "rootcanal"}, {MemberName: "peer", PortName: "controller"},
		}}}
		members := []*cpb.LeaseMember{
			{Name: "peer", Selection: &cpb.LeaseMember_ExporterName{ExporterName: phone}},
			{Name: "headunit", Selection: &cpb.LeaseMember_ExporterName{ExporterName: headunit}},
		}
		result, err := clientService.CreateLease(ctx, &cpb.CreateLeaseRequest{
			Parent:  "namespaces/" + memberNamespace,
			LeaseId: leaseName,
			Lease:   &cpb.Lease{Duration: durationpb.New(time.Hour), Members: members, Forwards: forwards},
		})
		Expect(err).NotTo(HaveOccurred())
		Expect(result.Forwards).To(HaveLen(1))

		// The API server refuses a change of the forwards.
		changed := getForwardLease()
		changed.Spec.Forwards[0].Endpoints[0].Port = "other"
		Expect(k8sClient.Update(ctx, changed)).NotTo(Succeed())

		// The bind, and the validation of the ports.
		reconcileLease()
		lease := getForwardLease()
		Expect(lease.BoundExporterNames()).To(ConsistOf(headunit, phone))
		serveLease(headunit)
		serveLease(phone)
		reconcileLease()
		Expect(forwardState()).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStatePending))
		uid := string(lease.UID)

		// Each exporter opens Listen and gets its setup.
		svc := newService()
		listenCtx, stopListen := context.WithCancel(ctx)
		defer stopListen()
		headunitStream := listen(listenCtx, svc, headunit)
		phoneStream := listen(listenCtx, svc, phone)
		var setup *pb.ListenResponse
		Eventually(headunitStream.sent, 10*time.Second).Should(Receive(&setup))
		Expect(setup.GetForwardSetup().GetDirection()).To(Equal(pb.PortDirection_PORT_DIRECTION_PROVIDES))
		Expect(setup.GetForwardSetup().GetLeaseUid()).To(Equal(uid))
		Eventually(phoneStream.sent, 10*time.Second).Should(Receive(&setup))
		Expect(setup.GetForwardSetup().GetDirection()).To(Equal(pb.PortDirection_PORT_DIRECTION_REQUIRES))
		Expect(setup.GetForwardSetup().GetPeers()).To(HaveLen(1))
		Expect(setup.GetForwardSetup().GetPeers()[0].GetMemberName()).To(Equal("headunit"))

		// DialPeer waits for the forward.
		_, err = svc.DialPeer(asExporter(ctx, phone), &pb.DialPeerRequest{LeaseUid: uid, ForwardName: "bt"})
		Expect(status.Code(err)).To(Equal(codes.FailedPrecondition))

		// The two ends report: the forward is Listening and the lease Ready.
		report(svc, headunit, pb.ForwardEndState_FORWARD_END_STATE_READY, 1000)
		report(svc, phone, pb.ForwardEndState_FORWARD_END_STATE_READY, 2000)
		reconcileLease()
		Expect(forwardState()).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateListening))
		Expect(meta.IsStatusConditionTrue(getForwardLease().Status.Conditions,
			string(jumpstarterdevv1alpha1.LeaseConditionTypeReady))).To(BeTrue())
		Expect(meta.IsStatusConditionTrue(getForwardLease().Status.Conditions,
			string(jumpstarterdevv1alpha1.LeaseConditionTypeForwardsReady))).To(BeTrue())

		// A connection: the requires end asks, the provides end gets the
		// same token on its Listen stream.
		dial, err := svc.DialPeer(asExporter(ctx, phone), &pb.DialPeerRequest{LeaseUid: uid, ForwardName: "bt"})
		Expect(err).NotTo(HaveOccurred())
		var connect *pb.ListenResponse
		Eventually(headunitStream.sent, 10*time.Second).Should(Receive(&connect))
		Expect(connect.GetForwardConnect().GetConnectionId()).To(Equal(dial.ConnectionId))
		Expect(connect.GetForwardConnect().GetRouterToken()).To(Equal(dial.RouterToken))
		Expect(connect.GetForwardConnect().GetPeerMemberName()).To(Equal("peer"))
		_, err = svc.DialPeer(asExporter(ctx, headunit), &pb.DialPeerRequest{LeaseUid: uid, ForwardName: "bt"})
		Expect(status.Code(err)).To(Equal(codes.PermissionDenied))

		report(svc, phone, pb.ForwardEndState_FORWARD_END_STATE_CONNECTED, 2001)
		reconcileLease()
		Expect(forwardState()).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateConnected))

		// The controller restarts: a new service has no memory. The state of
		// the ends is on the lease, so the forward stays Connected, and an
		// old report is still ignored.
		stopListen()
		restarted := newService()
		reconcileLease()
		Expect(forwardState()).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateConnected))
		Expect(getForwardLease().ForwardEnd("bt", "headunit").Sequence).To(Equal(uint64(1000)))
		Expect(getForwardLease().ForwardEnd("bt", "peer").Sequence).To(Equal(uint64(2001)))
		report(restarted, phone, pb.ForwardEndState_FORWARD_END_STATE_FAILED, 1500)
		reconcileLease()
		Expect(forwardState()).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateConnected))

		// The exporters open Listen again and get the setup again. The
		// current state again, with the same number, answers it.
		restartCtx, stopRestarted := context.WithCancel(ctx)
		defer stopRestarted()
		headunitStream = listen(restartCtx, restarted, headunit)
		phoneStream = listen(restartCtx, restarted, phone)
		Eventually(headunitStream.sent, 10*time.Second).Should(Receive(&setup))
		Expect(setup.GetForwardSetup()).NotTo(BeNil())
		Eventually(phoneStream.sent, 10*time.Second).Should(Receive(&setup))
		Expect(setup.GetForwardSetup()).NotTo(BeNil())
		end := getForwardLease().ForwardEnd("bt", "peer")
		Expect(end.AwaitsReport()).To(BeTrue())
		Expect(end.State).To(Equal(jumpstarterdevv1alpha1.LeaseForwardEndStateConnected))
		Expect(end.Sequence).To(Equal(uint64(2001)))
		report(restarted, phone, pb.ForwardEndState_FORWARD_END_STATE_CONNECTED, 2001)
		report(restarted, headunit, pb.ForwardEndState_FORWARD_END_STATE_READY, 1000)
		for _, member := range []string{"peer", "headunit"} {
			Expect(getForwardLease().ForwardEnd("bt", member).AwaitsReport()).To(BeFalse())
		}
		reconcileLease()
		Expect(forwardState()).To(Equal(jumpstarterdevv1alpha1.LeaseForwardStateConnected))

		// The lease ends: each exporter gets a teardown on its Listen stream,
		// and no report and no connection is accepted.
		ending := getForwardLease()
		ending.Spec.Release = true
		Expect(k8sClient.Update(ctx, ending)).To(Succeed())
		reconcileLease()
		Expect(getForwardLease().Status.Ended).To(BeTrue())
		var teardown *pb.ListenResponse
		Eventually(headunitStream.sent, 10*time.Second).Should(Receive(&teardown))
		Expect(teardown.GetForwardTeardown().GetForwardName()).To(Equal("bt"))
		Eventually(phoneStream.sent, 10*time.Second).Should(Receive(&teardown))
		Expect(teardown.GetForwardTeardown().GetForwardName()).To(Equal("bt"))

		_, err = restarted.ReportForward(asExporter(ctx, phone), &pb.ReportForwardRequest{
			LeaseUid: uid, ForwardName: "bt", State: pb.ForwardEndState_FORWARD_END_STATE_READY, Sequence: 3000,
		})
		Expect(status.Code(err)).To(Equal(codes.PermissionDenied))
		_, err = restarted.DialPeer(asExporter(ctx, phone), &pb.DialPeerRequest{LeaseUid: uid, ForwardName: "bt"})
		Expect(status.Code(err)).To(Equal(codes.PermissionDenied))
		Expect(getForwardLease().ForwardStatus("bt").Ends).To(BeEmpty())
	})
})
