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
	"net/url"
	"sync"
	"time"

	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"golang.org/x/exp/slices"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	"google.golang.org/protobuf/types/known/emptypb"
	authv1 "k8s.io/api/authentication/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/apimachinery/pkg/util/uuid"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/api/v1alpha1"
)

// Reference: config/default/kustomization.yaml
const nameSpace = "jumpstarter-router-system"
const namePrefix = "jumpstarter-router-"

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

func getFromMetadata(md metadata.MD, key string) (string, bool) {
	values := md.Get(key)
	if len(values) < 1 {
		return "", false
	}
	return values[0], true
}

func (s *ControllerService) authenticateExporter(ctx context.Context) (*jumpstarterdevv1alpha1.Exporter, error) {
	var exporter jumpstarterdevv1alpha1.Exporter

	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return nil, status.Errorf(codes.InvalidArgument, "missing metadata")
	}

	namespace, ok := getFromMetadata(md, "namespace")
	if !ok {
		return nil, status.Errorf(codes.InvalidArgument, "missing metadata: namespace")
	}

	name, ok := getFromMetadata(md, "name")
	if !ok {
		return nil, status.Errorf(codes.InvalidArgument, "missing metadata: name")
	}

	token, ok := getFromMetadata(md, "token")
	if !ok {
		return nil, status.Errorf(codes.InvalidArgument, "missing metadata: token")
	}

	exporterRef := types.NamespacedName{
		Namespace: namespace,
		Name:      name,
	}

	if err := s.Client.Get(
		ctx,
		exporterRef,
		&exporter,
	); err != nil {
		return nil, status.Errorf(codes.Internal, "unable to get exporter resource")
	}

	for _, ref := range exporter.Spec.Credentials {
		var secret corev1.Secret

		if err := s.Client.Get(ctx, types.NamespacedName{
			Namespace: ref.Namespace,
			Name:      ref.Name,
		}, &secret); err != nil {
			return nil, status.Errorf(codes.Internal, "unable to get secret resource")
		}

		if reference, ok := secret.Data["token"]; ok && slices.Equal(reference, []byte(token)) {
			return &exporter, nil
		}
	}

	return nil, status.Errorf(codes.Unauthenticated, "no matching credential")
}

func (s *ControllerService) Register(ctx context.Context, req *pb.RegisterRequest) (*pb.RegisterResponse, error) {
	exporter, err := s.authenticateExporter(ctx)
	if err != nil {
		return nil, err
	}

	exporter.Status.Conditions = []metav1.Condition{{
		Type:               "Available",
		Status:             "True",
		ObservedGeneration: exporter.GetGeneration(),
		LastTransitionTime: metav1.Time{Time: time.Now()},
		Reason:             "Register",
		Message:            "",
	}}
	if err := s.Status().Update(ctx, exporter); err != nil {
		return nil, status.Errorf(codes.Internal, "unable to update exporter status: %s", err)
	}

	return &pb.RegisterResponse{}, nil
}

func (s *ControllerService) Bye(ctx context.Context, req *pb.ByeRequest) (*emptypb.Empty, error) {
	exporter, err := s.authenticateExporter(ctx)
	if err != nil {
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
		return nil, status.Errorf(codes.Internal, "unable to update exporter status: %s", err)
	}

	return &emptypb.Empty{}, nil
}

func (s *ControllerService) Listen(req *pb.ListenRequest, stream pb.ControllerService_ListenServer) error {
	ctx := stream.Context()

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

	_, loaded := s.listen.LoadOrStore(exporter.GetName(), lctx)

	if loaded {
		return status.Errorf(codes.AlreadyExists, "exporter is already listening")
	}

	defer s.listen.Delete(exporter.GetName())

	select {
	case <-ctx.Done():
		return nil
	}
}

func (s *ControllerService) Dial(ctx context.Context, req *pb.DialRequest) (*pb.DialResponse, error) {
	// TODO: authenticate/authorize user with Identity/Lease resource

	value, ok := s.listen.Load(req.GetUuid())
	if !ok {
		return nil, status.Errorf(codes.Unavailable, "no matching listener")
	}

	stream := uuid.NewUUID()

	audience := (&url.URL{
		Scheme: "https",
		Host:   "router.jumpstarter.dev",
		Path:   fmt.Sprintf("/stream/%s", stream),
	}).String()

	expsecs := int64(3600)

	var tokenholder corev1.ServiceAccount

	tokenholderName := types.NamespacedName{
		Namespace: nameSpace,
		Name:      namePrefix + "tokenholder",
	}

	if err := s.Client.Get(ctx, tokenholderName, &tokenholder); err != nil {
		return nil, status.Errorf(codes.Internal, "failed to get tokenholder service account")
	}

	tokenRequest := authv1.TokenRequest{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: tokenholderName.Namespace,
			Name:      tokenholderName.Name,
		},
		Spec: authv1.TokenRequestSpec{
			Audiences:         []string{audience},
			ExpirationSeconds: &expsecs,
		},
	}

	if err := s.SubResource("token").Create(ctx, &tokenholder, &tokenRequest); err != nil {
		return nil, status.Errorf(codes.Internal, "failed to issue stream token: %s", err)
	}

	// TODO: find best router from list
	endpoint := "127.0.0.1:8083"

	if err := value.(listenContext).stream.Send(&pb.ListenResponse{
		RouterEndpoint: endpoint,
		RouterToken:    tokenRequest.Status.Token,
	}); err != nil {
		return nil, err
	}

	return &pb.DialResponse{
		RouterEndpoint: endpoint,
		RouterToken:    tokenRequest.Status.Token,
	}, nil
}

func (s *ControllerService) Start(ctx context.Context) error {
	log := log.FromContext(ctx)

	server := grpc.NewServer()

	// TODO: propagate base context

	pb.RegisterControllerServiceServer(server, s)

	listener, err := net.Listen("tcp", ":8082")
	if err != nil {
		return err
	}

	log.Info("Starting Controller Service")

	return server.Serve(listener)
}

// SetupWithManager sets up the controller with the Manager.
func (s *ControllerService) SetupWithManager(mgr ctrl.Manager) error {
	return mgr.Add(s)
}
