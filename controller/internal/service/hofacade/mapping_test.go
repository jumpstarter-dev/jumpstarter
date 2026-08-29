package hofacade

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/google/uuid"
	corev1 "k8s.io/api/core/v1"
	metav1 "k8s.io/apimachinery/pkg/apis/meta/v1"

	jumpstarterdevv1alpha1 "github.com/jumpstarter-dev/jumpstarter/controller/api/v1alpha1"
)

func freshLease(name string) *jumpstarterdevv1alpha1.Lease {
	return &jumpstarterdevv1alpha1.Lease{
		ObjectMeta: metav1.ObjectMeta{Name: name, Namespace: "testns"},
		Spec: jumpstarterdevv1alpha1.LeaseSpec{
			ClientRef: corev1.LocalObjectReference{Name: "alice"},
			Selector:  metav1.LabelSelector{MatchLabels: map[string]string{"pool": "cf"}},
			Duration:  &metav1.Duration{Duration: 30 * time.Minute},
		},
	}
}

func acquire(l *jumpstarterdevv1alpha1.Lease, exporter string) *jumpstarterdevv1alpha1.Lease {
	now := metav1.Now()
	l.Status.ExporterRef = &corev1.LocalObjectReference{Name: exporter}
	l.Status.BeginTime = &now
	l.SetStatusReady(true, "Ready", "An exporter has been acquired for the client")
	return l
}

func release(l *jumpstarterdevv1alpha1.Lease) *jumpstarterdevv1alpha1.Lease {
	l.Spec.Release = true
	return l
}

func end(l *jumpstarterdevv1alpha1.Lease) *jumpstarterdevv1alpha1.Lease {
	// Mirrors (*Lease).Release in api/v1alpha1/lease_helpers.go:410-416.
	now := metav1.Now()
	l.SetStatusReady(false, "Released", "The lease was marked for release")
	l.Status.Ended = true
	l.Status.EndTime = &now
	return l
}

// Test 8: fresh leases and Pending/NotAvailable (pool-exhausted) leases map to
// a create operation with done:false — pending is never an error.
func TestCreateOperationPending(t *testing.T) {
	l := freshLease("lease-1")
	op := CreateOperation(l)
	if op.Name != "lease-1" || op.Done {
		t.Errorf("fresh lease: got %+v, want name lease-1 done false", op)
	}
	if v, apperr := CreateResult(l); v != nil || apperr != nil {
		t.Errorf("fresh lease result: got (%v, %v), want (nil, nil)", v, apperr)
	}

	// Pool exhausted: reconciler sets Pending=True reason NotAvailable.
	l2 := freshLease("lease-2")
	l2.SetStatusPending("NotAvailable", "All exporters are leased")
	op = CreateOperation(l2)
	if op.Done {
		t.Errorf("pool-exhausted lease: got done true, want false (pending is not an error)")
	}
	if v, apperr := CreateResult(l2); v != nil || apperr != nil {
		t.Errorf("pool-exhausted result: got (%v, %v), want (nil, nil)", v, apperr)
	}
}

// Test 9: Ready=True with exporterRef means create-op done:true and
// CreateResult renders {"cvds":[{group:leaseName,name:exporterName,
// status:"Running",...}]} (stable group/name mapping, requirement F6).
func TestCreateOperationReady(t *testing.T) {
	l := acquire(freshLease("lease-1"), "exp-1")
	op := CreateOperation(l)
	if !op.Done {
		t.Fatalf("acquired lease: got done false, want true")
	}
	v, apperr := CreateResult(l)
	if apperr != nil {
		t.Fatalf("result: unexpected error %v", apperr)
	}
	b, err := json.Marshal(v)
	if err != nil {
		t.Fatal(err)
	}
	want := `{"cvds":[{"group":"lease-1","name":"exp-1","status":"Running","displays":[],"webrtc_device_id":"","adb_serial":"","adb_port":0}]}`
	if string(b) != want {
		t.Errorf("result:\n got  %s\n want %s", b, want)
	}
}

// Test 10: Unsatisfiable(NoAccess) and Invalid(InvalidSelector) leases are
// driver-terminal: op done:true and CreateResult a 500 AppError carrying the
// condition reason and message (async policy denial surfaces here).
// The reconciler also marks such leases Ended (lease_controller.go:161-165),
// so the failed conditions must outrank Ended in the phase ladder.
func TestCreateResultTerminalConditions(t *testing.T) {
	now := metav1.Now()

	uns := freshLease("lease-u")
	uns.SetStatusUnsatisfiable("NoAccess", "no policy allows this client")
	uns.Status.Ended = true
	uns.Status.EndTime = &now
	if op := CreateOperation(uns); !op.Done {
		t.Error("unsatisfiable lease: op should be done")
	}
	v, apperr := CreateResult(uns)
	if v != nil || apperr == nil {
		t.Fatalf("unsatisfiable result: got (%v, %v), want AppError", v, apperr)
	}
	if apperr.StatusCode != 500 {
		t.Errorf("unsatisfiable result status: got %d want 500", apperr.StatusCode)
	}
	if apperr.Msg != "NoAccess" {
		t.Errorf("unsatisfiable result msg: got %q want NoAccess", apperr.Msg)
	}
	if got := apperr.Error(); got != "NoAccess: no policy allows this client" {
		t.Errorf("unsatisfiable result details: got %q", got)
	}

	inv := freshLease("lease-i")
	inv.SetStatusInvalid("InvalidSelector", "empty selector")
	inv.Status.Ended = true
	inv.Status.EndTime = &now
	if op := CreateOperation(inv); !op.Done {
		t.Error("invalid lease: op should be done")
	}
	v, apperr = CreateResult(inv)
	if v != nil || apperr == nil {
		t.Fatalf("invalid result: got (%v, %v), want AppError", v, apperr)
	}
	if apperr.StatusCode != 500 || apperr.Msg != "InvalidSelector" {
		t.Errorf("invalid result: got status %d msg %q", apperr.StatusCode, apperr.Msg)
	}
}

