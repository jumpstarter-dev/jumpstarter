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
	"cmp"
	"context"
	"fmt"
	"math"
	"os"
	"regexp"
	"slices"
	"strings"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"golang.org/x/exp/maps"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	apierrors "k8s.io/apimachinery/pkg/api/errors"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/types"
	k8suuid "k8s.io/apimachinery/pkg/util/uuid"
	"k8s.io/client-go/util/retry"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/config"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/controller"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
)

// The controller side of the forwards of a lease (JEP-0016): the ports of a
// registration, the forward instructions on Listen, ReportForward and
// DialPeer.

const (
	// dialTokenLifetime is the lifetime of the router token of a client
	// connection (Dial).
	dialTokenLifetime = 30 * time.Minute
	// forwardTokenLifetime is the lifetime of the router token of a forwarded
	// connection (DialPeer). It limits only the time to open the stream.
	forwardTokenLifetime = 60 * time.Second
	// maxForwardMessageLength bounds the cause that an exporter reports for a
	// failed forward end, which is kept in the lease status.
	maxForwardMessageLength = 1024
	// maxDevicePorts is the maximum number of ports of one driver report
	// (the limit of Exporter.status.devices[].ports).
	maxDevicePorts = 64
	// maxPortLabelLength is the maximum length of a port name and of a
	// protocol tag.
	maxPortLabelLength = 63
	// defaultForwardSetupPollInterval is how often Listen looks at an
	// exporter whose beforeLease hook is not complete, to send its forward
	// setups after the hook.
	defaultForwardSetupPollInterval = time.Second
)

var portNamePattern = regexp.MustCompile(`^[a-z0-9]([-a-z0-9]*[a-z0-9])?$`)

// devicesFromReports converts the driver reports of a registration to the
// devices of the exporter status, with their ports. It returns an
// InvalidArgument error for a port that the controller cannot accept: a name
// that is not a DNS label or that two ports of the exporter have, or a port
// without a direction. An exporter that reports no ports gets devices without
// ports, as before.
func devicesFromReports(reports []*pb.DriverInstanceReport) ([]jumpstarterdevv1alpha1.Device, error) {
	devices := []jumpstarterdevv1alpha1.Device{}
	seen := map[string]struct{}{}
	for _, report := range reports {
		device := jumpstarterdevv1alpha1.Device{
			Uuid:       report.Uuid,
			ParentUuid: report.ParentUuid,
			Labels:     report.Labels,
		}
		if len(report.GetPorts()) > maxDevicePorts {
			return nil, status.Errorf(codes.InvalidArgument,
				"driver %s reports %d ports: the maximum is %d", report.GetUuid(), len(report.GetPorts()), maxDevicePorts)
		}
		for _, reported := range report.GetPorts() {
			name := reported.GetName()
			if len(name) > maxPortLabelLength || !portNamePattern.MatchString(name) {
				return nil, status.Errorf(codes.InvalidArgument,
					"invalid port name %q: a port name is a DNS label of at most %d characters", name, maxPortLabelLength)
			}
			if _, duplicate := seen[name]; duplicate {
				return nil, status.Errorf(codes.InvalidArgument,
					"duplicate port name %q: a port name is unique in one exporter", name)
			}
			seen[name] = struct{}{}
			if len(reported.GetProtocol()) > maxPortLabelLength {
				return nil, status.Errorf(codes.InvalidArgument,
					"port %q: the protocol has more than %d characters", name, maxPortLabelLength)
			}
			port, err := jumpstarterdevv1alpha1.DevicePortFromProtobuf(reported)
			if err != nil {
				return nil, status.Errorf(codes.InvalidArgument, "invalid port: %s", err)
			}
			device.Ports = append(device.Ports, *port)
		}
		devices = append(devices, device)
	}
	return devices, nil
}

// selectRouter returns the router whose labels match these labels best.
func (s *ControllerService) selectRouter(labels map[string]string) (config.RouterEntry, bool) {
	candidates := maps.Values(s.Router)
	if len(candidates) == 0 {
		return config.RouterEntry{}, false
	}
	slices.SortFunc(candidates, func(a config.RouterEntry, b config.RouterEntry) int {
		return -cmp.Compare(MatchLabels(a.Labels, labels), MatchLabels(b.Labels, labels))
	})
	return candidates[0], true
}

