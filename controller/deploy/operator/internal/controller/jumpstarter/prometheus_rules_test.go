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
	"os"
	"os/exec"
	"path/filepath"
	"testing"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
	"k8s.io/apimachinery/pkg/runtime/schema"
	"sigs.k8s.io/yaml"
)

var prometheusRuleGVK = schema.GroupVersionKind{
	Group: prometheusRuleGroup, Version: prometheusRuleVersion, Kind: prometheusRuleKind,
}

func TestPrometheusRulesEnabled(t *testing.T) {
	tests := []struct {
		name string
		js   *operatorv1alpha1.Jumpstarter
		want bool
	}{
		{"nil telemetry", testJumpstarter(), false},
		{"unset telemetry metrics", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			return js
		}(), false},
		{"explicit true", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			js.Spec.Telemetry.Metrics.PrometheusRules = new(true)
			return js
		}(), true},
		{"explicit false", func() *operatorv1alpha1.Jumpstarter {
			js := testJumpstarter()
			js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
			js.Spec.Telemetry.Metrics.PrometheusRules = new(false)
			return js
		}(), false},
	}
	for _, tc := range tests {
		t.Run(tc.name, func(t *testing.T) {
			if got := prometheusRulesEnabled(tc.js); got != tc.want {
				t.Fatalf("prometheusRulesEnabled() = %v, want %v", got, tc.want)
			}
		})
	}
}

func TestDesiredPrometheusRules(t *testing.T) {
	js := testJumpstarter()
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	js.Spec.Telemetry.Metrics.RuleLabels = map[string]string{
		"release":           "kube-prometheus-stack",
		prometheusRuleLabel: "false",
	}

	rules := desiredPrometheusRules(js)
	if got, want := rules.GetName(), "jumpstarter-starter-alerts"; got != want {
		t.Errorf("name = %q, want %q", got, want)
	}
	if got, want := rules.GetNamespace(), "lab"; got != want {
		t.Errorf("namespace = %q, want %q", got, want)
	}
	if rules.GroupVersionKind() != prometheusRuleGVK {
		t.Errorf("GVK = %v, want %v", rules.GroupVersionKind(), prometheusRuleGVK)
	}

	labels := rules.GetLabels()
	if labels[prometheusRuleLabel] != "true" {
		t.Errorf("%s = %q, want true (stable label must not be overridden)", prometheusRuleLabel, labels[prometheusRuleLabel])
	}
	if labels["release"] != "kube-prometheus-stack" {
		t.Errorf("release label = %q, want kube-prometheus-stack", labels["release"])
	}
	if labels["app.kubernetes.io/instance"] != js.Name {
		t.Errorf("instance label = %q, want %q", labels["app.kubernetes.io/instance"], js.Name)
	}

	spec, ok := rules.Object["spec"].(map[string]any)
	if !ok {
		t.Fatalf("spec is %T, want map[string]any", rules.Object["spec"])
	}
	groups, ok := spec["groups"].([]any)
	if !ok || len(groups) != 2 {
		t.Fatalf("groups = %#v, want 2 groups", spec["groups"])
	}

	alerts := 0
	for _, groupValue := range groups {
		group, ok := groupValue.(map[string]any)
		if !ok {
			t.Fatalf("group is %T, want map[string]any", groupValue)
		}
		ruleList, ok := group["rules"].([]any)
		if !ok || len(ruleList) == 0 {
			t.Fatalf("group %v has no rules", group["name"])
		}
		for _, ruleValue := range ruleList {
			rule, ok := ruleValue.(map[string]any)
			if !ok {
				t.Fatalf("rule is %T, want map[string]any", ruleValue)
			}
			for _, key := range []string{"alert", "expr", "for", "labels", "annotations"} {
				if rule[key] == nil {
					t.Errorf("rule %v is missing %q", rule["alert"], key)
				}
			}
			annotations, ok := rule["annotations"].(map[string]any)
			if !ok || annotations["runbook_url"] == nil {
				t.Errorf("rule %v is missing a runbook_url annotation", rule["alert"])
			}
			alerts++
		}
	}
	if alerts != 7 {
		t.Errorf("alerts = %d, want 7", alerts)
	}
}

func TestPrometheusRulesValidate(t *testing.T) {
	promtool, err := exec.LookPath("promtool")
	if err != nil {
		t.Skip("promtool is not installed")
	}

	js := testJumpstarter()
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	rules := desiredPrometheusRules(js)
	spec, ok := rules.Object["spec"].(map[string]any)
	if !ok {
		t.Fatalf("spec is %T, want map[string]any", rules.Object["spec"])
	}
	raw, err := yaml.Marshal(spec)
	if err != nil {
		t.Fatalf("marshal rules: %v", err)
	}
	path := filepath.Join(t.TempDir(), "rules.yaml")
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatalf("write rules: %v", err)
	}
	out, err := exec.Command(promtool, "check", "rules", path).CombinedOutput()
	if err != nil {
		t.Fatalf("promtool check rules failed: %v\n%s", err, out)
	}
}
