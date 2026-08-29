package howire

import (
	"encoding/json"
	"strings"
	"testing"
)

// mustMarshal marshals v or fails the test.
func mustMarshal(t *testing.T, v any) string {
	t.Helper()
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatalf("marshal %T: %v", v, err)
	}
	return string(b)
}

// Test 1: Operation marshals to exactly {"name":"...","done":false}.
// Pins upstream android-cuttlefish
// frontend/src/host_orchestrator/orchestrator/api/v1/messages.go:38-44:
// a bare-UUID name (no "operations/" prefix), a done bool, and nothing else.
func TestOperationWireShape(t *testing.T) {
	got := mustMarshal(t, Operation{Name: "d5e6a3f1-4b0c-4f66-9d6c-2b6a3f0e5a11", Done: false})
	want := `{"name":"d5e6a3f1-4b0c-4f66-9d6c-2b6a3f0e5a11","done":false}`
	if got != want {
		t.Errorf("Operation wire shape:\n got  %s\n want %s", got, want)
	}

	got = mustMarshal(t, Operation{Name: "x", Done: true})
	want = `{"name":"x","done":true}`
	if got != want {
		t.Errorf("Operation done wire shape:\n got  %s\n want %s", got, want)
	}
}

// Test 2: CVD marshals exactly group/name/status/displays/webrtc_device_id/
// adb_serial/adb_port with adb_port as a JSON number.
// Pins upstream orchestrator/api/v1/messages.go:46-61 (and the ABSENCE of any
// build_source field, which an earlier requirements sketch wrongly included).
func TestCVDWireShape(t *testing.T) {
	got := mustMarshal(t, CVD{
		Group:          "g1",
		Name:           "1",
		Status:         "Running",
		Displays:       []string{"720x1280"},
		WebRTCDeviceID: "cvd-1",
		ADBSerial:      "0.0.0.0:6520",
		ADBPort:        6520,
	})
	want := `{"group":"g1","name":"1","status":"Running","displays":["720x1280"],"webrtc_device_id":"cvd-1","adb_serial":"0.0.0.0:6520","adb_port":6520}`
	if got != want {
		t.Errorf("CVD wire shape:\n got  %s\n want %s", got, want)
	}
	if strings.Contains(got, "build_source") {
		t.Errorf("CVD must not carry a build_source field (upstream messages.go:46-61 has none): %s", got)
	}
}

// Test 3: ErrorMsg = {"error"} plus omitempty "details", with NO code field.
// Pins upstream frontend/src/liboperator/api/v1/messages.go:67-70. The HTTP
// status code lives only in the status line, never in the body.
func TestErrorMsgWireShape(t *testing.T) {
	got := mustMarshal(t, ErrorMsg{Error: "Operation not found"})
	want := `{"error":"Operation not found"}`
	if got != want {
		t.Errorf("ErrorMsg without details:\n got  %s\n want %s", got, want)
	}

	got = mustMarshal(t, ErrorMsg{Error: "boom", Details: "boom: cause"})
	want = `{"error":"boom","details":"boom: cause"}`
	if got != want {
		t.Errorf("ErrorMsg with details:\n got  %s\n want %s", got, want)
	}
	if strings.Contains(got, `"code"`) {
		t.Errorf("ErrorMsg must not carry a code field (liboperator/api/v1/messages.go:67-70): %s", got)
	}
}

// Test 4: CreateCVDRequest env_config round-trips arbitrary nested JSON
// byte-identically (json.RawMessage opacity, requirement F2).
// Pins upstream orchestrator/api/v1/messages.go:21-28, whose comment declares
// env_config a black box ("its content is unstable").
func TestCreateCVDRequestEnvConfigOpaque(t *testing.T) {
	// Compact input so the RawMessage round-trip is byte-identical.
	raw := `{"env_config":{"common":{"group_name":"cvd"},"instances":[{"vm":{"memory_mb":8192}},{"weird":[null,1.5,{"@image_dirs":["a/b"]}]}],"unknown_future_key":{"x":true}}}`
	var req CreateCVDRequest
	if err := json.Unmarshal([]byte(raw), &req); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	got := mustMarshal(t, req)
	if got != raw {
		t.Errorf("env_config round-trip not byte-identical:\n got  %s\n want %s", got, raw)
	}
}

// Test 5: empty ListCVDsResponse marshals to {"cvds":[]} and never null;
// CreateCVDResponse shares the envelope; ListOperationsResponse and
// EmptyResponse shapes are pinned too (messages.go CreateCVDResponse:31-33,
// ListCVDsResponse:67-69, ListOperationsResponse:35-37, EmptyResponse:109-111).
func TestResponseEnvelopes(t *testing.T) {
	got := mustMarshal(t, NewListCVDsResponse(nil))
	if got != `{"cvds":[]}` {
		t.Errorf("empty ListCVDsResponse: got %s want {\"cvds\":[]}", got)
	}

	got = mustMarshal(t, CreateCVDResponse{CVDs: []*CVD{}})
	if got != `{"cvds":[]}` {
		t.Errorf("empty CreateCVDResponse: got %s want {\"cvds\":[]}", got)
	}

	got = mustMarshal(t, ListOperationsResponse{Operations: []Operation{}})
	if got != `{"operations":[]}` {
		t.Errorf("empty ListOperationsResponse: got %s want {\"operations\":[]}", got)
	}

	got = mustMarshal(t, EmptyResponse{})
	if got != `{}` {
		t.Errorf("EmptyResponse: got %s want {}", got)
	}
}

// Guard: Operation is the ONLY howire type emitting a top-level "done" key.
// The Jumpstarter Python driver duck-types operations — any dict with a
// "done" key triggers its wait loop (driver.py _do_operation).
func TestOnlyOperationEmitsDoneKey(t *testing.T) {
	cases := map[string]any{
		"CVD":               CVD{},
		"ListCVDsResponse":  NewListCVDsResponse(nil),
		"CreateCVDResponse": CreateCVDResponse{CVDs: []*CVD{}},
		"ErrorMsg":          ErrorMsg{Error: "x"},
		"EmptyResponse":     EmptyResponse{},
	}
	for name, v := range cases {
		var m map[string]any
		if err := json.Unmarshal([]byte(mustMarshal(t, v)), &m); err != nil {
			t.Fatalf("%s: %v", name, err)
		}
		if _, ok := m["done"]; ok {
			t.Errorf("%s emits a top-level done key; the driver would treat it as an Operation", name)
		}
	}
}
