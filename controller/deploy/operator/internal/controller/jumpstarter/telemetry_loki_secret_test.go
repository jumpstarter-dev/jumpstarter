/*
Copyright 2026. The Jumpstarter Authors.

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

package jumpstarter

import (
	"context"
	"strings"
	"testing"

	appsv1 "k8s.io/api/apps/v1"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/client-go/tools/record"
	"sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	operatorv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/deploy/operator/api/v1alpha1"
)

func TestReconcileTelemetryDeploymentOmitsLokiWhenSecretMissing(t *testing.T) {
	js := lokiPushJS()
	js.Spec.Telemetry.Loki.SecretRef = "loki-credentials"
	r, c, rec := lokiTestReconciler(t)

	if err := r.reconcileTelemetryDeployment(context.Background(), js); err != nil {
		t.Fatal(err)
	}
	dep := getTelemetryDep(t, c)
	if hasLokiURL(dep) {
		t.Fatal("missing credential Secret must omit the Loki URL")
	}
	if hasEnv(dep.Spec.Template.Spec.Containers[0], "LOKI_TOKEN") {
		t.Fatal("missing credential Secret must not set Loki env vars")
	}
	if !hasEventReason(rec) {
		t.Fatal("expected LokiCredentialsMissing event")
	}
}

func TestReconcileTelemetryDeploymentOmitsLokiWhenSecretHasNoCredentials(t *testing.T) {
	js := lokiPushJS()
	js.Spec.Telemetry.Loki.SecretRef = "loki-credentials"
	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: "loki-credentials", Namespace: "ns"},
		Data:       map[string][]byte{"unrelated": []byte("x")},
	}
	r, c, rec := lokiTestReconciler(t, secret)

	if err := r.reconcileTelemetryDeployment(context.Background(), js); err != nil {
		t.Fatal(err)
	}
	if hasLokiURL(getTelemetryDep(t, c)) {
		t.Fatal("Secret without token, username, or password must omit the Loki URL")
	}
	if !hasEventReason(rec) {
		t.Fatal("expected LokiCredentialsMissing event")
	}
}

func TestReconcileTelemetryDeploymentKeepsLokiForTokenOnlySecret(t *testing.T) {
	js := lokiPushJS()
	js.Spec.Telemetry.Loki.SecretRef = "loki-credentials"
	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: "loki-credentials", Namespace: "ns"},
		Data:       map[string][]byte{"token": []byte("s3cret")},
	}
	r, c, rec := lokiTestReconciler(t, secret)

	if err := r.reconcileTelemetryDeployment(context.Background(), js); err != nil {
		t.Fatal(err)
	}
	dep := getTelemetryDep(t, c)
	if !hasLokiURL(dep) {
		t.Fatal("token-only Secret must keep the Loki URL")
	}
	got := dep.Spec.Template.Annotations[lokiSecretHashAnnotation]
	if got != secretDataHash(secret) {
		t.Fatalf("loki secret hash = %q, want %q", got, secretDataHash(secret))
	}
	if hasEventReason(rec) {
		t.Fatal("token-only Secret must not emit LokiCredentialsMissing")
	}
}

func TestReconcileTelemetryDeploymentKeepsLokiForPasswordOnlySecret(t *testing.T) {
	js := lokiPushJS()
	js.Spec.Telemetry.Loki.SecretRef = "loki-credentials"
	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: "loki-credentials", Namespace: "ns"},
		Data:       map[string][]byte{"password": []byte("s3cret")},
	}
	r, c, _ := lokiTestReconciler(t, secret)

	if err := r.reconcileTelemetryDeployment(context.Background(), js); err != nil {
		t.Fatal(err)
	}
	if !hasLokiURL(getTelemetryDep(t, c)) {
		t.Fatal("password-only Secret must keep the Loki URL")
	}
}

func TestReconcileTelemetryDeploymentKeepsUnauthenticatedLoki(t *testing.T) {
	js := lokiPushJS()
	r, c, rec := lokiTestReconciler(t)

	if err := r.reconcileTelemetryDeployment(context.Background(), js); err != nil {
		t.Fatal(err)
	}
	dep := getTelemetryDep(t, c)
	if !hasLokiURL(dep) {
		t.Fatal("Loki URL without secretRef must stay")
	}
	if _, ok := dep.Spec.Template.Annotations[lokiSecretHashAnnotation]; ok {
		t.Fatal("unauthenticated Loki must not set a credential hash")
	}
	if hasEventReason(rec) {
		t.Fatal("unauthenticated Loki must not emit LokiCredentialsMissing")
	}
}

func TestReconcileTelemetryDeploymentRollsWhenLokiSecretChanges(t *testing.T) {
	js := lokiPushJS()
	js.Spec.Telemetry.Loki.SecretRef = "loki-credentials"
	js.Spec.Telemetry.Loki.TLS.CASecretRef = "loki-ca-bundle"
	secret := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: "loki-credentials", Namespace: "ns"},
		Data:       map[string][]byte{"token": []byte("v1")},
	}
	ca := &corev1.Secret{
		ObjectMeta: metav1.ObjectMeta{Name: "loki-ca-bundle", Namespace: "ns"},
		Data:       map[string][]byte{"ca.crt": []byte("cert-v1")},
	}
	r, c, _ := lokiTestReconciler(t, secret, ca)

	if err := r.reconcileTelemetryDeployment(context.Background(), js); err != nil {
		t.Fatal(err)
	}
	first := getTelemetryDep(t, c)
	firstCred := first.Spec.Template.Annotations[lokiSecretHashAnnotation]
	firstCA := first.Spec.Template.Annotations[lokiCAHashAnnotation]
	if firstCred == "" || firstCA == "" {
		t.Fatalf("expected credential and CA hashes, got cred=%q ca=%q", firstCred, firstCA)
	}

	secret.Data["token"] = []byte("v2")
	if err := c.Update(context.Background(), secret); err != nil {
		t.Fatal(err)
	}
	if err := r.reconcileTelemetryDeployment(context.Background(), js); err != nil {
		t.Fatal(err)
	}
	second := getTelemetryDep(t, c)
	if second.Spec.Template.Annotations[lokiSecretHashAnnotation] == firstCred {
		t.Fatal("credential Secret change must change the pod hash")
	}
	if second.Spec.Template.Annotations[lokiCAHashAnnotation] != firstCA {
		t.Fatal("CA hash must stay when only the credential Secret changes")
	}
}

func TestLokiReferencedSecretKeys(t *testing.T) {
	js := lokiPushJS()
	js.Spec.Telemetry.Loki.SecretRef = "loki-credentials"
	js.Spec.Telemetry.Loki.TLS.CASecretRef = "loki-ca-bundle"

	got := lokiReferencedSecretKeys(js)
	want := []string{"ns/loki-credentials", "ns/loki-ca-bundle"}
	if strings.Join(got, ",") != strings.Join(want, ",") {
		t.Fatalf("keys = %v, want %v", got, want)
	}
}

func lokiPushJS() *operatorv1alpha1.Jumpstarter {
	js := phase3TelemetryJS("ns")
	js.Spec.Telemetry.Loki.URL = "https://loki:3100/loki/api/v1/push"
	return js
}

func lokiTestReconciler(t *testing.T, objs ...client.Object) (*JumpstarterReconciler, client.Client, *record.FakeRecorder) {
	t.Helper()
	s := runtime.NewScheme()
	if err := operatorv1alpha1.AddToScheme(s); err != nil {
		t.Fatal(err)
	}
	if err := appsv1.AddToScheme(s); err != nil {
		t.Fatal(err)
	}
	if err := corev1.AddToScheme(s); err != nil {
		t.Fatal(err)
	}
	c := fake.NewClientBuilder().WithScheme(s).WithObjects(objs...).Build()
	rec := record.NewFakeRecorder(8)
	return &JumpstarterReconciler{Client: c, Scheme: s, Recorder: rec}, c, rec
}

func getTelemetryDep(t *testing.T, c client.Client) *appsv1.Deployment {
	t.Helper()
	dep := &appsv1.Deployment{}
	err := c.Get(context.Background(), client.ObjectKey{Name: "js-telemetry", Namespace: "ns"}, dep)
	if err != nil {
		t.Fatal(err)
	}
	return dep
}

func hasLokiURL(dep *appsv1.Deployment) bool {
	for _, arg := range dep.Spec.Template.Spec.Containers[0].Args {
		if strings.HasPrefix(arg, "-loki-url=") {
			return true
		}
	}
	return false
}

func hasEventReason(rec *record.FakeRecorder) bool {
	for {
		select {
		case event := <-rec.Events:
			if strings.Contains(event, "LokiCredentialsMissing") {
				return true
			}
		default:
			return false
		}
	}
}
