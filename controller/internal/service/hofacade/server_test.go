package hofacade

import (
	"context"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"
	"k8s.io/apimachinery/pkg/runtime"
	"k8s.io/apimachinery/pkg/types"
	clientgoscheme "k8s.io/client-go/kubernetes/scheme"
	kclient "sigs.k8s.io/controller-runtime/pkg/client"
	"sigs.k8s.io/controller-runtime/pkg/client/fake"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
	virtualtargetv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/virtualtarget/v1alpha1"
)

const (
	testNS    = "testns"
	tokAlice  = "tok-alice"
	tokBob    = "tok-bob"
	tokCarol  = "tok-carol"
	testPool  = "cf-pool"
	testLease = 30 * time.Minute
)

func testScheme(t *testing.T) *runtime.Scheme {
	t.Helper()
	scheme := runtime.NewScheme()
	for _, add := range []func(*runtime.Scheme) error{
		clientgoscheme.AddToScheme,
		jumpstarterdevv1alpha1.AddToScheme,
		virtualtargetv1alpha1.AddToScheme,
	} {
		if err := add(scheme); err != nil {
			t.Fatal(err)
		}
	}
	return scheme
}

type fixture struct {
	client kclient.Client
	server *Server
	ts     *httptest.Server
}

// newFixture builds a fake-client-backed Server and serves Routes() over
// httptest. Clients alice/bob/carol and an ExporterSet with a non-empty
// selector are seeded; leases are seeded per test.
func newFixture(t *testing.T, extra ...kclient.Object) *fixture {
	t.Helper()
	objs := []kclient.Object{
		&jumpstarterdevv1alpha1.Client{ObjectMeta: metav1.ObjectMeta{Name: "alice", Namespace: testNS}},
		&jumpstarterdevv1alpha1.Client{ObjectMeta: metav1.ObjectMeta{Name: "bob", Namespace: testNS}},
		&jumpstarterdevv1alpha1.Client{ObjectMeta: metav1.ObjectMeta{Name: "carol", Namespace: testNS}},
		&virtualtargetv1alpha1.ExporterSet{
			ObjectMeta: metav1.ObjectMeta{Name: testPool, Namespace: testNS},
			Spec: virtualtargetv1alpha1.ExporterSetSpec{
				Selector: metav1.LabelSelector{MatchLabels: map[string]string{"pool": "cf"}},
			},
		},
	}
	objs = append(objs, extra...)
	c := fake.NewClientBuilder().
		WithScheme(testScheme(t)).
		WithStatusSubresource(&jumpstarterdevv1alpha1.Lease{}).
		WithObjects(objs...).
		Build()
	return newFixtureWithClient(t, c)
}

func newFixtureWithClient(t *testing.T, c kclient.Client) *fixture {
	t.Helper()
	srv := NewServer(Server{
		Client: c,
		Resolver: NewStaticClientResolver(c, testNS, map[string]string{
			tokAlice: "alice",
			tokBob:   "bob",
			tokCarol: "carol",
		}),
		Namespace:     testNS,
		PoolSelector:  &metav1.LabelSelector{MatchLabels: map[string]string{"pool": "cf"}},
		LeaseDuration: testLease,
		WaitDuration:  300 * time.Millisecond,
		PollInterval:  10 * time.Millisecond,
	})
	ts := httptest.NewServer(srv.Routes())
	t.Cleanup(ts.Close)
	return &fixture{client: c, server: srv, ts: ts}
}

// doReq issues a request and returns (status, trimmed body).
func (f *fixture) doReq(t *testing.T, method, path, token, body string) (int, string) {
	t.Helper()
	var rd io.Reader
	if body != "" {
		rd = strings.NewReader(body)
	}
	req, err := http.NewRequest(method, f.ts.URL+path, rd)
	if err != nil {
		t.Fatal(err)
	}
	if token != "" {
		req.Header.Set("Authorization", "Bearer "+token)
	}
	resp, err := f.ts.Client().Do(req)
	if err != nil {
		t.Fatal(err)
	}
	defer func() { _ = resp.Body.Close() }()
	b, err := io.ReadAll(resp.Body)
	if err != nil {
		t.Fatal(err)
	}
	return resp.StatusCode, strings.TrimSpace(string(b))
}

// createCVD posts a create request and returns the operation (== lease) name.
func (f *fixture) createCVD(t *testing.T, token string) string {
	t.Helper()
	code, body := f.doReq(t, http.MethodPost, "/cvds", token, `{"env_config":{"common":{"group_name":"cvd"}}}`)
	if code != http.StatusOK {
		t.Fatalf("POST /cvds: got %d %s", code, body)
	}
	var op struct {
		Name string `json:"name"`
		Done bool   `json:"done"`
	}
	if err := json.Unmarshal([]byte(body), &op); err != nil {
		t.Fatalf("POST /cvds body %q: %v", body, err)
	}
	if op.Done {
		t.Fatalf("POST /cvds returned done:true: %s", body)
	}
	return op.Name
}

func (f *fixture) getLease(t *testing.T, name string) *jumpstarterdevv1alpha1.Lease {
	t.Helper()
	var l jumpstarterdevv1alpha1.Lease
	if err := f.client.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: name}, &l); err != nil {
		t.Fatalf("get lease %s: %v", name, err)
	}
	return &l
}

// The mark* helpers copy the LeaseReconciler's exact status transitions
// (internal/controller/lease_controller.go, api/v1alpha1/lease_helpers.go).

