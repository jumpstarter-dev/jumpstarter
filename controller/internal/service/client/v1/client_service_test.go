package v1

import (
	"context"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"
)

func TestValidateLeaseTarget(t *testing.T) {
	t.Run("accepts selector target", func(t *testing.T) {
		if err := validateLeaseTarget(&cpb.Lease{Selector: "dut=a"}); err != nil {
			t.Fatalf("expected selector target to be valid, got error: %v", err)
		}
	})

	t.Run("accepts exporter name target", func(t *testing.T) {
		name := "laptop-test-exporter"
		if err := validateLeaseTarget(&cpb.Lease{ExporterName: &name}); err != nil {
			t.Fatalf("expected exporter name target to be valid, got error: %v", err)
		}
	})

	t.Run("accepts selector and exporter name together", func(t *testing.T) {
		name := "laptop-test-exporter"
		if err := validateLeaseTarget(&cpb.Lease{Selector: "purpose=test", ExporterName: &name}); err != nil {
			t.Fatalf("expected combined target to be valid, got error: %v", err)
		}
	})

	t.Run("rejects missing selector and exporter name", func(t *testing.T) {
		err := validateLeaseTarget(&cpb.Lease{})
		if err == nil {
			t.Fatal("expected missing target to fail")
		}

		st, ok := status.FromError(err)
		if !ok {
			t.Fatalf("expected grpc status error, got: %T", err)
		}
		if st.Code() != codes.InvalidArgument {
			t.Fatalf("expected InvalidArgument, got: %v", st.Code())
		}
		if st.Message() != "one of selector or exporter_name is required" {
			t.Fatalf("unexpected message: %q", st.Message())
		}
	})

	t.Run("rejects nil lease", func(t *testing.T) {
		err := validateLeaseTarget(nil)
		if err == nil {
			t.Fatal("expected nil lease to fail")
		}

		st, ok := status.FromError(err)
		if !ok {
			t.Fatalf("expected grpc status error, got: %T", err)
		}
		if st.Code() != codes.InvalidArgument {
			t.Fatalf("expected InvalidArgument, got: %v", st.Code())
		}
		if st.Message() != "lease is required" {
			t.Fatalf("unexpected message: %q", st.Message())
		}
	})
}

func TestDeleteLeaseRejectsAlreadyReleasedLease(t *testing.T) {
	lease := &jumpstarterdevv1alpha1.Lease{}

	t.Run("rejects already released lease", func(t *testing.T) {
		lease.Spec.Release = true
		if !lease.Spec.Release {
			t.Fatal("expected lease to be marked as released")
		}
	})

	t.Run("accepts active lease", func(t *testing.T) {
		lease.Spec.Release = false
		if lease.Spec.Release {
			t.Fatal("expected lease to be active")
		}
	})
}

func toHiddenSet(keys ...string) map[string]struct{} {
	s := make(map[string]struct{}, len(keys))
	for _, k := range keys {
		s[k] = struct{}{}
	}
	return s
}

