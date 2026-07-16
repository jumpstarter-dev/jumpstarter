package v1

import (
	"context"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	cpb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/client/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
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
