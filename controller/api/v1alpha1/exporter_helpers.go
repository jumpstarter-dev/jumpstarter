package v1alpha1

import (
	"fmt"
	"strings"

	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter/controller/internal/service/utils"
	"k8s.io/apimachinery/pkg/api/meta"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
)

// IsEnabled returns whether this exporter is enabled for lease assignment.
// Returns true if Enabled is nil (backward compatibility) or explicitly set to true.
func (e *Exporter) IsEnabled() bool {
	return e.Spec.Enabled == nil || *e.Spec.Enabled
}

// ValidateExporterEnabledForLease rejects disabled exporters unless the lease
// explicitly allows them. Callers can apply their own error transport around
// the returned validation error.
func ValidateExporterEnabledForLease(exporter *Exporter, allowDisabled bool) error {
	if exporter == nil || exporter.IsEnabled() || allowDisabled {
		return nil
	}

	return fmt.Errorf(
		"requested exporter %s is disabled. To lease a disabled exporter, set spec.allowDisabled: true on the Lease, "+
			"or use --allow-disabled with jmp create lease or jmp shell",
		exporter.Name,
	)
}

func (e *Exporter) InternalSubject() string {
	namespace, uid := getNamespaceAndUID(e.Namespace, e.UID, e.Annotations)
	return strings.Join([]string{"exporter", namespace, e.Name, uid}, ":")
}

func (e *Exporter) Usernames(prefix string) []string {
	usernames := []string{prefix + e.InternalSubject()}

	if e.Spec.Username != nil {
		usernames = append(usernames, *e.Spec.Username)
	}

	return usernames
}

func (e *Exporter) ToProtobuf() *cpb.Exporter {
	// get online status from conditions (deprecated, kept for backward compatibility)
	isOnline := meta.IsStatusConditionTrue(e.Status.Conditions, string(ExporterConditionTypeOnline))

	return &cpb.Exporter{
		Name:          utils.UnparseExporterIdentifier(kclient.ObjectKeyFromObject(e)),
		Labels:        e.Labels,
		Online:        isOnline, //nolint:staticcheck // populated for older clients still reading this field
		Status:        stringToProtoStatus(e.Status.ExporterStatusValue),
		StatusMessage: e.Status.StatusMessage,
		Enabled:       new(e.IsEnabled()),
	}
}

// stringToProtoStatus converts the CRD string value to the proto ExporterStatus enum
func stringToProtoStatus(state string) pb.ExporterStatus {
	switch state {
	case ExporterStatusOffline:
		return pb.ExporterStatus_EXPORTER_STATUS_OFFLINE
	case ExporterStatusAvailable:
		return pb.ExporterStatus_EXPORTER_STATUS_AVAILABLE
	case ExporterStatusBeforeLeaseHook:
		return pb.ExporterStatus_EXPORTER_STATUS_BEFORE_LEASE_HOOK
	case ExporterStatusLeaseReady:
		return pb.ExporterStatus_EXPORTER_STATUS_LEASE_READY
	case ExporterStatusAfterLeaseHook:
		return pb.ExporterStatus_EXPORTER_STATUS_AFTER_LEASE_HOOK
	case ExporterStatusBeforeLeaseHookFailed:
		return pb.ExporterStatus_EXPORTER_STATUS_BEFORE_LEASE_HOOK_FAILED
	case ExporterStatusAfterLeaseHookFailed:
		return pb.ExporterStatus_EXPORTER_STATUS_AFTER_LEASE_HOOK_FAILED
	default:
		return pb.ExporterStatus_EXPORTER_STATUS_UNSPECIFIED
	}
}

func (l *ExporterList) ToProtobuf() *cpb.ListExportersResponse {
	var jexporters []*cpb.Exporter
	for _, jexporter := range l.Items {
		jexporters = append(jexporters, jexporter.ToProtobuf())
	}
	return &cpb.ListExportersResponse{
		Exporters:     jexporters,
		NextPageToken: l.Continue,
	}
}

var leaseHookPhaseToProto = map[LeaseHookPhase]pb.LeaseHookPhase{
	LeaseHookPhaseRunning:   pb.LeaseHookPhase_LEASE_HOOK_PHASE_RUNNING,
	LeaseHookPhaseSucceeded: pb.LeaseHookPhase_LEASE_HOOK_PHASE_SUCCEEDED,
	LeaseHookPhaseFailed:    pb.LeaseHookPhase_LEASE_HOOK_PHASE_FAILED,
	LeaseHookPhaseSkipped:   pb.LeaseHookPhase_LEASE_HOOK_PHASE_SKIPPED,
}

var leaseHookFailureActionToProto = map[string]pb.LeaseHookFailureAction{
	"warn":     pb.LeaseHookFailureAction_LEASE_HOOK_FAILURE_ACTION_WARN,
	"endLease": pb.LeaseHookFailureAction_LEASE_HOOK_FAILURE_ACTION_END_LEASE,
	"exit":     pb.LeaseHookFailureAction_LEASE_HOOK_FAILURE_ACTION_EXIT,
}

// LeaseHookPhaseFromProto converts a proto hook phase; ok is false for an unspecified or unknown phase.
func LeaseHookPhaseFromProto(phase pb.LeaseHookPhase) (LeaseHookPhase, bool) {
	for k, v := range leaseHookPhaseToProto {
		if v == phase {
			return k, true
		}
	}
	return "", false
}

// LeaseHookFailureActionFromProto converts a proto failure action to its onFailure
// config value; unspecified or unknown actions map to "".
func LeaseHookFailureActionFromProto(action pb.LeaseHookFailureAction) string {
	for k, v := range leaseHookFailureActionToProto {
		if v == action {
			return k
		}
	}
	return ""
}

func (s *LeaseHookStatus) ToProtobuf() *pb.LeaseHookState {
	if s == nil {
		return nil
	}
	return &pb.LeaseHookState{
		Phase:     leaseHookPhaseToProto[s.Phase],
		OnFailure: leaseHookFailureActionToProto[s.OnFailure],
		Message:   s.Message,
		Attempts:  uint32(max(s.Attempts, 0)),
	}
}

// ToProtobuf converts the record; a nil record gives an empty, non-nil message.
func (h *ExporterLeaseHooks) ToProtobuf() *pb.LeaseHooks {
	if h == nil {
		return &pb.LeaseHooks{}
	}
	return &pb.LeaseHooks{
		LeaseName:   h.LeaseRef.Name,
		LeaseUid:    string(h.LeaseUID),
		ClientName:  h.ClientName,
		BeforeLease: h.BeforeLease.ToProtobuf(),
		AfterLease:  h.AfterLease.ToProtobuf(),
	}
}
