package hofacade

import (
	"context"
	"encoding/json"
	"net/http"
	"time"

	"github.com/google/uuid"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/hofacade/howire"
)

// INVARIANT: no non-operation response body may carry a top-level "done" key.
// The Python driver duck-types responses — any dict with "done" is treated as
// an Operation (driver.py _do_operation) — so only howire.Operation values
// may reach the wire with that key. Guarded by TestNoDoneKeyOutsideOperations
// and howire.TestOnlyOperationEmitsDoneKey.

// createCVD implements POST /cvds (F1/F2): authenticate, create an ordinary
// Lease for the caller, return a not-done Operation immediately. It never
// blocks on acquisition — pool exhaustion is a pending operation, not an
// error (F4).
func (s *Server) createCVD(r *http.Request, caller *jumpstarterdevv1alpha1.Client) (any, error) {
	var req howire.CreateCVDRequest
	if err := json.NewDecoder(r.Body).Decode(&req); err != nil {
		return nil, NewBadRequestError(howire.MsgMalformedJSON, err)
	}
	// env_config is an opaque black box (F2): never parsed, validated or
	// logged beyond its length. In v0 the pool's prewarm boots the device, so
	// it is otherwise unused.
	_ = len(req.EnvConfig)

	leaseName, err := uuid.NewV7()
	if err != nil {
		return nil, NewInternalError(howire.MsgInternal, err)
	}
	// The RequestLease shape (controller_service.go:1057-1074). ClientRef is
	// ALWAYS the authenticated caller: nothing in the request body can
	// influence attribution.
	lease := jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: s.Namespace,
			Name:      leaseName.String(),
		},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			ClientRef: corev1.LocalObjectReference{Name: caller.Name},
			Selector:  *s.PoolSelector,
			Duration:  &metav1.Duration{Duration: s.LeaseDuration},
		},
	}
	if err := s.Client.Create(r.Context(), &lease); err != nil {
		return nil, NewInternalError(howire.MsgInternal, err)
	}
	return CreateOperation(&lease), nil
}

// listCVDs implements GET /cvds: all of the caller's active acquired leases
// rendered as CVDs — and nothing else (F5/F10). Deliberate semantic
// divergence from upstream (which lists every group on the host) with an
// identical wire shape; leases created via `jmp` render too (the listing is
// not filtered to the fronted pool — --exporter-set only governs create
// selectors).
func (s *Server) listCVDs(r *http.Request, caller *jumpstarterdevv1alpha1.Client) (any, error) {
	leases, err := s.CallerActiveLeases(r.Context(), caller)
	if err != nil {
		return nil, NewInternalError(howire.MsgInternal, err)
	}
	cvds := []*howire.CVD{}
	for i := range leases {
		if leases[i].Status.ExporterRef != nil && !leases[i].Status.Ended {
			cvds = append(cvds, CVDFromLease(&leases[i]))
		}
	}
	return howire.NewListCVDsResponse(cvds), nil
}

// getCVDGroup implements GET /cvds/{group}.
func (s *Server) getCVDGroup(r *http.Request, caller *jumpstarterdevv1alpha1.Client) (any, error) {
	return s.getCVDCommon(r, caller, r.PathValue("group"), "")
}

// getCVD implements GET /cvds/{group}/{name}.
func (s *Server) getCVD(r *http.Request, caller *jumpstarterdevv1alpha1.Client) (any, error) {
	return s.getCVDCommon(r, caller, r.PathValue("group"), r.PathValue("name"))
}

func (s *Server) getCVDCommon(r *http.Request, caller *jumpstarterdevv1alpha1.Client, group, name string) (any, error) {
	lease, appErr := s.ResolveCallerLease(r.Context(), caller, group, msgCVDNotFound)
	if appErr != nil {
		return nil, appErr
	}
	// Ended, not-yet-acquired, and instance-name-mismatch are all the same
	// 404 as nonexistent (tenancy indistinguishability, F10).
	if lease.Status.Ended || lease.Status.ExporterRef == nil {
		return nil, NewNotFoundError(msgCVDNotFound, nil)
	}
	if name != "" && name != lease.Status.ExporterRef.Name {
		return nil, NewNotFoundError(msgCVDNotFound, nil)
	}
	return howire.NewListCVDsResponse([]*howire.CVD{CVDFromLease(lease)}), nil
}