// signRouterToken signs a router token for a stream: the two peers that call
// RouterService.Stream with it are connected.
func signRouterToken(stream string, lifetime time.Duration) (string, error) {
	now := time.Now()
	return jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.RegisteredClaims{
		Issuer:    "https://jumpstarter.dev/stream",
		Subject:   stream,
		Audience:  []string{"https://jumpstarter.dev/router"},
		ExpiresAt: jwt.NewNumericDate(now.Add(lifetime)),
		NotBefore: jwt.NewNumericDate(now),
		IssuedAt:  jwt.NewNumericDate(now),
		ID:        string(k8suuid.NewUUID()),
	}).SignedString([]byte(os.Getenv("ROUTER_KEY")))
}

// forwardCaller authenticates the exporter that calls ReportForward or
// DialPeer and checks the rule that the two RPCs share: the caller is the
// exporter of a Bound member of the active lease with this UID, and that
// member is an endpoint of the forward. For all other callers it returns
// PermissionDenied.
func (s *ControllerService) forwardCaller(
	ctx context.Context,
	leaseUID, forwardName string,
) (*jumpstarterdevv1alpha1.Exporter, *jumpstarterdevv1alpha1.Lease, *jumpstarterdevv1alpha1.LeaseMemberStatus, error) {
	exporter, err := s.authenticateExporter(ctx)
	if err != nil {
		return nil, nil, nil, err
	}
	if leaseUID == "" {
		return nil, nil, nil, status.Error(codes.InvalidArgument, "lease_uid is required")
	}
	if forwardName == "" {
		return nil, nil, nil, status.Error(codes.InvalidArgument, "forward_name is required")
	}
	lease, err := s.assignedLease(ctx, exporter)
	if err != nil {
		return nil, nil, nil, status.Errorf(codes.Internal, "unable to get the lease of exporter %s: %s", exporter.Name, err)
	}
	member, err := forwardEndpointMember(lease, exporter.Name, leaseUID, forwardName)
	if err != nil {
		return nil, nil, nil, err
	}
	return exporter, lease, member, nil
}

// forwardEndpointMember returns the Bound member of this exporter in the
// lease, if the lease is the active lease with this UID and the member is an
// endpoint of the forward. If not, it returns PermissionDenied.
func forwardEndpointMember(
	lease *jumpstarterdevv1alpha1.Lease,
	exporterName, leaseUID, forwardName string,
) (*jumpstarterdevv1alpha1.LeaseMemberStatus, error) {
	if lease == nil || string(lease.UID) != leaseUID || lease.Status.Ended {
		return nil, status.Errorf(codes.PermissionDenied,
			"exporter %s is not a bound member of an active lease with the UID %s", exporterName, leaseUID)
	}
	_, member := lease.ForwardEndpointForExporter(forwardName, exporterName)
	if member == nil {
		return nil, status.Errorf(codes.PermissionDenied,
			"exporter %s is not an endpoint of the forward %q of lease %s", exporterName, forwardName, lease.Name)
	}
	return member, nil
}

// ReportForward records the state that an exporter reports for its end of a
// forward. The state is kept in the lease status
// (status.forwards[].ends[]); the lease controller calculates the state of
// the forward from the states of all its ends.
//
// The write goes through the status subresource with the resource version
// that the lease was read with. When the lease controller, or the report of
// another end, changed the lease in between, the write fails with a conflict
// and is done again on the new lease: no writer replaces the change of
// another.
//
// A report is accepted when its sequence number is larger than the last
// accepted number of that end. A report that is not accepted is ignored, and
// the RPC succeeds: the exporter must not send it again. The same state with
// the same number is the answer to a repeated setup instruction: it changes
// nothing, and it counts as the report that the setup waits for.
func (s *ControllerService) ReportForward(
	ctx context.Context,
	req *pb.ReportForwardRequest,
) (*pb.ReportForwardResponse, error) {
	exporter, lease, member, err := s.forwardCaller(ctx, req.GetLeaseUid(), req.GetForwardName())
	if err != nil {
		return nil, err
	}
	logger := log.FromContext(ctx).WithValues(
		"exporter", types.NamespacedName{Namespace: exporter.Namespace, Name: exporter.Name},
		"lease_id", lease.Name, "lease_member", member.Name, "forward", req.GetForwardName(),
		"sequence", req.GetSequence())

	state, ok := jumpstarterdevv1alpha1.LeaseForwardEndStateFromProto(req.GetState())
	if !ok {
		return nil, status.Errorf(codes.InvalidArgument, "invalid forward end state %s", req.GetState())
	}
	// The lease status keeps the number as a 64-bit signed integer.
	if req.GetSequence() > math.MaxInt64 {
		return nil, status.Errorf(codes.InvalidArgument, "sequence %d is too large", req.GetSequence())
	}
	message := truncateUTF8(req.GetMessage(), maxForwardMessageLength)
	role := member.Name

	accepted := false
	first := true
	err = retry.RetryOnConflict(retry.DefaultRetry, func() error {
		if !first {
			if err := s.Client.Get(ctx, client.ObjectKeyFromObject(lease), lease); err != nil {
				if apierrors.IsNotFound(err) {
					return status.Errorf(codes.PermissionDenied,
						"exporter %s is not a bound member of an active lease with the UID %s", exporter.Name, req.GetLeaseUid())
				}
				return err
			}
			// The member can have another role name only on another lease,
			// which has another UID.
			if _, err := forwardEndpointMember(lease, exporter.Name, req.GetLeaseUid(), req.GetForwardName()); err != nil {
				return err
			}
		}
		first = false

		var changed bool
		end := lease.EnsureForwardEnd(req.GetForwardName(), role)
		changed, accepted = end.RecordReport(state, message, req.GetSequence(), time.Now())
		if !changed {
			return nil
		}
		return s.Client.Status().Update(ctx, lease)
	})
	if err != nil {
		if _, ok := status.FromError(err); ok {
			return nil, err
		}
		logger.Error(err, "unable to record forward report")
		return nil, status.Errorf(codes.Internal, "unable to record forward report: %s", err)
	}
	if accepted {
		logger.Info("Forward end reported", "state", state, "message", message)
	} else {
		logger.V(1).Info("Forward report with an old sequence number ignored", "state", state)
	}
	return &pb.ReportForwardResponse{}, nil
}

