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
	"strings"

	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"google.golang.org/grpc"
	"google.golang.org/grpc/reflection"
	ctrl "sigs.k8s.io/controller-runtime"
)

// TelemetryService receives structured log entries from exporters and clients,
// logs them via structured stdout, and will forward them to Loki in a future phase.
type TelemetryService struct {
	pb.UnimplementedTelemetryServiceServer

	// BindAddr is the TCP address to listen on (e.g. ":9093").
	BindAddr string
}

// PushLogs receives a batch of structured log entries and writes them via the
// controller-runtime logger (structured JSON to stdout).
// Future phase: forward to Loki push API.
func (s *TelemetryService) PushLogs(ctx context.Context, req *pb.PushLogsRequest) (*pb.PushLogsResponse, error) {
	logger := ctrl.Log.WithName("telemetry")

	for _, entry := range req.Entries {
		kvs := []any{
			"component", entry.Component,
			"exporter", entry.Exporter,
		}
		if entry.Lease != "" {
			kvs = append(kvs, "lease", entry.Lease)
		}
		if entry.Client != "" {
			kvs = append(kvs, "client", entry.Client)
		}
		if entry.Operation != "" {
			kvs = append(kvs, "operation", entry.Operation)
		}
		if entry.Result != "" {
			kvs = append(kvs, "result", entry.Result)
		}
		if entry.DriverType != "" {
			kvs = append(kvs, "driver_type", entry.DriverType)
		}
		// Append extra fields from spec.context and any driver-specific fields.
		for k, v := range entry.ExtraFields {
			kvs = append(kvs, k, v)
		}

		switch strings.ToLower(entry.Severity) {
		case "error", "critical":
			logger.Error(nil, entry.Message, kvs...)
		default:
			logger.Info(entry.Message, kvs...)
		}
	}

	return &pb.PushLogsResponse{
		Accepted: uint32(len(req.Entries)),
	}, nil
}

// Start starts the TelemetryService gRPC server and blocks until ctx is cancelled.
func (s *TelemetryService) Start(ctx context.Context) error {
	logger := ctrl.Log.WithName("telemetry")

	lis, err := net.Listen("tcp", s.BindAddr)
	if err != nil {
		return fmt.Errorf("telemetry: listen %s: %w", s.BindAddr, err)
	}

	srv := grpc.NewServer()
	pb.RegisterTelemetryServiceServer(srv, s)
	reflection.Register(srv)

	logger.Info("Telemetry service listening", "addr", s.BindAddr)

	errCh := make(chan error, 1)
	go func() {
		errCh <- srv.Serve(lis)
	}()

	select {
	case <-ctx.Done():
		srv.GracefulStop()
		return nil
	case err := <-errCh:
		return err
	}
}
