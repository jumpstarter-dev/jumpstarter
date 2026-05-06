/*
Copyright 2026. The Jumpstarter Authors

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

package e2e

import (
	"bufio"
	"bytes"
	"context"
	"crypto/tls"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"strings"
	"time"
)

// AdminClient is a thin REST client for the JEP-0014 admin HTTP API. It
// wraps net/http with the bearer token, the singular/plural mapping that
// the gateway uses for path construction, and a small helper that decodes
// every response as JSON. Production callers (Backstage plug-ins,
// dashboards, the OpenShift Console) speak the same wire format.
type AdminClient struct {
	BaseURL string // e.g. https://grpc.jumpstarter.127.0.0.1.nip.io:8082
	Token   string // OIDC id_token from Dex
	HTTP    *http.Client
}

// NewAdminClient builds an AdminClient that trusts the controller's TLS
// certificate. The controller serves on port 8082 with a self-signed
// certificate generated at startup, so InsecureSkipVerify is set; bearer
// token validation provides the actual auth boundary, not TLS pinning.
//
// endpoint is the host:port form returned by the e2e setup
// (e.g. "grpc.jumpstarter.127.0.0.1.nip.io:8082"). The token is the
// Dex-issued id_token for the persona under test.
func NewAdminClient(endpoint, token string) *AdminClient {
	return &AdminClient{
		BaseURL: "https://" + endpoint,
		Token:   token,
		HTTP: &http.Client{
			Timeout: 30 * time.Second,
			Transport: &http.Transport{
				TLSClientConfig: &tls.Config{
					MinVersion:         tls.VersionTLS12,
					InsecureSkipVerify: true,
				},
			},
		},
	}
}

// adminPath returns "/admin/v1/namespaces/<ns>/<plural>".
func adminPath(plural, ns string) string {
	return "/admin/v1/namespaces/" + ns + "/" + plural
}

// adminPathID returns "/admin/v1/namespaces/<ns>/<plural>/<id>".
func adminPathID(plural, ns, id string) string {
	return adminPath(plural, ns) + "/" + id
}

// singularIDParam returns the gRPC-gateway query-parameter name used by
// CreateX RPCs (e.g. "client_id" for "clients").
func singularIDParam(plural string) string {
	switch plural {
	case "clients":
		return "client_id"
	case "exporters":
		return "exporter_id"
	case "leases":
		return "lease_id"
	case "webhooks":
		return "webhook_id"
	}
	return ""
}

// do sends a single request and decodes the response body as JSON when
// the body is non-empty. It returns the HTTP status, the decoded body
// (nil if the body is empty), and the raw bytes for error reporting.
func (c *AdminClient) do(ctx context.Context, method, path string, body any, query url.Values) (int, map[string]any, []byte, error) {
	var rdr io.Reader
	if body != nil {
		buf, err := json.Marshal(body)
		if err != nil {
			return 0, nil, nil, err
		}
		rdr = bytes.NewReader(buf)
	}

	full := c.BaseURL + path
	if len(query) > 0 {
		full += "?" + query.Encode()
	}

	req, err := http.NewRequestWithContext(ctx, method, full, rdr)
	if err != nil {
		return 0, nil, nil, err
	}
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	req.Header.Set("Accept", "application/json")

	resp, err := c.HTTP.Do(req)
	if err != nil {
		return 0, nil, nil, err
	}
	defer resp.Body.Close()

	raw, err := io.ReadAll(resp.Body)
	if err != nil {
		return resp.StatusCode, nil, nil, err
	}
	if len(bytes.TrimSpace(raw)) == 0 {
		return resp.StatusCode, nil, raw, nil
	}
	var decoded map[string]any
	if err := json.Unmarshal(raw, &decoded); err != nil {
		// Non-JSON body (e.g. a plain "Forbidden" string from the auth
		// middleware) — return the raw bytes so callers can assert on
		// substrings.
		return resp.StatusCode, nil, raw, nil
	}
	return resp.StatusCode, decoded, raw, nil
}

// Get returns GetX result. plural is one of clients/exporters/leases/webhooks.
func (c *AdminClient) Get(ctx context.Context, plural, ns, id string) (int, map[string]any, []byte, error) {
	return c.do(ctx, http.MethodGet, adminPathID(plural, ns, id), nil, nil)
}

// List returns ListX result.
func (c *AdminClient) List(ctx context.Context, plural, ns string) (int, map[string]any, []byte, error) {
	return c.do(ctx, http.MethodGet, adminPath(plural, ns), nil, nil)
}

// Create posts a CreateX request. id is the desired resource name; body
// is the bare resource payload (the gateway binds it to the `<singular>`
// field of the wrapper request).
func (c *AdminClient) Create(ctx context.Context, plural, ns, id string, body map[string]any) (int, map[string]any, []byte, error) {
	q := url.Values{}
	if param := singularIDParam(plural); param != "" && id != "" {
		q.Set(param, id)
	}
	return c.do(ctx, http.MethodPost, adminPath(plural, ns), body, q)
}

// Update PATCHes UpdateX. The gateway binds body to the `<singular>` field
// of the wrapper; the URL provides `<singular>.name`.
func (c *AdminClient) Update(ctx context.Context, plural, ns, id string, body map[string]any) (int, map[string]any, []byte, error) {
	return c.do(ctx, http.MethodPatch, adminPathID(plural, ns, id), body, nil)
}

// Delete sends DELETE. Returns 200 on success, 404 if absent.
func (c *AdminClient) Delete(ctx context.Context, plural, ns, id string) (int, map[string]any, []byte, error) {
	return c.do(ctx, http.MethodDelete, adminPathID(plural, ns, id), nil, nil)
}

// WatchEvent is one envelope from the NDJSON Watch stream. The gRPC
// gateway wraps every streamed message in {"result": ...} for success
// and {"error": ...} for terminal errors. Either map is non-nil.
type WatchEvent struct {
	Result map[string]any
	Error  map[string]any
	Raw    []byte
}

// WatchStream wraps the channel + cancel func. InitialStatus exposes the
// HTTP status of the watch GET so callers can fail fast on 401/403 before
// they wait for events that will never arrive.
type WatchStream struct {
	Events        <-chan WatchEvent
	InitialStatus int
	cancel        context.CancelFunc
}

// Stop closes the underlying request and drains the channel.
func (w *WatchStream) Stop() { w.cancel() }

// Watch opens a long-lived NDJSON stream against the `:watch` endpoint.
// The returned channel emits one WatchEvent per stream message; it is
// closed when the stream ends or Stop is called.
func (c *AdminClient) Watch(ctx context.Context, plural, ns string) (*WatchStream, error) {
	wctx, cancel := context.WithCancel(ctx)
	full := c.BaseURL + adminPath(plural, ns) + ":watch"
	req, err := http.NewRequestWithContext(wctx, http.MethodGet, full, nil)
	if err != nil {
		cancel()
		return nil, err
	}
	if c.Token != "" {
		req.Header.Set("Authorization", "Bearer "+c.Token)
	}
	req.Header.Set("Accept", "application/json")

	resp, err := c.HTTP.Do(req)
	if err != nil {
		cancel()
		return nil, err
	}

	out := make(chan WatchEvent, 32)
	stream := &WatchStream{
		Events:        out,
		InitialStatus: resp.StatusCode,
		cancel: func() {
			cancel()
			_ = resp.Body.Close()
		},
	}

	if resp.StatusCode != http.StatusOK {
		// Drain the body so the connection can be reused, but do not
		// attempt to parse it as NDJSON. Close the channel immediately —
		// callers gate on InitialStatus before consuming.
		_, _ = io.Copy(io.Discard, resp.Body)
		_ = resp.Body.Close()
		close(out)
		cancel()
		return stream, nil
	}

	go func() {
		defer close(out)
		defer resp.Body.Close()
		// gRPC-gateway emits one JSON object per line for streaming
		// responses. Use a Scanner with a generous buffer in case a
		// single object spans more than the default 64 KiB.
		sc := bufio.NewScanner(resp.Body)
		sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)
		for sc.Scan() {
			line := sc.Bytes()
			trimmed := bytes.TrimSpace(line)
			if len(trimmed) == 0 {
				continue
			}
			var env struct {
				Result map[string]any `json:"result"`
				Error  map[string]any `json:"error"`
			}
			if err := json.Unmarshal(trimmed, &env); err != nil {
				// Surface unparsable lines as raw events so callers
				// can debug; do not panic the test binary.
				select {
				case out <- WatchEvent{Raw: append([]byte(nil), trimmed...)}:
				case <-wctx.Done():
					return
				}
				continue
			}
			select {
			case out <- WatchEvent{Result: env.Result, Error: env.Error, Raw: append([]byte(nil), trimmed...)}:
			case <-wctx.Done():
				return
			}
		}
	}()

	return stream, nil
}

// EventTypeOf returns the EventType string from a watch result envelope,
// flattening the proto enum (e.g. "EVENT_TYPE_ADDED").
func EventTypeOf(ev WatchEvent) string {
	if ev.Result == nil {
		return ""
	}
	if v, ok := ev.Result["event_type"].(string); ok {
		return v
	}
	if v, ok := ev.Result["eventType"].(string); ok {
		return v
	}
	return ""
}

// ResourceNameInEvent walks the LeaseEvent / ExporterEvent / etc. payload
// and returns the embedded resource's "name" field. The field key matches
// the singular form (e.g. "lease", "exporter").
func ResourceNameInEvent(ev WatchEvent, singular string) string {
	if ev.Result == nil {
		return ""
	}
	inner, ok := ev.Result[singular].(map[string]any)
	if !ok {
		return ""
	}
	if name, ok := inner["name"].(string); ok {
		return name
	}
	return ""
}

// ResourceName builds the `namespaces/<ns>/<plural>/<id>` form that
// admin.v1 RPCs use as the canonical resource identifier in responses.
func ResourceName(plural, ns, id string) string {
	return "namespaces/" + ns + "/" + plural + "/" + id
}

// ExtractInlineToken pulls the bootstrap token out of a CreateClient /
// CreateExporter response. The proto field is `token` (snake/camel both
// observed across gateway versions).
func ExtractInlineToken(resp map[string]any) string {
	if resp == nil {
		return ""
	}
	for _, k := range []string{"token", "inlineToken", "inline_token"} {
		if v, ok := resp[k].(string); ok && v != "" {
			return v
		}
	}
	return ""
}

// JoinErr concatenates a status code and the raw body for use in
// Gomega matcher messages.
func JoinErr(code int, raw []byte) string {
	return fmt.Sprintf("status=%d body=%s", code, strings.TrimSpace(string(raw)))
}