// markAcquired mirrors reconcileStatusExporterRef + reconcileStatusBeginEndTimes:
// status.exporterRef, status.beginTime and Ready=True/"Ready".
func (f *fixture) markAcquired(t *testing.T, leaseName, exporterName string) {
	t.Helper()
	ctx := context.Background()
	l := f.getLease(t, leaseName)
	now := metav1.Now()
	l.Status.ExporterRef = &corev1.LocalObjectReference{Name: exporterName}
	l.Status.BeginTime = &now
	l.SetStatusReady(true, "Ready", "An exporter has been acquired for the client")
	if err := f.client.Status().Update(ctx, l); err != nil {
		t.Fatalf("markAcquired %s: %v", leaseName, err)
	}
}

// markEndedStatus stamps Ended + the jumpstarter.dev/lease-ended label the
// reconciler adds (lease_controller.go:123-128).
func (f *fixture) markEndedStatus(t *testing.T, l *jumpstarterdevv1alpha1.Lease) {
	t.Helper()
	ctx := context.Background()
	if err := f.client.Status().Update(ctx, l); err != nil {
		t.Fatalf("status update %s: %v", l.Name, err)
	}
	if l.Labels == nil {
		l.Labels = make(map[string]string)
	}
	l.Labels[string(jumpstarterdevv1alpha1.LeaseLabelEnded)] = jumpstarterdevv1alpha1.LeaseLabelEndedValue
	if err := f.client.Update(ctx, l); err != nil {
		t.Fatalf("label update %s: %v", l.Name, err)
	}
}

// markEnded mirrors (*Lease).Release (lease_helpers.go:410-416) + ended label.
func (f *fixture) markEnded(t *testing.T, leaseName string) {
	t.Helper()
	l := f.getLease(t, leaseName)
	now := metav1.Now()
	l.SetStatusReady(false, "Released", "The lease was marked for release")
	l.Status.Ended = true
	l.Status.EndTime = &now
	f.markEndedStatus(t, l)
}

// markUnsatisfiable mirrors SetStatusUnsatisfiable + the forced Ended
// (lease_controller.go:161-165) + ended label.
func (f *fixture) markUnsatisfiable(t *testing.T, leaseName, reason, message string) {
	t.Helper()
	l := f.getLease(t, leaseName)
	now := metav1.Now()
	l.SetStatusUnsatisfiable(reason, "%s", message)
	l.Status.Ended = true
	l.Status.EndTime = &now
	f.markEndedStatus(t, l)
}

func cvdsBody(leaseName, exporterName string) string {
	return fmt.Sprintf(`{"cvds":[{"group":%q,"name":%q,"status":"Running","displays":[],"webrtc_device_id":"","adb_serial":"","adb_port":0}]}`, leaseName, exporterName)
}

// Test 14: POST /cvds returns 200 (never 201/202) with Operation{name:<uuidv7>,
// done:false}; the created Lease carries the authenticated caller's clientRef
// (the body cannot influence attribution), the pool's selector, and the
// configured duration; the op name IS the lease name.
func TestCreateCVDLeaseShape(t *testing.T) {
	f := newFixture(t)
	// Body tries to smuggle another client's attribution: only env_config is read.
	code, body := f.doReq(t, http.MethodPost, "/cvds", tokAlice,
		`{"env_config":{"x":1},"clientRef":{"name":"bob"},"client":"bob"}`)
	if code != http.StatusOK {
		t.Fatalf("POST /cvds: got %d %s, want 200", code, body)
	}
	var op struct {
		Name string `json:"name"`
		Done bool   `json:"done"`
	}
	if err := json.Unmarshal([]byte(body), &op); err != nil {
		t.Fatal(err)
	}
	if op.Done {
		t.Errorf("create op must start not-done: %s", body)
	}
	parsed, err := uuid.Parse(op.Name)
	if err != nil {
		t.Fatalf("op name %q is not a UUID: %v", op.Name, err)
	}
	if parsed.Version() != 7 {
		t.Errorf("op name %q: got UUID version %d, want 7", op.Name, parsed.Version())
	}

	l := f.getLease(t, op.Name)
	if l.Spec.ClientRef.Name != "alice" {
		t.Errorf("clientRef: got %q want alice (body must not influence attribution)", l.Spec.ClientRef.Name)
	}
	if got := l.Spec.Selector.MatchLabels["pool"]; got != "cf" {
		t.Errorf("selector: got %v, want the ExporterSet selector", l.Spec.Selector)
	}
	if l.Spec.Duration == nil || l.Spec.Duration.Duration != testLease {
		t.Errorf("duration: got %v want %v", l.Spec.Duration, testLease)
	}
}

// Test 15: malformed body -> 400 with the pinned upstream message; bizarre
// env_config shapes are accepted untouched.
func TestCreateCVDBodyHandling(t *testing.T) {
	f := newFixture(t)
	code, body := f.doReq(t, http.MethodPost, "/cvds", tokAlice, `{"env_config":`)
	if code != http.StatusBadRequest {
		t.Errorf("malformed JSON: got %d %s want 400", code, body)
	}
	var em map[string]any
	if err := json.Unmarshal([]byte(body), &em); err != nil {
		t.Fatal(err)
	}
	if em["error"] != "Malformed JSON in request" {
		t.Errorf("malformed JSON error: got %v", em["error"])
	}

	for _, b := range []string{
		`{"env_config":{"deeply":{"nested":[{"a":[[[1]]]}]}},"unknown_key":true}`,
		`{"env_config":[1,2,3]}`,
		`{"env_config":"just a string"}`,
		`{"env_config":null}`,
		`{}`,
	} {
		code, resp := f.doReq(t, http.MethodPost, "/cvds", tokAlice, b)
		if code != http.StatusOK {
			t.Errorf("body %s: got %d %s, want 200 (env_config is opaque)", b, code, resp)
		}
	}
}

