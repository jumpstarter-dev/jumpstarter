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
	"fmt"
	"math"
	"sort"
	"strconv"
	"strings"

	pb "github.com/jumpstarter-dev/jumpstarter/controller/internal/protocol/jumpstarter/v1"
	dto "github.com/prometheus/client_model/go"
	"google.golang.org/protobuf/types/known/timestamppb"
)

func dtoFromProtoFamilies(in []*pb.MetricsFamily) ([]*dto.MetricFamily, error) {
	out := make([]*dto.MetricFamily, 0, len(in))
	for _, fam := range in {
		if fam == nil || fam.GetName() == "" {
			continue
		}
		converted, err := dtoFromProtoFamily(fam)
		if err != nil {
			return nil, fmt.Errorf("family %s: %w", fam.GetName(), err)
		}
		if converted == nil || len(converted.Metric) == 0 {
			continue
		}
		out = append(out, converted)
	}
	return out, nil
}

func canonicalMetricFamilyName(fam *pb.MetricsFamily) string {
	name := fam.GetName()
	if fam.GetType() == pb.MetricsType_METRICS_TYPE_COUNTER && name != "" && !strings.HasSuffix(name, "_total") {
		// prometheus_client OpenMetrics collect() omits _total from the family
		// name. The hub encoder would otherwise emit TYPE unknown, and merge
		// would not join those series with text-path snapshots.
		return name + "_total"
	}
	return name
}

func dtoFromProtoFamily(fam *pb.MetricsFamily) (*dto.MetricFamily, error) {
	name := canonicalMetricFamilyName(fam)
	help := fam.GetHelp()
	mf := &dto.MetricFamily{
		Name: &name,
		Help: &help,
	}
	switch fam.GetType() {
	case pb.MetricsType_METRICS_TYPE_COUNTER:
		t := dto.MetricType_COUNTER
		mf.Type = &t
		for _, sample := range fam.GetSamples() {
			if skipCreatedSample(sample.GetName()) {
				continue
			}
			v := sample.GetValue()
			mf.Metric = append(mf.Metric, &dto.Metric{
				Label: dtoLabels(sample.GetLabels()),
				Counter: &dto.Counter{
					Value:    &v,
					Exemplar: dtoExemplar(sample.GetExemplar()),
				},
			})
		}
	case pb.MetricsType_METRICS_TYPE_GAUGE:
		t := dto.MetricType_GAUGE
		mf.Type = &t
		for _, sample := range fam.GetSamples() {
			v := sample.GetValue()
			mf.Metric = append(mf.Metric, &dto.Metric{
				Label: dtoLabels(sample.GetLabels()),
				Gauge: &dto.Gauge{Value: &v},
			})
		}
	case pb.MetricsType_METRICS_TYPE_UNTYPED, pb.MetricsType_METRICS_TYPE_UNSPECIFIED:
		t := dto.MetricType_UNTYPED
		mf.Type = &t
		for _, sample := range fam.GetSamples() {
			v := sample.GetValue()
			mf.Metric = append(mf.Metric, &dto.Metric{
				Label:   dtoLabels(sample.GetLabels()),
				Untyped: &dto.Untyped{Value: &v},
			})
		}
	case pb.MetricsType_METRICS_TYPE_HISTOGRAM:
		t := dto.MetricType_HISTOGRAM
		mf.Type = &t
		metrics, err := dtoHistogramMetrics(name, fam.GetSamples())
		if err != nil {
			return nil, err
		}
		mf.Metric = metrics
	case pb.MetricsType_METRICS_TYPE_SUMMARY:
		t := dto.MetricType_SUMMARY
		mf.Type = &t
		metrics, err := dtoSummaryMetrics(name, fam.GetSamples())
		if err != nil {
			return nil, err
		}
		mf.Metric = metrics
	default:
		return nil, fmt.Errorf("unsupported metric type %s", fam.GetType())
	}
	return mf, nil
}

func skipCreatedSample(sample string) bool {
	return strings.HasSuffix(sample, "_created")
}

func dtoLabels(in []*pb.MetricsLabel) []*dto.LabelPair {
	if len(in) == 0 {
		return nil
	}
	out := make([]*dto.LabelPair, 0, len(in))
	for _, lp := range in {
		if lp == nil || lp.GetName() == "" {
			continue
		}
		out = append(out, labelPair(lp.GetName(), lp.GetValue()))
	}
	sort.Slice(out, func(i, j int) bool { return out[i].GetName() < out[j].GetName() })
	return out
}

func dtoExemplar(ex *pb.MetricsExemplar) *dto.Exemplar {
	if ex == nil {
		return nil
	}
	if len(ex.GetLabels()) == 0 && ex.GetValue() == 0 && ex.GetTimestamp() == nil {
		return nil
	}
	v := ex.GetValue()
	out := &dto.Exemplar{
		Label: dtoLabels(ex.GetLabels()),
		Value: &v,
	}
	if ts := ex.GetTimestamp(); ts != nil && ts.IsValid() {
		out.Timestamp = timestamppb.New(ts.AsTime())
	}
	return out
}

type histAgg struct {
	labels  []*dto.LabelPair
	count   uint64
	sum     float64
	buckets []*dto.Bucket
}

