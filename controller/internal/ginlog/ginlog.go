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

func requestPath(u *url.URL) string {
	path := u.Path
	if query := redactedQuery(u.RawQuery); query != "" {
		path += "?" + query
	}
	return path
}

// AccessLog writes one structured access record per request through the
// controller's logr logger. Request fields stay in the log body; the cluster
// shipper promotes the component/exporter/namespace stream labels.
func AccessLog(logger logr.Logger) gin.HandlerFunc {
	return func(c *gin.Context) {
		start := time.Now()
		c.Next()

		keysAndValues := []any{
			"http_method", c.Request.Method,
			"http_path", requestPath(c.Request.URL),
			"http_status", c.Writer.Status(),
			"http_latency_ms", time.Since(start).Milliseconds(),
			"http_body_size", c.Writer.Size(),
			"client_ip", c.ClientIP(),
			"http_user_agent", c.Request.UserAgent(),
		}
		if lastErr := c.Errors.Last(); lastErr != nil {
			logger.Error(lastErr.Err, "http request", keysAndValues...)
			return
		}
		logger.Info("http request", keysAndValues...)
	}
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
//
// The logger must already be scoped to the component: the entrypoint installs
// ctrl.Log with "component"="controller", so adding the key again here would
// emit a duplicate field on every access and recovery record.
//
// Callers should set Gin to release mode before starting runtime engines.
func NewEngine(logger logr.Logger) *gin.Engine {
	engine := gin.New()
	engine.Use(AccessLog(logger), Recovery(logger))
	return engine
}