// Test 16: :wait happy path — acquisition mid-wait completes the long-poll
// with the byte-exact cvds body; GET /result returns the same, repeatably.
func TestWaitHappyPath(t *testing.T) {
	f := newFixture(t)
	name := f.createCVD(t, tokAlice)

	go func() {
		time.Sleep(50 * time.Millisecond)
		f.markAcquired(t, name, "exp-1")
	}()

	code, body := f.doReq(t, http.MethodPost, "/operations/"+name+"/:wait", tokAlice, "")
	if code != http.StatusOK {
		t.Fatalf(":wait: got %d %s want 200", code, body)
	}
	want := cvdsBody(name, "exp-1")
	if body != want {
		t.Errorf(":wait body:\n got  %s\n want %s", body, want)
	}

	for i := 0; i < 2; i++ {
		code, body = f.doReq(t, http.MethodGet, "/operations/"+name+"/result", tokAlice, "")
		if code != http.StatusOK || body != want {
			t.Errorf("result #%d: got %d %s", i, code, body)
		}
	}
}

// Test 17: pool exhaustion — the lease stays Pending, :wait long-polls then
// returns 503 with the exact upstream body; the lease is NOT released and a
// repeat :wait is allowed (and also times out).
func TestWaitPoolExhaustion(t *testing.T) {
	f := newFixture(t)
	name := f.createCVD(t, tokAlice)

	for i := 0; i < 2; i++ {
		start := time.Now()
		code, body := f.doReq(t, http.MethodPost, "/operations/"+name+"/:wait", tokAlice, "")
		if code != http.StatusServiceUnavailable {
			t.Fatalf(":wait #%d: got %d %s want 503", i, code, body)
		}
		if body != `{"error":"Wait for operation timed out"}` {
			t.Errorf(":wait #%d body: got %s", i, body)
		}
		if elapsed := time.Since(start); elapsed < 250*time.Millisecond {
			t.Errorf(":wait #%d returned after %v, want ~WaitDuration long-poll", i, elapsed)
		}
	}

	l := f.getLease(t, name)
	if l.Spec.Release || l.Status.Ended {
		t.Error("wait timeout must not release the lease (it stays queued)")
	}
}

// Test 18: :wait and /result on an Unsatisfiable lease return the
// driver-terminal 500 shape carrying the condition reason and message.
func TestWaitUnsatisfiable(t *testing.T) {
	f := newFixture(t)
	name := f.createCVD(t, tokAlice)
	f.markUnsatisfiable(t, name, "NoAccess", "denied by policy")

	want := `{"error":"NoAccess","details":"NoAccess: denied by policy"}`
	code, body := f.doReq(t, http.MethodPost, "/operations/"+name+"/:wait", tokAlice, "")
	if code != http.StatusInternalServerError || body != want {
		t.Errorf(":wait: got %d %s\n want 500 %s", code, body, want)
	}
	code, body = f.doReq(t, http.MethodGet, "/operations/"+name+"/result", tokAlice, "")
	if code != http.StatusInternalServerError || body != want {
		t.Errorf("result: got %d %s\n want 500 %s", code, body, want)
	}
}

// Test 19: distinct upstream 404 messages — pending op result is
// "Operation not done", unknown op is "Operation not found"
// (controller.go:599-618).
func TestResultPendingVsUnknown(t *testing.T) {
	f := newFixture(t)
	name := f.createCVD(t, tokAlice)

	code, body := f.doReq(t, http.MethodGet, "/operations/"+name+"/result", tokAlice, "")
	if code != http.StatusNotFound || body != `{"error":"Operation not done"}` {
		t.Errorf("pending result: got %d %s", code, body)
	}

	code, body = f.doReq(t, http.MethodGet, "/operations/"+uuid.NewString()+"/result", tokAlice, "")
	if code != http.StatusNotFound || body != `{"error":"Operation not found"}` {
		t.Errorf("unknown result: got %d %s", code, body)
	}
}

// Test 20: TENANCY on operations — bob probing alice's op names (create AND
// derived release) gets 404s byte-identical to probing a random UUID:
// guessable names leak nothing.
func TestTenancyOperations(t *testing.T) {
	f := newFixture(t)
	name := f.createCVD(t, tokAlice)
	f.markAcquired(t, name, "exp-1")
	// Give alice a release op too.
	code, _ := f.doReq(t, http.MethodDelete, "/cvds/"+name, tokAlice, "")
	if code != http.StatusOK {
		t.Fatalf("alice DELETE: got %d", code)
	}
	releaseOp := ReleaseOperationName(name)

	random := uuid.NewString()
	probes := []struct{ method, path string }{
		{http.MethodGet, "/operations/%s"},
		{http.MethodGet, "/operations/%s/result"},
		{http.MethodPost, "/operations/%s/:wait"},
	}
	for _, p := range probes {
		wantCode, wantBody := f.doReq(t, p.method, fmt.Sprintf(p.path, random), tokBob, "")
		if wantCode != http.StatusNotFound {
			t.Fatalf("%s random: got %d, want 404", p.path, wantCode)
		}
		for _, opName := range []string{name, releaseOp} {
			code, body := f.doReq(t, p.method, fmt.Sprintf(p.path, opName), tokBob, "")
			if code != wantCode || body != wantBody {
				t.Errorf("%s %s as bob: got %d %s, want byte-identical to random-UUID 404 (%d %s)",
					p.method, opName, code, body, wantCode, wantBody)
			}
		}
	}
}