// Test 11: release-in-flight leases keep create-op done:true and a renderable
// CreateResult (wasAcquired), while the release op is done:false until the
// lease actually ends; a lease ended before acquisition yields a 500 result.
func TestReleaseOperationPhases(t *testing.T) {
	// Release requested, not yet ended.
	l := release(acquire(freshLease("lease-1"), "exp-1"))
	if op := ReleaseOperation(l); op.Done {
		t.Error("release-in-flight: release op should not be done")
	}
	if op := CreateOperation(l); !op.Done {
		t.Error("release-in-flight: create op should stay done")
	}
	if v, apperr := CreateResult(l); v == nil || apperr != nil {
		t.Errorf("release-in-flight: CreateResult should still render cvds, got (%v, %v)", v, apperr)
	}

	// Ended after acquisition.
	l = end(release(acquire(freshLease("lease-2"), "exp-2")))
	if op := ReleaseOperation(l); !op.Done {
		t.Error("ended lease: release op should be done")
	}
	if v, apperr := CreateResult(l); v == nil || apperr != nil {
		t.Errorf("ended-after-acquire: CreateResult should render cvds, got (%v, %v)", v, apperr)
	}

	// Ended before acquisition (e.g. released while still queued).
	l = end(release(freshLease("lease-3")))
	if op := CreateOperation(l); !op.Done {
		t.Error("ended-before-acquire: create op should be done")
	}
	v, apperr := CreateResult(l)
	if v != nil || apperr == nil || apperr.StatusCode != 500 {
		t.Errorf("ended-before-acquire: want 500 AppError, got (%v, %v)", v, apperr)
	}
}

// Test 12: derived operation names are deterministic, distinct across inputs,
// and parse as bare UUIDs (wire-identical to upstream uuid.New().String()).
func TestDerivedOperationNames(t *testing.T) {
	r1 := ReleaseOperationName("lease-1")
	r1again := ReleaseOperationName("lease-1")
	r2 := ReleaseOperationName("lease-2")
	reset1 := ResetOperationName("alice")
	reset2 := ResetOperationName("bob")

	if r1 != r1again {
		t.Errorf("ReleaseOperationName not deterministic: %s vs %s", r1, r1again)
	}
	if r1 == r2 {
		t.Errorf("ReleaseOperationName collision across leases: %s", r1)
	}
	if reset1 == reset2 {
		t.Errorf("ResetOperationName collision across clients: %s", reset1)
	}
	if r1 == reset1 {
		t.Errorf("release and reset name spaces collide: %s", r1)
	}
	for _, name := range []string{r1, r2, reset1, reset2} {
		if _, err := uuid.Parse(name); err != nil {
			t.Errorf("derived op name %q is not a bare UUID: %v", name, err)
		}
	}
	// A lease named like another lease's name+"/release" input must not collide
	// with the create-op namespace: derived names are UUIDs, lease names are
	// UUIDv7 — just assert the release name differs from its lease name.
	if ReleaseOperationName(r1) == r1 {
		t.Error("release op name equals its input")
	}
}

// Golden cross-version pin: derived operation names must stay stable across
// facade versions and restarts (in-flight clients keep polling them through a
// rolling upgrade), so facadeUUIDNamespace and the "/release"/"/reset" suffix
// inputs may NEVER change. These literals were computed once from
// uuid.NewSHA1(6a1e57a4-9c2d-4d0b-8f3e-2b7c1d9e5f60, input); any change to
// the namespace UUID or the suffixes fails this test.
func TestDerivedOperationNamesGolden(t *testing.T) {
	if got, want := ReleaseOperationName("lease-1"), "6f5f8f24-7028-5f16-949c-fdb4ad50976f"; got != want {
		t.Errorf("ReleaseOperationName(\"lease-1\"): got %s want %s — the derived-name scheme MUST NOT change across versions", got, want)
	}
	if got, want := ResetOperationName("alice"), "7852b330-ba0b-51fd-973e-33eea171856b"; got != want {
		t.Errorf("ResetOperationName(\"alice\"): got %s want %s — the derived-name scheme MUST NOT change across versions", got, want)
	}
}

// Test 13: CVDFromLease v0 placeholders pinned — displays [], webrtc_device_id
// "", adb_serial "", adb_port 0 (no per-device proxying in v0, F14); status is
// "Starting" until Ready=True.
func TestCVDFromLeasePlaceholders(t *testing.T) {
	l := freshLease("lease-1")
	now := metav1.Now()
	l.Status.ExporterRef = &corev1.LocalObjectReference{Name: "exp-9"}
	l.Status.BeginTime = &now
	// Not Ready yet.
	cvd := CVDFromLease(l)
	if cvd.Status != "Starting" {
		t.Errorf("pre-Ready status: got %q want Starting", cvd.Status)
	}

	acquire(l, "exp-9")
	cvd = CVDFromLease(l)
	if cvd.Group != "lease-1" || cvd.Name != "exp-9" {
		t.Errorf("group/name: got %s/%s want lease-1/exp-9", cvd.Group, cvd.Name)
	}
	if cvd.Status != "Running" {
		t.Errorf("status: got %q want Running", cvd.Status)
	}
	if cvd.Displays == nil || len(cvd.Displays) != 0 {
		t.Errorf("displays: got %#v want empty non-nil slice", cvd.Displays)
	}
	if cvd.WebRTCDeviceID != "" || cvd.ADBSerial != "" || cvd.ADBPort != 0 {
		t.Errorf("v0 placeholders violated: %+v", cvd)
	}
}
