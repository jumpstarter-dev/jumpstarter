package v1alpha1

import (
	"context"
	"fmt"
	"time"

	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

func (l *Lease) GetExporterSelector() (labels.Selector, error) {
	return metav1.LabelSelectorAsSelector(&l.Spec.Selector)
}

func (l *Lease) SetStatusPending(reason, messageFormat string, a ...any) {
	l.SetStatusCondition(LeaseConditionTypePending, true, reason, messageFormat, a...)
}

func (l *Lease) SetStatusReady(status bool, reason, messageFormat string, a ...any) {
	l.SetStatusCondition(LeaseConditionTypeReady, status, reason, messageFormat, a...)
}

func (l *Lease) SetStatusUnsatisfiable(reason, messageFormat string, a ...any) {
	l.SetStatusCondition(LeaseConditionTypeUnsatisfiable, true, reason, messageFormat, a...)
}

func (l *Lease) SetStatusInvalid(reason, messageFormat string, a ...any) {
	l.SetStatusCondition(LeaseConditionTypeInvalid, true, reason, messageFormat, a...)
}

func (l *Lease) SetStatusCondition(
	condition LeaseConditionType,
	status bool,
	reason, messageFormat string, a ...any) {

	var statusCondition metav1.ConditionStatus

	if status {
		statusCondition = metav1.ConditionTrue
	} else {
		statusCondition = metav1.ConditionFalse
	}

	meta.SetStatusCondition(&l.Status.Conditions, metav1.Condition{
		Type:               string(condition),
		Status:             statusCondition,
		ObservedGeneration: l.Generation,
		LastTransitionTime: metav1.Time{
			Time: time.Now(),
		},
		Reason:  reason,
		Message: fmt.Sprintf(messageFormat, a...),
	})
}

func (l *Lease) GetExporterName() string {
	if l.Status.ExporterRef == nil {
		return "(none)"
	}
	return l.Status.ExporterRef.Name
}

func (l *Lease) GetClientName() string {
	return l.Spec.ClientRef.Name
}

func (l *Lease) Release(ctx context.Context) {
	logger := log.FromContext(ctx)
	logger.Info("The lease has been marked for release", "lease", l.Name, "exporter", l.GetExporterName(), "client", l.GetClientName())
	l.SetStatusReady(false, "Released", "The lease was marked for release")
	l.Status.Ended = true
	l.Status.EndTime = &metav1.Time{Time: time.Now()}
}

func (l *Lease) Expire(ctx context.Context) {
	logger := log.FromContext(ctx)
	logger.Info("The lease has expired", "lease", l.Name, "exporter", l.GetExporterName(), "client", l.GetClientName())
	l.SetStatusReady(false, "Expired", "The lease has expired")
	l.Status.Ended = true
	l.Status.EndTime = &metav1.Time{Time: time.Now()}
}
