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

package e2e

import (
	"context"
	"errors"
	"io"
	"strings"
	"time"

	. "github.com/onsi/gomega"
	dto "github.com/prometheus/client_model/go"
	"github.com/prometheus/common/expfmt"
	"github.com/prometheus/common/model"
)

// expectServiceMetrics performs a live HTTP GET of a Service's /metrics
// endpoint through the Kubernetes API server service proxy. This proves that
// the Service selector resolves to a ready endpoint and that the endpoint
// returns a non-empty Prometheus/OpenMetrics response.
func expectServiceMetrics(namespace, service, port string, assert func(Gomega, string)) {
	Eventually(func(g Gomega) {
		scrapeCtx, cancel := context.WithTimeout(ctx, 10*time.Second)
		defer cancel()

		body, err := clientset.CoreV1().Services(namespace).
			ProxyGet("http", service, port, "/metrics", nil).
			DoRaw(scrapeCtx)
		g.Expect(err).NotTo(HaveOccurred())
		g.Expect(body).NotTo(BeEmpty())
		g.Expect(string(body)).To(ContainSubstring("# HELP"))
		assert(g, string(body))
	}, 5*time.Minute, 2*time.Second).Should(Succeed())
}

// parsePrometheusFamilies parses the Prometheus text exposition format.
func parsePrometheusFamilies(g Gomega, body string) map[string]*dto.MetricFamily {
	parser := expfmt.NewTextParser(model.UTF8Validation)
	families, err := parser.TextToMetricFamilies(strings.NewReader(body))
	g.Expect(err).NotTo(HaveOccurred())
	return families
}

// parseOpenMetricsFamilies parses the OpenMetrics exposition format used by
// the Telemetry service.
func parseOpenMetricsFamilies(g Gomega, body string) map[string]*dto.MetricFamily {
	decoder := expfmt.NewDecoder(strings.NewReader(body), expfmt.NewFormat(expfmt.TypeOpenMetrics))
	families := make(map[string]*dto.MetricFamily)
	for {
		var family dto.MetricFamily
		err := decoder.Decode(&family)
		if errors.Is(err, io.EOF) {
			return families
		}
		g.Expect(err).NotTo(HaveOccurred())
		families[family.GetName()] = &family
	}
}

// expectMetricLabelValue asserts that a metric family contains at least one
// sample with the given label name/value pair.
func expectMetricLabelValue(g Gomega, family *dto.MetricFamily, name, value string) {
	g.Expect(family).NotTo(BeNil())
	for _, metric := range family.GetMetric() {
		for _, label := range metric.GetLabel() {
			if label.GetName() == name && label.GetValue() == value {
				return
			}
		}
	}
	g.Expect(false).To(BeTrue(),
		"metric family %s has no sample with label %s=%s", family.GetName(), name, value)
}
