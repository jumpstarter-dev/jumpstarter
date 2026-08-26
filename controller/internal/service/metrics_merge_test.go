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
	"bytes"
	"strings"
	"testing"

	dto "github.com/prometheus/client_model/go"
	"github.com/prometheus/common/expfmt"
)

func TestApplyMetric_OverwritesExporterRemapsDriverTypeAndFiltersExemplars(t *testing.T) {
	one := 1.0
	m := &dto.Metric{
		Label: []*dto.LabelPair{
			labelPair("exporter", "spoofed"),
			labelPair("driver_type", "tuya"),
			labelPair("operation", "on"),
		},
		Counter: &dto.Counter{
			Value: &one,
			Exemplar: &dto.Exemplar{
				Label: []*dto.LabelPair{
					labelPair("client", "ci"),
					labelPair("lease_id", "abc"),
					labelPair("trace_id", "drop-me"),
				},
			},
		},
	}
	applyMetric(m, mergeConfig{
		exporterName: "sidekick",
		driverTypes:  setToMap(DefaultDriverTypeEnum),
		exemplarKeys: setToMap(DefaultExemplarKeys),
	})

	if got, _ := getLabel(m, "exporter"); got != "sidekick" {
		t.Errorf("exporter label = %q, want sidekick", got)
	}
	if got, _ := getLabel(m, "driver_type"); got != "other" {
		t.Errorf("driver_type = %q, want other", got)
	}
	ex := m.Counter.GetExemplar()
	if ex == nil {
		t.Fatal("expected exemplar to be kept")
	}
	got := map[string]string{}
	for _, lp := range ex.GetLabel() {
		got[lp.GetName()] = lp.GetValue()
	}
	if got["client"] != "ci" || got["lease_id"] != "abc" {
		t.Errorf("exemplar labels = %v, want client=ci lease_id=abc", got)
	}
	if _, ok := got["trace_id"]; ok {
		t.Errorf("trace_id should have been dropped, got %v", got)
	}
}

func TestApplyMetric_KeepsAllowlistedDriverType(t *testing.T) {
	m := &dto.Metric{
		Label: []*dto.LabelPair{
			labelPair("driver_type", "power"),
		},
	}
	applyMetric(m, mergeConfig{
		exporterName: "exp-a",
		driverTypes:  setToMap(DefaultDriverTypeEnum),
		exemplarKeys: setToMap(DefaultExemplarKeys),
	})
	if got, _ := getLabel(m, "driver_type"); got != "power" {
		t.Errorf("driver_type = %q, want power", got)
	}
	if got, _ := getLabel(m, "exporter"); got != "exp-a" {
		t.Errorf("exporter = %q, want exp-a", got)
	}
}

func TestMergeSnapshots_CombinesExportersAndSkipsInvalidText(t *testing.T) {
	cfg := func(name string) mergeConfig {
		return mergeConfig{
			exporterName: name,
			driverTypes:  setToMap(DefaultDriverTypeEnum),
			exemplarKeys: setToMap(DefaultExemplarKeys),
		}
	}
	textA := []byte(`# TYPE jumpstarter_operations_total counter
# HELP jumpstarter_operations_total Total operations performed.
jumpstarter_operations_total{exporter="a",operation="on",result="success",driver_type="power"} 2.0
# EOF
`)
	textB := []byte(`# TYPE jumpstarter_operations_total counter
jumpstarter_operations_total{exporter="b",operation="off",result="success",driver_type="power"} 3.0
# EOF
`)
	families := mergeSnapshots([]exporterSnapshot{
		{name: "exp-a", text: textA},
		{name: "exp-b", text: textB},
		{name: "bad", text: []byte("not metrics")},
	}, nil, cfg)

	var ops *dto.MetricFamily
	for _, f := range families {
		if f.GetName() == "jumpstarter_operations_total" {
			ops = f
			break
		}
	}
	if ops == nil {
		t.Fatal("missing jumpstarter_operations_total")
	}
	if len(ops.Metric) != 2 {
		t.Fatalf("got %d series, want 2 (invalid snapshot omitted)", len(ops.Metric))
	}
	exporters := map[string]bool{}
	for _, m := range ops.Metric {
		name, _ := getLabel(m, "exporter")
		exporters[name] = true
	}
	if !exporters["exp-a"] || !exporters["exp-b"] {
		t.Errorf("exporters = %v, want exp-a and exp-b", exporters)
	}
}

func TestEncodeMetricFamilies_WritesOpenMetricsEOF(t *testing.T) {
	name := "jumpstarter_scrape_timeouts_total"
	help := "Exporter MetricsStream scrapes that exceeded scrapeTimeout."
	metricType := dto.MetricType_COUNTER
	zero := 0.0
	families := []*dto.MetricFamily{{
		Name: &name,
		Help: &help,
		Type: &metricType,
		Metric: []*dto.Metric{{
			Counter: &dto.Counter{Value: &zero},
		}},
	}}
	var buf bytes.Buffer
	if err := encodeMetricFamilies(&buf, families); err != nil {
		t.Fatalf("encode: %v", err)
	}
	body := buf.String()
	if !strings.Contains(body, scrapeTimeoutsMetric) {
		t.Errorf("encoded body missing %s:\n%s", scrapeTimeoutsMetric, body)
	}
	if !strings.Contains(body, "# EOF") {
		t.Errorf("OpenMetrics body missing # EOF:\n%s", body)
	}
	dec := expfmt.NewDecoder(strings.NewReader(body), expfmt.NewFormat(expfmt.TypeOpenMetrics))
	mf := new(dto.MetricFamily)
	if err := dec.Decode(mf); err != nil {
		t.Fatalf("re-decode: %v", err)
	}
	if mf.GetName() != scrapeTimeoutsMetric {
		t.Errorf("decoded name = %q", mf.GetName())
	}
}