// Test 21: GET /operations lists only the caller's not-done ops (pending
// creates + releases in flight); done ops leave the list but stay fetchable
// by name; reset ops are never listed.
func TestListOperations(t *testing.T) {
	f := newFixture(t)
	pending := f.createCVD(t, tokAlice)
	acquired := f.createCVD(t, tokAlice)
	f.markAcquired(t, acquired, "exp-1")
	releasing := f.createCVD(t, tokAlice)
	f.markAcquired(t, releasing, "exp-2")
	if code, _ := f.doReq(t, http.MethodDelete, "/cvds/"+releasing, tokAlice, ""); code != http.StatusOK {
		t.Fatal("delete failed")
	}
	bobs := f.createCVD(t, tokBob)

	code, body := f.doReq(t, http.MethodGet, "/operations", tokAlice, "")
	if code != http.StatusOK {
		t.Fatalf("GET /operations: %d %s", code, body)
	}
	var resp struct {
		Operations []struct {
			Name string `json:"name"`
			Done bool   `json:"done"`
		} `json:"operations"`
	}
	if err := json.Unmarshal([]byte(body), &resp); err != nil {
		t.Fatal(err)
	}
	names := map[string]bool{}
	for _, op := range resp.Operations {
		names[op.Name] = true
		if op.Done {
			t.Errorf("listed op %s is done; only running ops are listed (upstream MapOM.ListRunning)", op.Name)
		}
	}
	if !names[pending] {
		t.Error("pending create op missing from list")
	}
	if !names[ReleaseOperationName(releasing)] {
		t.Error("release-in-flight op missing from list")
	}
	if names[acquired] {
		t.Error("done create op must leave the list")
	}
	if names[bobs] {
		t.Error("bob's op leaked into alice's list")
	}
	if names[ResetOperationName("alice")] {
		t.Error("reset ops are never listed (stateless model)")
	}

	// Done ops stay fetchable by name by their owner.
	code, body = f.doReq(t, http.MethodGet, "/operations/"+acquired, tokAlice, "")
	if code != http.StatusOK || body != fmt.Sprintf(`{"name":%q,"done":true}`, acquired) {
		t.Errorf("done op fetch: got %d %s", code, body)
	}
}

// Test 22: TENANCY GET /cvds — strictly the caller's acquired leases; pending
// and ended leases absent; empty listing is {"cvds":[]} never null; alice and
// bob see disjoint sets.
func TestTenancyListCVDs(t *testing.T) {
	f := newFixture(t)
	aliceAcquired := f.createCVD(t, tokAlice)
	f.markAcquired(t, aliceAcquired, "exp-a")
	_ = f.createCVD(t, tokAlice) // pending, no exporterRef
	aliceEnded := f.createCVD(t, tokAlice)
	f.markAcquired(t, aliceEnded, "exp-e")
	f.markEnded(t, aliceEnded)
	bobAcquired := f.createCVD(t, tokBob)
	f.markAcquired(t, bobAcquired, "exp-b")

	code, body := f.doReq(t, http.MethodGet, "/cvds", tokAlice, "")
	if code != http.StatusOK || body != cvdsBody(aliceAcquired, "exp-a") {
		t.Errorf("alice GET /cvds: got %d %s\n want %s", code, body, cvdsBody(aliceAcquired, "exp-a"))
	}

	code, body = f.doReq(t, http.MethodGet, "/cvds", tokBob, "")
	if code != http.StatusOK || body != cvdsBody(bobAcquired, "exp-b") {
		t.Errorf("bob GET /cvds: got %d %s", code, body)
	}

	code, body = f.doReq(t, http.MethodGet, "/cvds", tokCarol, "")
	if code != http.StatusOK || body != `{"cvds":[]}` {
		t.Errorf("carol GET /cvds: got %d %s, want empty envelope", code, body)
	}
}

// Test 23: GET /cvds/{group} and /{group}/{name} — owner gets the single-CVD
// envelope; wrong owner, nonexistent, ended, and instance-name mismatch are
// byte-identical 404s.
func TestGetCVDGroupTenancy(t *testing.T) {
	f := newFixture(t)
	name := f.createCVD(t, tokAlice)
	f.markAcquired(t, name, "exp-1")
	ended := f.createCVD(t, tokAlice)
	f.markAcquired(t, ended, "exp-2")
	f.markEnded(t, ended)

	want := cvdsBody(name, "exp-1")
	code, body := f.doReq(t, http.MethodGet, "/cvds/"+name, tokAlice, "")
	if code != http.StatusOK || body != want {
		t.Errorf("owner GET /cvds/{group}: got %d %s", code, body)
	}
	code, body = f.doReq(t, http.MethodGet, "/cvds/"+name+"/exp-1", tokAlice, "")
	if code != http.StatusOK || body != want {
		t.Errorf("owner GET /cvds/{group}/{name}: got %d %s", code, body)
	}

	// Reference 404: nonexistent group.
	refCode, refBody := f.doReq(t, http.MethodGet, "/cvds/"+uuid.NewString(), tokAlice, "")
	if refCode != http.StatusNotFound {
		t.Fatalf("nonexistent group: got %d, want 404", refCode)
	}

	probes := []struct {
		desc, method, path, token string
	}{
		{"wrong owner group", http.MethodGet, "/cvds/" + name, tokBob},
		{"wrong owner instance", http.MethodGet, "/cvds/" + name + "/exp-1", tokBob},
		{"ended group", http.MethodGet, "/cvds/" + ended, tokAlice},
		{"instance name mismatch", http.MethodGet, "/cvds/" + name + "/other-exp", tokAlice},
		{"nonexistent instance", http.MethodGet, "/cvds/" + uuid.NewString() + "/exp-1", tokAlice},
	}
	for _, p := range probes {
		code, body := f.doReq(t, p.method, p.path, p.token, "")
		if code != refCode || body != refBody {
			t.Errorf("%s: got %d %s, want byte-identical to nonexistent (%d %s)", p.desc, code, body, refCode, refBody)
		}
	}
}