// deleteCVDGroup implements DELETE /cvds/{group} (F7): release the lease by
// patching spec.release=true — NEVER kclient.Delete — and return the derived
// release Operation. Idempotent: a repeat DELETE returns the same operation.
func (s *Server) deleteCVDGroup(r *http.Request, caller *jumpstarterdevv1alpha1.Client) (any, error) {
	return s.deleteCVDCommon(r, caller, r.PathValue("group"), "")
}

// deleteCVD implements DELETE /cvds/{group}/{name}. Every CVD is exactly one
// lease (one-CVD groups), so deleting the instance releases the lease too;
// an instance name that is not the assigned exporter is a 404.
func (s *Server) deleteCVD(r *http.Request, caller *jumpstarterdevv1alpha1.Client) (any, error) {
	return s.deleteCVDCommon(r, caller, r.PathValue("group"), r.PathValue("name"))
}

func (s *Server) deleteCVDCommon(r *http.Request, caller *jumpstarterdevv1alpha1.Client, group, name string) (any, error) {
	lease, appErr := s.ResolveCallerLease(r.Context(), caller, group, msgCVDNotFound)
	if appErr != nil {
		return nil, appErr
	}
	if lease.Status.Ended {
		return nil, NewNotFoundError(msgCVDNotFound, nil)
	}
	if name != "" && (lease.Status.ExporterRef == nil || name != lease.Status.ExporterRef.Name) {
		return nil, NewNotFoundError(msgCVDNotFound, nil)
	}
	if !lease.Spec.Release {
		patch := kclient.MergeFrom(lease.DeepCopy())
		lease.Spec.Release = true
		if err := s.Client.Patch(r.Context(), lease, patch); err != nil {
			return nil, NewInternalError(howire.MsgInternal, err)
		}
	}
	return ReleaseOperation(lease), nil
}

// opKind classifies a resolved operation name.
type opKind int

const (
	opKindCreate opKind = iota
	opKindRelease
	opKindReset
)

// resolveOperation is the stateless operation resolver: (a) a lease named
// exactly like the operation is a create op; (b) a name matching the derived
// release-op name of one of the caller's leases whose release was requested
// or that already ended is a release op (a release op only "exists" once it
// was started — the mapping.go phase-table invariant); (c) the caller's
// derived reset name is the reset op; (d) anything else — including other
// clients' operations — is the uniform 404 (F10).
func (s *Server) resolveOperation(ctx context.Context, caller *jumpstarterdevv1alpha1.Client, name string) (opKind, *jumpstarterdevv1alpha1.Lease, *AppError) {
	var lease jumpstarterdevv1alpha1.Lease
	err := s.getLease(ctx, name, &lease)
	switch {
	case err == nil:
		if lease.Spec.ClientRef.Name != caller.Name {
			return 0, nil, NewNotFoundError(howire.MsgOperationNotFound, nil)
		}
		return opKindCreate, &lease, nil
	case kclient.IgnoreNotFound(err) != nil:
		return 0, nil, NewInternalError(howire.MsgInternal, err)
	}

	leases, lerr := s.callerLeases(ctx, caller, false)
	if lerr != nil {
		return 0, nil, NewInternalError(howire.MsgInternal, lerr)
	}
	for i := range leases {
		if !leases[i].Spec.Release && !leases[i].Status.Ended {
			// Release was never requested and the lease has not ended: the
			// derived release op does not exist yet, so its name must 404
			// like any other unknown operation.
			continue
		}
		if ReleaseOperationName(leases[i].Name) == name {
			return opKindRelease, &leases[i], nil
		}
	}

	if name == ResetOperationName(caller.Name) {
		return opKindReset, nil, nil
	}
	return 0, nil, NewNotFoundError(howire.MsgOperationNotFound, nil)
}