func dtoHistogramMetrics(family string, samples []*pb.MetricsSample) ([]*dto.Metric, error) {
	grouped := map[string]*histAgg{}
	order := make([]string, 0)
	for _, sample := range samples {
		if sample == nil {
			continue
		}
		sname := sample.GetName()
		if skipCreatedSample(sname) {
			continue
		}
		labels := dtoLabels(sample.GetLabels())
		base, le, isBucket := splitLeLabel(labels)
		key := labelsKey(base)
		agg, ok := grouped[key]
		if !ok {
			agg = &histAgg{labels: base}
			grouped[key] = agg
			order = append(order, key)
		}
		switch {
		case sname == family+"_count":
			count, err := uint64Count(sample.GetValue())
			if err != nil {
				return nil, err
			}
			agg.count = count
		case sname == family+"_sum":
			agg.sum = sample.GetValue()
		case isBucket || sname == family+"_bucket":
			bound, err := parseLe(le)
			if err != nil {
				return nil, fmt.Errorf("bucket le %q: %w", le, err)
			}
			count, err := uint64Count(sample.GetValue())
			if err != nil {
				return nil, err
			}
			agg.buckets = append(agg.buckets, &dto.Bucket{
				CumulativeCount: &count,
				UpperBound:      &bound,
				Exemplar:        dtoExemplar(sample.GetExemplar()),
			})
		default:
			// Ignore native-histogram or unknown suffixes.
		}
	}
	out := make([]*dto.Metric, 0, len(order))
	for _, key := range order {
		agg := grouped[key]
		sort.Slice(agg.buckets, func(i, j int) bool {
			return agg.buckets[i].GetUpperBound() < agg.buckets[j].GetUpperBound()
		})
		out = append(out, &dto.Metric{
			Label: agg.labels,
			Histogram: &dto.Histogram{
				SampleCount: &agg.count,
				SampleSum:   &agg.sum,
				Bucket:      agg.buckets,
			},
		})
	}
	return out, nil
}

func dtoSummaryMetrics(family string, samples []*pb.MetricsSample) ([]*dto.Metric, error) {
	type sumAgg struct {
		labels    []*dto.LabelPair
		count     uint64
		sum       float64
		quantiles []*dto.Quantile
	}
	grouped := map[string]*sumAgg{}
	order := make([]string, 0)
	for _, sample := range samples {
		if sample == nil || skipCreatedSample(sample.GetName()) {
			continue
		}
		labels := dtoLabels(sample.GetLabels())
		base, qv, isQuantile, err := splitQuantileLabel(labels)
		if err != nil {
			return nil, err
		}
		key := labelsKey(base)
		agg, ok := grouped[key]
		if !ok {
			agg = &sumAgg{labels: base}
			grouped[key] = agg
			order = append(order, key)
		}
		switch sample.GetName() {
		case family + "_count":
			count, err := uint64Count(sample.GetValue())
			if err != nil {
				return nil, err
			}
			agg.count = count
		case family + "_sum":
			agg.sum = sample.GetValue()
		default:
			if isQuantile {
				q := qv
				v := sample.GetValue()
				agg.quantiles = append(agg.quantiles, &dto.Quantile{Quantile: &q, Value: &v})
			}
		}
	}
	out := make([]*dto.Metric, 0, len(order))
	for _, key := range order {
		agg := grouped[key]
		out = append(out, &dto.Metric{
			Label: agg.labels,
			Summary: &dto.Summary{
				SampleCount: &agg.count,
				SampleSum:   &agg.sum,
				Quantile:    agg.quantiles,
			},
		})
	}
	return out, nil
}

func splitLeLabel(labels []*dto.LabelPair) (base []*dto.LabelPair, le string, ok bool) {
	base = make([]*dto.LabelPair, 0, len(labels))
	for _, lp := range labels {
		if lp.GetName() == "le" {
			le = lp.GetValue()
			ok = true
			continue
		}
		base = append(base, lp)
	}
	return base, le, ok
}

func splitQuantileLabel(labels []*dto.LabelPair) (base []*dto.LabelPair, q float64, ok bool, err error) {
	base = make([]*dto.LabelPair, 0, len(labels))
	for _, lp := range labels {
		if lp.GetName() == "quantile" {
			v, perr := strconv.ParseFloat(lp.GetValue(), 64)
			if perr != nil {
				return nil, 0, false, fmt.Errorf("quantile %q: %w", lp.GetValue(), perr)
			}
			q = v
			ok = true
			continue
		}
		base = append(base, lp)
	}
	return base, q, ok, nil
}

func uint64Count(v float64) (uint64, error) {
	if math.IsNaN(v) || math.IsInf(v, 0) || v < 0 || v != math.Trunc(v) {
		return 0, fmt.Errorf("invalid count %v", v)
	}
	return uint64(v), nil
}

func parseLe(le string) (float64, error) {
	switch le {
	case "+Inf", "Inf", "inf":
		return math.Inf(1), nil
	case "-Inf":
		return math.Inf(-1), nil
	}
	return strconv.ParseFloat(le, 64)
}

func labelsKey(labels []*dto.LabelPair) string {
	var b strings.Builder
	for _, lp := range labels {
		b.WriteString(lp.GetName())
		b.WriteByte('=')
		b.WriteString(lp.GetValue())
		b.WriteByte(0)
	}
	return b.String()
}