// Test 24: DELETE /cvds/{group}[/{name}] patches spec.release=true (never
// deletes the CR, never writes status or labels), returns the derived release
// Operation, is idempotent, and :wait yields 200 {} once the lease ends.
func TestDeleteCVD(t *testing.T) {
	f := newFixture(t)
	name := f.createCVD(t, tokAlice)
	f.markAcquired(t, name, "exp-1")

	wantOp := fmt.Sprintf(`{"name":%q,"done":false}`, ReleaseOperationName(name))
	code, body := f.doReq(t, http.MethodDelete, "/cvds/"+name, tokAlice, "")
	if code != http.StatusOK || body != wantOp {
		t.Fatalf("DELETE: got %d %s want 200 %s", code, body, wantOp)
	}

	l := f.getLease(t, name)
	if !l.Spec.Release {
		t.Error("DELETE must patch spec.release=true")
	}
	if l.Status.Ended {
		t.Error("the facade must not write lease status (that is the reconciler's job)")
	}
	if _, ok := l.Labels[string(jumpstarterdevv1alpha1.LeaseLabelEnded)]; ok {
		t.Error("the facade must not stamp the ended label")
	}

	// Repeat DELETE (group and instance form): idempotent, same derived op.
	code, body = f.doReq(t, http.MethodDelete, "/cvds/"+name, tokAlice, "")
	if code != http.StatusOK || body != wantOp {
		t.Errorf("repeat DELETE: got %d %s", code, body)
	}
	code, body = f.doReq(t, http.MethodDelete, "/cvds/"+name+"/exp-1", tokAlice, "")
	if code != http.StatusOK || body != wantOp {
		t.Errorf("instance DELETE: got %d %s", code, body)
	}

	// Reconciler ends the lease -> release op completes; :wait returns {}.
	f.markEnded(t, name)
	code, body = f.doReq(t, http.MethodPost, "/operations/"+ReleaseOperationName(name)+"/:wait", tokAlice, "")
	if code != http.StatusOK || body != `{}` {
		t.Errorf("release :wait after end: got %d %s want 200 {}", code, body)
	}
	code, body = f.doReq(t, http.MethodGet, "/operations/"+ReleaseOperationName(name)+"/result", tokAlice, "")
	if code != http.StatusOK || body != `{}` {
		t.Errorf("release result after end: got %d %s want 200 {}", code, body)
	}
}

// A release op only "exists" once release was requested or the lease ended
// (the mapping.go phase-table invariant): probing the derived release-op name
// of a caller's own ACTIVE, never-deleted lease is the uniform 404 — an
// operation that was never started must not report as running. After DELETE
// the same name resolves.
func TestReleaseOpUnresolvableBeforeRelease(t *testing.T) {
	f := newFixture(t)
	name := f.createCVD(t, tokAlice)
	f.markAcquired(t, name, "exp-1")
	releaseOp := ReleaseOperationName(name)

	for _, p := range []struct{ method, path string }{
		{http.MethodGet, "/operations/" + releaseOp},
		{http.MethodGet, "/operations/" + releaseOp + "/result"},
		{http.MethodPost, "/operations/" + releaseOp + "/:wait"},
	} {
		code, body := f.doReq(t, p.method, p.path, tokAlice, "")
		if code != http.StatusNotFound || body != `{"error":"Operation not found"}` {
			t.Errorf("%s %s before release: got %d %s, want uniform 404", p.method, p.path, code, body)
		}
	}

	if code, _ := f.doReq(t, http.MethodDelete, "/cvds/"+name, tokAlice, ""); code != http.StatusOK {
		t.Fatalf("DELETE failed")
	}
	code, body := f.doReq(t, http.MethodGet, "/operations/"+releaseOp, tokAlice, "")
	if code != http.StatusOK || body != fmt.Sprintf(`{"name":%q,"done":false}`, releaseOp) {
		t.Errorf("release op after DELETE: got %d %s", code, body)
	}
}

// Test 25: TENANCY DELETE — bob deleting alice's group gets a 404 identical
// to nonexistent AND causes no side effect; deleting an ended lease is 404.
func TestTenancyDelete(t *testing.T) {
	f := newFixture(t)
	name := f.createCVD(t, tokAlice)
	f.markAcquired(t, name, "exp-1")

	refCode, refBody := f.doReq(t, http.MethodDelete, "/cvds/"+uuid.NewString(), tokBob, "")
	if refCode != http.StatusNotFound {
		t.Fatalf("nonexistent DELETE: got %d", refCode)
	}
	code, body := f.doReq(t, http.MethodDelete, "/cvds/"+name, tokBob, "")
	if code != refCode || body != refBody {
		t.Errorf("bob DELETE alice's: got %d %s, want identical to nonexistent (%d %s)", code, body, refCode, refBody)
	}
	if l := f.getLease(t, name); l.Spec.Release {
		t.Error("bob's DELETE must not release alice's lease")
	}

	ended := f.createCVD(t, tokAlice)
	f.markAcquired(t, ended, "exp-2")
	f.markEnded(t, ended)
	code, body = f.doReq(t, http.MethodDelete, "/cvds/"+ended, tokAlice, "")
	if code != refCode || body != refBody {
		t.Errorf("DELETE ended lease: got %d %s, want identical 404", code, body)
	}
}

