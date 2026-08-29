package hofacade

// The stateless Lease -> HO-wire view. Every HO wire object the facade
// serves (Operation, CVD, operation result) is a pure function of Lease CR
// state, recomputed per request: there is no operation store, no poller
// goroutine, and no booking state of any kind. The facade is restart-safe by
// construction (strictly better than upstream, whose in-memory MapOM loses
// ALL operations on restart) and leases created by `jmp` render identically
// to facade-created ones.
//
// Phase -> wire table (each row pinned by mapping_test.go):
//
//	lease state                          create op   release op   create result
//	----------------------------------   ---------   ----------   -----------------------------
//	fresh / Pending (incl. exhaustion)   done:false  n/a          pending (nil, nil)
//	Ready=True + exporterRef             done:true   done:false*  200 {"cvds":[CVD]}
//	Unsatisfiable/Invalid = True         done:true   done:true**  500 {reason, details:message}
//	spec.release && !status.ended        done:true   done:false   200 {"cvds":[CVD]} if acquired
//	status.ended (after acquire)         done:true   done:true    200 {"cvds":[CVD]}
//	status.ended (before acquire)        done:true   done:true    500 released-before-acquisition
//
//	*  a release op only "exists" (resolves) once spec.release is set or the
//	   lease ended; done tracks status.ended either way.
//	** failed leases are also marked Ended by the reconciler
//	   (lease_controller.go:161-165), which is why the failed conditions
//	   outrank Ended in the ladder below.
//
// Group/name stability (F6): group = lease name for the lease's whole life,
// name = status.exporterRef.name — identical across GET /cvds, DELETE, and
// operation results. The Python driver adopts the returned group/name
// (driver.py:411,437), so no driver change is needed.

import (
	"errors"

	"github.com/google/uuid"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/hofacade/howire"
)

// facadeUUIDNamespace is the fixed UUID namespace for derived operation
// names. NEVER change it: release/reset operation names are
// uuid.NewSHA1(facadeUUIDNamespace, input) and must stay stable across
// facade versions and restarts so in-flight clients can keep polling them.
var facadeUUIDNamespace = uuid.MustParse("6a1e57a4-9c2d-4d0b-8f3e-2b7c1d9e5f60")

// ReleaseOperationName derives the deterministic release-operation name for a
// lease: DELETE is idempotent (repeat DELETEs return the same operation) and
// GET /operations/{name} can resolve it by scanning the caller's leases. The
// result is a bare UUID, wire-identical to upstream uuid.New().String()
// (operation.go:88-110).
func ReleaseOperationName(leaseName string) string {
	return uuid.NewSHA1(facadeUUIDNamespace, []byte(leaseName+"/release")).String()
}

// ResetOperationName derives the deterministic reset-operation name for a
// client (POST /reset).
func ResetOperationName(clientName string) string {
	return uuid.NewSHA1(facadeUUIDNamespace, []byte(clientName+"/reset")).String()
}

type opPhase int

const (
	opPending opPhase = iota
	opReady
	opFailed
	opReleasing
	opEnded
)

// leasePhase classifies a lease's status into the ladder above. The failed
// conditions are checked before Ended because the reconciler forces Ended on
// Unsatisfiable/Invalid leases (lease_controller.go:161-165) and the terminal
// reason must survive into the operation result.
func leasePhase(l *jumpstarterdevv1alpha1.Lease) opPhase {
	if meta.IsStatusConditionTrue(l.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable)) ||
		meta.IsStatusConditionTrue(l.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypeInvalid)) {
		return opFailed
	}
	if l.Status.Ended {
		return opEnded
	}
	if l.Spec.Release {
		return opReleasing
	}
	if meta.IsStatusConditionTrue(l.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypeReady)) {
		return opReady
	}
	return opPending
}

// wasAcquired reports whether an exporter was ever assigned and the lease
// actually began ("acquired" == status.exporterRef + status.beginTime, set by
// reconcileStatusExporterRef/reconcileStatusBeginEndTimes).
func wasAcquired(l *jumpstarterdevv1alpha1.Lease) bool {
	return l.Status.ExporterRef != nil && l.Status.BeginTime != nil
}

// CreateOperation is the create LRO view of a lease: the operation name IS
// the lease name (a UUIDv7, wire-identical to upstream's bare-UUID op names)
// and it is done as soon as the lease left the pending phase — ready, failed,
// releasing and ended all mean the create LRO finished.
func CreateOperation(l *jumpstarterdevv1alpha1.Lease) howire.Operation {
	return howire.Operation{Name: l.Name, Done: leasePhase(l) != opPending}
}

// ReleaseOperation is the release LRO view of a lease (DELETE /cvds/...):
// done once the reconciler actually ended the lease.
func ReleaseOperation(l *jumpstarterdevv1alpha1.Lease) howire.Operation {
	return howire.Operation{Name: ReleaseOperationName(l.Name), Done: l.Status.Ended}
}

// CreateResult computes the create operation's result per the phase table.
// (nil, nil) means the operation is still pending (callers map that to
// "Operation not done" / keep waiting).
func CreateResult(l *jumpstarterdevv1alpha1.Lease) (any, *AppError) {
	switch phase := leasePhase(l); phase {
	case opReady:
		return &howire.CreateCVDResponse{CVDs: []*howire.CVD{CVDFromLease(l)}}, nil
	case opReleasing, opEnded:
		if wasAcquired(l) {
			return &howire.CreateCVDResponse{CVDs: []*howire.CVD{CVDFromLease(l)}}, nil
		}
		return nil, NewInternalError("lease released before acquisition", nil)
	case opFailed:
		reason, message := terminalCondition(l)
		return nil, NewInternalError(reason, errors.New(message))
	default: // opPending
		return nil, nil
	}
}

// terminalCondition extracts the reason/message of the failed condition
// (Unsatisfiable first, then Invalid). The reason becomes the wire "error"
// and the message its "details" cause — the driver-terminal 500 shape; async
// ExporterAccessPolicy denials (Unsatisfiable/NoAccess) surface here.
func terminalCondition(l *jumpstarterdevv1alpha1.Lease) (reason, message string) {
	for _, t := range []jumpstarterdevv1alpha1.LeaseConditionType{
		jumpstarterdevv1alpha1.LeaseConditionTypeUnsatisfiable,
		jumpstarterdevv1alpha1.LeaseConditionTypeInvalid,
	} {
		if c := meta.FindStatusCondition(l.Status.Conditions, string(t)); c != nil && c.Status == metav1.ConditionTrue {
			return c.Reason, c.Message
		}
	}
	return "lease failed", ""
}

// CVDFromLease renders the lease's assigned exporter as an HO CVD. Requires
// status.exporterRef != nil. Mapping: group = lease name, name = exporter
// name, status = "Running" iff Ready=True else "Starting". Displays,
// webrtc_device_id, adb_serial and adb_port are v0 placeholders (F14): the
// facade does no per-device proxying yet, so driver methods that need them
// (get_adb_port, wait_boot) are out of the v0 acid-test scope.
func CVDFromLease(l *jumpstarterdevv1alpha1.Lease) *howire.CVD {
	status := howire.StatusStarting
	if meta.IsStatusConditionTrue(l.Status.Conditions, string(jumpstarterdevv1alpha1.LeaseConditionTypeReady)) {
		status = howire.StatusRunning
	}
	return &howire.CVD{
		Group:    l.Name,
		Name:     l.Status.ExporterRef.Name,
		Status:   status,
		Displays: []string{},
	}
}
