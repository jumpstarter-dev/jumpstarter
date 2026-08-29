/*
Copyright 2026 The Jumpstarter Authors

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

package cuttlefish

import (
	"bytes"
	"encoding/json"
	"strings"
	"testing"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
	apiextensionsv1 "k8s.io/apiextensions-apiserver/pkg/apis/apiextensions/v1"
)

func TestEnrichInjectsDefaults(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "cuttlefish",
			Type:   cuttlefishDriverType,
			Config: mustJSON(map[string]interface{}{}),
		},
	}

	result, err := New("dev").EnrichExporterExport(drivers, nil)
	if err != nil {
		t.Fatal(err)
	}

	cf := findDriver(result, "cuttlefish")
	if cf == nil {
		t.Fatal("cuttlefish driver not found in result")
	}

	config := unmarshalConfig(t, cf.Config)
	if got := config["host"]; got != "127.0.0.1" {
		t.Errorf("host = %v, want 127.0.0.1", got)
	}
	if got := config["port"]; got != float64(2080) {
		t.Errorf("port = %v, want 2080", got)
	}
	if got := config["boot_timeout"]; got != float64(600) {
		t.Errorf("boot_timeout = %v, want 600", got)
	}
	if _, ok := config["operator_port"]; ok {
		t.Error("operator_port injected without parameter")
	}
	if _, ok := config["env_config"]; ok {
		t.Error("env_config injected without parameter")
	}
}

func TestEnrichNilConfigDefaults(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name: "cuttlefish",
			Type: cuttlefishDriverType,
		},
	}

	result, err := New("dev").EnrichExporterExport(drivers, nil)
	if err != nil {
		t.Fatal(err)
	}

	config := unmarshalConfig(t, findDriver(result, "cuttlefish").Config)
	if got := config["host"]; got != "127.0.0.1" {
		t.Errorf("host = %v, want 127.0.0.1", got)
	}
	if got := config["port"]; got != float64(2080) {
		t.Errorf("port = %v, want 2080", got)
	}
	if got := config["boot_timeout"]; got != float64(600) {
		t.Errorf("boot_timeout = %v, want 600", got)
	}
}

func TestEnrichPortFromParameters(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{Name: "cuttlefish", Type: cuttlefishDriverType},
	}
	params := map[string]interface{}{
		"hostOrchestrator": map[string]interface{}{"port": float64(3080)},
	}

	result, err := New("dev").EnrichExporterExport(drivers, params)
	if err != nil {
		t.Fatal(err)
	}

	config := unmarshalConfig(t, findDriver(result, "cuttlefish").Config)
	if got := config["port"]; got != float64(3080) {
		t.Errorf("port = %v, want 3080", got)
	}
}

func TestEnrichNeverOverridesExplicitValues(t *testing.T) {
	envConfig := map[string]interface{}{"instances": []interface{}{map[string]interface{}{"vm": "explicit"}}}
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name: "cuttlefish",
			Type: cuttlefishDriverType,
			Config: mustJSON(map[string]interface{}{
				"host":          "10.0.0.5",
				"port":          9999,
				"boot_timeout":  30,
				"operator_port": 2222,
				"env_config":    envConfig,
			}),
		},
	}
	params := map[string]interface{}{
		"hostOrchestrator": map[string]interface{}{"port": float64(3080)},
		"operator":         map[string]interface{}{"port": float64(1081)},
		"envConfig":        map[string]interface{}{"instances": []interface{}{}},
	}

	result, err := New("dev").EnrichExporterExport(drivers, params)
	if err != nil {
		t.Fatal(err)
	}

	config := unmarshalConfig(t, findDriver(result, "cuttlefish").Config)
	if got := config["host"]; got != "10.0.0.5" {
		t.Errorf("host = %v, want explicit 10.0.0.5", got)
	}
	if got := config["port"]; got != float64(9999) {
		t.Errorf("port = %v, want explicit 9999", got)
	}
	if got := config["boot_timeout"]; got != float64(30) {
		t.Errorf("boot_timeout = %v, want explicit 30", got)
	}
	if got := config["operator_port"]; got != float64(2222) {
		t.Errorf("operator_port = %v, want explicit 2222", got)
	}
	wantEnv, _ := json.Marshal(envConfig)
	gotEnv, _ := json.Marshal(config["env_config"])
	if !bytes.Equal(gotEnv, wantEnv) {
		t.Errorf("env_config = %s, want explicit %s", gotEnv, wantEnv)
	}
}

func TestEnrichOperatorPortOnlyWhenParameterSet(t *testing.T) {
	// (a) parameter set, template absent: injected.
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{Name: "cuttlefish", Type: cuttlefishDriverType},
	}
	params := map[string]interface{}{
		"operator": map[string]interface{}{"port": float64(1081)},
	}
	result, err := New("dev").EnrichExporterExport(drivers, params)
	if err != nil {
		t.Fatal(err)
	}
	config := unmarshalConfig(t, findDriver(result, "cuttlefish").Config)
	if got := config["operator_port"]; got != float64(1081) {
		t.Errorf("operator_port = %v, want 1081", got)
	}

	// (b) no parameter: key absent.
	result, err = New("dev").EnrichExporterExport(drivers, nil)
	if err != nil {
		t.Fatal(err)
	}
	config = unmarshalConfig(t, findDriver(result, "cuttlefish").Config)
	if _, ok := config["operator_port"]; ok {
		t.Error("operator_port injected without parameter")
	}

	// (c) parameter set but template already has operator_port: template wins.
	drivers = []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "cuttlefish",
			Type:   cuttlefishDriverType,
			Config: mustJSON(map[string]interface{}{"operator_port": 2222}),
		},
	}
	result, err = New("dev").EnrichExporterExport(drivers, params)
	if err != nil {
		t.Fatal(err)
	}
	config = unmarshalConfig(t, findDriver(result, "cuttlefish").Config)
	if got := config["operator_port"]; got != float64(2222) {
		t.Errorf("operator_port = %v, want template value 2222", got)
	}
}

func TestEnrichPrewarmWithEnvConfig(t *testing.T) {
	envConfig := map[string]interface{}{"instances": []interface{}{}}
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{Name: "cuttlefish", Type: cuttlefishDriverType},
	}
	params := map[string]interface{}{"envConfig": envConfig}

	result, err := New("dev").EnrichExporterExport(drivers, params)
	if err != nil {
		t.Fatal(err)
	}
	config := unmarshalConfig(t, findDriver(result, "cuttlefish").Config)
	if config["prewarm"] != true {
		t.Errorf("prewarm = %v, want true when envConfig parameter is set (DD-6)", config["prewarm"])
	}

	// Template-provided prewarm is never overridden.
	drivers = []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "cuttlefish",
			Type:   cuttlefishDriverType,
			Config: mustJSON(map[string]interface{}{"prewarm": false}),
		},
	}
	result, err = New("dev").EnrichExporterExport(drivers, params)
	if err != nil {
		t.Fatal(err)
	}
	config = unmarshalConfig(t, findDriver(result, "cuttlefish").Config)
	if config["prewarm"] != false {
		t.Errorf("prewarm = %v, want template-provided false preserved", config["prewarm"])
	}

	// No envConfig parameter: no prewarm key injected.
	result, err = New("dev").EnrichExporterExport(
		[]virtualtargetv1alpha1.DriverConfig{{Name: "cuttlefish", Type: cuttlefishDriverType}},
		map[string]interface{}{},
	)
	if err != nil {
		t.Fatal(err)
	}
	config = unmarshalConfig(t, findDriver(result, "cuttlefish").Config)
	if _, ok := config["prewarm"]; ok {
		t.Error("prewarm injected without envConfig parameter")
	}
}

func TestEnrichEnvConfigFromParameters(t *testing.T) {
	envConfig := map[string]interface{}{
		"instances": []interface{}{
			map[string]interface{}{"vm": map[string]interface{}{"cpus": float64(4)}},
		},
	}
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{Name: "cuttlefish", Type: cuttlefishDriverType},
	}
	params := map[string]interface{}{"envConfig": envConfig}

	result, err := New("dev").EnrichExporterExport(drivers, params)
	if err != nil {
		t.Fatal(err)
	}
	config := unmarshalConfig(t, findDriver(result, "cuttlefish").Config)
	wantEnv, _ := json.Marshal(envConfig)
	gotEnv, _ := json.Marshal(config["env_config"])
	if !bytes.Equal(gotEnv, wantEnv) {
		t.Errorf("env_config = %s, want %s", gotEnv, wantEnv)
	}

	// Template-provided env_config preserved.
	templateEnv := map[string]interface{}{"instances": []interface{}{}}
	drivers = []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "cuttlefish",
			Type:   cuttlefishDriverType,
			Config: mustJSON(map[string]interface{}{"env_config": templateEnv}),
		},
	}
	result, err = New("dev").EnrichExporterExport(drivers, params)
	if err != nil {
		t.Fatal(err)
	}
	config = unmarshalConfig(t, findDriver(result, "cuttlefish").Config)
	wantEnv, _ = json.Marshal(templateEnv)
	gotEnv, _ = json.Marshal(config["env_config"])
	if !bytes.Equal(gotEnv, wantEnv) {
		t.Errorf("env_config = %s, want template value %s", gotEnv, wantEnv)
	}
}

func TestEnrichNoWrapperDrivers(t *testing.T) {
	lone := []virtualtargetv1alpha1.DriverConfig{
		{Name: "cuttlefish", Type: cuttlefishDriverType},
	}
	result, err := New("dev").EnrichExporterExport(lone, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(result) != len(lone) {
		t.Errorf("len(result) = %d, want %d (no wrapper drivers)", len(result), len(lone))
	}
	if findDriver(result, "tcp") != nil {
		t.Error("unexpected tcp wrapper driver injected")
	}

	mixed := []virtualtargetv1alpha1.DriverConfig{
		{Name: "cuttlefish", Type: cuttlefishDriverType},
		{Name: "power", Ref: "cuttlefish.power"},
	}
	result, err = New("dev").EnrichExporterExport(mixed, nil)
	if err != nil {
		t.Fatal(err)
	}
	if len(result) != len(mixed) {
		t.Errorf("len(result) = %d, want %d (no wrapper drivers)", len(result), len(mixed))
	}
	if findDriver(result, "tcp") != nil {
		t.Error("unexpected tcp wrapper driver injected")
	}
}

func TestEnrichLeavesOtherDriversUntouched(t *testing.T) {
	tcpConfig := mustJSON(map[string]interface{}{"host": "10.0.0.1", "port": 3333})
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "tcp",
			Type:   "jumpstarter_driver_network.driver.TcpNetwork",
			Config: tcpConfig,
		},
		{Name: "cuttlefish", Type: cuttlefishDriverType},
		{Name: "power", Ref: "cuttlefish.power"},
	}

	result, err := New("dev").EnrichExporterExport(drivers, nil)
	if err != nil {
		t.Fatal(err)
	}

	if len(result) != 3 {
		t.Fatalf("len(result) = %d, want 3", len(result))
	}
	if result[0].Name != "tcp" || result[1].Name != "cuttlefish" || result[2].Name != "power" {
		t.Fatalf("driver order changed: %#v", result)
	}
	if !bytes.Equal(result[0].Config.Raw, tcpConfig.Raw) {
		t.Errorf("tcp config changed: %s, want %s", result[0].Config.Raw, tcpConfig.Raw)
	}
	if result[2].Config != nil {
		t.Errorf("ref-only entry gained config: %#v", result[2].Config)
	}
	if result[2].Ref != "cuttlefish.power" {
		t.Errorf("ref-only entry Ref = %q, want cuttlefish.power", result[2].Ref)
	}
}

func TestEnrichInvalidConfigJSON(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{
			Name:   "cuttlefish",
			Type:   cuttlefishDriverType,
			Config: &apiextensionsv1.JSON{Raw: []byte("{not json")},
		},
	}

	_, err := New("dev").EnrichExporterExport(drivers, nil)
	if err == nil {
		t.Fatal("EnrichExporterExport() error = nil, want error")
	}
	if !strings.Contains(err.Error(), "unmarshal cuttlefish driver config") {
		t.Errorf("error %q does not mention unmarshal cuttlefish driver config", err)
	}
}

func TestEnrichInvalidPortParameter(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{
		{Name: "cuttlefish", Type: cuttlefishDriverType},
	}
	params := map[string]interface{}{
		"hostOrchestrator": map[string]interface{}{"port": "abc"},
	}

	_, err := New("dev").EnrichExporterExport(drivers, params)
	if err == nil {
		t.Fatal("EnrichExporterExport() error = nil, want error for invalid port parameter")
	}
}

// --- helpers ---

func findDriver(drivers []virtualtargetv1alpha1.DriverConfig, name string) *virtualtargetv1alpha1.DriverConfig {
	for i := range drivers {
		if drivers[i].Name == name {
			return &drivers[i]
		}
	}
	return nil
}

func unmarshalConfig(t *testing.T, raw *apiextensionsv1.JSON) map[string]interface{} {
	t.Helper()
	if raw == nil || raw.Raw == nil {
		t.Fatal("config is nil")
	}
	var config map[string]interface{}
	if err := json.Unmarshal(raw.Raw, &config); err != nil {
		t.Fatalf("unmarshal config: %v", err)
	}
	return config
}
