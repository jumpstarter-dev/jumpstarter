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

package main

import (
	"context"
	"flag"
	"os"
	"os/signal"
	"syscall"

	ctrl "sigs.k8s.io/controller-runtime"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"

	"github.com/go-logr/logr"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/config"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/service"

	_ "google.golang.org/grpc/encoding/gzip"
)

var (
	// Version information - set via ldflags at build time
	version   = "dev"
	gitCommit = "unknown"
	buildDate = "unknown"
)

func main() {
	opts := zap.Options{}
	opts.BindFlags(flag.CommandLine)

	flag.Parse()

	ctrl.SetLogger(zap.New(zap.UseFlagOptions(&opts)))
	logger := ctrl.Log.WithName("router")
	ctx := logr.NewContext(context.Background(), logger)

	// Print version information
	logger.Info("Jumpstarter Router starting",
		"version", version,
		"gitCommit", gitCommit,
		"buildDate", buildDate,
	)

	cfg := ctrl.GetConfigOrDie()
	client, err := kclient.New(cfg, kclient.Options{})
	if err != nil {
		logger.Error(err, "failed to create k8s client")
		os.Exit(1)
	}

	serverOption, err := config.LoadRouterConfiguration(ctx, client, kclient.ObjectKey{
		Namespace: os.Getenv("NAMESPACE"),
		Name:      "jumpstarter-controller",
	})
	if err != nil {
		logger.Error(err, "failed to load router configuration")
		os.Exit(1)
	}

	svc := service.RouterService{
		ServerOption: serverOption,
	}

	err = svc.Start(ctx)
	if err != nil {
		logger.Error(err, "failed to start router service")
		os.Exit(1)
	}

	sigs := make(chan os.Signal, 1)
	signal.Notify(sigs, syscall.SIGINT, syscall.SIGTERM)
	sig := <-sigs
	logger.Info("received signal, exiting", "signal", sig)
}