// operationView renders the Operation object for GET /operations/{name}.
func (s *Server) operationView(ctx context.Context, caller *jumpstarterdevv1alpha1.Client, kind opKind, lease *jumpstarterdevv1alpha1.Lease) (howire.Operation, *AppError) {
	switch kind {
	case opKindCreate:
		return CreateOperation(lease), nil
	case opKindRelease:
		return ReleaseOperation(lease), nil
	default: // opKindReset
		done, err := s.resetDone(ctx, caller)
		if err != nil {
			return howire.Operation{}, NewInternalError(howire.MsgInternal, err)
		}
		return howire.Operation{Name: ResetOperationName(caller.Name), Done: done}, nil
	}
}

// operationOutcome computes (done, result-value, result-error) for /result
// and /:wait, per the phase table in mapping.go.
func (s *Server) operationOutcome(ctx context.Context, caller *jumpstarterdevv1alpha1.Client, kind opKind, lease *jumpstarterdevv1alpha1.Lease) (bool, any, *AppError) {
	switch kind {
	case opKindCreate:
		if !CreateOperation(lease).Done {
			return false, nil, nil
		}
		value, appErr := CreateResult(lease)
		return true, value, appErr
	case opKindRelease:
		if !lease.Status.Ended {
			return false, nil, nil
		}
		// A completed delete-style operation's value is {} (upstream
		// execcvdcommandaction.go:73-76).
		return true, &howire.EmptyResponse{}, nil
	default: // opKindReset
		done, err := s.resetDone(ctx, caller)
		if err != nil {
			return false, nil, NewInternalError(howire.MsgInternal, err)
		}
		if !done {
			return false, nil, nil
		}
		return true, &howire.EmptyResponse{}, nil
	}
}

// getOperation implements GET /operations/{name}.
func (s *Server) getOperation(r *http.Request, caller *jumpstarterdevv1alpha1.Client) (any, error) {
	kind, lease, appErr := s.resolveOperation(r.Context(), caller, r.PathValue("name"))
	if appErr != nil {
		return nil, appErr
	}
	op, appErr := s.operationView(r.Context(), caller, kind, lease)
	if appErr != nil {
		return nil, appErr
	}
	return op, nil
}

// listOperations implements GET /operations: only the caller's RUNNING
// (not-done) operations, mirroring upstream MapOM.ListRunning
// (operation.go:112-122) — done operations leave the list but stay fetchable
// by name. Reset operations are never listed: they are derived on demand and
// have no "created" record in the stateless model (documented gap).
func (s *Server) listOperations(r *http.Request, caller *jumpstarterdevv1alpha1.Client) (any, error) {
	leases, err := s.CallerActiveLeases(r.Context(), caller)
	if err != nil {
		return nil, NewInternalError(howire.MsgInternal, err)
	}
	ops := []howire.Operation{}
	for i := range leases {
		if createOp := CreateOperation(&leases[i]); !createOp.Done {
			ops = append(ops, createOp)
		}
		if leases[i].Spec.Release && !leases[i].Status.Ended {
			ops = append(ops, ReleaseOperation(&leases[i]))
		}
	}
	return &howire.ListOperationsResponse{Operations: ops}, nil
}

// getOperationResult implements GET /operations/{name}/result, mirroring
// upstream getOperationResultHandler (controller.go:599-618): 404 "Operation
// not found" for unknown, 404 "Operation not done" while pending, the stored
// error's own status/body for failed operations, else 200 + value.
func (s *Server) getOperationResult(r *http.Request, caller *jumpstarterdevv1alpha1.Client) (any, error) {
	kind, lease, appErr := s.resolveOperation(r.Context(), caller, r.PathValue("name"))
	if appErr != nil {
		return nil, appErr
	}
	done, value, resErr := s.operationOutcome(r.Context(), caller, kind, lease)
	if !done {
		return nil, NewNotFoundError(howire.MsgOperationNotDone, nil)
	}
	if resErr != nil {
		return nil, resErr
	}
	return value, nil
}