// DialPeer opens one connection of a forward for the exporter of a requires
// endpoint, or for the initiator of a path. As Dial does for a client, it
// creates a stream ID, signs a router token for it, and gives the token to
// the two peers: to the exporter of the other end in a ForwardConnect
// instruction on its Listen stream, and to the caller in the response.
func (s *ControllerService) DialPeer(ctx context.Context, req *pb.DialPeerRequest) (*pb.DialPeerResponse, error) {
	exporter, lease, member, err := s.forwardCaller(ctx, req.GetLeaseUid(), req.GetForwardName())
	if err != nil {
		return nil, err
	}
	forwardName := req.GetForwardName()
	logger := log.FromContext(ctx).WithValues(
		"exporter", types.NamespacedName{Namespace: exporter.Namespace, Name: exporter.Name},
		"lease_id", lease.Name, "lease_member", member.Name, "forward", forwardName)

	forwardStatus := lease.ForwardStatus(forwardName)
	if forwardStatus == nil || !forwardStatus.State.IsOperational() {
		state, cause := jumpstarterdevv1alpha1.LeaseForwardStatePending, ""
		if forwardStatus != nil {
			state = forwardStatus.State
			if forwardStatus.Message != "" {
				cause = ": " + forwardStatus.Message
			}
		}
		return nil, status.Errorf(codes.FailedPrecondition,
			"forward %q of lease %s is %s%s", forwardName, lease.Name, state, cause)
	}

	exporters, err := controller.ForwardExporters(ctx, s.Client, lease)
	if err != nil {
		logger.Error(err, "unable to get the exporters of the forward")
		return nil, status.Errorf(codes.Internal, "unable to get the exporters of the forward: %s", err)
	}
	var plan *controller.ForwardPlan
	plans := controller.PlanForwards(lease, exporters)
	for i := range plans {
		if plans[i].Name == forwardName {
			plan = &plans[i]
		}
	}
	if plan == nil || !plan.Active() {
		cause := ""
		if plan != nil {
			cause = ": " + plan.Message
		}
		return nil, status.Errorf(codes.FailedPrecondition,
			"forward %q of lease %s cannot open a connection%s", forwardName, lease.Name, cause)
	}
	target := plan.DialTarget(member.Name)
	if target == nil {
		return nil, status.Errorf(codes.PermissionDenied,
			"role %q cannot open a connection on the forward %q: only a requires endpoint or the initiator of a path can",
			member.Name, forwardName)
	}

	router, ok := s.selectRouter(exporter.Labels)
	if !ok {
		logger.Error(fmt.Errorf("no router available"), "no router available")
		return nil, status.Error(codes.Unavailable, "no router available")
	}

	connectionID := string(k8suuid.NewUUID())
	token, err := signRouterToken(connectionID, forwardTokenLifetime)
	if err != nil {
		logger.Error(err, "unable to sign token")
		return nil, status.Errorf(codes.Internal, "unable to sign token")
	}

	// The exporter of the other end has no Listen stream: Unavailable. Its
	// Listen queue is full: ResourceExhausted.
	if err := s.sendToListener(ctx, listenQueueKey(lease.Name, target.Exporter), &pb.ListenResponse{
		Forward: &pb.ListenResponse_ForwardConnect{ForwardConnect: &pb.ForwardConnect{
			LeaseUid:       string(lease.UID),
			ForwardName:    forwardName,
			ConnectionId:   connectionID,
			PeerMemberName: member.Name,
			RouterEndpoint: router.Endpoint,
			RouterToken:    token,
		}},
	}); err != nil {
		logger.Info("Forward connection rejected", "peer_member", target.Member, "error", err.Error())
		return nil, err
	}

	logger.Info("Forward connection assigned stream", "stream", connectionID, "peer_member", target.Member,
		"router", router.Endpoint)
	return &pb.DialPeerResponse{
		RouterEndpoint: router.Endpoint,
		RouterToken:    token,
		ConnectionId:   connectionID,
	}, nil
}

