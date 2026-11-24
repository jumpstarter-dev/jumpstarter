package cuttlefish

import (
	"encoding/json"
	"fmt"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"testing"

	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
)

func TestRemoteBluetoothController_envConfigAndHealth(t *testing.T) {
	drivers := []virtualtargetv1alpha1.DriverConfig{{Name: "cuttlefish", Type: cuttlefishDriverType}}
	result, err := enrichExporterExportForTest(drivers, map[string]any{
		"default_build": "aosp/test", "bluetooth_controller": "remote", "webrtc_turn": true,
	})
	if err != nil {
		t.Fatal(err)
	}
	config := configFor(t, result[0])
	envConfig := config["env_config"].(map[string]any)
	// No simulator of its own, and no netsim that would look for another instance's.
	if envConfig["netsim_bt"] != false || envConfig["netsim_uwb"] != false {
		t.Errorf("env_config = %#v", envConfig)
	}
	// Neither netsim nor RootCanal listens in this Pod: only the display is checked.
	if fmt.Sprint(config["health_ports"]) != fmt.Sprint([]any{float64(webUIPortDefault), float64(turnPortDefault)}) {
		t.Errorf("health_ports = %v", config["health_ports"])
	}

	result, err = enrichExporterExportForTest(drivers, map[string]any{"default_build": "aosp/test", "bluetooth_controller": "remote"})
	if err != nil {
		t.Fatal(err)
	}
	if ports, ok := configFor(t, result[0])["health_ports"].([]any); !ok || len(ports) != 0 {
		t.Errorf("health_ports must be an empty list, got %#v", configFor(t, result[0])["health_ports"])
	}
}

func TestRemoteBluetoothController_validation(t *testing.T) {
	cuttlefish := virtualtargetv1alpha1.DriverConfig{Name: "cuttlefish", Type: cuttlefishDriverType}
	for _, value := range []any{"shared", true, ""} {
		if _, err := enrichExporterExportForTest([]virtualtargetv1alpha1.DriverConfig{cuttlefish}, map[string]any{"bluetooth_controller": value}); err == nil {
			t.Errorf("accepted bluetooth_controller=%v", value)
		}
	}
	// "local" is the default, with the simulator pinned as before.
	result, err := enrichExporterExportForTest([]virtualtargetv1alpha1.DriverConfig{cuttlefish}, map[string]any{"bluetooth_controller": "local"})
	if err != nil {
		t.Fatal(err)
	}
	if envConfig := configFor(t, result[0])["env_config"].(map[string]any); envConfig["netsim_bt"] != true {
		t.Errorf("env_config = %#v", envConfig)
	}

	remote := map[string]any{"bluetooth_controller": "remote"}
	// A template that asks for the simulator contradicts the class.
	for _, envConfig := range []map[string]any{{"netsim_bt": true}, {"netsim_uwb": true}} {
		driver := cuttlefish
		driver.Config = mustJSON(map[string]any{"env_config": envConfig})
		if _, err := enrichExporterExportForTest([]virtualtargetv1alpha1.DriverConfig{driver}, remote); err == nil || !strings.Contains(err.Error(), "bluetooth_controller") {
			t.Errorf("env_config %v: %v", envConfig, err)
		}
	}
	// Drivers of the Pod's own simulator have nothing to talk to.
	for _, driverType := range []string{netsimDriverType, btPeerDriverType} {
		drivers := []virtualtargetv1alpha1.DriverConfig{cuttlefish, {Name: "simulator", Type: driverType}}
		if _, err := enrichExporterExportForTest(drivers, remote); err == nil || !strings.Contains(err.Error(), "runs none") {
			t.Errorf("%s: %v", driverType, err)
		}
	}
}

func TestRemoteBluetoothController_runtimeSetsThePresets(t *testing.T) {
	params := map[string]any{"fetch_images": true, "runtime_launcher": true}
	if script := initContainer(t, renderTestPod(t, params), runtimeContainerName).Command[2]; strings.Contains(script, "rootcanal_instance_num") {
		t.Fatalf("a local controller must leave the presets alone: %q", script)
	}
	params["bluetooth_controller"] = "remote"
	script := initContainer(t, renderTestPod(t, params), runtimeContainerName).Command[2]
	// Before anything can create a guest.
	if !strings.HasPrefix(script, remoteBluetoothCommand()) || !strings.Contains(script, runtimePresetGlob) || !strings.Contains(script, fetchedPresetGlob) {
		t.Fatalf("runtime must set the presets first: %q", script)
	}
	if remoteHCIPort != 7301 || !runtimePortReserved(remoteHCIPort) {
		t.Fatalf("the exporter listens for the guest's HCI on %d", remoteHCIPort)
	}
}

func TestRemoteBluetoothScript(t *testing.T) {
	bash, err := exec.LookPath("bash")
	if err != nil {
		t.Skip("bash not available")
	}
	dir := t.TempDir()
	phone := filepath.Join(dir, "cvd_config_phone.json")
	auto := filepath.Join(dir, "cvd_config_auto.json")
	if err := os.WriteFile(phone, []byte("{\n\t\"x_res\" : 720,\n\t\"memory_mb\" : 4096\n}\n"), 0o644); err != nil {
		t.Fatal(err)
	}
	if err := os.WriteFile(auto, []byte(`{"display_configs": [{"x_res": 1080}], "rootcanal_instance_num": 2}`), 0o644); err != nil {
		t.Fatal(err)
	}
	script := "set -e\n" + remoteBluetoothScript(filepath.Join(dir, "cvd_config_*.json"), filepath.Join(dir, "missing", "cvd_config_*.json"))
	for range 2 { // a restarted runtime container runs it again
		if out, err := exec.Command(bash, "-c", script).CombinedOutput(); err != nil {
			t.Fatalf("%v: %s", err, out)
		}
	}
	for path, keys := range map[string]int{phone: 3, auto: 2} {
		raw, err := os.ReadFile(path)
		if err != nil {
			t.Fatal(err)
		}
		var preset map[string]any
		if err := json.Unmarshal(raw, &preset); err != nil {
			t.Fatalf("%s is not JSON any more: %v: %s", path, err, raw)
		}
		if preset["rootcanal_instance_num"] != float64(remoteRootcanalInstance) || len(preset) != keys {
			t.Errorf("%s = %v", path, preset)
		}
	}

	// Nothing to carry the flag: the runtime must not start.
	out, err := exec.Command(bash, "-c", "set -e\n"+remoteBluetoothScript(filepath.Join(dir, "none_*.json"))).CombinedOutput()
	if err == nil || !strings.Contains(string(out), "no Cuttlefish config preset") {
		t.Fatalf("err=%v out=%s", err, out)
	}
}