// waitOperation implements POST /operations/{name}/:wait, mirroring upstream
// waitOperationHandler (controller.go:620-644): block up to WaitDuration for
// the operation to finish, then reply exactly as /result would; on expiry
// reply 503 {"error":"Wait for operation timed out"} — the driver's retry
// loop depends on that exact shape. Implemented as a bounded poll against
// the (cached) client; watch-based waiting is a production follow-up.
func (s *Server) waitOperation(r *http.Request, caller *jumpstarterdevv1alpha1.Client) (any, error) {
	ctx := r.Context()
	name := r.PathValue("name")

	kind, lease, appErr := s.resolveOperation(ctx, caller, name)
	if appErr != nil {
		return nil, appErr
	}

	deadline := time.NewTimer(s.WaitDuration)
	defer deadline.Stop()
	ticker := time.NewTicker(s.PollInterval)
	defer ticker.Stop()

	for {
		done, value, resErr := s.operationOutcome(ctx, caller, kind, lease)
		if done {
			if resErr != nil {
				return nil, resErr
			}
			return value, nil
		}

		select {
		case <-ctx.Done():
			return nil, NewServiceUnavailableError(howire.MsgWaitTimeout, nil)
		case <-deadline.C:
			return nil, NewServiceUnavailableError(howire.MsgWaitTimeout, nil)
		case <-ticker.C:
		}

		kind2, lease2, appErr := s.resolveOperation(ctx, caller, name)
		if appErr != nil {
			// A release op can vanish mid-wait: the ended Lease is owned by
			// its Exporter (lease_controller.go:130-141), so pool recycling
			// (ExitAndReplace) can garbage-collect it between polls. That
			// only ever happens once the lease ended, so it counts as
			// done + {}.
			if kind == opKindRelease && appErr.StatusCode == http.StatusNotFound {
				return &howire.EmptyResponse{}, nil
			}
			return nil, appErr
		}
		kind, lease = kind2, lease2
	}
}

// resetDone is the reset operation's done-predicate: the caller has zero
// releases in flight (spec.release && !status.ended). A completed reset stays
// done even after new creates (new leases have release=false), and only the
// caller's own leases are consulted.
func (s *Server) resetDone(ctx context.Context, caller *jumpstarterdevv1alpha1.Client) (bool, error) {
	leases, err := s.CallerActiveLeases(ctx, caller)
	if err != nil {
		return false, err
	}
	for i := range leases {
		if leases[i].Spec.Release && !leases[i].Status.Ended {
			return false, nil
		}
	}
	return true, nil
}

// reset implements POST /reset (the Python driver's recovery path): release
// ALL and ONLY the caller's active leases and return the derived reset
// Operation. Provably scoped: it patches nothing outside CallerActiveLeases.
// Known stateless quirks (accepted, harmless for the driver's fire-and-poll
// usage): reset ops never appear in GET /operations, and a concurrent DELETE
// by the same caller can hold the done-predicate false a little longer.
func (s *Server) reset(r *http.Request, caller *jumpstarterdevv1alpha1.Client) (any, error) {
	ctx := r.Context()
	leases, err := s.CallerActiveLeases(ctx, caller)
	if err != nil {
		return nil, NewInternalError(howire.MsgInternal, err)
	}
	for i := range leases {
		lease := &leases[i]
		if lease.Spec.Release || lease.Status.Ended {
			continue
		}
		patch := kclient.MergeFrom(lease.DeepCopy())
		lease.Spec.Release = true
		if err := s.Client.Patch(ctx, lease, patch); err != nil {
			return nil, NewInternalError(howire.MsgInternal, err)
		}
	}
	done, err := s.resetDone(ctx, caller)
	if err != nil {
		return nil, NewInternalError(howire.MsgInternal, err)
	}
	return howire.Operation{Name: ResetOperationName(caller.Name), Done: done}, nil
}
