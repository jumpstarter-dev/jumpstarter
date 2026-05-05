/*
Copyright 2026.

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

    http://www.apache.org/licenses/LICENSE-2.0
*/

package e2e

import (
	"bytes"
	"context"
	"encoding/json"
	"io"
	"net/http"
	"strings"
	"time"

	. "github.com/onsi/ginkgo/v2"
	. "github.com/onsi/gomega"

	"github.com/golang-jwt/jwt/v5"
	"github.com/google/uuid"
	"google.golang.org/grpc/credentials"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter-controller/api/v1alpha1"
	"github.com/jumpstarter-dev/jumpstarter-controller/internal/admin/identity"

	"github.com/go-logr/logr"
	"sigs.k8s.io/controller-runtime/pkg/log/zap"
)

// ginkgoLogger produces a logr.Logger writing to GinkgoWriter so the
// dispatcher's diagnostic output is captured per spec.
func ginkgoLogger() logr.Logger {
	return zap.New(zap.WriteTo(GinkgoWriter), zap.UseDevMode(true))
}

// ============================================================================
// JWT helpers — these run against the in-test signer to mint tokens with
// custom claims (subject, expiry, audience). The Signer.Token convenience
// only supports a single 365-day expiry and the "jumpstarter" audience.
// ============================================================================

// tokenFor mints a token signed by the suite primary signer with the given
// subject, audience override, and lifetime offset.
func tokenFor(sub string, opts ...tokenOpt) string {
	GinkgoHelper()
	o := tokenOpts{
		audience: signer.Audience(),
		issuer:   signer.Issuer(),
		ttl:      time.Hour,
	}
	for _, fn := range opts {
		fn(&o)
	}
	claims := jwt.RegisteredClaims{
		Issuer:    o.issuer,
		Subject:   sub,
		Audience:  []string{o.audience},
		IssuedAt:  jwt.NewNumericDate(time.Now().Add(-1 * time.Minute)),
		ExpiresAt: jwt.NewNumericDate(time.Now().Add(o.ttl)),
	}
	priv := signerKey
	if o.useAltSigner {
		priv = altSignerKey
	}
	tok, err := jwt.NewWithClaims(jwt.SigningMethodES256, claims).SignedString(priv)
	Expect(err).NotTo(HaveOccurred())
	return tok
}

// usernameFor returns the username string that the AuthenticationConfiguration
// derives for the given JWT subject under the "internal:" prefix. Tests use
// this to bind RBAC roles.
func usernameFor(sub string) string { return "internal:" + sub }

type tokenOpts struct {
	audience     string
	issuer       string
	ttl          time.Duration
	useAltSigner bool
}

type tokenOpt func(*tokenOpts)

func withAudience(a string) tokenOpt { return func(o *tokenOpts) { o.audience = a } }
func withIssuer(i string) tokenOpt   { return func(o *tokenOpts) { o.issuer = i } }
func withTTL(d time.Duration) tokenOpt {
	return func(o *tokenOpts) { o.ttl = d }
}
func withAltSigner() tokenOpt { return func(o *tokenOpts) { o.useAltSigner = true } }

// ============================================================================
// gRPC bearer credentials — sent on every RPC issued from the test gRPC
// client. Insecure transport is fine because we dial bufconn.
// ============================================================================

type bearerCreds struct {
	token string
}

func (b bearerCreds) GetRequestMetadata(_ context.Context, _ ...string) (map[string]string, error) {
	if b.token == "" {
		return nil, nil
	}
	return map[string]string{"authorization": "Bearer " + b.token}, nil
}

func (b bearerCreds) RequireTransportSecurity() bool { return false }

var _ credentials.PerRPCCredentials = bearerCreds{}

// ============================================================================
// Namespace + RBAC helpers — every test creates its own namespace via
// makeNamespace so specs do not leak resources into one another.
// ============================================================================

func makeNamespace(ctx context.Context) string {
	GinkgoHelper()
	name := "ns-" + strings.ReplaceAll(uuid.New().String(), "-", "")[:12]
	ns := &corev1.Namespace{ObjectMeta: metav1.ObjectMeta{Name: name}}
	Expect(k8sClient.Create(ctx, ns)).To(Succeed())
	DeferCleanup(func() {
		_ = k8sClient.Delete(context.Background(), ns)
	})
	return name
}

// makeClientCRDForCaller pre-provisions a Client CRD owned by the given
// identity in the namespace, which Lease.Create requires (it lookups by
// owner-hash to discover the caller's ClientRef).
func makeClientCRDForCaller(ctx context.Context, ns, sub string) string {
	GinkgoHelper()
	id := ownerIdentity(sub)
	c := &jumpstarterdevv1alpha1.Client{
		ObjectMeta: metav1.ObjectMeta{
			Namespace: ns,
			Name:      "client-" + safeSubjectName(sub),
			Annotations: map[string]string{
				identity.OwnerAnnotation: id.OwnerHash(),
			},
		},
	}
	Expect(k8sClient.Create(ctx, c)).To(Succeed())
	return c.Name
}

// ownerIdentity reconstructs the production identity.Identity for a JWT
// subject, mirroring what MultiIssuerAuthenticator builds for tokens
// validated against the in-test signer.
func ownerIdentity(sub string) identity.Identity {
	return identity.Identity{
		Issuer:   signer.Issuer(),
		Subject:  sub,
		Username: usernameFor(sub),
	}
}

// makeSecret creates a kube Secret with a single key/value entry. Used by
// webhook delivery tests that need a real HMAC signing key.
func makeSecret(ctx context.Context, ns, name, key, value string) {
	GinkgoHelper()
	s := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Namespace: ns, Name: name},
		Data:       map[string][]byte{key: []byte(value)},
	}
	Expect(k8sClient.Create(ctx, s)).To(Succeed())
}

// nsParent formats the gRPC `parent` identifier for a namespace.
// Production code parses "namespaces/<ns>" via ParseNamespaceIdentifier;
// there is no exported Unparse for namespace, so tests build it inline.
func nsParent(ns string) string { return "namespaces/" + ns }

// leaseName / exporterName / clientName / webhookName return resource
// identifiers in the form Get/Update/Delete RPCs accept.
func leaseName(ns, id string) string    { return "namespaces/" + ns + "/leases/" + id }
func exporterName(ns, id string) string { return "namespaces/" + ns + "/exporters/" + id }
func clientName(ns, id string) string   { return "namespaces/" + ns + "/clients/" + id }
func webhookName(ns, id string) string  { return "namespaces/" + ns + "/webhooks/" + id }

// ============================================================================
// REST request helpers — tests issue typed JSON requests against the
// httptest server in gateway_rest_test.go through these wrappers.
// ============================================================================

func restJSON(method, path, token string, body any) (int, []byte) {
	GinkgoHelper()
	var rdr io.Reader
	if body != nil {
		buf, err := json.Marshal(body)
		Expect(err).NotTo(HaveOccurred())
		rdr = bytes.NewReader(buf)
	}
	req, err := http.NewRequest(method, restURL(path), rdr)
	Expect(err).NotTo(HaveOccurred())
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	resp, err := restServer.Client().Do(req)
	Expect(err).NotTo(HaveOccurred())
	defer resp.Body.Close()
	out, err := io.ReadAll(resp.Body)
	Expect(err).NotTo(HaveOccurred())
	return resp.StatusCode, out
}

