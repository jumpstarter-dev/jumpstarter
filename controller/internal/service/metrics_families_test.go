/*
Copyright 2026.

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

package service

import (
	"math"
	"strings"
	"testing"

	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	dto "github.com/prometheus/client_model/go"
)

func pbL(kv ...string) []*pb.MetricsLabel {
	out := make([]*pb.MetricsLabel, 0, len(kv)/2)
	for i := 0; i+1 < len(kv); i += 2 {
		out = append(out, &pb.MetricsLabel{Name: kv[i], Value: kv[i+1]})
	}
	return out
}

func TestDtoFromProtoFamilies_CounterAndHistogramExemplars(t *testing.T) {
	families, err := dtoFromProtoFamilies([]*pb.MetricsFamily{
		{
			Name: "jumpstarter_operations_total",
			Help: "Total operations performed.",
			Type: pb.MetricsType_METRICS_TYPE_COUNTER,
			Samples: []*pb.MetricsSample{{
				Name:   "jumpstarter_operations_total",
				Labels: pbL("exporter", "sidekick", "operation", "on", "result", "success", "driver_type", "power"),
				Value:  2,
				Exemplar: &pb.MetricsExemplar{
					Labels: pbL("client", "ci-bot", "lease_id", "lease-1"),
					Value:  1,
				},
			}},
		},
		{
			Name: "jumpstarter_operation_duration_seconds",
			Help: "Duration of each operation.",
			Type: pb.MetricsType_METRICS_TYPE_HISTOGRAM,
			Samples: []*pb.MetricsSample{
				{
					Name:   "jumpstarter_operation_duration_seconds_bucket",
					Labels: pbL("exporter", "sidekick", "le", "0.005", "operation", "on", "result", "success", "driver_type", "power"),
					Value:  1,
					Exemplar: &pb.MetricsExemplar{
						Labels: pbL("lease_id", "lease-1"),
						Value:  0.00262,
					},
				},
				{
					Name:   "jumpstarter_operation_duration_seconds_bucket",
					Labels: pbL("exporter", "sidekick", "le", "+Inf", "operation", "on", "result", "success", "driver_type", "power"),
					Value:  1,
				},
				{
					Name:   "jumpstarter_operation_duration_seconds_sum",
					Labels: pbL("exporter", "sidekick", "operation", "on", "result", "success", "driver_type", "power"),
					Value:  0.00262,
				},
				{
					Name:   "jumpstarter_operation_duration_seconds_count",
					Labels: pbL("exporter", "sidekick", "operation", "on", "result", "success", "driver_type", "power"),
					Value:  1,
				},
			},
		},
	})
	if err != nil {
		t.Fatalf("dtoFromProtoFamilies: %v", err)
	}

	var ops, dur *dto.MetricFamily
	for _, f := range families {
		switch f.GetName() {
		case "jumpstarter_operations_total":
			ops = f
		case "jumpstarter_operation_duration_seconds":
			dur = f
		}
	}
	if ops == nil || len(ops.Metric) != 1 {
		t.Fatalf("counter family = %+v", ops)
	}
	ex := ops.Metric[0].GetCounter().GetExemplar()
	if ex == nil {
		t.Fatal("counter exemplar missing")
	}
	got := map[string]string{}
	for _, lp := range ex.GetLabel() {
		got[lp.GetName()] = lp.GetValue()
	}
	if got["client"] != "ci-bot" || got["lease_id"] != "lease-1" {
		t.Errorf("counter exemplar = %v", got)
	}

	if dur == nil || len(dur.Metric) != 1 {
		t.Fatalf("histogram family = %+v", dur)
	}
	h := dur.Metric[0].GetHistogram()
	if h.GetSampleCount() != 1 {
		t.Errorf("sample_count = %d", h.GetSampleCount())
	}
	if len(h.GetBucket()) != 2 {
		t.Fatalf("buckets = %d, want 2", len(h.GetBucket()))
	}
	var infBound bool
	var bucketEx *dto.Exemplar
	for _, b := range h.GetBucket() {
		if math.IsInf(b.GetUpperBound(), 1) {
			infBound = true
		}
		if b.GetExemplar() != nil {
			bucketEx = b.GetExemplar()
		}
	}
	if !infBound {
		t.Error("missing +Inf bucket")
	}
	if bucketEx == nil {
		t.Fatal("histogram bucket exemplar missing")
	}
	foundLease := false
	for _, lp := range bucketEx.GetLabel() {
		if lp.GetName() == "lease_id" && lp.GetValue() == "lease-1" {
			foundLease = true
		}
	}
	if !foundLease {
		t.Errorf("bucket exemplar labels = %v", bucketEx.GetLabel())
	}
}

func TestDtoFromProtoFamilies_CanonicalizesOpenMetricsCounterName(t *testing.T) {
	families, err := dtoFromProtoFamilies([]*pb.MetricsFamily{{
		Name: "jumpstarter_operations",
		Help: "Total operations performed.",
		Type: pb.MetricsType_METRICS_TYPE_COUNTER,
		Samples: []*pb.MetricsSample{{
			Name:   "jumpstarter_operations_total",
			Labels: pbL("operation", "on"),
			Value:  1,
			Exemplar: &pb.MetricsExemplar{
				Labels: pbL("lease_id", "lease-1"),
				Value:  1,
			},
		}, {
			Name:   "jumpstarter_operations_created",
			Labels: pbL("operation", "on"),
			Value:  1.789e9,
		}},
	}})
	if err != nil {
		t.Fatalf("dtoFromProtoFamilies: %v", err)
	}
	if len(families) != 1 {
		t.Fatalf("families = %d, want 1", len(families))
	}
	if families[0].GetName() != "jumpstarter_operations_total" {
		t.Errorf("name = %q, want jumpstarter_operations_total", families[0].GetName())
	}
	if len(families[0].Metric) != 1 {
		t.Fatalf("created sample should be dropped, got %d metrics", len(families[0].Metric))
	}
	if families[0].Metric[0].GetCounter().GetExemplar() == nil {
		t.Fatal("exemplar dropped during canonicalize")
	}
}

func TestMergeSnapshots_PrefersFamiliesOverUnparseableText(t *testing.T) {
	var calls int
	families := mergeSnapshots([]exporterSnapshot{{
		name: "sidekick",
		text: []byte(pythonOpenMetricsWithExemplar),
		families: []*pb.MetricsFamily{{
			Name: "jumpstarter_operation_duration_seconds",
			Help: "Duration of each operation.",
			Type: pb.MetricsType_METRICS_TYPE_HISTOGRAM,
			Samples: []*pb.MetricsSample{
				{
					Name:   "jumpstarter_operation_duration_seconds_bucket",
					Labels: pbL("le", "0.005", "operation", "on", "result", "success", "driver_type", "power"),
					Value:  1,
					Exemplar: &pb.MetricsExemplar{
						Labels: pbL("lease_id", "lease-1", "trace_id", "drop-me"),
						Value:  0.00262,
					},
				},
				{
					Name:   "jumpstarter_operation_duration_seconds_sum",
					Labels: pbL("operation", "on", "result", "success", "driver_type", "power"),
					Value:  0.00262,
				},
				{
					Name:   "jumpstarter_operation_duration_seconds_count",
					Labels: pbL("operation", "on", "result", "success", "driver_type", "power"),
					Value:  1,
				},
			},
		}},
	}}, nil, testMergeCfg, func(string, error) { calls++ })

	if calls != 0 {
		t.Fatalf("parse-error callbacks = %d, want 0 when families sidecar is present", calls)
	}
	var dur *dto.MetricFamily
	for _, f := range families {
		if f.GetName() == "jumpstarter_operation_duration_seconds" {
			dur = f
		}
	}
	if dur == nil || len(dur.Metric) != 1 {
		t.Fatalf("expected merged histogram, got %+v", families)
	}
	if got, _ := getLabel(dur.Metric[0], "exporter"); got != "sidekick" {
		t.Errorf("exporter = %q, want sidekick", got)
	}
	ex := dur.Metric[0].GetHistogram().GetBucket()[0].GetExemplar()
	if ex == nil {
		t.Fatal("allowlisted exemplar dropped")
	}
	for _, lp := range ex.GetLabel() {
		if lp.GetName() == "trace_id" {
			t.Errorf("trace_id should have been filtered, got %v", ex.GetLabel())
		}
	}
	foundLease := false
	for _, lp := range ex.GetLabel() {
		if lp.GetName() == "lease_id" && lp.GetValue() == "lease-1" {
			foundLease = true
		}
	}
	if !foundLease {
		t.Errorf("lease_id missing from filtered exemplar: %v", ex.GetLabel())
	}
}

func TestDtoFromProtoFamilies_RejectsMalformedSummaryQuantile(t *testing.T) {
	_, err := dtoFromProtoFamilies([]*pb.MetricsFamily{{
		Name: "rpc_latency_seconds",
		Help: "RPC latency.",
		Type: pb.MetricsType_METRICS_TYPE_SUMMARY,
		Samples: []*pb.MetricsSample{
			{
				Name:   "rpc_latency_seconds",
				Labels: pbL("quantile", "not-a-number"),
				Value:  0.2,
			},
			{
				Name:  "rpc_latency_seconds_count",
				Value: 10,
			},
			{
				Name:  "rpc_latency_seconds_sum",
				Value: 1.5,
			},
		},
	}})
	if err == nil {
		t.Fatal("expected error for non-numeric quantile label")
	}
}

func TestDtoFromProtoFamilies_RejectsInvalidCounts(t *testing.T) {
	cases := []struct {
		name    string
		family  *pb.MetricsFamily
		wantErr string
	}{
		{
			name: "histogram negative count",
			family: &pb.MetricsFamily{
				Name: "op_duration_seconds",
				Type: pb.MetricsType_METRICS_TYPE_HISTOGRAM,
				Samples: []*pb.MetricsSample{
					{Name: "op_duration_seconds_count", Value: -1},
					{Name: "op_duration_seconds_sum", Value: 0.1},
				},
			},
			wantErr: "count",
		},
		{
			name: "histogram fractional count",
			family: &pb.MetricsFamily{
				Name: "op_duration_seconds",
				Type: pb.MetricsType_METRICS_TYPE_HISTOGRAM,
				Samples: []*pb.MetricsSample{
					{Name: "op_duration_seconds_count", Value: 1.5},
					{Name: "op_duration_seconds_sum", Value: 0.1},
				},
			},
			wantErr: "count",
		},
		{
			name: "histogram nan bucket",
			family: &pb.MetricsFamily{
				Name: "op_duration_seconds",
				Type: pb.MetricsType_METRICS_TYPE_HISTOGRAM,
				Samples: []*pb.MetricsSample{
					{Name: "op_duration_seconds_bucket", Labels: pbL("le", "+Inf"), Value: math.NaN()},
					{Name: "op_duration_seconds_count", Value: 1},
				},
			},
			wantErr: "count",
		},
		{
			name: "summary infinite count",
			family: &pb.MetricsFamily{
				Name: "rpc_latency_seconds",
				Type: pb.MetricsType_METRICS_TYPE_SUMMARY,
				Samples: []*pb.MetricsSample{
					{Name: "rpc_latency_seconds_count", Value: math.Inf(1)},
					{Name: "rpc_latency_seconds_sum", Value: 1},
				},
			},
			wantErr: "count",
		},
	}
	for _, tc := range cases {
		t.Run(tc.name, func(t *testing.T) {
			_, err := dtoFromProtoFamilies([]*pb.MetricsFamily{tc.family})
			if err == nil {
				t.Fatal("expected error for invalid count")
			}
			if !strings.Contains(err.Error(), tc.wantErr) {
				t.Fatalf("error %q, want substring %q", err, tc.wantErr)
			}
		})
	}
}