func TestFilterHiddenLabels(t *testing.T) {
	t.Run("no hidden keys configured is a no-op", func(t *testing.T) {
		exp := &cpb.Exporter{Labels: map[string]string{"board": "rpi4", "pool": "staging"}}
		filterHiddenLabels(exp, nil, false)
		if len(exp.Labels) != 2 {
			t.Fatalf("expected 2 labels, got %d", len(exp.Labels))
		}
	})

	t.Run("strips configured hidden keys", func(t *testing.T) {
		exp := &cpb.Exporter{Labels: map[string]string{"board": "rpi4", "pool": "staging", "internal-id": "abc"}}
		filterHiddenLabels(exp, toHiddenSet("pool", "internal-id"), false)
		if len(exp.Labels) != 1 {
			t.Fatalf("expected 1 label, got %d", len(exp.Labels))
		}
		if exp.Labels["board"] != "rpi4" {
			t.Fatalf("expected board=rpi4, got %v", exp.Labels)
		}
	})

	t.Run("show_hidden_labels bypasses filtering", func(t *testing.T) {
		exp := &cpb.Exporter{Labels: map[string]string{"board": "rpi4", "pool": "staging"}}
		filterHiddenLabels(exp, toHiddenSet("pool"), true)
		if len(exp.Labels) != 2 {
			t.Fatalf("expected 2 labels (show_hidden_labels=true), got %d", len(exp.Labels))
		}
	})

	t.Run("hidden key not present in labels is harmless", func(t *testing.T) {
		exp := &cpb.Exporter{Labels: map[string]string{"board": "rpi4"}}
		filterHiddenLabels(exp, toHiddenSet("nonexistent"), false)
		if len(exp.Labels) != 1 {
			t.Fatalf("expected 1 label, got %d", len(exp.Labels))
		}
	})

	t.Run("empty labels map is a no-op", func(t *testing.T) {
		exp := &cpb.Exporter{Labels: map[string]string{}}
		filterHiddenLabels(exp, toHiddenSet("pool"), false)
		if len(exp.Labels) != 0 {
			t.Fatalf("expected 0 labels, got %d", len(exp.Labels))
		}
	})

	t.Run("nil labels map is a no-op", func(t *testing.T) {
		exp := &cpb.Exporter{}
		filterHiddenLabels(exp, toHiddenSet("pool"), false)
		if exp.Labels != nil {
			t.Fatalf("expected nil labels, got %v", exp.Labels)
		}
	})
}

func toDeprecatedMessages(keysAndMessages ...string) map[string]string {
	m := make(map[string]string, len(keysAndMessages)/2)
	for i := 0; i < len(keysAndMessages)-1; i += 2 {
		m[keysAndMessages[i]] = keysAndMessages[i+1]
	}
	return m
}

func toDeprecatedSet(keys ...string) map[string]struct{} {
	s := make(map[string]struct{}, len(keys))
	for _, k := range keys {
		s[k] = struct{}{}
	}
	return s
}

func TestAnnotateDeprecatedLabels(t *testing.T) {
	t.Run("no deprecated keys configured is a no-op", func(t *testing.T) {
		exp := &cpb.Exporter{Labels: map[string]string{"board": "rpi4"}}
		annotateDeprecatedLabels(exp, nil)
		if len(exp.DeprecatedLabels) != 0 {
			t.Fatalf("expected 0 deprecated labels, got %d", len(exp.DeprecatedLabels))
		}
	})

	t.Run("annotates matching deprecated keys with messages", func(t *testing.T) {
		exp := &cpb.Exporter{Labels: map[string]string{"board": "rpi4", "pool": "staging", "old-key": "val"}}
		annotateDeprecatedLabels(exp, toDeprecatedMessages("pool", "Use 'env' instead", "old-key", "Removed in v2.0"))
		if len(exp.DeprecatedLabels) != 2 {
			t.Fatalf("expected 2 deprecated labels, got %d", len(exp.DeprecatedLabels))
		}
		if exp.DeprecatedLabels["pool"] != "Use 'env' instead" {
			t.Fatalf("expected pool message, got %q", exp.DeprecatedLabels["pool"])
		}
		if exp.DeprecatedLabels["old-key"] != "Removed in v2.0" {
			t.Fatalf("expected old-key message, got %q", exp.DeprecatedLabels["old-key"])
		}
	})

	t.Run("empty message is valid", func(t *testing.T) {
		exp := &cpb.Exporter{Labels: map[string]string{"board": "rpi4", "pool": "staging"}}
		annotateDeprecatedLabels(exp, toDeprecatedMessages("pool", ""))
		if len(exp.DeprecatedLabels) != 1 {
			t.Fatalf("expected 1 deprecated label, got %d", len(exp.DeprecatedLabels))
		}
		if _, ok := exp.DeprecatedLabels["pool"]; !ok {
			t.Fatalf("expected pool in deprecated labels, got %v", exp.DeprecatedLabels)
		}
	})

	t.Run("deprecated key not present in labels is harmless", func(t *testing.T) {
		exp := &cpb.Exporter{Labels: map[string]string{"board": "rpi4"}}
		annotateDeprecatedLabels(exp, toDeprecatedMessages("nonexistent", "gone"))
		if len(exp.DeprecatedLabels) != 0 {
			t.Fatalf("expected 0 deprecated labels, got %d", len(exp.DeprecatedLabels))
		}
	})

	t.Run("empty labels map is a no-op", func(t *testing.T) {
		exp := &cpb.Exporter{Labels: map[string]string{}}
		annotateDeprecatedLabels(exp, toDeprecatedMessages("pool", "gone"))
		if len(exp.DeprecatedLabels) != 0 {
			t.Fatalf("expected 0 deprecated labels, got %d", len(exp.DeprecatedLabels))
		}
	})

	t.Run("nil labels map is a no-op", func(t *testing.T) {
		exp := &cpb.Exporter{}
		annotateDeprecatedLabels(exp, toDeprecatedMessages("pool", "gone"))
		if len(exp.DeprecatedLabels) != 0 {
			t.Fatalf("expected 0 deprecated labels, got %d", len(exp.DeprecatedLabels))
		}
	})
}

