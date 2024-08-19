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
	"net"
	"os"
	"strings"
	"sync"
	"time"

	"github.com/golang-jwt/jwt/v5"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/reflection"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/durationpb"
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
	logger := log.FromContext(ctx)

	token, err := BearerTokenFromContext(ctx)
	if err != nil {
		return nil, err
	}

	clientRef, err := controller.VerifyObjectToken(
		token,
		"https://jumpstarter.dev/controller",
		"https://jumpstarter.dev/controller",
	)
	if err != nil {
		return nil, err
	}

	var client jumpstarterdevv1alpha1.Client

	logger.Info("authenticating client", "client", clientRef)
	if err := s.Client.Get(
		ctx,
		types.NamespacedName{
			Namespace: clientRef.Namespace,
			Name:      clientRef.Name,
		},
		&client,
	); err != nil {
		logger.Error(err, "unable to get client resource", "client", clientRef)
		return nil, status.Errorf(codes.Internal, "unable to get client resource")
	}

	if client.UID != clientRef.UID {
		return nil, status.Errorf(codes.Internal, "client UID mismatch")
	}

	return &client, nil
}

func (s *ControllerService) authenticateExporter(ctx context.Context) (*jumpstarterdevv1alpha1.Exporter, error) {
	token, err := BearerTokenFromContext(ctx)
	if err != nil {
		return nil, err
	}

	exporterRef, err := controller.VerifyObjectToken(
		token,
		"https://jumpstarter.dev/controller",
		"https://jumpstarter.dev/controller",
	)
	if err != nil {
		return nil, err
	}

	var exporter jumpstarterdevv1alpha1.Exporter

	if err := s.Client.Get(
		ctx,
		types.NamespacedName{
			Namespace: exporterRef.Namespace,
			Name:      exporterRef.Name,
		},
		&exporter,
	); err != nil {
		return nil, status.Errorf(codes.Internal, "unable to get exporter resource")
	}

	if exporter.UID != exporterRef.UID {
		return nil, status.Errorf(codes.Internal, "client UID mismatch")
	}

	return &exporter, nil
}

func (s *ControllerService) Register(ctx context.Context, req *pb.RegisterRequest) (*pb.RegisterResponse, error) {

	logger := log.FromContext(ctx)

	logger.Info("Registering exporter", "request", req)

	exporter, err := s.authenticateExporter(ctx)
	if err != nil {
		logger.Error(err, "unable to authenticate exporter")
		return nil, err
	}

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

	if err := s.Update(ctx, exporter); err != nil {
		logger.Error(err, "unable to update exporter", "exporter", exporter)
		return nil, status.Errorf(codes.Internal, "unable to update exporter: %s", err)
	}

	exporter.Status.Conditions = []metav1.Condition{{
		Type:               "Available",
		Status:             "True",
		ObservedGeneration: exporter.GetGeneration(),
		LastTransitionTime: metav1.Time{Time: time.Now()},
		Reason:             "Register",
		Message:            "",
	}}

	devices := []jumpstarterdevv1alpha1.Device{}
	for _, device := range req.Reports {
		devices = append(devices, jumpstarterdevv1alpha1.Device{
			Uuid:       device.Uuid,
			ParentUuid: device.ParentUuid,
			Labels:     device.Labels,
		})
	}
	exporter.Status.Uuid = req.Uuid
	exporter.Status.Devices = devices

	if err := s.Status().Update(ctx, exporter); err != nil {
		logger.Error(err, "unable to update exporter status", "exporter", exporter)
		return nil, status.Errorf(codes.Internal, "unable to update exporter status: %s", err)
	}

	return &pb.RegisterResponse{}, nil
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

	exporter.Status.Conditions = []metav1.Condition{{
		Type:               "Available",
		Status:             "False",
		ObservedGeneration: exporter.GetGeneration(),
		LastTransitionTime: metav1.Time{Time: time.Now()},
		Reason:             "Bye",
		Message:            req.GetReason(),
	}}

	if err := s.Status().Update(ctx, exporter); err != nil {
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
			Uuid:    exporter.Status.Uuid,
			Labels:  exporter.GetLabels(),
			Reports: reports,
		}
	}

	return &pb.ListExportersResponse{
		Exporters: results,
	}, nil
}

func (s *ControllerService) LeaseExporter(
	ctx context.Context,
	req *pb.LeaseExporterRequest,
) (*pb.LeaseExporterResponse, error) {
	// TODO: implement permission checking and book keeping
	return &pb.LeaseExporterResponse{
		LeaseExporterResponseOneof: &pb.LeaseExporterResponse_Success{
			Success: &pb.LeaseExporterResponseSuccess{
				Duration: durationpb.New(req.Duration.AsDuration()),
			},
		},
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

	_, loaded := s.listen.LoadOrStore(exporter.Status.Uuid, lctx)

	if loaded {
		// TODO: in this case we should probably end the previous listener
		//       and start the new one?
		logger.Error(nil, "exporter is already listening", "exporter", exporter.GetName())
		return status.Errorf(codes.AlreadyExists, "exporter is already listening")
	}

	defer s.listen.Delete(exporter.GetName())

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

	value, ok := s.listen.Load(req.GetUuid())
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

func (s *ControllerService) Start(ctx context.Context) error {
	logger := log.FromContext(ctx)

	server := grpc.NewServer()

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
