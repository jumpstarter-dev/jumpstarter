// Package healthz provides custom health check functions for controller-runtime managers.
package healthz

import (
	"fmt"
	"net/http"

	"sigs.k8s.io/controller-runtime/pkg/healthz"
	"sigs.k8s.io/controller-runtime/pkg/manager"
)

// LeaderElectionCheck returns a healthz.Checker that reports not-ready until
// the manager has been elected leader (or leader election is disabled).
// This ensures that non-leader replicas are removed from Service endpoints,
// preventing traffic from reaching pods that are not actively reconciling
// or serving gRPC.
func LeaderElectionCheck(mgr manager.Manager) healthz.Checker {
	return func(_ *http.Request) error {
		select {
		case <-mgr.Elected():
			return nil
		default:
			return fmt.Errorf("not yet leader")
		}
	}
}