// exporterReadyForForwards reports whether the beforeLease hook of the
// exporter is complete: a forward starts after the hooks. An exporter that
// reports no status has no hooks.
func exporterReadyForForwards(exporter *jumpstarterdevv1alpha1.Exporter) bool {
	switch exporter.Status.ExporterStatusValue {
	case jumpstarterdevv1alpha1.ExporterStatusLeaseReady, jumpstarterdevv1alpha1.ExporterStatusUnspecified, "":
		return true
	}
	return false
}

func (s *ControllerService) forwardSetupPollInterval() time.Duration {
	if s.ForwardSetupPollInterval > 0 {
		return s.ForwardSetupPollInterval
	}
	return defaultForwardSetupPollInterval
}

// listenForwards sends the forward instructions of one Listen stream: the
// setup of each forward that the exporter is an endpoint of, one time for the
// stream, and a teardown when the forward ends before the stream does. Only
// the goroutine of the Listen call uses it.
type listenForwards struct {
	service      *ControllerService
	stream       pb.ControllerService_ListenServer
	leaseKey     types.NamespacedName
	leaseUID     types.UID
	exporterName string
	// ready is set when the beforeLease hook of the exporter is complete.
	ready bool
	// sent has the forwards that got a setup on this stream and no teardown.
	sent map[string]bool
	// seen is the part of the lease that the last sync used.
	seen string
	// sendErr is the error of a send on the stream. The stream is then broken.
	sendErr error
}

func (f *listenForwards) send(response *pb.ListenResponse) {
	if f.sendErr == nil {
		f.sendErr = f.stream.Send(response)
	}
}

func (f *listenForwards) teardown(forwards ...string) {
	for _, name := range forwards {
		if !f.sent[name] {
			continue
		}
		delete(f.sent, name)
		f.send(&pb.ListenResponse{Forward: &pb.ListenResponse_ForwardTeardown{ForwardTeardown: &pb.ForwardTeardown{
			LeaseUid:    string(f.leaseUID),
			ForwardName: name,
		}}})
	}
}

// fingerprint is the part of the lease that decides the instructions of this
// stream: the end of the lease, the states of the members, and whether each
// forward of the member can get a setup. The reports of the ends, which
// change the lease often, are not a part of it.
func (f *listenForwards) fingerprint(lease *jumpstarterdevv1alpha1.Lease, member string) string {
	var b strings.Builder
	fmt.Fprintf(&b, "ended=%t;", lease.Status.Ended)
	for i := range lease.Status.Members {
		fmt.Fprintf(&b, "%s=%s;", lease.Status.Members[i].Name, lease.Status.Members[i].State)
	}
	for _, forward := range lease.ForwardsOfMember(member) {
		blocked := false
		if forwardStatus := lease.ForwardStatus(forward.Name); forwardStatus != nil {
			blocked = forwardStatus.State.In(
				jumpstarterdevv1alpha1.LeaseForwardStateFailed, jumpstarterdevv1alpha1.LeaseForwardStateDisabled)
		}
		fmt.Fprintf(&b, "%s:%t:%t;", forward.Name, f.sent[forward.Name], blocked)
	}
	return b.String()
}

