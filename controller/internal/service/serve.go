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
	"errors"
	"net"
	"net/http"
)

// serveUntilDone serves srv on lis until ctx is done, then closes it.
//
// The manager stops its runnables by cancelling their context and waits for
// them to return before it gives up the leader lease, for up to its graceful
// shutdown period. A server that only returned once its listener failed kept
// a stopping controller holding the lease for that whole period.
func serveUntilDone(ctx context.Context, srv *http.Server, lis net.Listener) error {
	go func() {
		<-ctx.Done()
		_ = srv.Close()
	}()
	if err := srv.Serve(lis); !errors.Is(err, http.ErrServerClosed) {
		return err
	}
	return nil
}
