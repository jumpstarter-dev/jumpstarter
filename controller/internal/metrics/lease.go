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

// Package metrics provides Jumpstarter controller Prometheus metrics (JEP-0013 Phase 2).
package metrics

import (
	"github.com/prometheus/client_golang/prometheus"
	ctrlmetrics "sigs.k8s.io/controller-runtime/pkg/metrics"
)

const (
	// LeaseAcquisitionsTotal is the JEP-0013 counter for lease acquire attempts.
	LeaseAcquisitionsTotal = "jumpstarter_lease_acquisitions_total"

	ResultSuccess = "success"
	ResultFailure = "failure"
)

// DefaultExemplarKeys are the JEP-0013 default exemplar allowlist keys.
var DefaultExemplarKeys = []string{"client", "lease_id"}

// LeaseMetrics holds lease-related Prometheus collectors.
type LeaseMetrics struct {
	acquisitions *prometheus.CounterVec
}

// NewLeaseMetrics constructs lease metrics. Call Register before RecordAcquisition.
func NewLeaseMetrics() *LeaseMetrics {
	return &LeaseMetrics{
		acquisitions: prometheus.NewCounterVec(
			prometheus.CounterOpts{
				Name: LeaseAcquisitionsTotal,
				Help: "Lease acquire attempts on the Jumpstarter controller.",
			},
			[]string{"result"},
		),
	}
}

// Register registers collectors with the given registerer.
func (m *LeaseMetrics) Register(r prometheus.Registerer) error {
	return r.Register(m.acquisitions)
}

// MustRegister registers collectors and panics on error.
func (m *LeaseMetrics) MustRegister(r prometheus.Registerer) {
	r.MustRegister(m.acquisitions)
}

// MustRegisterWithControllerRuntime registers on the controller-runtime metrics registry.
func (m *LeaseMetrics) MustRegisterWithControllerRuntime() {
	m.MustRegister(ctrlmetrics.Registry)
}

// RecordAcquisition increments jumpstarter_lease_acquisitions_total for result
// and attaches exemplar labels from the allowlist (client, lease_id when present).
func (m *LeaseMetrics) RecordAcquisition(result string, exemplars map[string]string) {
	if m == nil || m.acquisitions == nil {
		return
	}
	metric, err := m.acquisitions.GetMetricWithLabelValues(result)
	if err != nil {
		return
	}
	labels := filterExemplarLabels(exemplars)
	if len(labels) > 0 {
		if adder, ok := metric.(prometheus.ExemplarAdder); ok {
			adder.AddWithExemplar(1, labels)
			return
		}
	}
	metric.Inc()
}

func filterExemplarLabels(in map[string]string) prometheus.Labels {
	if len(in) == 0 {
		return nil
	}
	out := prometheus.Labels{}
	for _, key := range DefaultExemplarKeys {
		if v, ok := in[key]; ok && v != "" {
			out[key] = v
		}
	}
	if len(out) == 0 {
		return nil
	}
	return out
}

// Default is the process-wide lease metrics instance used by the controller.
var Default = NewLeaseMetrics()

// RegisterDefaults registers Default with the controller-runtime registry.
func RegisterDefaults() {
	Default.MustRegisterWithControllerRuntime()
}
