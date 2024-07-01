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
	"net/url"
	"strings"
	"sync"

	"github.com/golang-jwt/jwt/v5"
	pb "github.com/jumpstarter-dev/jumpstarter-protocol/go/jumpstarter/v1"
	"golang.org/x/exp/slices"
	"google.golang.org/grpc"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/metadata"
	"google.golang.org/grpc/status"
	authv1 "k8s.io/api/authentication/v1"
	"k8s.io/apimachinery/pkg/runtime"
	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

// RouterService exposes a gRPC service
type RouterService struct {
	pb.UnimplementedRouterServiceServer
	client.Client
	Scheme  *runtime.Scheme
	pending sync.Map
}

type streamContext struct {
	cancel context.CancelFunc
	stream pb.RouterService_StreamServer
}

func (s *RouterService) authenticate(ctx context.Context) (string, error) {
	md, ok := metadata.FromIncomingContext(ctx)
	if !ok {
		return "", status.Errorf(codes.InvalidArgument, "missing metadata")
	}

	authorizations := md.Get("authorization")

	if len(authorizations) < 1 {
		return "", status.Errorf(codes.Unauthenticated, "missing authorization header")
	}

	// Reference: https://www.rfc-editor.org/rfc/rfc7230#section-3.2.2
	// A sender MUST NOT generate multiple header fields with the same field name in a message
	if len(authorizations) > 1 {
		return "", status.Errorf(codes.InvalidArgument, "multiple authorization headers")
	}

	// Invariant: len(authorizations) == 1
	authorization := authorizations[0]

	// Reference: https://github.com/golang-jwt/jwt/blob/62e504c2/request/extractor.go#L93
	if len(authorization) < 7 || !strings.EqualFold(authorization[:7], "Bearer ") {
		return "", status.Errorf(codes.InvalidArgument, "malformed authorization header")
	}

	// Invariant: len(authorization) >= 7
	token := authorization[7:]

	parser := jwt.NewParser()

	parsed, _, err := parser.ParseUnverified(token, &jwt.RegisteredClaims{})
	if err != nil {
		return "", status.Errorf(codes.InvalidArgument, "invalid jwt token")
	}

	audiences, err := parsed.Claims.GetAudience()
	if err != nil {
		return "", status.Errorf(codes.InvalidArgument, "invalid jwt audience")
	}

	var matched []*url.URL

	for _, audience := range audiences {
		aud, err := url.Parse(audience)
		// skip unrecognized audiences
		if err != nil {
			continue
		}
		// skip non local audiences
		if aud.Scheme != "https" || aud.Host != "router.jumpstarter.dev" || !strings.HasPrefix(aud.Path, "/stream/") {
			continue
		}
		// add local audience to matched list
		matched = append(matched, aud)
	}

	if len(matched) != 1 {
		return "", status.Errorf(codes.InvalidArgument, "invalid number of local jwt audience")
	}

	// Invariant: len(matched) == 1
	audience := matched[0]

	tokenReview := authv1.TokenReview{
		Spec: authv1.TokenReviewSpec{
			Token:     token,
			Audiences: []string{audience.String()},
		},
	}
	if err := s.Client.Create(ctx, &tokenReview); err != nil {
		return "", status.Errorf(codes.Unauthenticated, "failed to create token review")
	}

	if !tokenReview.Status.Authenticated ||
		tokenReview.Status.User.Username != "system:serviceaccount:"+nameSpace+":"+namePrefix+"tokenholder" ||
		!slices.Contains(tokenReview.Status.Audiences, audience.String()) {
		return "", status.Errorf(codes.Unauthenticated, "unauthenticated jwt token")
	}

	return strings.TrimPrefix(audience.Path, "/stream/"), nil
}

func (s *RouterService) Stream(stream pb.RouterService_StreamServer) error {
	ctx := stream.Context()

	streamName, err := s.authenticate(ctx)
	if err != nil {
		return err
	}

	ctx, cancel := context.WithCancel(ctx)
	defer cancel()

	sctx := streamContext{
		cancel: cancel,
		stream: stream,
	}

	actual, loaded := s.pending.LoadOrStore(streamName, sctx)
	if loaded {
		defer actual.(streamContext).cancel()
		return forward(ctx, stream, actual.(streamContext).stream)
	} else {
		select {
		case <-ctx.Done():
			return nil
		}
	}
}

func (s *RouterService) Start(ctx context.Context) error {
	log := log.FromContext(ctx)

	server := grpc.NewServer()

	// TODO: propagate base context

	pb.RegisterRouterServiceServer(server, s)

	listener, err := net.Listen("tcp", ":8083")
	if err != nil {
		return err
	}

	log.Info("Starting Router Service")

	return server.Serve(listener)
}

// SetupWithManager sets up the controller with the Manager.
func (s *RouterService) SetupWithManager(mgr ctrl.Manager) error {
	return mgr.Add(s)
}
