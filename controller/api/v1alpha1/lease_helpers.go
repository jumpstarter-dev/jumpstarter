package v1alpha1

import (
	"context"
	"fmt"
	"time"

	cpb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/client/v1"
	pb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/v1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/service/utils"
	"google.golang.org/protobuf/types/known/durationpb"
	"google.golang.org/protobuf/types/known/timestamppb"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/labels"
	"k8s.io/apimachinery/pkg/types"
	"k8s.io/utils/ptr"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

func LeaseFromProtobuf(
	req *cpb.Lease,
	key types.NamespacedName,
	clientRef corev1.LocalObjectReference,
) (*Lease, error) {
	selector, err := metav1.ParseToLabelSelector(req.Selector)
	if err != nil {
		return nil, err
	}

	return &Lease{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: key.Namespace,
			Name:      key.Name,
		},
		Spec: LeaseSpec{
			ClientRef: clientRef,
			Duration:  metav1.Duration{Duration: req.Duration.AsDuration()},
			Selector:  *selector,
		},
	}, nil
}

func (l *Lease) ToProtobuf() *cpb.Lease {
	var conditions []*pb.Condition
	for _, condition := range l.Status.Conditions {
		conditions = append(conditions, &pb.Condition{
			Type:               &condition.Type,
			Status:             (*string)(&condition.Status),
			ObservedGeneration: &condition.ObservedGeneration,
			LastTransitionTime: &pb.Time{
				Seconds: &condition.LastTransitionTime.ProtoTime().Seconds,
				Nanos:   &condition.LastTransitionTime.ProtoTime().Nanos,
			},
			Reason:  &condition.Reason,
			Message: &condition.Message,
		})
	}

	lease := cpb.Lease{
		Name:              fmt.Sprintf("namespaces/%s/leases/%s", l.Namespace, l.Name),
		Selector:          metav1.FormatLabelSelector(&l.Spec.Selector),
		Duration:          durationpb.New(l.Spec.Duration.Duration),
		EffectiveDuration: durationpb.New(l.Spec.Duration.Duration), // TODO: implement lease renewal
		Client:            ptr.To(fmt.Sprintf("namespaces/%s/clients/%s", l.Namespace, l.Spec.ClientRef.Name)),
		Conditions:        conditions,
		// TODO: implement scheduled leases
		BeginTime: nil,
		EndTime:   nil,
	}

	if l.Status.BeginTime != nil {
		lease.EffectiveBeginTime = timestamppb.New(l.Status.BeginTime.Time)
	}
	if l.Status.EndTime != nil {
		lease.EffectiveEndTime = timestamppb.New(l.Status.EndTime.Time)
	}
	if l.Status.ExporterRef != nil {
		lease.Exporter = ptr.To(utils.UnparseExporterIdentifier(kclient.ObjectKey{
			Namespace: l.Namespace,
			Name:      l.Status.ExporterRef.Name,
		}))
	}

	return &lease
}

func (l *LeaseList) ToProtobuf() *cpb.ListLeasesResponse {
	var jleases []*cpb.Lease
	for _, jlease := range l.Items {
		jleases = append(jleases, jlease.ToProtobuf())
	}
	return &cpb.ListLeasesResponse{
		Leases:        jleases,
		NextPageToken: l.Continue,
	}
}

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
