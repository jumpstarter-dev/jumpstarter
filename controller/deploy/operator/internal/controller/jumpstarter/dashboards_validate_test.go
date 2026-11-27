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
	"sigs.k8s.io/yaml"
)

// TestPersesDashboardValidate runs offline `percli lint` on the dashboard the
// operator would generate. It skips unless PERCLI and PERCLI_PLUGINS are set;
// PERCLI_PLUGINS must point at extracted plugin directories, not archives.
func TestPersesDashboardValidate(t *testing.T) {
	percli := os.Getenv("PERCLI")
	plugins := os.Getenv("PERCLI_PLUGINS")
	if percli == "" || plugins == "" {
		t.Skip("set PERCLI and PERCLI_PLUGINS to run offline percli validation")
	}

	js := testJumpstarter()
	js.Spec.Telemetry = &operatorv1alpha1.TelemetryConfig{}
	dashboard := desiredPersesDashboard(js)
	spec, ok := dashboard.Object["spec"].(map[string]any)
	if !ok {
		t.Fatalf("spec is %T, want map[string]any", dashboard.Object["spec"])
	}
	config, ok := spec["config"].(map[string]any)
	if !ok {
		t.Fatalf("config is %T, want map[string]any", spec["config"])
	}

	// percli lint accepts a Perses Dashboard, not the Kubernetes CR.
	wrapper := map[string]any{
		"kind": "Dashboard",
		"metadata": map[string]any{
			"name":    dashboard.GetName(),
			"project": js.Namespace,
		},
		"spec": config,
	}
	raw, err := yaml.Marshal(wrapper)
	if err != nil {
		t.Fatalf("marshal dashboard: %v", err)
	}
	path := filepath.Join(t.TempDir(), "dashboard.yaml")
	if err := os.WriteFile(path, raw, 0o600); err != nil {
		t.Fatalf("write dashboard: %v", err)
	}

	out, err := exec.Command(percli, "lint", "-f", path, "--plugin.path", plugins).CombinedOutput()
	if err != nil {
		t.Fatalf("percli lint failed: %v\n%s", err, out)
	}
}
