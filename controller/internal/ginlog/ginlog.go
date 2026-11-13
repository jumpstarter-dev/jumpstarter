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

// Package ginlog provides structured Gin request and recovery logging.
package ginlog

import (
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"runtime/debug"
	"strings"
	"time"

	"github.com/gin-gonic/gin"
	"github.com/go-logr/logr"
)

// sensitiveQueryKeys contains query parameter names whose values must never be
// written to access logs. Matching is case-insensitive; token, secret, and
// password substrings are handled separately for vendor-specific names.
var sensitiveQueryKeys = map[string]struct{}{
	"access_token":  {},
	"apikey":        {},
	"api_key":       {},
	"assertion":     {},
	"authorization": {},
	"client_secret": {},
	"code":          {},
	"credential":    {},
	"id_token":      {},
	"jwt":           {},
	"passwd":        {},
	"password":      {},
	"refresh_token": {},
	"secret":        {},
	"sig":           {},
	"signature":     {},
	"token":         {},
}

// redactedQuery returns a query string with sensitive values replaced by
// [REDACTED]. The original parameter names and non-sensitive values are kept.
// If the query cannot be parsed, the whole query is redacted rather than
// risking a credential leak.
func redactedQuery(rawQuery string) string {
	if rawQuery == "" {
		return ""
	}
	values, err := url.ParseQuery(rawQuery)
	if err != nil {
		return "[REDACTED]"
	}
	for key := range values {
		if isSensitiveQueryKey(key) {
			for i := range values[key] {
				values[key][i] = "[REDACTED]"
			}
		}
	}
	return values.Encode()
}

func isSensitiveQueryKey(key string) bool {
	normalized := strings.ToLower(strings.TrimSuffix(key, "[]"))
	if _, ok := sensitiveQueryKeys[normalized]; ok {
		return true
	}
	return strings.Contains(normalized, "token") ||
		strings.Contains(normalized, "secret") ||
		strings.Contains(normalized, "password")
}

func requestPath(p gin.LogFormatterParams) string {
	if p.Request == nil || p.Request.URL == nil {
		return p.Path
	}
	path := p.Request.URL.Path
	if query := redactedQuery(p.Request.URL.RawQuery); query != "" {
		path += "?" + query
	}
	return path
}

// LogFormatter turns a Gin request into one JSONL record. High-cardinality
// request fields stay in the body; only the Jumpstarter stream labels are
// promoted by the log shipper.
func LogFormatter(component string) gin.LogFormatter {
	return func(p gin.LogFormatterParams) string {
		entry := map[string]any{
			"ts":              p.TimeStamp.UTC().Format(time.RFC3339Nano),
			"level":           "info",
			"component":       component,
			"msg":             "http request",
			"http_method":     p.Method,
			"http_path":       requestPath(p),
			"http_status":     p.StatusCode,
			"http_latency_ms": p.Latency.Milliseconds(),
			"http_body_size":  p.BodySize,
			"client_ip":       p.ClientIP,
		}
		if p.ErrorMessage != "" {
			entry["level"] = "error"
			entry["error"] = p.ErrorMessage
		}
		if p.Request != nil {
			entry["http_user_agent"] = p.Request.UserAgent()
		}

		encoded, err := json.Marshal(entry)
		if err != nil {
			return fmt.Sprintf("{\"component\":%q,\"level\":\"error\",\"msg\":\"failed to encode gin log\",\"error\":%q}\n",
				component, err.Error())
		}
		return string(encoded) + "\n"
	}
}

// AccessLogger writes one JSON object per Gin request.
func AccessLogger(component string) gin.HandlerFunc {
	return AccessLoggerWithWriter(component, gin.DefaultWriter)
}

// AccessLoggerWithWriter is AccessLogger with an explicit output writer,
// primarily for tests.
func AccessLoggerWithWriter(component string, output io.Writer) gin.HandlerFunc {
	return gin.LoggerWithConfig(gin.LoggerConfig{
		Formatter: LogFormatter(component),
		Output:    output,
	})
}

// Recovery logs panics through the structured logger instead of Gin's default
// text stack-trace writer.
func Recovery(logger logr.Logger) gin.HandlerFunc {
	return gin.CustomRecoveryWithWriter(io.Discard, func(c *gin.Context, recovered any) {
		logger.Error(fmt.Errorf("panic: %v", recovered), "panic recovered",
			"stack", string(debug.Stack()))
		c.AbortWithStatus(http.StatusInternalServerError)
	})
}

// NewEngine returns a Gin engine with structured access and recovery logging.
// Callers should set Gin to release mode before starting runtime engines.
func NewEngine(component string, logger logr.Logger) *gin.Engine {
	engine := gin.New()
	engine.Use(AccessLogger(component), Recovery(logger))
	return engine
}