// Test 26: POST /reset releases ALL and ONLY the caller's active leases; the
// reset op is done:false while releases drain, done:true after; a zero-lease
// caller is done:true immediately; a later create does not un-done it.
func TestReset(t *testing.T) {
	f := newFixture(t)
	a1 := f.createCVD(t, tokAlice)
	f.markAcquired(t, a1, "exp-1")
	a2 := f.createCVD(t, tokAlice) // pending
	b1 := f.createCVD(t, tokBob)
	f.markAcquired(t, b1, "exp-b")

	resetOp := ResetOperationName("alice")
	code, body := f.doReq(t, http.MethodPost, "/reset", tokAlice, "")
	if code != http.StatusOK || body != fmt.Sprintf(`{"name":%q,"done":false}`, resetOp) {
		t.Fatalf("POST /reset: got %d %s", code, body)
	}
	for _, n := range []string{a1, a2} {
		if l := f.getLease(t, n); !l.Spec.Release {
			t.Errorf("alice's lease %s not released by reset", n)
		}
	}
	if l := f.getLease(t, b1); l.Spec.Release {
		t.Error("reset touched bob's lease")
	}

	// Releases drain -> reset op flips done.
	f.markEnded(t, a1)
	f.markEnded(t, a2)
	code, body = f.doReq(t, http.MethodGet, "/operations/"+resetOp, tokAlice, "")
	if code != http.StatusOK || body != fmt.Sprintf(`{"name":%q,"done":true}`, resetOp) {
		t.Errorf("reset op after drain: got %d %s", code, body)
	}
	code, body = f.doReq(t, http.MethodGet, "/operations/"+resetOp+"/result", tokAlice, "")
	if code != http.StatusOK || body != `{}` {
		t.Errorf("reset result: got %d %s want {}", code, body)
	}

	// A new create does NOT flip the completed reset op back to not-done.
	_ = f.createCVD(t, tokAlice)
	code, body = f.doReq(t, http.MethodGet, "/operations/"+resetOp, tokAlice, "")
	if code != http.StatusOK || body != fmt.Sprintf(`{"name":%q,"done":true}`, resetOp) {
		t.Errorf("reset op after new create: got %d %s, must stay done", code, body)
	}

	// Zero-lease caller: done immediately.
	code, body = f.doReq(t, http.MethodPost, "/reset", tokCarol, "")
	if code != http.StatusOK || body != fmt.Sprintf(`{"name":%q,"done":true}`, ResetOperationName("carol")) {
		t.Errorf("carol POST /reset: got %d %s, want done:true", code, body)
	}
}

// Test 27: jmp parity (the VIEW invariant) — a Lease created directly on the
// cluster (as `jmp lease` would) appears in the owner's GET /cvds once
// acquired and is releasable via facade DELETE; there is no parallel booking
// state anywhere.
func TestJmpLeaseParity(t *testing.T) {
	f := newFixture(t)
	jmpName := uuid.NewString()
	lease := &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{Name: jmpName, Namespace: testNS},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			ClientRef: corev1.LocalObjectReference{Name: "alice"},
			Selector:  metav1.LabelSelector{MatchLabels: map[string]string{"anything": "else"}},
			Duration:  &metav1.Duration{Duration: time.Hour},
		},
	}
	if err := f.client.Create(context.Background(), lease); err != nil {
		t.Fatal(err)
	}
	f.markAcquired(t, jmpName, "exp-jmp")

	code, body := f.doReq(t, http.MethodGet, "/cvds", tokAlice, "")
	if code != http.StatusOK || body != cvdsBody(jmpName, "exp-jmp") {
		t.Errorf("jmp lease missing from facade listing: %d %s", code, body)
	}

	code, _ = f.doReq(t, http.MethodDelete, "/cvds/"+jmpName, tokAlice, "")
	if code != http.StatusOK {
		t.Errorf("jmp lease DELETE via facade: got %d", code)
	}
	if l := f.getLease(t, jmpName); !l.Spec.Release {
		t.Error("facade DELETE did not release the jmp lease")
	}
}

// Test 28: restart resilience — a brand-new Server over pre-existing leases
// answers operation reads with zero warm-up (fully stateless derived
// operations; upstream loses ALL ops on restart).
func TestRestartResilience(t *testing.T) {
	f := newFixture(t)
	name := f.createCVD(t, tokAlice)
	f.markAcquired(t, name, "exp-1")
	released := f.createCVD(t, tokAlice)
	f.markAcquired(t, released, "exp-2")
	if code, _ := f.doReq(t, http.MethodDelete, "/cvds/"+released, tokAlice, ""); code != http.StatusOK {
		t.Fatal("delete failed")
	}

	// Brand-new Server instance over the same backing store.
	f2 := newFixtureWithClient(t, f.client)

	code, body := f2.doReq(t, http.MethodGet, "/operations/"+name, tokAlice, "")
	if code != http.StatusOK || body != fmt.Sprintf(`{"name":%q,"done":true}`, name) {
		t.Errorf("restart GET op: got %d %s", code, body)
	}
	code, body = f2.doReq(t, http.MethodGet, "/operations/"+name+"/result", tokAlice, "")
	if code != http.StatusOK || body != cvdsBody(name, "exp-1") {
		t.Errorf("restart result: got %d %s", code, body)
	}
	code, body = f2.doReq(t, http.MethodGet, "/operations/"+ReleaseOperationName(released), tokAlice, "")
	if code != http.StatusOK || body != fmt.Sprintf(`{"name":%q,"done":false}`, ReleaseOperationName(released)) {
		t.Errorf("restart release op: got %d %s", code, body)
	}
	code, body = f2.doReq(t, http.MethodGet, "/operations/"+uuid.NewString(), tokAlice, "")
	if code != http.StatusNotFound || body != `{"error":"Operation not found"}` {
		t.Errorf("restart unknown op: got %d %s", code, body)
	}
}

