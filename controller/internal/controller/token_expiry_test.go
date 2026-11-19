package controller

import (
	"testing"
	"time"

	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
)

func TestReconcileTokenExpiry(t *testing.T) {
	for _, tc := range []struct {
		name                 string
		remaining, threshold time.Duration
		zero                 bool
		wantStatus           metav1.ConditionStatus
		wantReason           string
	}{
		{name: "no expiry", zero: true},
		{name: "expired", remaining: -time.Hour, threshold: tokenExpiryWarningThreshold, wantStatus: metav1.ConditionTrue, wantReason: "Expired"},
		{name: "within seven days", remaining: 6 * 24 * time.Hour, threshold: tokenExpiryWarningThreshold, wantStatus: metav1.ConditionTrue, wantReason: "ExpiringSoon"},
		{name: "within thirty days", remaining: 20 * 24 * time.Hour, threshold: tokenExpiryWarningThreshold, wantStatus: metav1.ConditionTrue, wantReason: "ExpiringSoon"},
		{name: "valid", remaining: 31 * 24 * time.Hour, threshold: tokenExpiryWarningThreshold, wantStatus: metav1.ConditionFalse, wantReason: "Valid"},
		{name: "short-lived warning", remaining: 5 * time.Minute, threshold: 6 * time.Minute, wantStatus: metav1.ConditionTrue, wantReason: "ExpiringSoon"},
		{name: "short-lived valid", remaining: 12 * time.Minute, threshold: 6 * time.Minute, wantStatus: metav1.ConditionFalse, wantReason: "Valid"},
	} {
		t.Run(tc.name, func(t *testing.T) {
			conditions := []metav1.Condition{{Type: "Unrelated", Status: metav1.ConditionTrue, Reason: "Preserved"}}
			var expiresAt *metav1.Time
			expiry := time.Now().Add(tc.remaining)
			if tc.zero {
				expiry = time.Time{}
			}
			reconcileTokenExpiryWithThreshold(&conditions, &expiresAt, expiry, 42, "TokenExpiring", tc.threshold, exporterExpiringReason)
			if meta.FindStatusCondition(conditions, "Unrelated").Reason != "Preserved" {
				t.Fatal("unrelated condition changed")
			}
			condition := meta.FindStatusCondition(conditions, "TokenExpiring")
			if tc.zero {
				if condition != nil || expiresAt != nil {
					t.Fatal("zero expiry changed status")
				}
				return
			}
			if expiresAt == nil || !expiresAt.Time.Equal(expiry) {
				t.Fatal("expiry was not recorded")
			}
			if condition == nil || condition.Status != tc.wantStatus || condition.Reason != tc.wantReason || condition.ObservedGeneration != 42 {
				t.Fatalf("unexpected condition: %+v", condition)
			}
		})
	}
}

func TestClientTokenExpiryReasonsRemainCompatible(t *testing.T) {
	for _, tc := range []struct {
		remaining time.Duration
		reason    string
	}{
		{remaining: 6 * 24 * time.Hour, reason: "ExpiresWithin7Days"},
		{remaining: 20 * 24 * time.Hour, reason: "ExpiresWithin30Days"},
	} {
		t.Run(tc.reason, func(t *testing.T) {
			var conditions []metav1.Condition
			var expiresAt *metav1.Time
			reconcileTokenExpiry(&conditions, &expiresAt, time.Now().Add(tc.remaining), 1, "TokenExpiring")
			condition := meta.FindStatusCondition(conditions, "TokenExpiring")
			if condition == nil || condition.Status != metav1.ConditionTrue || condition.Reason != tc.reason {
				t.Fatalf("unexpected condition: %+v", condition)
			}
		})
	}
}
