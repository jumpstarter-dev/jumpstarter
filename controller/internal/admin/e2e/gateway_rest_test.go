/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package e2e

import (
	"context"
	"encoding/json"
	"net/http"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"
)

// gateway_rest_test exercises the grpc-gateway REST surface under the
// in-test auth shim (see suite_test.go: restAuthShim). Production
// controller_service.go currently registers the gateway HandlerServer
// directly with no HTTP middleware — gRPC interceptors do NOT run on the
// REST path. The shim mirrors the gRPC interceptors so these tests
// validate the *intended* end-to-end behavior of REST callers; the
// "production lacks REST auth" gap is tracked separately.
var _ = Describe("admin REST gateway (with test auth shim)", func() {
	var ns string

	BeforeEach(func() {
		ns = makeNamespace(context.Background())
		applyClusterRoleBinding(context.Background(), usernameFor("alice"),
			[]string{"get", "list", "create", "update", "patch", "delete"},
			[]string{"leases", "exporters", "clients", "webhooks"})
		seedExporter(context.Background(), ns, "exp-rest")
	})

	It("rejects requests with no Authorization header", func() {
		code, _ := restJSON(http.MethodGet, "/admin/v1/namespaces/"+ns+"/leases", "", nil)
		Expect(code).To(Equal(http.StatusUnauthorized))
	})

	It("rejects requests from unbound users", func() {
		code, _ := restJSON(http.MethodGet, "/admin/v1/namespaces/"+ns+"/leases", tokenFor("nobody"), nil)
		Expect(code).To(Equal(http.StatusForbidden))
	})

	It("lists exporters via GET", func() {
		code, body := restJSON(http.MethodGet, "/admin/v1/namespaces/"+ns+"/exporters", tokenFor("alice"), nil)
		Expect(code).To(Equal(http.StatusOK))
		var resp struct {
			Exporters []map[string]any `json:"exporters"`
		}
		Expect(json.Unmarshal(body, &resp)).To(Succeed())
		Expect(resp.Exporters).To(HaveLen(1))
	})

	It("gets a single exporter via GET", func() {
		code, body := restJSON(http.MethodGet,
			"/admin/v1/namespaces/"+ns+"/exporters/exp-rest", tokenFor("alice"), nil)
		Expect(code).To(Equal(http.StatusOK))
		var resp struct {
			Name string `json:"name"`
		}
		Expect(json.Unmarshal(body, &resp)).To(Succeed())
		Expect(resp.Name).To(Equal(exporterName(ns, "exp-rest")))
	})

	It("lists webhooks via GET (sanity check that the URL pattern matches)", func() {
		code, body := restJSON(http.MethodGet,
			"/admin/v1/namespaces/"+ns+"/webhooks", tokenFor("alice"), nil)
		Expect(code).To(Equal(http.StatusOK), "body=%s", string(body))
	})

	It("creates a webhook via POST", func() {
		// The grpc-gateway pattern binds the request body to the
		// `webhook` field of WebhookCreateRequest, with `parent` from the
		// URL path and `webhook_id` from a query parameter. So the JSON
		// body is the bare Webhook payload, not the wrapped request.
		makeSecret(context.Background(), ns, "rest-secret", "rest-key", "abc")

		body := map[string]any{
			"url":        "https://example.invalid/h",
			"secret_ref": "rest-secret/rest-key",
			"events":     []string{"EVENT_CLASS_LEASE_CREATED"},
		}
		code, respBody := restJSON(http.MethodPost,
			"/admin/v1/namespaces/"+ns+"/webhooks?webhook_id=wh-rest",
			tokenFor("alice"), body)
		Expect(code).To(Equal(http.StatusOK), "body=%s", string(respBody))
	})

	It("deletes an exporter via DELETE", func() {
		code, _ := restJSON(http.MethodDelete,
			"/admin/v1/namespaces/"+ns+"/exporters/exp-rest", tokenFor("alice"), nil)
		Expect(code).To(Equal(http.StatusOK))
	})

	It("returns NotFound (404) for missing resources", func() {
		code, _ := restJSON(http.MethodGet,
			"/admin/v1/namespaces/"+ns+"/exporters/missing", tokenFor("alice"), nil)
		Expect(code).To(Equal(http.StatusNotFound))
	})

	It("PATCH updates exporter labels", func() {
		// The Update gateway binds the body to ExporterUpdateRequest's
		// `exporter` field; the URL provides `exporter.name`.
		body := map[string]any{
			"labels": map[string]string{"env": "rest"},
		}
		code, _ := restJSON(http.MethodPatch,
			"/admin/v1/namespaces/"+ns+"/exporters/exp-rest", tokenFor("alice"), body)
		Expect(code).To(Equal(http.StatusOK))
	})
})