// sync sends the instructions that the current lease asks for and that this
// stream did not get. It returns the errors of the API server; the error of a
// send is in sendErr.
func (f *listenForwards) sync(ctx context.Context) error {
	s := f.service
	var lease jumpstarterdevv1alpha1.Lease
	if err := s.Client.Get(ctx, f.leaseKey, &lease); err != nil {
		if apierrors.IsNotFound(err) {
			f.teardown(maps.Keys(f.sent)...)
			return nil
		}
		return err
	}
	member := lease.MemberStatusForExporter(f.exporterName, jumpstarterdevv1alpha1.LeaseMemberStateBound)
	if lease.UID != f.leaseUID || lease.Status.Ended || member == nil {
		// The lease ended, or it lost this member. The exporter closes its
		// listeners with or without this instruction.
		names := maps.Keys(f.sent)
		slices.Sort(names)
		f.teardown(names...)
		return nil
	}

	seen := f.fingerprint(&lease, member.Name)
	if seen == f.seen {
		return nil
	}

	exporters, err := controller.ForwardExporters(ctx, s.Client, &lease)
	if err != nil {
		return err
	}
	var setups []*pb.ForwardSetup
	for _, plan := range controller.PlanForwards(&lease, exporters) {
		local := plan.Endpoint(member.Name)
		switch {
		case f.sent[plan.Name] && plan.MemberLost:
			// A member that the forward cannot operate without is Lost.
			f.teardown(plan.Name)
		case !f.sent[plan.Name] && plan.Active() && local != nil:
			setup := &pb.ForwardSetup{
				LeaseUid:      string(lease.UID),
				ForwardName:   plan.Name,
				MemberName:    member.Name,
				Direction:     local.Direction.ToProtobuf(),
				LocalPortName: local.Port,
				Initiator:     plan.IsPath() && plan.Initiator == member.Name,
			}
			for _, peer := range plan.Peers(member.Name) {
				setup.Peers = append(setup.Peers, &pb.ForwardPeer{MemberName: peer.Member, PortName: peer.Port})
			}
			setups = append(setups, setup)
		}
	}

	if len(setups) > 0 {
		// The time of the setup is written before the instruction goes out,
		// so that the report of the exporter is always after it.
		if err := s.recordForwardSetups(ctx, &lease, member.Name, setups); err != nil {
			return err
		}
		for _, setup := range setups {
			f.sent[setup.ForwardName] = true
			f.send(&pb.ListenResponse{Forward: &pb.ListenResponse_ForwardSetup{ForwardSetup: setup}})
		}
	}
	f.seen = f.fingerprint(&lease, member.Name)
	return nil
}

// recordForwardSetups writes the time of the setup instructions of one member
// to the status of its forward ends. The report time limit of the lease
// controller starts there. As ReportForward does, it writes the status with
// the resource version that the lease was read with.
func (s *ControllerService) recordForwardSetups(
	ctx context.Context,
	lease *jumpstarterdevv1alpha1.Lease,
	member string,
	setups []*pb.ForwardSetup,
) error {
	uid := lease.UID
	first := true
	return retry.RetryOnConflict(retry.DefaultRetry, func() error {
		if !first {
			if err := s.Client.Get(ctx, client.ObjectKeyFromObject(lease), lease); err != nil {
				return err
			}
			if lease.UID != uid {
				return fmt.Errorf("lease %s was replaced", lease.Name)
			}
		}
		first = false
		now := time.Now()
		for _, setup := range setups {
			lease.EnsureForwardEnd(setup.ForwardName, member).RecordSetup(now)
		}
		return s.Client.Status().Update(ctx, lease)
	})
}

// watchLeaseChanges signals each change of one lease until the context ends.
// It signals once after each start of the watch too, for the changes that the
// watch did not see. Signals are not counted: the reader looks at the lease.
func (s *ControllerService) watchLeaseChanges(ctx context.Context, key types.NamespacedName) <-chan struct{} {
	changes := make(chan struct{}, 1)
	signal := func() {
		select {
		case changes <- struct{}{}:
		default:
		}
	}
	go func() {
		logger := log.FromContext(ctx)
		for ctx.Err() == nil {
			watcher, err := s.Client.Watch(ctx, &jumpstarterdevv1alpha1.LeaseList{}, &client.ListOptions{
				FieldSelector: fields.OneTermEqualSelector("metadata.name", key.Name),
				Namespace:     key.Namespace,
			})
			if err != nil {
				if ctx.Err() == nil {
					logger.Error(err, "failed to watch the lease for its forwards", "lease_id", key.Name)
				}
			} else {
				signal()
				func() {
					defer watcher.Stop()
					for {
						select {
						case <-ctx.Done():
							return
						case event, ok := <-watcher.ResultChan():
							if !ok {
								return
							}
							if lease, isLease := event.Object.(*jumpstarterdevv1alpha1.Lease); isLease && lease.Name != key.Name {
								continue
							}
							signal()
						}
					}
				}()
			}
			// The API server closes a watch after some time: start it again.
			select {
			case <-ctx.Done():
			case <-time.After(time.Second):
			}
		}
	}()
	return changes
}
