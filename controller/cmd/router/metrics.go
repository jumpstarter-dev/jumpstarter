/*
Copyright 2026. The Jumpstarter Authors.

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
	"net"
	"net/http"
	"time"

	"github.com/prometheus/client_golang/prometheus/promhttp"
)

// startMetricsServer starts an HTTP server exposing GET /metrics.
// addr "0" or empty disables the server and returns ("", nil).
// addr ending with ":0" binds an ephemeral port; the returned listen address
// is host:port suitable for http.Get.
func startMetricsServer(addr string) (string, error) {
	if addr == "" || addr == "0" {
		return "", nil
	}

	ln, err := net.Listen("tcp", addr)
	if err != nil {
		return "", err
	}

	mux := http.NewServeMux()
	mux.Handle("/metrics", promhttp.Handler())

	srv := &http.Server{
		Handler:           mux,
		ReadHeaderTimeout: 10 * time.Second,
		ReadTimeout:       30 * time.Second,
		WriteTimeout:      30 * time.Second,
		// IdleTimeout is generous so Prometheus scrape keepalives survive
		// typical scrape intervals without churning connections.
		IdleTimeout: 5 * time.Minute,
	}
	go func() {
		_ = srv.Serve(ln)
	}()

	return ln.Addr().String(), nil
}
