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
	pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/credentials"
	"google.golang.org/grpc/reflection"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/durationpb"
	"google.golang.org/protobuf/types/known/timestamppb"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/selection"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/uuid"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/controller"
)

// ControlerService exposes a gRPC service
type ControllerService struct {
	pb.UnimplementedControllerServiceServer
	client.Client
	Scheme *runtime.Scheme
	listen sync.Map
}

type listenContext struct {
	cancel context.CancelFunc
	stream pb.ControllerService_ListenServer
}

func (s *ControllerService) authenticateClient(ctx context.Context) (*jumpstarterdevv1alpha1.Client, error) {
	token, err := BearerTokenFromContext(ctx)
	if err != nil {
		return nil, err
	}

	return controller.VerifyObjectToken[jumpstarterdevv1alpha1.Client](
		ctx,
		token,
		"https://jumpstarter.dev/controller",
		"https://jumpstarter.dev/controller",
		s.Client,
	)
}

func (s *ControllerService) authenticateExporter(ctx context.Context) (*jumpstarterdevv1alpha1.Exporter, error) {
	token, err := BearerTokenFromContext(ctx)
	if err != nil {
		return nil, err
	}

	return controller.VerifyObjectToken[jumpstarterdevv1alpha1.Exporter](
		ctx,
		token,
		"https://jumpstarter.dev/controller",
		"https://jumpstarter.dev/controller",
		s.Client,
	)
}

func (s *ControllerService) Register(ctx context.Context, req *pb.RegisterRequest) (*pb.RegisterResponse, error) {

	logger := log.FromContext(ctx)

	logger.Info("Registering exporter", "request", req)

	exporter, err := s.authenticateExporter(ctx)
	if err != nil {
		logger.Error(err, "unable to authenticate exporter")
		return nil, err
	}

	original := client.StrategicMergeFrom(exporter.DeepCopy())

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

	if err := s.Patch(ctx, exporter, original); err != nil {
		logger.Error(err, "unable to update exporter", "exporter", exporter)
		return nil, status.Errorf(codes.Internal, "unable to update exporter: %s", err)
	}

	original = client.StrategicMergeFrom(exporter.DeepCopy())

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

	if err := s.Status().Patch(ctx, exporter, original); err != nil {
		logger.Error(err, "unable to update exporter status", "exporter", exporter)
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

	original := client.StrategicMergeFrom(exporter.DeepCopy())
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

	if err := s.Status().Patch(ctx, exporter, original); err != nil {
		logger.Error(err, "unable to update exporter status", "exporter", exporter.Name)
		return nil, status.Errorf(codes.Internal, "unable to update exporter status: %s", err)
	}

	logger.Info("exporter unregistered, updated as unavailable", "exporter", exporter.Name)

	return &pb.UnregisterResponse{}, nil
}

func (s *ControllerService) ListExporters(
	ctx context.Context,
	req *pb.ListExportersRequest,
) (*pb.ListExportersResponse, error) {
	logger := log.FromContext(ctx)

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

	if err := s.List(ctx, &exporters, &client.ListOptions{
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

	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	lctx := listenContext{
		cancel: cancel,
		stream: stream,
	}

	_, loaded := s.listen.LoadOrStore(exporter.UID, lctx)

	if loaded {
		// TODO: in this case we should probably end the previous listener
		//       and start the new one?
		logger.Error(nil, "exporter is already listening", "exporter", exporter.GetName())
		return status.Errorf(codes.AlreadyExists, "exporter is already listening")
	}

	defer func() {
		s.listen.Delete(exporter.UID)
		original := client.StrategicMergeFrom(exporter.DeepCopy())
		meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
			Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
			Status:             metav1.ConditionFalse,
			ObservedGeneration: exporter.Generation,
			LastTransitionTime: metav1.Time{
				Time: time.Now(),
			},
			Reason: "Disconnect",
		})
		if err = s.Status().Patch(ctx, exporter, original); err != nil {
			logger.Error(err, "unable to update exporter status", "exporter", exporter)
		}
	}()

	original := client.StrategicMergeFrom(exporter.DeepCopy())
	meta.SetStatusCondition(&exporter.Status.Conditions, metav1.Condition{
		Type:               string(jumpstarterdevv1alpha1.ExporterConditionTypeOnline),
		Status:             metav1.ConditionTrue,
		ObservedGeneration: exporter.Generation,
		LastTransitionTime: metav1.Time{
			Time: time.Now(),
		},
		Reason: "Connect",
	})
	if err = s.Status().Patch(ctx, exporter, original); err != nil {
		logger.Error(err, "unable to update exporter status", "exporter", exporter)
	}

	<-ctx.Done()
	return nil
}

func (s *ControllerService) Dial(ctx context.Context, req *pb.DialRequest) (*pb.DialResponse, error) {
	logger := log.FromContext(ctx)
	client, err := s.authenticateClient(ctx)
	if err != nil {
		logger.Error(err, "unable to authenticate client")
		return nil, err
	}

	// TODO: authorize user with Client/Lease resource

	value, ok := s.listen.Load(types.UID(req.GetUuid()))
	if !ok {
		logger.Error(nil, "no matching listener", "client", client.GetName(), "uuid", req.GetUuid())
		return nil, status.Errorf(codes.Unavailable, "no matching listener")
	}

	// TODO: put the name of the listener in the listen context, so we can
	//       log it here

	stream := uuid.NewUUID()

	token, err := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.RegisteredClaims{
		Issuer:    "https://jumpstarter.dev/stream",
		Subject:   string(stream),
		Audience:  []string{"https://jumpstarter.dev/router"},
		ExpiresAt: jwt.NewNumericDate(time.Now().Add(time.Minute * 30)),
		NotBefore: jwt.NewNumericDate(time.Now()),
		IssuedAt:  jwt.NewNumericDate(time.Now()),
		ID:        string(uuid.NewUUID()),
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

	if err := value.(listenContext).stream.Send(response); err != nil {
		logger.Error(err, "failed to send listen response", "response", response)
		return nil, err
	}

	logger.Info("Client dial assigned stream ", "client", client.GetName(), "stream", stream)
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
	if err := s.Get(ctx, types.NamespacedName{
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

	var lease jumpstarterdevv1alpha1.Lease = jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: client.Namespace,
			Name:      string(uuid.NewUUID()), // TODO: human readable name
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
	if err := s.Create(ctx, &lease); err != nil {
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
	if err := s.Get(ctx, types.NamespacedName{
		Namespace: jclient.Namespace,
		Name:      req.Name,
	}, &lease); err != nil {
		return nil, err
	}

	if lease.Spec.ClientRef.Name != jclient.Name {
		return nil, fmt.Errorf("ReleaseLease permission denied")
	}

	original := client.StrategicMergeFrom(lease.DeepCopy())
	lease.Spec.Release = true

	if err := s.Patch(ctx, &lease, original); err != nil {
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
	if err := s.List(
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

	server := grpc.NewServer(grpc.Creds(credentials.NewServerTLSFromCert(cert)))

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
