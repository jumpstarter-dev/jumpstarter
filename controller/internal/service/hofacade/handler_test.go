package hofacade

import (
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
)

// Test 6: the handler dispatch contract pins upstream
// frontend/src/host_orchestrator/orchestrator/controller.go:145-181:
// (nil, nil) -> bare 200 with an empty body and no JSON Content-Type
// (the /_debug/statusz contract); (obj, nil) -> 200 application/json.
func TestDispatchNilResultBare200(t *testing.T) {
	rec := httptest.NewRecorder()
	h := unauthenticated(func(r *http.Request) (any, error) { return nil, nil })
	h(rec, httptest.NewRequest(http.MethodGet, "/_debug/statusz", nil))

	if rec.Code != http.StatusOK {
		t.Errorf("status: got %d want 200", rec.Code)
	}
	if rec.Body.Len() != 0 {
		t.Errorf("body: got %q want empty", rec.Body.String())
	}
	if ct := rec.Header().Get("Content-Type"); strings.Contains(ct, "json") {
		t.Errorf("Content-Type: got %q, want no JSON content type on a bare 200", ct)
	}
}

func TestDispatchObjectResult200JSON(t *testing.T) {
	rec := httptest.NewRecorder()
	h := unauthenticated(func(r *http.Request) (any, error) {
		return map[string]string{"hello": "world"}, nil
	})
	h(rec, httptest.NewRequest(http.MethodGet, "/x", nil))

	if rec.Code != http.StatusOK {
		t.Errorf("status: got %d want 200", rec.Code)
	}
	if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
		t.Errorf("Content-Type: got %q want application/json", ct)
	}
	if got := strings.TrimSpace(rec.Body.String()); got != `{"hello":"world"}` {
		t.Errorf("body: got %s", got)
	}
}

// Test 7: each AppError constructor's status code and body, pinning upstream
// frontend/src/liboperator/operator/errors.go:23-62. details is "msg: cause"
// and omitted entirely when there is no cause; a non-AppError error becomes
// exactly 500 {"error":"Internal server error","details":"Internal server
// error: boom"} (controller.go replyJSONErr fallback).
func TestWriteErrorAppErrorTable(t *testing.T) {
	cause := errors.New("cause")
	cases := []struct {
		name     string
		err      error
		wantCode int
		wantBody string
	}{
		{"bad request", NewBadRequestError("Malformed JSON in request", cause), 400,
			`{"error":"Malformed JSON in request","details":"Malformed JSON in request: cause"}`},
		{"unauthorized", NewUnauthorizedError("Unauthorized"), 401,
			`{"error":"Unauthorized"}`},
		{"not found no cause", NewNotFoundError("Operation not found", nil), 404,
			`{"error":"Operation not found"}`},
		{"not found with cause", NewNotFoundError("Operation not found", cause), 404,
			`{"error":"Operation not found","details":"Operation not found: cause"}`},
		{"conflict", NewConflictError("conflict", nil), 409,
			`{"error":"conflict"}`},
		{"internal", NewInternalError("Internal server error", cause), 500,
			`{"error":"Internal server error","details":"Internal server error: cause"}`},
		{"not implemented", NewNotImplementedError("Not implemented"), 501,
			`{"error":"Not implemented"}`},
		{"service unavailable", NewServiceUnavailableError("Wait for operation timed out", nil), 503,
			`{"error":"Wait for operation timed out"}`},
	}
	for _, tc := range cases {
		rec := httptest.NewRecorder()
		writeError(rec, tc.err)
		if rec.Code != tc.wantCode {
			t.Errorf("%s: status got %d want %d", tc.name, rec.Code, tc.wantCode)
		}
		if got := strings.TrimSpace(rec.Body.String()); got != tc.wantBody {
			t.Errorf("%s: body\n got  %s\n want %s", tc.name, got, tc.wantBody)
		}
		if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
			t.Errorf("%s: Content-Type got %q want application/json", tc.name, ct)
		}
	}
}

func TestWriteErrorNonAppErrorExact500(t *testing.T) {
	rec := httptest.NewRecorder()
	writeError(rec, errors.New("boom"))
	if rec.Code != http.StatusInternalServerError {
		t.Errorf("status: got %d want 500", rec.Code)
	}
	want := `{"error":"Internal server error","details":"Internal server error: boom"}`
	if got := strings.TrimSpace(rec.Body.String()); got != want {
		t.Errorf("body:\n got  %s\n want %s", got, want)
	}
}

func TestNotImplementedHandler(t *testing.T) {
	rec := httptest.NewRecorder()
	notImplemented()(rec, httptest.NewRequest(http.MethodPost, "/cvds/g/:stop", nil))
	if rec.Code != http.StatusNotImplemented {
		t.Errorf("status: got %d want 501", rec.Code)
	}
	var m map[string]any
	if err := json.Unmarshal(rec.Body.Bytes(), &m); err != nil {
		t.Fatalf("body is not JSON: %v", err)
	}
	if m["error"] != "Not implemented" {
		t.Errorf("error: got %v want %q", m["error"], "Not implemented")
	}
}

// The authed middleware maps any resolver failure to the resolver's error and
// never invokes the wrapped handler.
func TestAuthedRejectsWithoutInvokingHandler(t *testing.T) {
	s := &Server{Resolver: ResolverFunc(func(r *http.Request) (*jumpstarterdevv1alpha1.Client, error) {
		return nil, NewUnauthorizedError("Unauthorized")
	})}
	invoked := false
	rec := httptest.NewRecorder()
	s.authed(func(r *http.Request, caller *jumpstarterdevv1alpha1.Client) (any, error) {
		invoked = true
		return nil, nil
	})(rec, httptest.NewRequest(http.MethodGet, "/cvds", nil))

	if invoked {
		t.Error("handler invoked despite auth failure")
	}
	if rec.Code != http.StatusUnauthorized {
		t.Errorf("status: got %d want 401", rec.Code)
	}
	if got := strings.TrimSpace(rec.Body.String()); got != `{"error":"Unauthorized"}` {
		t.Errorf("body: got %s", got)
	}
}
