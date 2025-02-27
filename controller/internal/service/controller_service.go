/*
Copyright 2024.

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
	"fmt"
	"net"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authentication"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/authorization"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/oidc"
	pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/peer"
	"google.golang.org/grpc/reflection"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/durationpb"
	"google.golang.org/protobuf/types/known/timestamppb"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/fields"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/selection"
	"k8s.io/apimachinery/pkg/types"
	k8suuid "k8s.io/apimachinery/pkg/util/uuid"
	"k8s.io/apimachinery/pkg/watch"
	"k8s.io/apiserver/pkg/authorization/authorizer"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/controller"
)

// ControlerService exposes a gRPC service
type ControllerService struct {
	pb.UnimplementedControllerServiceServer
	Client       client.WithWatch
	Scheme       *runtime.Scheme
	Authn        authentication.ContextAuthenticator
	Authz        authorizer.Authorizer
	Attr         authorization.ContextAttributesGetter
	listenQueues sync.Map
}

type wrappedStream struct {
	grpc.ServerStream
}

func logContext(ctx context.Context) context.Context {
	p, ok := peer.FromContext(ctx)
	if ok {
		return log.IntoContext(ctx, log.FromContext(ctx, "peer", p.Addr))
	}
	return ctx
}

func (w *wrappedStream) Context() context.Context {
	return logContext(w.ServerStream.Context())
}

func (s *ControllerService) authenticateClient(ctx context.Context) (*jumpstarterdevv1alpha1.Client, error) {
	return oidc.VerifyClientObjectToken(
		ctx,
		s.Authn,
		s.Authz,
		s.Attr,
		s.Client,
	)
}

func (s *ControllerService) authenticateExporter(ctx context.Context) (*jumpstarterdevv1alpha1.Exporter, error) {
	return oidc.VerifyExporterObjectToken(
		ctx,
		s.Authn,
		s.Authz,
		s.Attr,
		s.Client,
	)
}

func (s *ControllerService) Register(ctx context.Context, req *pb.RegisterRequest) (*pb.RegisterResponse, error) {
	logger := log.FromContext(ctx)

	exporter, err := s.authenticateExporter(ctx)
	if err != nil {
		logger.Error(err, "unable to authenticate exporter")
		return nil, err
	}

	logger = logger.WithValues("exporter", types.NamespacedName{
		Namespace: exporter.Namespace,
		Name:      exporter.Name,
	})

	logger.Info("Registering exporter")

	original := client.MergeFrom(exporter.DeepCopy())

	if exporter.Labels == nil {
		exporter.Labels = make(map[string]string)
	}

	for k := range exporter.Labels {
		if strings.HasPrefix(k, "jumpstarter.dev/") {
			delete(exporter.Labels, k)
		}
	}

	for k, v := range req.Labels {
		if strings.HasPrefix(k, "jumpstarter.dev/") {
			exporter.Labels[k] = v
		}
	}

	if err := s.Client.Patch(ctx, exporter, original); err != nil {
		logger.Error(err, "unable to update exporter")
		return nil, status.Errorf(codes.Internal, "unable to update exporter: %s", err)
	}

	original = client.MergeFrom(exporter.DeepCopy())

	meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
		Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeRegistered),
		Status:             metav1.ConditionTrue,
		ObservedGeneration: exporter.Generation,
		LastTransitionTime: metav1.Time{
			Time: time.Now(),
		},
		Reason: "Register",
	})

	devices := []jumpstarterdevv1alpha1.Device{}
	for _, device := range req.Reports {
		devices = append(devices, jumpstarterdevv1alpha1.Device{
			Uuid:       device.Uuid,
			ParentUuid: device.ParentUuid,
			Labels:     device.Labels,
		})
	}
	exporter.Status.Devices = devices

	if err := s.Client.Status().Patch(ctx, exporter, original); err != nil {
		logger.Error(err, "unable to update exporter status")
		return nil, status.Errorf(codes.Internal, "unable to update exporter status: %s", err)
	}

	return &pb.RegisterResponse{
		Uuid: string(exporter.UID),
	}, nil
}

func (s *ControllerService) Unregister(
	ctx context.Context,
	req *pb.UnregisterRequest,
) (
	*pb.UnregisterResponse,
	error,
) {
	logger := log.FromContext(ctx)

	exporter, err := s.authenticateExporter(ctx)
	if err != nil {
		logger.Error(err, "unable to authenticate exporter")
		return nil, err
	}

	logger = logger.WithValues("exporter", types.NamespacedName{
		Namespace: exporter.Namespace,
		Name:      exporter.Name,
	})

	original := client.MergeFrom(exporter.DeepCopy())
	meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
		Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeRegistered),
		Status:             metav1.ConditionFalse,
		ObservedGeneration: exporter.Generation,
		LastTransitionTime: metav1.Time{
			Time: time.Now(),
		},
		Reason:  "Bye",
		Message: req.GetReason(),
	})

	if err := s.Client.Status().Patch(ctx, exporter, original); err != nil {
		logger.Error(err, "unable to update exporter status")
		return nil, status.Errorf(codes.Internal, "unable to update exporter status: %s", err)
	}

	logger.Info("exporter unregistered, updated as unavailable")

	return &pb.UnregisterResponse{}, nil
}

func (s *ControllerService) ListExporters(
	ctx context.Context,
	req *pb.ListExportersRequest,
) (*pb.ListExportersResponse, error) {
	logger := log.FromContext(ctx)

	if _, err := s.authenticateClient(ctx); err != nil {
		return nil, err
	}

	var exporters jumpstarterdevv1alpha1.ExporterList

	selector := labels.Everything()

	for k, v := range req.GetLabels() {
		requirement, err := labels.NewRequirement(k, selection.Equals, []string{v})
		if err != nil {
			logger.Error(err, "unable to create label requirement")
			return nil, status.Errorf(codes.Internal, "unable to create label requirement")
		}
		selector = selector.Add(*requirement)
	}

	if err := s.Client.List(ctx, &exporters, &client.ListOptions{
		LabelSelector: selector,
	}); err != nil {
		logger.Error(err, "unable to list exporters")
		return nil, status.Errorf(codes.Internal, "unable to list exporters")
	}

	results := make([]*pb.GetReportResponse, len(exporters.Items))

	for i, exporter := range exporters.Items {
		reports := []*pb.DriverInstanceReport{}
		for _, device := range exporter.Status.Devices {
			reports = append(reports, &pb.DriverInstanceReport{
				Uuid:       device.Uuid,
				ParentUuid: device.ParentUuid,
				Labels:     device.Labels,
			})
		}
		results[i] = &pb.GetReportResponse{
			Labels:  exporter.GetLabels(),
			Reports: reports,
		}
	}

	return &pb.ListExportersResponse{
		Exporters: results,
	}, nil
}

func (s *ControllerService) Listen(req *pb.ListenRequest, stream pb.ControllerService_ListenServer) error {
	ctx := stream.Context()
	logger := log.FromContext(ctx)

	exporter, err := s.authenticateExporter(ctx)
	if err != nil {
		return err
	}

	logger = logger.WithValues("exporter", types.NamespacedName{
		Namespace: exporter.Namespace,
		Name:      exporter.Name,
	})

	leaseName := req.GetLeaseName()
	if leaseName == "" {
		err := fmt.Errorf("empty lease name")
		logger.Error(err, "lease name not specified in dial request")
		return err
	}

	logger.WithValues("lease", types.NamespacedName{
		Namespace: exporter.Namespace,
		Name:      leaseName,
	})

	var lease jumpstarterdevv1alpha1.Lease
	if err := s.Client.Get(
		ctx,
		types.NamespacedName{Namespace: exporter.Namespace, Name: leaseName},
		&lease,
	); err != nil {
		logger.Error(err, "unable to get lease")
		return err
	}

	if lease.Status.ExporterRef == nil || lease.Status.ExporterRef.Name != exporter.Name {
		err := fmt.Errorf("permission denied")
		logger.Error(err, "lease not held by exporter")
		return err
	}

	queue, _ := s.listenQueues.LoadOrStore(leaseName, make(chan *pb.ListenResponse, 8))
	for {
		select {
		case <-ctx.Done():
			return nil
		case msg := <-queue.(chan *pb.ListenResponse):
			if err := stream.Send(msg); err != nil {
				return err
			}
		}
	}
}

func (s *ControllerService) Status(req *pb.StatusRequest, stream pb.ControllerService_StatusServer) error {
	ctx := stream.Context()
	logger := log.FromContext(ctx)

	exporter, err := s.authenticateExporter(ctx)
	if err != nil {
		logger.Error(err, "unable to authenticate exporter")
		return err
	}

	logger = logger.WithValues("exporter", types.NamespacedName{
		Namespace: exporter.Namespace,
		Name:      exporter.Name,
	})

	original := client.MergeFrom(exporter.DeepCopy())
	meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
		Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
		Status:             metav1.ConditionTrue,
		ObservedGeneration: exporter.Generation,
		LastTransitionTime: metav1.Time{
			Time: time.Now(),
		},
		Reason: "Connect",
	})
	if err = s.Client.Status().Patch(ctx, exporter, original); err != nil {
		logger.Error(err, "unable to update exporter status")
	}

	defer func() {
		// Make sure defer runs under a fresh context
		// otherwise these operations would fail if the rpc context is cancelled
		ctx, cancel := context.WithTimeout(context.Background(), time.Second*10)
		if err := s.Client.Get(
			ctx,
			types.NamespacedName{Name: exporter.Name, Namespace: exporter.Namespace},
			exporter,
		); err != nil {
			logger.Error(err, "unable to refresh exporter status, continuing anyway")
		}
		original := client.MergeFrom(exporter.DeepCopy())
		meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
			Status:             metav1.ConditionFalse,
			ObservedGeneration: exporter.Generation,
			LastTransitionTime: metav1.Time{
				Time: time.Now(),
			},
			Reason: "Disconnect",
		})
		if err = s.Client.Status().Patch(ctx, exporter, original); err != nil {
			logger.Error(err, "unable to update exporter status, continuing anyway")
		}
		cancel()
	}()

	watcher, err := s.Client.Watch(ctx, &jumpstarterdevv1alpha1.ExporterList{}, &client.ListOptions{
		FieldSelector: fields.OneTermEqualSelector("metadata.name", exporter.Name),
		Namespace:     exporter.Namespace,
	})
	if err != nil {
		logger.Error(err, "failed to watch exporter")
		return err
	}

	defer watcher.Stop()
	for result := range watcher.ResultChan() {
		switch result.Type {
		case watch.Added, watch.Modified, watch.Deleted:
			exporter = result.Object.(*jumpstarterdevv1alpha1.Exporter)
			leased := exporter.Status.LeaseRef != nil
			leaseName := (*string)(nil)
			clientName := (*string)(nil)
			if leased {
				leaseName = &exporter.Status.LeaseRef.Name
				var lease jumpstarterdevv1alpha1.Lease
				if err := s.Client.Get(
					ctx,
					types.NamespacedName{Namespace: exporter.Namespace, Name: *leaseName},
					&lease,
				); err != nil {
					logger.Error(err, "failed to get lease on exporter")
					return err
				}
				clientName = &lease.Spec.ClientRef.Name
			}
			if err = stream.Send(&pb.StatusResponse{
				Leased:     leased,
				LeaseName:  leaseName,
				ClientName: clientName,
			}); err != nil {
				return err
			}
		case watch.Error:
			return fmt.Errorf("received error when watching exporter")
		}
	}
	return nil
}

func (s *ControllerService) Dial(ctx context.Context, req *pb.DialRequest) (*pb.DialResponse, error) {
	logger := log.FromContext(ctx)

	client, err := s.authenticateClient(ctx)
	if err != nil {
		logger.Error(err, "unable to authenticate client")
		return nil, err
	}

	logger = logger.WithValues("client", types.NamespacedName{
		Namespace: client.Namespace,
		Name:      client.Name,
	})

	leaseName := req.GetLeaseName()
	if leaseName == "" {
		err := fmt.Errorf("empty lease name")
		logger.Error(err, "lease name not specified in dial request")
		return nil, err
	}

	logger = logger.WithValues("lease", types.NamespacedName{
		Namespace: client.Namespace,
		Name:      leaseName,
	})

	var lease jumpstarterdevv1alpha1.Lease
	if err := s.Client.Get(
		ctx,
		types.NamespacedName{Namespace: client.Namespace, Name: leaseName},
		&lease,
	); err != nil {
		logger.Error(err, "unable to get lease")
		return nil, err
	}

	if lease.Spec.ClientRef.Name != client.Name {
		err := fmt.Errorf("permission denied")
		logger.Error(err, "lease not held by client")
		return nil, err
	}

	stream := k8suuid.NewUUID()

	token, err := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.RegisteredClaims{
		Issuer:    "https://jumpstarter.dev/stream",
		Subject:   string(stream),
		Audience:  []string{"https://jumpstarter.dev/router"},
		ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Minute * 30)),
		NotBefore: jwt.NewNumericDate(time.Now()),
		IssuedAt:  jwt.NewNumericDate(time.Now()),
		ID:        string(k8suuid.NewUUID()),
	}).SignedString([]byte(os.Getenv("ROUTER_KEY")))

	if err != nil {
		logger.Error(err, "unable to sign token")
		return nil, status.Errorf(codes.Internal, "unable to sign token")
	}

	// TODO: find best router from list
	endpoint := routerEndpoint()

	response := &pb.ListenResponse{
		RouterEndpoint: endpoint,
		RouterToken:    token,
	}

	queue, _ := s.listenQueues.LoadOrStore(leaseName, make(chan *pb.ListenResponse, 8))
	select {
	case <-ctx.Done():
		return nil, ctx.Err()
	case queue.(chan *pb.ListenResponse) <- response:
	}

	logger.Info("Client dial assigned stream", "stream", stream)
	return &pb.DialResponse{
		RouterEndpoint: endpoint,
		RouterToken:    token,
	}, nil
}

func (s *ControllerService) GetLease(
	ctx context.Context,
	req *pb.GetLeaseRequest,
) (*pb.GetLeaseResponse, error) {
	client, err := s.authenticateClient(ctx)
	if err != nil {
		return nil, err
	}

	var lease jumpstarterdevv1alpha1.Lease
	if err := s.Client.Get(ctx, types.NamespacedName{
		Namespace: client.Namespace,
		Name:      req.Name,
	}, &lease); err != nil {
		return nil, err
	}

	if lease.Spec.ClientRef.Name != client.Name {
		return nil, fmt.Errorf("GetLease permission denied")
	}

	var matchExpressions []*pb.LabelSelectorRequirement
	for _, exp := range lease.Spec.Selector.MatchExpressions {
		matchExpressions = append(matchExpressions, &pb.LabelSelectorRequirement{
			Key:      exp.Key,
			Operator: string(exp.Operator),
			Values:   exp.Values,
		})
	}

	var beginTime *timestamppb.Timestamp
	if lease.Status.BeginTime != nil {
		beginTime = timestamppb.New(lease.Status.BeginTime.Time)
	}
	var endTime *timestamppb.Timestamp
	if lease.Status.EndTime != nil {
		beginTime = timestamppb.New(lease.Status.EndTime.Time)
	}
	var exporterUuid *string
	if lease.Status.ExporterRef != nil {
		var exporter jumpstarterdevv1alpha1.Exporter
		if err := s.Client.Get(
			ctx,
			types.NamespacedName{Namespace: client.Namespace, Name: lease.Status.ExporterRef.Name},
			&exporter,
		); err != nil {
			return nil, fmt.Errorf("GetLease fetch exporter uuid failed")
		}
		exporterUuid = (*string)(&exporter.UID)
	}

	var conditions []*pb.Condition
	for _, condition := range lease.Status.Conditions {
		conditions = append(conditions, &pb.Condition{
			Type:               &condition.Type,
			Status:             (*string)(&condition.Status),
			ObservedGeneration: &condition.ObservedGeneration,
			LastTransitionTime: &pb.Time{
				Seconds: &condition.LastTransitionTime.ProtoTime().Seconds,
				Nanos:   &condition.LastTransitionTime.ProtoTime().Nanos,
			},
			Reason:  &condition.Reason,
			Message: &condition.Message,
		})
	}

	return &pb.GetLeaseResponse{
		Duration:     durationpb.New(lease.Spec.Duration.Duration),
		Selector:     &pb.LabelSelector{MatchExpressions: matchExpressions, MatchLabels: lease.Spec.Selector.MatchLabels},
		BeginTime:    beginTime,
		EndTime:      endTime,
		ExporterUuid: exporterUuid,
		Conditions:   conditions,
	}, nil
}

func (s *ControllerService) RequestLease(
	ctx context.Context,
	req *pb.RequestLeaseRequest,
) (*pb.RequestLeaseResponse, error) {
	client, err := s.authenticateClient(ctx)
	if err != nil {
		return nil, err
	}

	var matchLabels map[string]string
	var matchExpressions []metav1.LabelSelectorRequirement
	if req.Selector != nil {
		matchLabels = req.Selector.MatchLabels
		for _, exp := range req.Selector.MatchExpressions {
			matchExpressions = append(matchExpressions, metav1.LabelSelectorRequirement{
				Key:      exp.Key,
				Operator: metav1.LabelSelectorOperator(exp.Operator),
				Values:   exp.Values,
			})
		}
	}

	leaseName, err := uuid.NewV7()
	if err != nil {
		return nil, err
	}

	var lease jumpstarterdevv1alpha1.Lease = jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: client.Namespace,
			Name:      leaseName.String(),
		},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			ClientRef: corev1.LocalObjectReference{
				Name: client.Name,
			},
			Duration: metav1.Duration{Duration: req.Duration.AsDuration()},
			Selector: metav1.LabelSelector{
				MatchLabels:      matchLabels,
				MatchExpressions: matchExpressions,
			},
		},
	}
	if err := s.Client.Create(ctx, &lease); err != nil {
		return nil, err
	}

	return &pb.RequestLeaseResponse{
		Name: lease.Name,
	}, nil
}

func (s *ControllerService) ReleaseLease(
	ctx context.Context,
	req *pb.ReleaseLeaseRequest,
) (*pb.ReleaseLeaseResponse, error) {
	jclient, err := s.authenticateClient(ctx)
	if err != nil {
		return nil, err
	}

	var lease jumpstarterdevv1alpha1.Lease
	if err := s.Client.Get(ctx, types.NamespacedName{
		Namespace: jclient.Namespace,
		Name:      req.Name,
	}, &lease); err != nil {
		return nil, err
	}

	if lease.Spec.ClientRef.Name != jclient.Name {
		return nil, fmt.Errorf("ReleaseLease permission denied")
	}

	original := client.MergeFrom(lease.DeepCopy())
	lease.Spec.Release = true

	if err := s.Client.Patch(ctx, &lease, original); err != nil {
		return nil, err
	}

	return &pb.ReleaseLeaseResponse{}, nil
}

func (s *ControllerService) ListLeases(
	ctx context.Context,
	req *pb.ListLeasesRequest,
) (*pb.ListLeasesResponse, error) {
	jclient, err := s.authenticateClient(ctx)
	if err != nil {
		return nil, err
	}

	var leases jumpstarterdevv1alpha1.LeaseList
	if err := s.Client.List(
		ctx,
		&leases,
		client.InNamespace(jclient.Namespace),
		controller.MatchingActiveLeases(),
	); err != nil {
		return nil, err
	}

	var leaseNames []string
	for _, lease := range leases.Items {
		if lease.Spec.ClientRef.Name == jclient.Name {
			leaseNames = append(leaseNames, lease.Name)
		}
	}

	return &pb.ListLeasesResponse{
		Names: leaseNames,
	}, nil
}

func (s *ControllerService) Start(ctx context.Context) error {
	logger := log.FromContext(ctx)

	dnsnames, ipaddresses, err := endpointToSAN(controllerEndpoint())
	if err != nil {
		return err
	}

	cert, err := NewSelfSignedCertificate("jumpstarter controller", dnsnames, ipaddresses)
	if err != nil {
		return err
	}

	server := grpc.NewServer(grpc.Creds(credentials.NewServerTLSFromCert(cert)), grpc.UnaryInterceptor(func(
		gctx context.Context,
		req any,
		_ *grpc.UnaryServerInfo,
		handler grpc.UnaryHandler,
	) (resp any, err error) {
		return handler(logContext(gctx), req)
	}), grpc.StreamInterceptor(func(
		srv any,
		ss grpc.ServerStream,
		_ *grpc.StreamServerInfo,
		handler grpc.StreamHandler,
	) error {
		return handler(srv, &wrappedStream{ServerStream: ss})
	}))

	pb.RegisterControllerServiceServer(server, s)

	// Register reflection service on gRPC server.
	reflection.Register(server)

	listener, err := net.Listen("tcp", ":8082")
	if err != nil {
		return err
	}

	logger.Info("Starting Controller grpc service")

	go func() {
		<-ctx.Done()
		logger.Info("Stopping Controller gRPC service")
		server.Stop()
	}()

	return server.Serve(listener)
}

// SetupWithManager sets up the controller with the Manager.
func (s *ControllerService) SetupWithManager(mgr ctrl.Manager) error {
	return mgr.Add(s)
}