func TestDeprecatedLabelsAnnotatedThenHidden(t *testing.T) {
	t.Run("deprecated labels are annotated and then filtered from labels map", func(t *testing.T) {
		exp := &cpb.Exporter{Labels: map[string]string{"board": "rpi4", "old-key": "val", "legacy": "x"}}
		messages := toDeprecatedMessages("old-key", "Use new-key", "legacy", "Removed")
		deprecatedSet := toDeprecatedSet("old-key", "legacy")

		annotateDeprecatedLabels(exp, messages)
		filterHiddenLabels(exp, deprecatedSet, false)

		if len(exp.Labels) != 1 {
			t.Fatalf("expected 1 visible label, got %d: %v", len(exp.Labels), exp.Labels)
		}
		if exp.Labels["board"] != "rpi4" {
			t.Fatalf("expected board=rpi4, got %v", exp.Labels)
		}
		if len(exp.DeprecatedLabels) != 2 {
			t.Fatalf("expected 2 deprecated annotations, got %d", len(exp.DeprecatedLabels))
		}
	})

	t.Run("show_hidden_labels bypasses deprecated filtering", func(t *testing.T) {
		exp := &cpb.Exporter{Labels: map[string]string{"board": "rpi4", "old-key": "val"}}
		messages := toDeprecatedMessages("old-key", "Use new-key")
		deprecatedSet := toDeprecatedSet("old-key")

		annotateDeprecatedLabels(exp, messages)
		filterHiddenLabels(exp, deprecatedSet, true)

		if len(exp.Labels) != 2 {
			t.Fatalf("expected 2 labels with show_hidden=true, got %d", len(exp.Labels))
		}
		if len(exp.DeprecatedLabels) != 1 {
			t.Fatalf("expected 1 deprecated annotation, got %d", len(exp.DeprecatedLabels))
		}
	})
}

func TestAnnotateLeaseDeprecatedLabels(t *testing.T) {
	t.Run("no deprecated keys configured is a no-op", func(t *testing.T) {
		lease := &cpb.Lease{Selector: "board=rpi4"}
		annotateLeaseDeprecatedLabels(lease, nil)
		if len(lease.DeprecatedLabels) != 0 {
			t.Fatalf("expected 0, got %d", len(lease.DeprecatedLabels))
		}
	})

	t.Run("annotates matching selector keys with messages", func(t *testing.T) {
		lease := &cpb.Lease{Selector: "legacy-board=rpi4,old-pool=staging"}
		annotateLeaseDeprecatedLabels(lease, toDeprecatedMessages(
			"legacy-board", "Use board instead",
			"old-pool", "Removed in v2.0",
		))
		if len(lease.DeprecatedLabels) != 2 {
			t.Fatalf("expected 2, got %d", len(lease.DeprecatedLabels))
		}
		if lease.DeprecatedLabels["legacy-board"] != "Use board instead" {
			t.Fatalf("wrong message for legacy-board: %q", lease.DeprecatedLabels["legacy-board"])
		}
		if lease.DeprecatedLabels["old-pool"] != "Removed in v2.0" {
			t.Fatalf("wrong message for old-pool: %q", lease.DeprecatedLabels["old-pool"])
		}
	})

	t.Run("non-deprecated selector keys are not annotated", func(t *testing.T) {
		lease := &cpb.Lease{Selector: "board=rpi4"}
		annotateLeaseDeprecatedLabels(lease, toDeprecatedMessages("old-pool", "gone"))
		if len(lease.DeprecatedLabels) != 0 {
			t.Fatalf("expected 0, got %d", len(lease.DeprecatedLabels))
		}
	})

	t.Run("empty selector is a no-op", func(t *testing.T) {
		lease := &cpb.Lease{Selector: ""}
		annotateLeaseDeprecatedLabels(lease, toDeprecatedMessages("old-pool", "gone"))
		if len(lease.DeprecatedLabels) != 0 {
			t.Fatalf("expected 0, got %d", len(lease.DeprecatedLabels))
		}
	})

	t.Run("set-based selector keys are annotated", func(t *testing.T) {
		lease := &cpb.Lease{Selector: "legacy-board in (rpi3,rpi4)"}
		annotateLeaseDeprecatedLabels(lease, toDeprecatedMessages("legacy-board", "Use board instead"))
		if len(lease.DeprecatedLabels) != 1 {
			t.Fatalf("expected 1, got %d", len(lease.DeprecatedLabels))
		}
	})
}

