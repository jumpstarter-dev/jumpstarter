/*
Copyright 2026.

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
	"net/http"
	"testing"
	"time"
)

// The controller's gRPC streams stay open for as long as exporters and
// clients are connected, so stopping must not wait for them to end.
func TestServeUntilDoneReturnsWithAStreamOpen(t *testing.T) {
	lis, err := net.Listen("tcp", "127.0.0.1:0")
	if err != nil {
		t.Fatal(err)
	}
	streaming := make(chan struct{})
	srv := &http.Server{Handler: http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		w.WriteHeader(http.StatusOK)
		w.(http.Flusher).Flush()
		close(streaming)
		<-r.Context().Done()
	})}

	ctx, cancel := context.WithCancel(context.Background())
	served := make(chan error, 1)
	go func() { served <- serveUntilDone(ctx, srv, lis) }()

	resp, err := http.Get("http://" + lis.Addr().String())
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	<-streaming

	cancel()
	select {
	case err := <-served:
		if err != nil {
			t.Fatalf("serveUntilDone returned %v, want nil", err)
		}
	case <-time.After(5 * time.Second):
		t.Fatal("serveUntilDone did not return after its context was done")
	}
}