// Test 29: GET /_debug/statusz is a bare unauthenticated 200; the
// out-of-scope HO surface answers 501 with the ErrorMsg shape; reserved
// UI-tier paths (deliberately unregistered) answer a JSON 404 instead.
func TestStatuszAndNotImplementedSweep(t *testing.T) {
	f := newFixture(t)

	code, body := f.doReq(t, http.MethodGet, "/_debug/statusz", "", "")
	if code != http.StatusOK || body != "" {
		t.Errorf("statusz: got %d %q, want bare 200 with empty body", code, body)
	}

	sweep := []struct{ method, path string }{
		{http.MethodPost, "/cvds/g/:stop"},
		{http.MethodPost, "/cvds/g/:start"},
		{http.MethodPost, "/cvds/g/:bugreport"},
		{http.MethodPost, "/cvds/g/n/:powerwash"},
		{http.MethodPost, "/cvds/g/n/:restart"},
		{http.MethodPost, "/cvds/g/n/snapshots"},
		{http.MethodPut, "/v1/userartifacts/x"},
		{http.MethodGet, "/cvds/g/n/logs/"},
		{http.MethodGet, "/_debug/varz"},
		{http.MethodPost, "/cvd_imgs_dirs"},
		{http.MethodDelete, "/snapshots/id1"},
		{http.MethodGet, "/cvdbugreports/u1"},
	}
	for _, p := range sweep {
		code, body := f.doReq(t, p.method, p.path, "", "")
		if code != http.StatusNotImplemented {
			t.Errorf("%s %s: got %d %s, want 501", p.method, p.path, code, body)
			continue
		}
		var em map[string]any
		if err := json.Unmarshal([]byte(body), &em); err != nil || em["error"] == "" {
			t.Errorf("%s %s: 501 body not ErrorMsg-shaped: %s", p.method, p.path, body)
		}
	}

	// Reserved (unregistered) UI-tier paths: JSON 404, not 501.
	for _, path := range []string{"/devices", "/infra_config", "/polled_connections"} {
		code, body := f.doReq(t, http.MethodGet, path, "", "")
		if code != http.StatusNotFound {
			t.Errorf("GET %s: got %d, want 404 (reserved for the UI tier)", path, code)
		}
		var em map[string]any
		if err := json.Unmarshal([]byte(body), &em); err != nil {
			t.Errorf("GET %s: 404 body not JSON: %s", path, body)
		}
	}
}

// Test 30: routing fidelity — the literal ':wait' segment matches; a
// colon-less 'wait' does not; the %-encoded form's behavior is pinned
// (net/http ServeMux decodes %3A before matching, so %3Await matches too);
// wrong method is 405.
func TestRoutingFidelity(t *testing.T) {
	f := newFixture(t)
	name := f.createCVD(t, tokAlice) // stays pending

	code, _ := f.doReq(t, http.MethodPost, "/operations/"+name+"/:wait", tokAlice, "")
	if code != http.StatusServiceUnavailable {
		t.Errorf("literal :wait on pending op: got %d, want 503", code)
	}

	code, _ = f.doReq(t, http.MethodPost, "/operations/"+name+"/wait", tokAlice, "")
	if code != http.StatusNotFound {
		t.Errorf("colon-less wait: got %d, want 404 (must not match)", code)
	}

	// Pinned behavior: ServeMux matching operates on the decoded path, so the
	// %-escaped colon still reaches the :wait route (documented divergence
	// candidate vs gorilla/mux, which matches the raw path).
	code, _ = f.doReq(t, http.MethodPost, "/operations/"+name+"/%3Await", tokAlice, "")
	if code != http.StatusServiceUnavailable {
		t.Errorf("%%3Await: got %d; ServeMux decodes before matching, expected 503", code)
	}

	code, _ = f.doReq(t, http.MethodDelete, "/operations/"+name, tokAlice, "")
	if code != http.StatusMethodNotAllowed {
		t.Errorf("wrong method: got %d, want 405", code)
	}
}

// Test 31: duck-type guard — no non-operation response body carries a
// top-level "done" key (the driver treats any dict with "done" as an
// Operation).
func TestNoDoneKeyOutsideOperations(t *testing.T) {
	f := newFixture(t)
	name := f.createCVD(t, tokAlice)
	f.markAcquired(t, name, "exp-1")

	bodies := map[string]string{}
	_, bodies["GET /cvds"] = f.doReq(t, http.MethodGet, "/cvds", tokAlice, "")
	_, bodies["GET /cvds/{group}"] = f.doReq(t, http.MethodGet, "/cvds/"+name, tokAlice, "")
	_, bodies["result"] = f.doReq(t, http.MethodGet, "/operations/"+name+"/result", tokAlice, "")
	_, bodies["404 error"] = f.doReq(t, http.MethodGet, "/operations/"+uuid.NewString(), tokAlice, "")
	_, bodies["401 error"] = f.doReq(t, http.MethodGet, "/cvds", "", "")

	for desc, body := range bodies {
		var m map[string]any
		if err := json.Unmarshal([]byte(body), &m); err != nil {
			t.Errorf("%s: body not a JSON object: %s", desc, body)
			continue
		}
		if _, ok := m["done"]; ok {
			t.Errorf("%s: body carries a top-level done key (driver would duck-type it as an Operation): %s", desc, body)
		}
	}
}

// waitOperation's GC-race handling (handlers.go): an ended lease is owned by
// its Exporter, so pool recycling can garbage-collect the Lease CR mid-wait.
// For a RELEASE op that only ever happens once the lease ended, so the vanish
// counts as done + {}; for a CREATE op the same vanish propagates the 404.
func TestWaitLeaseGCMidWait(t *testing.T) {
	deleteLeaseAfter := func(name string, d time.Duration, c kclient.Client) {
		go func() {
			time.Sleep(d)
			_ = c.Delete(context.Background(), &jumpstarterdevv1alpha1.Lease{
				ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: testNS},
			})
		}()
	}

	t.Run("release op counts as done", func(t *testing.T) {
		f := newFixture(t)
		name := f.createCVD(t, tokAlice)
		f.markAcquired(t, name, "exp-1")
		if code, _ := f.doReq(t, http.MethodDelete, "/cvds/"+name, tokAlice, ""); code != http.StatusOK {
			t.Fatal("DELETE failed")
		}
		deleteLeaseAfter(name, 50*time.Millisecond, f.client)
		code, body := f.doReq(t, http.MethodPost, "/operations/"+ReleaseOperationName(name)+"/:wait", tokAlice, "")
		if code != http.StatusOK || body != `{}` {
			t.Errorf("release :wait across GC: got %d %s, want 200 {}", code, body)
		}
	})

	t.Run("create op propagates the 404", func(t *testing.T) {
		f := newFixture(t)
		name := f.createCVD(t, tokAlice) // stays pending
		deleteLeaseAfter(name, 50*time.Millisecond, f.client)
		code, body := f.doReq(t, http.MethodPost, "/operations/"+name+"/:wait", tokAlice, "")
		if code != http.StatusNotFound || body != `{"error":"Operation not found"}` {
			t.Errorf("create :wait across GC: got %d %s, want 404 Operation not found", code, body)
		}
	})
}

