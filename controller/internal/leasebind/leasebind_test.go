/*
Copyright 2026.

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

package leasebind

import (
	"fmt"
	"maps"
	"reflect"
	"strings"
	"testing"
)

// free returns candidates that pass every stage, without labels.
func free(names ...string) []Candidate {
	out := make([]Candidate, 0, len(names))
	for _, name := range names {
		out = append(out, Candidate{Name: name, Stage: StageFree})
	}
	return out
}

func labelled(name string, stage Stage, kv ...string) Candidate {
	labels := map[string]string{}
	for i := 0; i+1 < len(kv); i += 2 {
		labels[kv[i]] = kv[i+1]
	}
	return Candidate{Name: name, Stage: stage, Labels: labels}
}

func expectBound(t *testing.T, result Result, want map[string]string) {
	t.Helper()
	if result.Failure != nil {
		t.Fatalf("unexpected failure: %+v", result.Failure)
	}
	if !maps.Equal(result.Bound, want) {
		t.Fatalf("bound = %v, want %v", result.Bound, want)
	}
}

func expectFailure(t *testing.T, result Result, stage Stage, reason string, roles, exporters []string) *Failure {
	t.Helper()
	f := result.Failure
	if f == nil {
		t.Fatalf("expected failure %s, got bound %v", reason, result.Bound)
	}
	if f.Stage != stage || f.Reason != reason {
		t.Fatalf("failure = stage %d reason %s (%s), want stage %d reason %s",
			f.Stage, f.Reason, f.Message, stage, reason)
	}
	if !reflect.DeepEqual(f.Roles, roles) || !reflect.DeepEqual(f.Exporters, exporters) {
		t.Fatalf("failure roles %v exporters %v, want %v %v", f.Roles, f.Exporters, roles, exporters)
	}
	return f
}

func TestTwoMembersWithTheSameCandidatesBindDifferentExporters(t *testing.T) {
	result := Solve([]Member{
		{Name: "phone", Candidates: free("px-01", "px-02")},
		{Name: "phone-2", Candidates: free("px-01", "px-02")},
	}, nil)
	expectBound(t, result, map[string]string{"phone": "px-01", "phone-2": "px-02"})
}

// Member a can use x and y, member b only x. First fit gives x to a and
// nothing to b.
func TestAugmentingPathExample(t *testing.T) {
	result := Solve([]Member{
		{Name: "a", Candidates: free("x", "y")},
		{Name: "b", Candidates: free("x")},
	}, nil)
	expectBound(t, result, map[string]string{"a": "y", "b": "x"})
}

func TestSameAsFirstFitWithoutContention(t *testing.T) {
	members := []Member{
		{Name: "a", Candidates: free("e3", "e1", "e2")},
		{Name: "opt", Optional: true, Candidates: free("e3", "e1", "e4")},
		{Name: "b", Candidates: free("e3", "e2", "e1")},
		{Name: "c", Candidates: free("e2", "e1", "e5")},
	}
	// First fit: required members in order, then optional members; each
	// takes its best free candidate.
	want := map[string]string{}
	taken := map[string]bool{}
	for _, optional := range []bool{false, true} {
		for _, m := range members {
			if m.Optional != optional {
				continue
			}
			for _, c := range m.Candidates {
				if !taken[c.Name] {
					taken[c.Name] = true
					want[m.Name] = c.Name
					break
				}
			}
		}
	}
	if len(want) != len(members) {
		t.Fatalf("the example must bind with first fit, got %v", want)
	}
	expectBound(t, Solve(members, nil), want)
}

func TestOptionalMemberDoesNotDisplaceRequiredMember(t *testing.T) {
	result := Solve([]Member{
		{Name: "opt", Optional: true, Candidates: free("x")},
		{Name: "req", Candidates: free("x")},
	}, nil)
	expectBound(t, result, map[string]string{"req": "x"})
	if got := result.Omitted["opt"].Reason; got != ReasonNotAvailable {
		t.Fatalf("omitted reason = %q, want %s", got, ReasonNotAvailable)
	}
}

func TestOptionalMemberMovesRequiredMemberToBind(t *testing.T) {
	result := Solve([]Member{
		{Name: "req", Candidates: free("x", "y")},
		{Name: "opt", Optional: true, Candidates: free("x")},
	}, nil)
	expectBound(t, result, map[string]string{"req": "y", "opt": "x"})
}

func TestOmittedOptionalMemberReportsTheStageThatRemovedItsCandidates(t *testing.T) {
	for stage, reason := range map[Stage]string{
		StageApproved:  ReasonOffline,
		StageOnline:    ReasonNotAvailable,
		StageUnclaimed: ReasonNotReady,
		StageReady:     ReasonNotAvailable,
	} {
		result := Solve([]Member{
			{Name: "req", Candidates: free("x")},
			{Name: "opt", Optional: true, Candidates: []Candidate{{Name: "y", Stage: stage}}},
		}, nil)
		expectBound(t, result, map[string]string{"req": "x"})
		if got := result.Omitted["opt"].Reason; got != reason {
			t.Errorf("candidate at stage %d: omitted reason = %q, want %s", stage, got, reason)
		}
	}
}

func TestFailureClassificationByStage(t *testing.T) {
	cases := []struct {
		// stage of the second exporter; the first passes every stage
		second Stage
		stage  Stage
		reason string
	}{
		{StageApproved, StageOnline, ReasonOffline},
		{StageOnline, StageUnclaimed, ReasonNotAvailable},
		{StageUnclaimed, StageReady, ReasonNotReady},
		{StageReady, StageFree, ReasonNotAvailable},
	}
	for _, tc := range cases {
		candidates := []Candidate{{Name: "px-01", Stage: StageFree}, {Name: "px-02", Stage: tc.second}}
		result := Solve([]Member{
			{Name: "phone", Candidates: candidates},
			{Name: "phone-2", Candidates: candidates},
		}, nil)
		f := expectFailure(t, result, tc.stage, tc.reason, []string{"phone", "phone-2"}, []string{"px-01"})
		if f.Unsatisfiable() {
			t.Errorf("stage %d must not be unsatisfiable", tc.stage)
		}
	}
}

func TestInsufficientExporters(t *testing.T) {
	result := Solve([]Member{
		{Name: "headunit", Candidates: free("hu-01")},
		{Name: "phone", Candidates: free("px-01")},
		{Name: "phone-2", Candidates: free("px-01")},
	}, nil)
	f := expectFailure(t, result, StageApproved, ReasonInsufficientExporters,
		[]string{"phone", "phone-2"}, []string{"px-01"})
	if !f.Unsatisfiable() {
		t.Fatal("too few exporters in the first stage must be unsatisfiable")
	}
	want := "roles `phone`, `phone-2` must have 2 different exporters; only 1 is available: `px-01`"
	if f.Message != want {
		t.Fatalf("message = %q, want %q", f.Message, want)
	}
}

func TestConstraintSelectsASharedValue(t *testing.T) {
	members := []Member{
		{Name: "ecu", Candidates: []Candidate{
			labelled("ecu-1", StageFree, "bus", "can-1"),
			labelled("ecu-2", StageFree, "bus", "can-2"),
		}},
		{Name: "probe", Candidates: []Candidate{
			labelled("probe-2", StageFree, "bus", "can-2"),
		}},
	}
	result := Solve(members, []Constraint{{Members: []string{"ecu", "probe"}, SameLabel: "bus"}})
	expectBound(t, result, map[string]string{"ecu": "ecu-2", "probe": "probe-2"})
}

func TestExporterWithoutTheLabelDoesNotSatisfyAConstraint(t *testing.T) {
	members := []Member{
		{Name: "ecu", Candidates: []Candidate{
			labelled("ecu-0", StageFree),
			labelled("ecu-1", StageFree, "bus", "can-1"),
		}},
		{Name: "probe", Candidates: []Candidate{
			labelled("probe-0", StageFree),
			labelled("probe-1", StageFree, "bus", "can-1"),
		}},
	}
	result := Solve(members, []Constraint{{Members: []string{"ecu", "probe"}, SameLabel: "bus"}})
	expectBound(t, result, map[string]string{"ecu": "ecu-1", "probe": "probe-1"})
}

func TestConstraintIgnoresAnOptionalMemberWithoutExporter(t *testing.T) {
	members := []Member{
		{Name: "ecu", Candidates: []Candidate{labelled("ecu-1", StageFree, "bus", "can-1")}},
		{Name: "probe", Candidates: []Candidate{labelled("probe-1", StageFree, "bus", "can-1")}},
		{Name: "logger", Optional: true, Candidates: []Candidate{labelled("logger-2", StageFree, "bus", "can-2")}},
	}
	result := Solve(members, []Constraint{{Members: []string{"ecu", "probe", "logger"}, SameLabel: "bus"}})
	expectBound(t, result, map[string]string{"ecu": "ecu-1", "probe": "probe-1"})
	if got := result.Omitted["logger"].Reason; got != ReasonConstraintUnsatisfiable {
		t.Fatalf("omitted reason = %q, want %s", got, ReasonConstraintUnsatisfiable)
	}
}

func TestOptionalMemberThatBindsSatisfiesTheConstraint(t *testing.T) {
	members := []Member{
		{Name: "ecu", Candidates: []Candidate{
			labelled("ecu-1", StageFree, "bus", "can-1"),
			labelled("ecu-2", StageFree, "bus", "can-2"),
		}},
		{Name: "logger", Optional: true, Candidates: []Candidate{labelled("logger-1", StageFree, "bus", "can-1")}},
	}
	constraints := []Constraint{{Members: []string{"ecu", "logger"}, SameLabel: "bus"}}
	expectBound(t, Solve(members, constraints), map[string]string{"ecu": "ecu-1", "logger": "logger-1"})
}

// With one required member a constraint has no effect when the optional
// member cannot bind: the required member can use an exporter without the label.
func TestConstraintWithOneBoundMemberHasNoEffect(t *testing.T) {
	members := []Member{
		{Name: "ecu", Candidates: []Candidate{labelled("ecu-0", StageFree)}},
		{Name: "logger", Optional: true, Candidates: []Candidate{labelled("logger-1", StageFree, "bus", "can-1")}},
	}
	result := Solve(members, []Constraint{{Members: []string{"ecu", "logger"}, SameLabel: "bus"}})
	expectBound(t, result, map[string]string{"ecu": "ecu-0"})
	if _, omitted := result.Omitted["logger"]; !omitted {
		t.Fatal("logger must be omitted")
	}
}

func TestConstraintUnsatisfiable(t *testing.T) {
	members := []Member{
		{Name: "ecu", Candidates: []Candidate{labelled("ecu-1", StageFree, "bus", "can-1")}},
		{Name: "probe", Candidates: []Candidate{labelled("probe-2", StageFree, "bus", "can-2")}},
	}
	result := Solve(members, []Constraint{{Members: []string{"ecu", "probe"}, SameLabel: "bus"}})
	f := expectFailure(t, result, StageApproved, ReasonConstraintUnsatisfiable, nil, nil)
	if !f.Unsatisfiable() {
		t.Fatal("must be unsatisfiable")
	}
	for _, want := range []string{"`bus`", "`ecu`, `probe`", "`can-1`, `can-2`"} {
		if !strings.Contains(f.Message, want) {
			t.Errorf("message %q does not contain %s", f.Message, want)
		}
	}
}

// The bench that satisfies the constraint is offline: the lease waits.
func TestConstraintFailsAtALaterStage(t *testing.T) {
	members := []Member{
		{Name: "ecu", Candidates: []Candidate{
			labelled("ecu-1", StageFree, "bus", "can-1"),
			labelled("ecu-2", StageApproved, "bus", "can-2"),
		}},
		{Name: "probe", Candidates: []Candidate{labelled("probe-2", StageFree, "bus", "can-2")}},
	}
	result := Solve(members, []Constraint{{Members: []string{"ecu", "probe"}, SameLabel: "bus"}})
	expectFailure(t, result, StageOnline, ReasonOffline, nil, nil)
}

func TestConstraintsWithTheSameKeyAndMemberAreJoined(t *testing.T) {
	bench := func(role string) []Candidate {
		return []Candidate{
			labelled(role+"-1", StageFree, "bus", "can-1"),
			labelled(role+"-2", StageFree, "bus", "can-2"),
		}
	}
	members := []Member{
		{Name: "a", Candidates: bench("a")},
		{Name: "b", Candidates: bench("b")},
		{Name: "c", Candidates: []Candidate{labelled("c-2", StageFree, "bus", "can-2")}},
	}
	constraints := []Constraint{
		{Members: []string{"a", "b"}, SameLabel: "bus"},
		{Members: []string{"b", "c"}, SameLabel: "bus"},
	}
	p := newProblem(members, constraints)
	if len(p.joined) != 1 || len(p.joined[0].members) != 3 {
		t.Fatalf("joined = %+v, want one constraint with three members", p.joined)
	}
	expectBound(t, Solve(members, constraints), map[string]string{"a": "a-2", "b": "b-2", "c": "c-2"})
}

// benches gives each role one exporter in each of n racks; rack i is on bus i.
func benches(n int, stageOf func(rack int) Stage, roles ...string) []Member {
	members := make([]Member, 0, len(roles))
	for _, role := range roles {
		m := Member{Name: role}
		for i := range n {
			m.Candidates = append(m.Candidates, labelled(
				fmt.Sprintf("%s-%03d", role, i), stageOf(i),
				"rack", fmt.Sprintf("rack-%03d", i), "bus", fmt.Sprintf("bus-%03d", i)))
		}
		members = append(members, m)
	}
	return members
}

// 40 racks and 40 buses are 1,600 combinations of values, but only 40 exist
// on a bench. The only free bench is the last one.
func TestTwoConstraintsWithManyBenchesStayBelowTheLimit(t *testing.T) {
	members := benches(40, func(rack int) Stage {
		if rack == 39 {
			return StageFree
		}
		return StageOnline
	}, "a", "b", "c")
	constraints := []Constraint{
		{Members: []string{"a", "b"}, SameLabel: "rack"},
		{Members: []string{"b", "c"}, SameLabel: "bus"},
	}
	expectBound(t, Solve(members, constraints), map[string]string{"a": "a-039", "b": "b-039", "c": "c-039"})
}

// Two constraints without a common member: every pair of a rack and a bus is
// possible for the candidates, and none assigns, because roles c and d share
// one exporter on each bus.
func TestConstraintLimit(t *testing.T) {
	members := benches(40, func(int) Stage { return StageFree }, "a", "b", "c")
	members = append(members, Member{Name: "d", Candidates: members[2].Candidates})
	constraints := []Constraint{
		{Members: []string{"a", "b"}, SameLabel: "rack"},
		{Members: []string{"c", "d"}, SameLabel: "bus"},
	}
	result := Solve(members, constraints)
	f := expectFailure(t, result, StageApproved, ReasonConstraintLimit, nil, nil)
	if !f.Unsatisfiable() {
		t.Fatal("the limit must be unsatisfiable")
	}
}

func TestConstraintOnMembersThatAreNotInTheProblemIsIgnored(t *testing.T) {
	members := []Member{{Name: "ecu", Candidates: free("ecu-0")}}
	result := Solve(members, []Constraint{{Members: []string{"ecu", "excluded"}, SameLabel: "bus"}})
	expectBound(t, result, map[string]string{"ecu": "ecu-0"})
}

func TestResultIsDeterministic(t *testing.T) {
	members := benches(6, func(int) Stage { return StageFree }, "a", "b")
	members[1].Candidates = members[0].Candidates
	constraints := []Constraint{{Members: []string{"a", "b"}, SameLabel: "rack"}}
	first := Solve(members, constraints)
	for range 20 {
		if again := Solve(members, constraints); !reflect.DeepEqual(first, again) {
			t.Fatalf("results differ: %+v and %+v", first, again)
		}
	}
}
