package v1

import (
	"testing"

	cpb "github.com/jumpstarter-dev/jumpstarter-controller/internal/protocol/jumpstarter/client/v1"
	"google.golang.org/grpc/codes"
	"google.golang.org/grpc/status"
)

func FuzzValidateLeaseTarget(f *testing.F) {
	f.Add("dut=a", "", false, true)
	f.Add("", "laptop-test-exporter", true, true)
	f.Add("purpose=test", "my-exporter", true, true)
	f.Add("", "", false, true)
	f.Add("", "", true, true)
	f.Add("", "", false, false)

	f.Fuzz(func(t *testing.T, selector, exporterName string, hasExporterName, hasLease bool) {
		if !hasLease {
			err := validateLeaseTarget(nil)
			if err == nil {
				t.Fatal("validateLeaseTarget(nil) should always return error")
			}
			st, ok := status.FromError(err)
			if !ok || st.Code() != codes.InvalidArgument {
				t.Errorf("nil lease should return InvalidArgument, got: %v", err)
			}
			return
		}

		lease := &cpb.Lease{Selector: selector}
		if hasExporterName {
			lease.ExporterName = &exporterName
		}

		err := validateLeaseTarget(lease)

		hasSelector := selector != ""
		hasExpName := hasExporterName && exporterName != ""

		if !hasSelector && !hasExpName {
			if err == nil {
				t.Error("validateLeaseTarget should reject lease with neither selector nor exporter_name")
			}
			st, ok := status.FromError(err)
			if !ok || st.Code() != codes.InvalidArgument {
				t.Errorf("expected InvalidArgument for missing target, got: %v", err)
			}
		} else {
			if err != nil {
				t.Errorf("validateLeaseTarget should accept lease with selector=%q exporter_name=%q (has=%v), got: %v",
					selector, exporterName, hasExporterName, err)
			}
		}
	})
}
