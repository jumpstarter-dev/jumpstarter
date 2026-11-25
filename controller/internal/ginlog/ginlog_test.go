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

package ginlog

import (
	"bytes"
	"encoding/json"
	"errors"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"

	"github.com/gin-gonic/gin"
	"github.com/go-logr/logr"
	ctrlzap "sigs.k8s.io/controller-runtime/pkg/log/zap"
)

func testLogger(output *bytes.Buffer) logr.Logger {
	return ctrlzap.New(
		ctrlzap.UseDevMode(false),
		ctrlzap.WriteTo(output),
	).WithValues("component", "controller")
}

func TestAccessLogWritesStructuredRecord(t *testing.T) {
	gin.SetMode(gin.TestMode)
	var output bytes.Buffer
	engine := gin.New()
	engine.Use(AccessLog(testLogger(&output)))
	engine.GET("/example", func(c *gin.Context) {
		c.String(http.StatusOK, "ok")
	})

	engine.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/example", nil))

	line := strings.TrimSpace(output.String())
	if line == "" {
		t.Fatal("expected an access log line")
	}
	var entry map[string]any
	if err := json.Unmarshal([]byte(line), &entry); err != nil {
		t.Fatalf("access log is not valid JSON: %v\n%s", err, line)
	}
	if got := entry["component"]; got != "controller" {
		t.Fatalf("component = %v, want controller", got)
	}
	if got := entry["level"]; got != "info" {
		t.Fatalf("level = %v, want info", got)
	}
	if got := entry["msg"]; got != "http request" {
		t.Fatalf("msg = %v, want http request", got)
	}
	if got := entry["http_method"]; got != http.MethodGet {
		t.Fatalf("http_method = %v, want GET", got)
	}
	if got := entry["http_path"]; got != "/example" {
		t.Fatalf("http_path = %v, want /example", got)
	}
	if got := entry["http_status"]; got != float64(http.StatusOK) {
		t.Fatalf("http_status = %v, want 200", got)
	}
}

func TestAccessLogRedactsSensitiveQueryParameters(t *testing.T) {
	gin.SetMode(gin.TestMode)
	var output bytes.Buffer
	engine := gin.New()
	engine.Use(AccessLog(testLogger(&output)))
	engine.GET("/login", func(c *gin.Context) {
		c.String(http.StatusOK, "ok")
	})

	engine.ServeHTTP(httptest.NewRecorder(),
		httptest.NewRequest(http.MethodGet, "/login?token=super-secret&client=visible", nil))

	line := strings.TrimSpace(output.String())
	if line == "" {
		t.Fatal("expected an access log line")
	}
	if strings.Contains(line, "super-secret") {
		t.Fatalf("sensitive query value leaked into access log: %s", line)
	}
	var entry map[string]any
	if err := json.Unmarshal([]byte(line), &entry); err != nil {
		t.Fatalf("access log is not valid JSON: %v\n%s", err, line)
	}
	if got := entry["http_path"]; got != "/login?client=visible&token=%5BREDACTED%5D" {
		t.Fatalf("http_path = %v, want redacted query path", got)
	}
}

func TestAccessLogLogsRequestErrors(t *testing.T) {
	gin.SetMode(gin.TestMode)
	var output bytes.Buffer
	engine := gin.New()
	engine.Use(AccessLog(testLogger(&output)))
	engine.GET("/error", func(c *gin.Context) {
		_ = c.Error(errors.New("line1\nline2"))
		c.Status(http.StatusInternalServerError)
	})

	engine.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/error", nil))

	var entry map[string]any
	if err := json.Unmarshal([]byte(strings.TrimSpace(output.String())), &entry); err != nil {
		t.Fatalf("formatter output is not valid JSON: %v\n%s", err, output.String())
	}
	if got := entry["level"]; got != "error" {
		t.Fatalf("level = %v, want error", got)
	}
	if got := entry["error"]; got != "line1\nline2" {
		t.Fatalf("error = %v, want escaped multi-line value", got)
	}
}

func TestRedactedQueryHandlesMalformedInput(t *testing.T) {
	if got := redactedQuery("token=secret;bad"); got != "[REDACTED]" {
		t.Fatalf("redactedQuery() = %q, want [REDACTED]", got)
	}
}

func TestRecoveryReturns500(t *testing.T) {
	gin.SetMode(gin.TestMode)
	oldWriter := gin.DefaultErrorWriter
	var errorOutput bytes.Buffer
	gin.DefaultErrorWriter = &errorOutput
	t.Cleanup(func() { gin.DefaultErrorWriter = oldWriter })

	engine := gin.New()
	engine.Use(Recovery(logr.Discard()))
	engine.GET("/panic", func(c *gin.Context) {
		panic("boom")
	})

	response := httptest.NewRecorder()
	engine.ServeHTTP(response, httptest.NewRequest(http.MethodGet, "/panic", nil))

	if response.Code != http.StatusInternalServerError {
		t.Fatalf("status = %d, want %d", response.Code, http.StatusInternalServerError)
	}
	if errorOutput.Len() != 0 {
		t.Fatalf("recovery wrote text output: %s", errorOutput.String())
	}
}

func TestNewEngineDoesNotDuplicateComponent(t *testing.T) {
	gin.SetMode(gin.TestMode)
	var output bytes.Buffer
	engine := NewEngine(testLogger(&output))
	engine.GET("/example", func(c *gin.Context) {
		c.String(http.StatusOK, "ok")
	})

	engine.ServeHTTP(httptest.NewRecorder(), httptest.NewRequest(http.MethodGet, "/example", nil))

	line := strings.TrimSpace(output.String())
	if line == "" {
		t.Fatal("expected an access log line")
	}
	// The entrypoint already scopes the logger with component=controller;
	// NewEngine must not add the key a second time.
	if got := strings.Count(line, `"component"`); got != 1 {
		t.Fatalf("component appears %d times, want 1: %s", got, line)
	}
}
