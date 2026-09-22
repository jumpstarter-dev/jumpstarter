/*
Copyright 2024.

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

package controller

import (
	"context"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	corev1 "k8s.io/api/core/v1"
	"k8s.io/apimachinery/pkg/api/meta"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func sharingTestScheme() *runtime.Scheme {
	s := runtime.NewScheme()
	_ = jumpstarterdevv1alpha1.AddToScheme(s)
	return s
}

func newSharingReconciler(objs ...kclient.Object) *LeaseReconciler {
	scheme := sharingTestScheme()
	c := fake.NewClientBuilder().WithScheme(scheme).WithObjects(objs...).Build()
	return &LeaseReconciler{Client: c, Scheme: scheme}
}

// sharedLease builds a lease that already has an assigned exporter and a
// non-empty desired shared-with set, which is the state in which
// reconcileSharedWithPolicies performs policy filtering.
func sharedLease(owner, exporter string, shared ...string) *jumpstarterdevv1alpha1.Lease {
	l := &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{Name: "lease1", Namespace: "default"},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			ClientRef:  corev1.LocalObjectReference{Name: owner},
			SharedWith: shared,
		},
	}
	l.Status.ExporterRef = &corev1.LocalObjectReference{Name: exporter}
	return l
}

func sharedAccessCondition(lease *jumpstarterdevv1alpha1.Lease) *metav1.Condition {
	return meta.FindStatusCondition(
		lease.Status.Conditions,
		string(jumpstarterdevv1alpha1.LeaseConditionTypeSharedAccessReady),
	)
}

func TestReconcileSharedWithPolicies(t *testing.T) {
	exporter := &jumpstarterdevv1alpha1.Exporter{
		ObjectMeta: metav1.ObjectMeta{Name: "exp1", Namespace: "default",
			Labels: map[string]string{"board": "rpi4"}},
	}
	alice := &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{Name: "alice", Namespace: "default",
			Labels: map[string]string{"team": "devops"}},
	}

	// allowPolicy grants clients labelled team=devops on rpi4 exporters.
	allowPolicy := func() *jumpstarterdevv1alpha1.ExporterAccessPolicy {
		return &jumpstarterdevv1alpha1.ExporterAccessPolicy{
			ObjectMeta: metav1.ObjectMeta{Name: "allow", Namespace: "default"},
			Spec: jumpstarterdevv1alpha1.ExporterAccessPolicySpec{
				ExporterSelector: metav1.LabelSelector{
					MatchLabels: map[string]string{"board": "rpi4"},
				},
				Policies: []jumpstarterdevv1alpha1.Policy{{
					From: []jumpstarterdevv1alpha1.From{{
						ClientSelector: metav1.LabelSelector{
							MatchLabels: map[string]string{"team": "devops"},
						},
					}},
				}},
			},
		}
	}

	// malformedPolicy has an exporter selector that fails to convert (the In
	// operator requires a non-empty value set), so ClientAllowedByPolicy returns
	// an error when it is evaluated.
	malformedPolicy := func() *jumpstarterdevv1alpha1.ExporterAccessPolicy {
		return &jumpstarterdevv1alpha1.ExporterAccessPolicy{
			ObjectMeta: metav1.ObjectMeta{Name: "malformed", Namespace: "default"},
			Spec: jumpstarterdevv1alpha1.ExporterAccessPolicySpec{
				ExporterSelector: metav1.LabelSelector{
					MatchExpressions: []metav1.LabelSelectorRequirement{{
						Key:      "board",
						Operator: metav1.LabelSelectorOpIn,
						Values:   nil,
					}},
				},
				Policies: []jumpstarterdevv1alpha1.Policy{{
					From: []jumpstarterdevv1alpha1.From{{
						ClientSelector: metav1.LabelSelector{
							MatchLabels: map[string]string{"team": "devops"},
						},
					}},
				}},
			},
		}
	}

	t.Run("clean path grants access and marks SharedAccessReady=True", func(t *testing.T) {
		r := newSharingReconciler(exporter, alice, allowPolicy())
		lease := sharedLease("owner", "exp1", "alice")

		r.reconcileSharedWithPolicies(context.Background(), lease)

		if len(lease.Status.SharedWith) != 1 || lease.Status.SharedWith[0] != "alice" {
			t.Fatalf("expected effective shared-with [alice], got %v", lease.Status.SharedWith)
		}
		cond := sharedAccessCondition(lease)
		if cond == nil {
			t.Fatal("expected SharedAccessReady condition to be set")
		}
		if cond.Status != metav1.ConditionTrue {
			t.Fatalf("expected SharedAccessReady=True, got %s (reason %s)", cond.Status, cond.Reason)
		}
	})

	t.Run("malformed policy fails closed and marks SharingDegraded", func(t *testing.T) {
		r := newSharingReconciler(exporter, alice, malformedPolicy())
		lease := sharedLease("owner", "exp1", "alice")

		// Must not panic or otherwise abort; it returns no error by design.
		r.reconcileSharedWithPolicies(context.Background(), lease)

		if len(lease.Status.SharedWith) != 0 {
			t.Fatalf("expected client excluded (fail closed), got effective shared-with %v", lease.Status.SharedWith)
		}
		cond := sharedAccessCondition(lease)
		if cond == nil {
			t.Fatal("expected SharedAccessReady condition to be set")
		}
		if cond.Status != metav1.ConditionFalse {
			t.Fatalf("expected SharedAccessReady=False, got %s", cond.Status)
		}
		if cond.Reason != "SharingDegraded" {
			t.Fatalf("expected reason SharingDegraded, got %s", cond.Reason)
		}
	})

	t.Run("deleted shared client is excluded (fail closed)", func(t *testing.T) {
		// "ghost" is not present in the fake client, so its lookup returns
		// NotFound and it must be dropped from the effective set rather than
		// aborting the reconcile.
		r := newSharingReconciler(exporter, alice, allowPolicy())
		lease := sharedLease("owner", "exp1", "alice", "ghost")

		r.reconcileSharedWithPolicies(context.Background(), lease)

		if len(lease.Status.SharedWith) != 1 || lease.Status.SharedWith[0] != "alice" {
			t.Fatalf("expected effective shared-with [alice] (ghost excluded), got %v", lease.Status.SharedWith)
		}
	})

	t.Run("assigned exporter deleted marks SharingDegraded without aborting", func(t *testing.T) {
		// Exporter exp1 is absent, so the exporter lookup fails. The prior
		// effective set must be preserved and the condition marked degraded.
		r := newSharingReconciler(alice, allowPolicy())
		lease := sharedLease("owner", "exp1", "alice")
		lease.Status.SharedWith = []string{"alice"} // previously-persisted set

		r.reconcileSharedWithPolicies(context.Background(), lease)

		if len(lease.Status.SharedWith) != 1 || lease.Status.SharedWith[0] != "alice" {
			t.Fatalf("expected prior effective set preserved, got %v", lease.Status.SharedWith)
		}
		cond := sharedAccessCondition(lease)
		if cond == nil || cond.Status != metav1.ConditionFalse || cond.Reason != "SharingDegraded" {
			t.Fatalf("expected SharedAccessReady=False reason SharingDegraded, got %+v", cond)
		}
	})
}
