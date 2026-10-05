/*
Copyright 2026. The Jumpstarter Authors.

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

package metrics

import (
	"context"
	"strings"
	"testing"
	"unicode/utf8"

	"github.com/go-logr/logr/funcr"
	jlog "github.com/jumpstarter-dev/jumpstarter/controller/internal/log"
	"github.com/prometheus/client_golang/prometheus"
	dto "github.com/prometheus/client_model/go"
	"github.com/prometheus/common/expfmt"
	"sigs.k8s.io/controller-runtime/pkg/log"
)

func TestLeaseAcquisitionsMetricName(t *testing.T) {
	if LeaseAcquisitionsTotal != "jumpstarter_lease_acquisitions_total" {
		t.Fatalf("unexpected metric name %q", LeaseAcquisitionsTotal)
	}
}

func TestRegisterInitializesZeroSeries(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewLeaseMetrics()
	if err := m.Register(reg); err != nil {
		t.Fatalf("register: %v", err)
	}

	families, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}
	var found *dto.MetricFamily
	for _, f := range families {
		if f.GetName() == LeaseAcquisitionsTotal {
			found = f
			break
		}
	}
	if found == nil {
		t.Fatalf("metric %s not found after register", LeaseAcquisitionsTotal)
	}
	success := sampleValue(found, map[string]string{"result": ResultSuccess})
	failure := sampleValue(found, map[string]string{"result": ResultFailure})
	if success != 0 {
		t.Fatalf("success count = %v, want 0 before observations", success)
	}
	if failure != 0 {
		t.Fatalf("failure count = %v, want 0 before observations", failure)
	}
}

func TestRecordLeaseAcquisitionIncrementsCounter(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewLeaseMetrics()
	if err := m.Register(reg); err != nil {
		t.Fatalf("register: %v", err)
	}

	ctx := context.Background()
	m.RecordAcquisition(ctx, ResultSuccess, map[string]string{
		"client":   "p16v",
		"lease_id": "lease-abc",
	})
	m.RecordAcquisition(ctx, ResultFailure, map[string]string{
		"client":   "p16v",
		"lease_id": "lease-def",
	})

	families, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}

	var found *dto.MetricFamily
	for _, f := range families {
		if f.GetName() == LeaseAcquisitionsTotal {
			found = f
			break
		}
	}
	if found == nil {
		t.Fatalf("metric %s not found in registry output", LeaseAcquisitionsTotal)
	}
	if found.GetType() != dto.MetricType_COUNTER {
		t.Fatalf("expected COUNTER, got %v", found.GetType())
	}

	success := sampleValue(found, map[string]string{"result": ResultSuccess})
	failure := sampleValue(found, map[string]string{"result": ResultFailure})
	if success != 1 {
		t.Fatalf("success count = %v, want 1", success)
	}
	if failure != 1 {
		t.Fatalf("failure count = %v, want 1", failure)
	}
}

func TestRecordLeaseAcquisitionAttachesExemplars(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewLeaseMetrics()
	if err := m.Register(reg); err != nil {
		t.Fatalf("register: %v", err)
	}

	m.RecordAcquisition(context.Background(), ResultSuccess, map[string]string{
		"client":   "ci-bot",
		"lease_id": "lease-xyz",
		"ignored":  "should-not-appear",
	})

	families, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}

	var metric *dto.Metric
	for _, f := range families {
		if f.GetName() != LeaseAcquisitionsTotal {
			continue
		}
		for _, met := range f.Metric {
			if labelValue(met, "result") == ResultSuccess {
				metric = met
				break
			}
		}
	}
	if metric == nil {
		t.Fatal("success sample not found")
	}
	if metric.GetCounter() == nil || metric.GetCounter().GetExemplar() == nil {
		t.Fatal("expected exemplar on success observation")
	}

	ex := exemplarMap(metric.GetCounter().GetExemplar())
	if ex["client"] != "ci-bot" {
		t.Fatalf("exemplar client = %q, want ci-bot", ex["client"])
	}
	if ex["lease_id"] != "lease-xyz" {
		t.Fatalf("exemplar lease_id = %q, want lease-xyz", ex["lease_id"])
	}
	if _, ok := ex["ignored"]; ok {
		t.Fatal("non-allowlisted exemplar key should not be present")
	}
}

func TestPrometheusTextContainsSeries(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewLeaseMetrics()
	if err := m.Register(reg); err != nil {
		t.Fatalf("register: %v", err)
	}
	m.RecordAcquisition(context.Background(), ResultSuccess, map[string]string{"client": "c", "lease_id": "l"})

	var b strings.Builder
	families, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}
	for _, f := range families {
		if _, err := expfmt.MetricFamilyToText(&b, f); err != nil {
			t.Fatalf("encode: %v", err)
		}
	}
	out := b.String()
	if !strings.Contains(out, LeaseAcquisitionsTotal) {
		t.Fatalf("Prometheus text missing %s:\n%s", LeaseAcquisitionsTotal, out)
	}
	if !strings.Contains(out, `result="success"`) {
		t.Fatalf("Prometheus text missing result label:\n%s", out)
	}
}

func TestRecordAcquisitionTruncatesOverBudgetExemplars(t *testing.T) {
	reg := prometheus.NewRegistry()
	m := NewLeaseMetrics()
	if err := m.Register(reg); err != nil {
		t.Fatalf("register: %v", err)
	}

	var logged []string
	logger := funcr.New(func(_, args string) {
		logged = append(logged, args)
	}, funcr.Options{Verbosity: jlog.LevelWarning})
	ctx := log.IntoContext(context.Background(), logger)

	// Combined key+value runes exceed OpenMetrics' 128 limit; must not panic,
	// must still increment, and must attach a constrained exemplar.
	long := strings.Repeat("a", 200)
	m.RecordAcquisition(ctx, ResultSuccess, map[string]string{
		"client":   long,
		"lease_id": long,
	})

	if len(logged) == 0 {
		t.Fatal("expected warning log when exemplar budget is exceeded")
	}
	if !strings.Contains(logged[0], "OpenMetrics budget") {
		t.Fatalf("unexpected log message %q", logged[0])
	}

	families, err := reg.Gather()
	if err != nil {
		t.Fatalf("gather: %v", err)
	}
	var found *dto.MetricFamily
	for _, f := range families {
		if f.GetName() == LeaseAcquisitionsTotal {
			found = f
			break
		}
	}
	if found == nil {
		t.Fatalf("metric %s not found", LeaseAcquisitionsTotal)
	}
	if sampleValue(found, map[string]string{"result": ResultSuccess}) != 1 {
		t.Fatal("counter must increment even when exemplars are truncated")
	}

	var metric *dto.Metric
	for _, met := range found.Metric {
		if labelValue(met, "result") == ResultSuccess {
			metric = met
			break
		}
	}
	if metric == nil || metric.GetCounter() == nil || metric.GetCounter().GetExemplar() == nil {
		t.Fatal("expected constrained exemplar on success observation")
	}
	ex := exemplarMap(metric.GetCounter().GetExemplar())
	total := 0
	for k, v := range ex {
		total += utf8.RuneCountInString(k) + utf8.RuneCountInString(v)
	}
	if total > maxExemplarRunes {
		t.Fatalf("exemplar rune budget exceeded: %d > %d (%v)", total, maxExemplarRunes, ex)
	}
	if ex["client"] == "" {
		t.Fatal("expected truncated client exemplar to be retained")
	}
	if utf8.RuneCountInString(ex["client"]) > maxExemplarRunes-utf8.RuneCountInString("client") {
		t.Fatalf("client exemplar value too long: %q", ex["client"])
	}
}

func sampleValue(f *dto.MetricFamily, want map[string]string) float64 {
	for _, m := range f.Metric {
		ok := true
		for k, v := range want {
			if labelValue(m, k) != v {
				ok = false
				break
			}
		}
		if ok {
			return m.GetCounter().GetValue()
		}
	}
	return 0
}

func labelValue(m *dto.Metric, name string) string {
	for _, l := range m.Label {
		if l.GetName() == name {
			return l.GetValue()
		}
	}
	return ""
}

func exemplarMap(e *dto.Exemplar) map[string]string {
	out := make(map[string]string)
	for _, l := range e.Label {
		out[l.GetName()] = l.GetValue()
	}
	return out
}
