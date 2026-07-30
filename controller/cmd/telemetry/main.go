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

// jumpstarter-telemetry receives structured log entries from exporters and clients
// via the PushLogs gRPC RPC and writes them to structured stdout for downstream
// log shippers (Promtail, Grafana Alloy, Vector) to forward to Loki.
//
// Future phases will add direct Loki push and MetricsStream for reverse-scrape
// of exporter prometheus_client registries.
package main

import (
	"context"
	"flag"
	"os"
	"os/signal"
	"syscall"

	ctrl "sigs.k8s.io/controller-runtime"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"

	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service"
)

var (
	// Version information — set via ldflags at build time.
	version   = "dev"
	gitCommit = "unknown"
	buildDate = "unknown"
)

func main() {
	var bindAddr string
	flag.StringVar(&bindAddr, "grpc-bind", ":9093", "TCP address to bind the gRPC server to")

	opts := zap.Options{}
	opts.BindFlags(flag.CommandLine)
	flag.Parse()

	ctrl.SetLogger(zap.New(zap.UseFlagOptions(&opts)).WithValues("component", "telemetry"))
	logger := ctrl.Log.WithName("setup")

	logger.Info("Jumpstarter Telemetry starting",
		"version", version,
		"gitCommit", gitCommit,
		"buildDate", buildDate,
		"bindAddr", bindAddr,
	)

	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	svc := &service.TelemetryService{
		BindAddr: bindAddr,
	}

	errCh := make(chan error, 1)
	go func() {
		errCh <- svc.Start(ctx)
	}()

	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)

	select {
	case sig := <-sigs:
		logger.Info("received signal, shutting down", "signal", sig)
		cancel()
	case err := <-errCh:
		if err != nil {
			logger.Error(err, "telemetry service exited with error")
			os.Exit(1)
		}
	}
}
