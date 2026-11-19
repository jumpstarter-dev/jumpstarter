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

package jumpstarter

import (
	"encoding/json"
	"os"
	"path/filepath"
	"regexp"
	"slices"
	"strings"
	"testing"

	"k8s.io/apimachinery/pkg/apis/meta/v1/unstructured"
)

// grafanaSamplePath is the optional Grafana dashboard shipped in the docs. It
// mirrors the operator's Perses dashboard, so it is checked against the builder
// rather than maintained as an independent artifact (RQ5).
var grafanaSamplePath = filepath.Join(
	"..", "..", "..", "..", "..", "..",
	"docs", "source", "_static", "jumpstarter-grafana-dashboard.json",
)

var jumpstarterMetricPattern = regexp.MustCompile(`jumpstarter_[a-z0-9_]*[a-z0-9]`)

type grafanaSample struct {
	UID    string `json:"uid"`
	Title  string `json:"title"`
	Panels []grafanaPanel
}

type grafanaPanel struct {
	ID         int    `json:"id"`
	Title      string `json:"title"`
	Type       string `json:"type"`
	Datasource struct {
		Type string `json:"type"`
		UID  string `json:"uid"`
	} `json:"datasource"`
	Targets []struct {
		Expr       string `json:"expr"`
		Datasource struct {
			Type string `json:"type"`
			UID  string `json:"uid"`
		} `json:"datasource"`
	} `json:"targets"`
}

func loadGrafanaSample(t *testing.T) grafanaSample {
	t.Helper()

	content, err := os.ReadFile(grafanaSamplePath)
	if err != nil {
		t.Fatalf("read Grafana sample: %v", err)
	}

	var sample grafanaSample
	if err := json.Unmarshal(content, &sample); err != nil {
		t.Fatalf("parse Grafana sample: %v", err)
	}
	return sample
}

// persesDashboardQueries returns the PromQL/LogQL expressions and panel titles
// from the operator-generated Perses dashboard.
func persesDashboardQueries(t *testing.T, dashboard *unstructured.Unstructured) (queries, titles []string) {
	t.Helper()

	panels, found, err := unstructured.NestedMap(dashboard.Object, "spec", "config", "panels")
	if err != nil || !found {
		t.Fatalf("Perses dashboard has no spec.config.panels (found=%v, err=%v)", found, err)
	}

	for _, raw := range panels {
		panel, ok := raw.(map[string]any)
		if !ok {
			t.Fatalf("panel is not an object: %#v", raw)
		}
		spec, ok := panel["spec"].(map[string]any)
		if !ok {
			t.Fatalf("panel spec is not an object: %#v", panel)
		}
		if display, ok := spec["display"].(map[string]any); ok {
			if name, ok := display["name"].(string); ok {
				titles = append(titles, name)
			}
		}
		panelQueries, ok := spec["queries"].([]any)
		if !ok {
			continue
		}
		for _, rawQuery := range panelQueries {
			query, ok := rawQuery.(map[string]any)
			if !ok {
				continue
			}
			querySpec, ok := query["spec"].(map[string]any)
			if !ok {
				continue
			}
			plugin, ok := querySpec["plugin"].(map[string]any)
			if !ok {
				continue
			}
			pluginSpec, ok := plugin["spec"].(map[string]any)
			if !ok {
				continue
			}
			if expr, ok := pluginSpec["query"].(string); ok {
				queries = append(queries, expr)
			}
		}
	}
	return queries, titles
}

// metricNames returns the sorted, de-duplicated Jumpstarter metric names
// referenced by the given expressions.
func metricNames(expressions []string) []string {
	seen := map[string]struct{}{}
	for _, expr := range expressions {
		for _, match := range jumpstarterMetricPattern.FindAllString(expr, -1) {
			seen[match] = struct{}{}
		}
	}
	names := make([]string, 0, len(seen))
	for name := range seen {
		names = append(names, name)
	}
	slices.Sort(names)
	return names
}

// TestGrafanaSampleMatchesPersesDashboard keeps the optional Grafana artifact
// from drifting: the same metrics, panel titles, and panel count as the
// operator-generated Perses dashboard.
func TestGrafanaSampleMatchesPersesDashboard(t *testing.T) {
	sample := loadGrafanaSample(t)

	if sample.UID != "jumpstarter-overview" {
		t.Errorf("Grafana uid = %q, want jumpstarter-overview", sample.UID)
	}
	if sample.Title != "Jumpstarter Overview" {
		t.Errorf("Grafana title = %q, want Jumpstarter Overview", sample.Title)
	}

	persesQueries, persesTitles := persesDashboardQueries(t, desiredPersesDashboard(testJumpstarter()))

	var grafanaQueries []string
	var grafanaTitles []string
	for _, panel := range sample.Panels {
		if panel.Title == "" || panel.Type == "" {
			t.Errorf("Grafana panel %d is missing a title or type", panel.ID)
		}
		if panel.Datasource.UID == "" {
			t.Errorf("Grafana panel %q has no datasource uid", panel.Title)
		}
		grafanaTitles = append(grafanaTitles, panel.Title)
		for _, target := range panel.Targets {
			if target.Datasource.UID == "" {
				t.Errorf("Grafana panel %q target has no datasource uid", panel.Title)
			}
			if strings.TrimSpace(target.Expr) == "" {
				t.Errorf("Grafana panel %q target has an empty expression", panel.Title)
			}
			grafanaQueries = append(grafanaQueries, target.Expr)
		}
	}

	if len(sample.Panels) != len(persesTitles) {
		t.Errorf("Grafana panels = %d, Perses panels = %d", len(sample.Panels), len(persesTitles))
	}

	slices.Sort(persesTitles)
	slices.Sort(grafanaTitles)
	if !slices.Equal(persesTitles, grafanaTitles) {
		t.Errorf("panel titles differ:\n Grafana: %v\n Perses:  %v", grafanaTitles, persesTitles)
	}

	persesMetrics := metricNames(persesQueries)
	grafanaMetrics := metricNames(grafanaQueries)
	if len(persesMetrics) == 0 {
		t.Fatal("no Jumpstarter metrics found in the Perses dashboard")
	}
	if !slices.Equal(persesMetrics, grafanaMetrics) {
		t.Errorf("metric sets differ:\n Grafana: %v\n Perses:  %v", grafanaMetrics, persesMetrics)
	}

	// The dashboard is opt-in and documented: it must reference the datasource
	// variables, not a hard-coded Grafana datasource UID.
	for _, panel := range sample.Panels {
		if !strings.HasPrefix(panel.Datasource.UID, "${ds_") {
			t.Errorf("panel %q datasource uid = %q, want a ${ds_*} variable", panel.Title, panel.Datasource.UID)
		}
	}
}
