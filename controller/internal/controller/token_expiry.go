package controller

import (
	"fmt"
	"time"

	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

const (
	tokenExpiryWarningThreshold = 30 * 24 * time.Hour
	tokenExpiryRequeueInterval  = 24 * time.Hour
)

func reconcileTokenExpiry(
	conditions *[]metav1.Condition,
	tokenExpiresAt **metav1.Time,
	expiryTime time.Time,
	generation int64,
	conditionType string,
) {
	if expiryTime.IsZero() {
		return
	}

	t := metav1.NewTime(expiryTime)
	*tokenExpiresAt = &t

	remaining := time.Until(expiryTime)

	if remaining <= 0 {
		meta.SetStatusCondition(conditions, metav1.Condition{
			Type:               conditionType,
			Status:             metav1.ConditionTrue,
			ObservedGeneration: generation,
			Reason:             "Expired",
			Message:            fmt.Sprintf("Token expired on %s", expiryTime.UTC().Format(time.RFC3339)),
		})
	} else if remaining <= 7*24*time.Hour {
		meta.SetStatusCondition(conditions, metav1.Condition{
			Type:               conditionType,
			Status:             metav1.ConditionTrue,
			ObservedGeneration: generation,
			Reason:             "ExpiresWithin7Days",
			Message:            fmt.Sprintf("Token expires on %s (%d days remaining)", expiryTime.UTC().Format(time.RFC3339), int(remaining.Hours()/24)),
		})
	} else if remaining <= tokenExpiryWarningThreshold {
		meta.SetStatusCondition(conditions, metav1.Condition{
			Type:               conditionType,
			Status:             metav1.ConditionTrue,
			ObservedGeneration: generation,
			Reason:             "ExpiresWithin30Days",
			Message:            fmt.Sprintf("Token expires on %s (%d days remaining)", expiryTime.UTC().Format(time.RFC3339), int(remaining.Hours()/24)),
		})
	} else {
		meta.SetStatusCondition(conditions, metav1.Condition{
			Type:               conditionType,
			Status:             metav1.ConditionFalse,
			ObservedGeneration: generation,
			Reason:             "Valid",
			Message:            fmt.Sprintf("Token expires on %s (%d days remaining)", expiryTime.UTC().Format(time.RFC3339), int(remaining.Hours()/24)),
		})
	}
}
