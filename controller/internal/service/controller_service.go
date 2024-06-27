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
	"time"

	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"golang.org/x/exp/slices"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/api/v1alpha1"
)

// ControlerService exposes a gRPC service
type ControllerService struct {
	pb.UnimplementedControllerServiceServer
	client.Client
	Scheme *runtime.Scheme
}

func getFromMetadata(md metadata.MD, key string) (string, bool) {
	values := md.Get(key)
	if len(values) < 1 {
		return "", false
	}
	return values[0], true
}

func (s *ControllerService) Register(ctx context.Context, req *pb.RegisterRequest) (*pb.RegisterResponse, error) {
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
			exporter.Status.Conditions = []metav1.Condition{{
				Type:               "Available",
				Status:             "True",
				ObservedGeneration: exporter.GetGeneration(),
				LastTransitionTime: metav1.Time{Time: time.Now()},
				Reason:             "Register",
				Message:            "",
			}}
			if err := s.Status().Update(ctx, &exporter); err != nil {
				return nil, status.Errorf(codes.Internal, "unable to update exporter status: %s", err)
			}
			return &pb.RegisterResponse{}, nil
		}
	}

	return nil, status.Errorf(codes.Unauthenticated, "no matching credential")
}

func (s *ControllerService) Start(ctx context.Context) error {
	log := log.FromContext(ctx)

	server := grpc.NewServer()

	// TODO: propagate base context

	pb.RegisterControllerServiceServer(server, s)

	os.Remove("/tmp/jumpstarter-controller.sock")

	// TODO: use TLS
	listener, err := net.Listen("unix", "/tmp/jumpstarter-controller.sock")
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