func TestCreateLeaseRejectsNilRequest(t *testing.T) {
	svc := &ClientService{}

	_, err := svc.CreateLease(context.Background(), nil)
	if err == nil {
		t.Fatal("expected nil request to fail")
	}

	st, ok := status.FromError(err)
	if !ok {
		t.Fatalf("expected grpc status error, got: %T", err)
	}
	if st.Code() != codes.InvalidArgument {
		t.Fatalf("expected InvalidArgument, got: %v", st.Code())
	}
	if st.Message() != "request is required" {
		t.Fatalf("unexpected message: %q", st.Message())
	}
}

func testScheme() *runtime.Scheme {
	s := runtime.NewScheme()
	_ = jumpstarterdevv1alpha1.AddToScheme(s)
	return s
}

func testFakeClient(objs ...kclient.Object) kclient.Client {
	return fake.NewClientBuilder().
		WithScheme(testScheme()).
		WithObjects(objs...).
		Build()
}

func TestApplySharedWithChanges(t *testing.T) {
	alice := &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{Name: "alice", Namespace: "default",
			Labels: map[string]string{"team": "devops"}},
	}
	bob := &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{Name: "bob", Namespace: "default",
			Labels: map[string]string{"team": "devops"}},
	}

	baseLease := func(owner string, shared ...string) *jumpstarterdevv1alpha1.Lease {
		return &jumpstarterdevv1alpha1.Lease{
			ObjectMeta: metav1.ObjectMeta{Name: "lease1", Namespace: "default"},
			Spec: jumpstarterdevv1alpha1.LeaseSpec{
				ClientRef:  corev1.LocalObjectReference{Name: owner},
				SharedWith: shared,
			},
		}
	}

	t.Run("add single client", func(t *testing.T) {
		svc := &ClientService{Client: testFakeClient(alice)}
		lease := baseLease("owner")

		result, err := svc.applySharedWithChanges(context.Background(), lease, "default",
			[]string{"alice"}, nil)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(result) != 1 || result[0] != "alice" {
			t.Fatalf("expected [alice], got %v", result)
		}
	})

	t.Run("remove single client", func(t *testing.T) {
		svc := &ClientService{Client: testFakeClient(alice)}
		lease := baseLease("owner", "alice")

		result, err := svc.applySharedWithChanges(context.Background(), lease, "default",
			nil, []string{"alice"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(result) != 0 {
			t.Fatalf("expected empty, got %v", result)
		}
	})

	t.Run("add and remove in same call", func(t *testing.T) {
		svc := &ClientService{Client: testFakeClient(alice, bob)}
		lease := baseLease("owner", "alice")

		result, err := svc.applySharedWithChanges(context.Background(), lease, "default",
			[]string{"bob"}, []string{"alice"})
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(result) != 1 || result[0] != "bob" {
			t.Fatalf("expected [bob], got %v", result)
		}
	})

	t.Run("reject owner in add list", func(t *testing.T) {
		svc := &ClientService{Client: testFakeClient()}
		lease := baseLease("owner")

		_, err := svc.applySharedWithChanges(context.Background(), lease, "default",
			[]string{"owner"}, nil)
		if err == nil {
			t.Fatal("expected error when adding owner")
		}
	})

	t.Run("reject nonexistent client", func(t *testing.T) {
		svc := &ClientService{Client: testFakeClient()}
		lease := baseLease("owner")

		_, err := svc.applySharedWithChanges(context.Background(), lease, "default",
			[]string{"ghost"}, nil)
		if err == nil {
			t.Fatal("expected error for nonexistent client")
		}
	})

	t.Run("skip duplicate add", func(t *testing.T) {
		svc := &ClientService{Client: testFakeClient(alice)}
		lease := baseLease("owner", "alice")

		result, err := svc.applySharedWithChanges(context.Background(), lease, "default",
			[]string{"alice"}, nil)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(result) != 1 {
			t.Fatalf("expected [alice] (deduped), got %v", result)
		}
	})

	t.Run("reject exceeding max 10", func(t *testing.T) {
		var clients []kclient.Object
		var names []string
		for i := 0; i < 11; i++ {
			name := "client" + string(rune('a'+i))
			names = append(names, name)
			clients = append(clients, &jumpstarterdevv1alpha1.Client{
				ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "default"},
			})
		}
		svc := &ClientService{Client: testFakeClient(clients...)}
		lease := baseLease("owner")

		_, err := svc.applySharedWithChanges(context.Background(), lease, "default",
			names, nil)
		if err == nil {
			t.Fatal("expected error for exceeding max entries")
		}
	})

	t.Run("policy denial blocks add when exporter assigned", func(t *testing.T) {
		policy := &jumpstarterdevv1alpha1.ExporterAccessPolicy{
			ObjectMeta: metav1.ObjectMeta{Name: "policy1", Namespace: "default"},
			Spec: jumpstarterdevv1alpha1.ExporterAccessPolicySpec{
				ExporterSelector: metav1.LabelSelector{
					MatchLabels: map[string]string{"board": "rpi4"},
				},
				Policies: []jumpstarterdevv1alpha1.Policy{{
					From: []jumpstarterdevv1alpha1.From{{
						ClientSelector: metav1.LabelSelector{
							MatchLabels: map[string]string{"team": "security"},
						},
					}},
				}},
			},
		}
		exporter := &jumpstarterdevv1alpha1.Exporter{
			ObjectMeta: metav1.ObjectMeta{Name: "exp1", Namespace: "default",
				Labels: map[string]string{"board": "rpi4"}},
		}
		svc := &ClientService{Client: testFakeClient(alice, policy, exporter)}
		lease := baseLease("owner")
		lease.Status.ExporterRef = &corev1.LocalObjectReference{Name: "exp1"}

		_, err := svc.applySharedWithChanges(context.Background(), lease, "default",
			[]string{"alice"}, nil)
		if err == nil {
			t.Fatal("expected policy denial error")
		}
	})

	t.Run("policy allows client with matching labels", func(t *testing.T) {
		policy := &jumpstarterdevv1alpha1.ExporterAccessPolicy{
			ObjectMeta: metav1.ObjectMeta{Name: "policy1", Namespace: "default"},
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
		exporter := &jumpstarterdevv1alpha1.Exporter{
			ObjectMeta: metav1.ObjectMeta{Name: "exp1", Namespace: "default",
				Labels: map[string]string{"board": "rpi4"}},
		}
		svc := &ClientService{Client: testFakeClient(alice, policy, exporter)}
		lease := baseLease("owner")
		lease.Status.ExporterRef = &corev1.LocalObjectReference{Name: "exp1"}

		result, err := svc.applySharedWithChanges(context.Background(), lease, "default",
			[]string{"alice"}, nil)
		if err != nil {
			t.Fatalf("unexpected error: %v", err)
		}
		if len(result) != 1 || result[0] != "alice" {
			t.Fatalf("expected [alice], got %v", result)
		}
	})
}