// A client disconnect (request context canceled) ends the :wait long-poll
// promptly with the 503 wait-timeout shape instead of polling on to the
// deadline (handlers.go ctx.Done() arm).
func TestWaitClientDisconnect(t *testing.T) {
	f := newFixture(t)
	name := f.createCVD(t, tokAlice) // stays pending

	var caller jumpstarterdevv1alpha1.Client
	if err := f.client.Get(context.Background(), types.NamespacedName{Namespace: testNS, Name: "alice"}, &caller); err != nil {
		t.Fatal(err)
	}

	ctx, cancel := context.WithCancel(context.Background())
	go func() {
		time.Sleep(30 * time.Millisecond)
		cancel()
	}()
	req := httptest.NewRequest(http.MethodPost, "/operations/"+name+"/:wait", nil).WithContext(ctx)
	req.SetPathValue("name", name)

	start := time.Now()
	_, err := f.server.waitOperation(req, &caller)
	appErr, ok := err.(*AppError)
	if !ok || appErr.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("canceled :wait: got %v, want 503 AppError", err)
	}
	if elapsed := time.Since(start); elapsed >= f.server.WaitDuration {
		t.Errorf("canceled :wait returned after %v; must return promptly on ctx.Done, not at WaitDuration", elapsed)
	}
}

// Read-your-own-writes: when the cached client lags a just-created Lease
// (informer watch-event latency), the by-name resolvers fall back to the
// uncached APIReader instead of answering a terminal 404 — the Python driver
// calls :wait immediately after POST /cvds and does not retry 404s.
func TestCacheMissFallsBackToAPIReader(t *testing.T) {
	// Two stores sharing the Client CRs: `stale` simulates the informer cache
	// that has not yet seen the Lease; `fresh` is the API server.
	scheme := testScheme(t)
	clients := []kclient.Object{
		&jumpstarterdevv1alpha1.Client{ObjectMeta: metav1.ObjectMeta{Name: "alice", Namespace: testNS}},
	}
	stale := fake.NewClientBuilder().WithScheme(scheme).WithObjects(clients...).Build()
	fresh := fake.NewClientBuilder().WithScheme(scheme).WithObjects(clients...).Build()

	lease := &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{Name: uuid.NewString(), Namespace: testNS},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			ClientRef: corev1.LocalObjectReference{Name: "alice"},
			Selector:  metav1.LabelSelector{MatchLabels: map[string]string{"pool": "cf"}},
			Duration:  &metav1.Duration{Duration: testLease},
		},
	}
	if err := fresh.Create(context.Background(), lease); err != nil {
		t.Fatal(err)
	}

	srv := NewServer(Server{
		Client:        stale,
		APIReader:     fresh,
		Resolver:      NewStaticClientResolver(stale, testNS, map[string]string{tokAlice: "alice"}),
		Namespace:     testNS,
		PoolSelector:  &metav1.LabelSelector{MatchLabels: map[string]string{"pool": "cf"}},
		LeaseDuration: testLease,
		WaitDuration:  300 * time.Millisecond,
		PollInterval:  10 * time.Millisecond,
	})
	ts := httptest.NewServer(srv.Routes())
	t.Cleanup(ts.Close)
	f := &fixture{client: stale, server: srv, ts: ts}

	code, body := f.doReq(t, http.MethodGet, "/operations/"+lease.Name, tokAlice, "")
	if code != http.StatusOK || body != fmt.Sprintf(`{"name":%q,"done":false}`, lease.Name) {
		t.Errorf("GET op through cache miss: got %d %s, want the pending operation", code, body)
	}
	// /result distinguishes resolution from completion: through the fallback
	// the op resolves, so the pending answer is "Operation not done" — never
	// the terminal "Operation not found" the driver would give up on.
	code, body = f.doReq(t, http.MethodGet, "/operations/"+lease.Name+"/result", tokAlice, "")
	if code != http.StatusNotFound || body != `{"error":"Operation not done"}` {
		t.Errorf("result through cache miss: got %d %s, want 404 Operation not done", code, body)
	}
}

// Test 33: the real Server.Start lifecycle — binds 127.0.0.1:0, serves
// statusz, and shuts down gracefully (cmd/router metrics.go idiom).
func TestServerStartLifecycle(t *testing.T) {
	f := newFixture(t)
	addr, shutdown, err := f.server.Start("127.0.0.1:0")
	if err != nil {
		t.Fatalf("Start: %v", err)
	}
	if addr == "" || shutdown == nil {
		t.Fatal("Start returned empty addr or nil shutdown")
	}

	client := &http.Client{Timeout: 2 * time.Second}
	resp, err := client.Get("http://" + addr + "/_debug/statusz")
	if err != nil {
		t.Fatalf("GET statusz: %v", err)
	}
	_ = resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Errorf("statusz over real listener: got %d", resp.StatusCode)
	}

	ctx, cancel := context.WithTimeout(context.Background(), 2*time.Second)
	defer cancel()
	if err := shutdown(ctx); err != nil {
		t.Errorf("shutdown: %v", err)
	}
	if _, err := client.Get("http://" + addr + "/_debug/statusz"); err == nil {
		t.Error("server still serving after shutdown")
	}
}
