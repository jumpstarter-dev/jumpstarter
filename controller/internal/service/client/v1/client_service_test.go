package v1

import (
	"testing"

	cpb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/client/v1"
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
