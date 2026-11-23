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

// Package leasebind assigns exporters to the members of a lease. It is pure:
// the lease controller resolves the candidates of each member and the filter
// stage that each candidate reaches, and this package decides which member
// gets which exporter, or why no assignment exists.
package leasebind

import (
	"fmt"
	"slices"
	"strings"
)

// Stage is a filter stage of the bind procedure. Each stage keeps a subset of
// the candidates of the stage before it.
type Stage int

const (
	// StageApproved: selected, enabled, and approved by policy.
	StageApproved Stage = iota
	// StageOnline: also registered and online.
	StageOnline
	// StageUnclaimed: also not claimed by a different active lease or its
	// exclusion group.
	StageUnclaimed
	// StageReady: also Available and not in cleanup.
	StageReady
	// StageFree: also not held with spot access. Only these can be bound.
	StageFree
)

// Reasons of a failure and of an omitted optional member.
const (
	ReasonInsufficientExporters   = "InsufficientExporters"
	ReasonConstraintUnsatisfiable = "ConstraintUnsatisfiable"
	ReasonConstraintLimit         = "ConstraintLimit"
	ReasonOffline                 = "Offline"
	ReasonNotAvailable            = "NotAvailable"
	ReasonNotReady                = "NotReady"
)

// CombinationLimit is the number of combinations of constraint values that a
// search tries before it stops.
const CombinationLimit = 1024

// Candidate is an exporter that a member can use.
type Candidate struct {
	Name   string
	Labels map[string]string
	// Stage is the last stage whose filter the exporter passes.
	Stage Stage
}

// Member is one role of the lease with its candidates in preference order.
// The order must be total, so that the result is always the same.
type Member struct {
	Name       string
	Optional   bool
	Candidates []Candidate
}

// Constraint requires the exporters of its members to have the same value of
// a label.
type Constraint struct {
	Members   []string
	SameLabel string
}

// Omission says why an optional member got no exporter.
type Omission struct {
	Reason  string
	Message string
}

// Failure says why the required members cannot be assigned.
type Failure struct {
	// Stage is the first filter stage whose candidates admit no assignment.
	Stage  Stage
	Reason string
	// Roles are the members that compete for Exporters. Both are empty when
	// only the constraints fail.
	Roles     []string
	Exporters []string
	Message   string
}

// Unsatisfiable reports whether waiting cannot help: the failure is in the
// first stage, or the search was stopped at its limit.
func (f *Failure) Unsatisfiable() bool {
	return f.Stage == StageApproved || f.Reason == ReasonConstraintLimit
}

// Result of Solve. Either Failure is set, or Bound has an exporter for every
// required member and Omitted has every optional member that got none.
type Result struct {
	Bound   map[string]string
	Omitted map[string]Omission
	Failure *Failure
}

func stageReason(stage Stage) string {
	switch stage {
	case StageOnline:
		return ReasonOffline
	case StageReady:
		return ReasonNotReady
	case StageUnclaimed, StageFree:
		return ReasonNotAvailable
	default:
		return ReasonInsufficientExporters
	}
}

func stageAdjective(stage Stage) string {
	switch stage {
	case StageOnline:
		return "online"
	case StageUnclaimed:
		return "not leased"
	case StageReady:
		return "ready"
	case StageFree:
		return "free of spot access leases"
	default:
		return "available"
	}
}

// joined is a set of constraints with the same label key that share members.
type joined struct {
	key string
	// members are indexes into problem.members, in spec order.
	members  []int
	required int
	// values are the values of the key among the first-stage candidates of
	// the members, sorted.
	values []string
}

// choice is the value selected for a joined constraint. A constraint with
// fewer than two required members can also have no effect: then at most one
// of its members binds.
type choice struct {
	value    string
	noEffect bool
}

type problem struct {
	members []Member
	joined  []joined
	// constraintsOf lists, for each member, the indexes of its joined constraints.
	constraintsOf [][]int
}

func newProblem(members []Member, constraints []Constraint) *problem {
	p := &problem{members: members, constraintsOf: make([][]int, len(members))}
	index := map[string]int{}
	for i, m := range members {
		index[m.Name] = i
	}

	// Join the constraints that have the same key and a common member.
	type group struct {
		key     string
		members map[int]bool
	}
	var groups []*group
	for _, c := range constraints {
		current := &group{key: c.SameLabel, members: map[int]bool{}}
		for _, name := range c.Members {
			if i, ok := index[name]; ok {
				current.members[i] = true
			}
		}
		// Merge into the earliest group that it joins, to keep the spec
		// order; the new constraint can also connect earlier groups.
		var target *group
		kept := make([]*group, 0, len(groups)+1)
		for _, g := range groups {
			if g.key != current.key || !sharesMember(g.members, current.members) {
				kept = append(kept, g)
				continue
			}
			if target == nil {
				target = g
				kept = append(kept, g)
			}
			for i := range g.members {
				current.members[i] = true
			}
		}
		if target == nil {
			kept = append(kept, current)
		} else {
			target.members = current.members
		}
		groups = kept
	}

	for _, g := range groups {
		// A constraint needs two members that can bind.
		if len(g.members) < 2 {
			continue
		}
		j := joined{key: g.key}
		values := map[string]bool{}
		for i := range members {
			if !g.members[i] {
				continue
			}
			j.members = append(j.members, i)
			if !members[i].Optional {
				j.required++
			}
			for _, c := range members[i].Candidates {
				if v, ok := c.Labels[g.key]; ok {
					values[v] = true
				}
			}
		}
		for v := range values {
			j.values = append(j.values, v)
		}
		slices.Sort(j.values)
		for _, i := range j.members {
			p.constraintsOf[i] = append(p.constraintsOf[i], len(p.joined))
		}
		p.joined = append(p.joined, j)
	}
	return p
}

func sharesMember(a, b map[int]bool) bool {
	for i := range a {
		if b[i] {
			return true
		}
	}
	return false
}

// allowed reports whether member m can use candidate c at this stage with the
// values selected so far. chosen has one entry for each joined constraint that
// has a selection; later constraints are not applied.
func (p *problem) allowed(m int, c *Candidate, stage Stage, chosen []choice) bool {
	if c.Stage < stage {
		return false
	}
	for _, j := range p.constraintsOf[m] {
		if j >= len(chosen) {
			continue
		}
		if chosen[j].noEffect {
			if !p.keptWithoutEffect(m, j) {
				return false
			}
			continue
		}
		if v, ok := c.Labels[p.joined[j].key]; !ok || v != chosen[j].value {
			return false
		}
	}
	return true
}

// keptWithoutEffect reports whether member m can still bind when joined
// constraint j has no effect, which lets only one of its members bind: its
// required member, or else its first optional member.
func (p *problem) keptWithoutEffect(m, j int) bool {
	if !p.members[m].Optional {
		return true
	}
	return p.joined[j].required == 0 && p.joined[j].members[0] == m
}

func (p *problem) hasCandidate(m int, stage Stage, chosen []choice) bool {
	for i := range p.members[m].Candidates {
		if p.allowed(m, &p.members[m].Candidates[i], stage, chosen) {
			return true
		}
	}
	return false
}

// matching is an assignment of exporters to members.
type matching struct {
	exporterOf map[int]string
	memberOf   map[string]int
}

func newMatching() *matching {
	return &matching{exporterOf: map[int]string{}, memberOf: map[string]int{}}
}

// augment gives member m an exporter. It takes the best free candidate of m;
// when none is free, it moves members that have an exporter to other
// candidates of theirs along the shortest path that frees one. A member that
// has an exporter keeps one. On failure it returns the members and the
// exporters that the search visited: those members compete for those exporters.
func (p *problem) augment(
	mt *matching, m int, stage Stage, chosen []choice,
) (ok bool, members []int, exporters []string) {
	reachedFrom := map[string]int{}
	queue := []int{m}
	members = []int{m}
	for len(queue) > 0 {
		u := queue[0]
		queue = queue[1:]
		for i := range p.members[u].Candidates {
			c := &p.members[u].Candidates[i]
			if _, seen := reachedFrom[c.Name]; seen || !p.allowed(u, c, stage, chosen) {
				continue
			}
			reachedFrom[c.Name] = u
			exporters = append(exporters, c.Name)
			owner, taken := mt.memberOf[c.Name]
			if taken {
				queue = append(queue, owner)
				members = append(members, owner)
				continue
			}
			// Free exporter: move each member on the path to the exporter
			// that it reached.
			exporter := c.Name
			for {
				member := reachedFrom[exporter]
				previous, had := mt.exporterOf[member]
				mt.exporterOf[member] = exporter
				mt.memberOf[exporter] = member
				if member == m || !had {
					break
				}
				exporter = previous
			}
			return true, nil, nil
		}
	}
	slices.Sort(members)
	slices.Sort(exporters)
	return false, members, exporters
}

// assignRequired assigns the required members in spec order.
func (p *problem) assignRequired(stage Stage, chosen []choice) (mt *matching, members []int, exporters []string) {
	mt = newMatching()
	for m := range p.members {
		if p.members[m].Optional {
			continue
		}
		if ok, members, exporters := p.augment(mt, m, stage, chosen); !ok {
			return nil, members, exporters
		}
	}
	return mt, nil, nil
}

// search selects one value for each joined constraint in sequence, in sorted
// order, and returns the first complete combination that assigns all the
// required members. limit is set when it stopped at CombinationLimit.
func (p *problem) search(stage Stage) (mt *matching, chosen []choice, limit bool) {
	tried := 0
	var walk func(chosen []choice) (*matching, []choice)
	walk = func(chosen []choice) (*matching, []choice) {
		j := len(chosen)
		if j == len(p.joined) {
			tried++
			if tried > CombinationLimit {
				limit = true
				return nil, nil
			}
			if mt, _, _ := p.assignRequired(stage, chosen); mt != nil {
				return mt, slices.Clone(chosen)
			}
			return nil, nil
		}
		options := make([]choice, 0, len(p.joined[j].values)+1)
		for _, v := range p.joined[j].values {
			options = append(options, choice{value: v})
		}
		if p.joined[j].required < 2 {
			options = append(options, choice{noEffect: true})
		}
		for _, option := range options {
			next := append(chosen, option)
			// Skip a value that leaves a required member of this constraint
			// without a candidate, together with the values selected before.
			if !p.requiredHaveCandidates(j, stage, next) {
				continue
			}
			if mt, result := walk(next); mt != nil || limit {
				return mt, result
			}
		}
		return nil, nil
	}
	mt, chosen = walk(make([]choice, 0, len(p.joined)))
	return mt, chosen, limit
}

func (p *problem) requiredHaveCandidates(j int, stage Stage, chosen []choice) bool {
	for _, m := range p.joined[j].members {
		if !p.members[m].Optional && !p.hasCandidate(m, stage, chosen) {
			return false
		}
	}
	return true
}

// Solve assigns an exporter to each member. Required members are assigned
// first, in the order given, then as many optional members as possible. When
// the required members cannot be assigned from the StageFree candidates, the
// failure names the first stage that admits no assignment.
func Solve(members []Member, constraints []Constraint) Result {
	p := newProblem(members, constraints)

	if mt, chosen, _ := p.search(StageFree); mt != nil {
		result := Result{Bound: map[string]string{}, Omitted: map[string]Omission{}}
		for m := range p.members {
			if !p.members[m].Optional {
				continue
			}
			if ok, _, _ := p.augment(mt, m, StageFree, chosen); !ok {
				result.Omitted[p.members[m].Name] = p.omission(m, chosen)
			}
		}
		for m, exporter := range mt.exporterOf {
			result.Bound[p.members[m].Name] = exporter
		}
		return result
	}

	for stage := StageApproved; stage <= StageFree; stage++ {
		mt, _, limit := p.search(stage)
		if limit {
			return Result{Failure: &Failure{
				Stage:  stage,
				Reason: ReasonConstraintLimit,
				Message: fmt.Sprintf(
					"the constraint search stopped after %d combinations of label values", CombinationLimit),
			}}
		}
		if mt == nil {
			return Result{Failure: p.classify(stage)}
		}
	}
	// Not reachable: the StageFree search above failed without the limit.
	return Result{Failure: p.classify(StageFree)}
}

// classify describes why no assignment exists at this stage.
func (p *problem) classify(stage Stage) *Failure {
	f := &Failure{Stage: stage, Reason: stageReason(stage)}
	if mt, members, exporters := p.assignRequired(stage, nil); mt == nil {
		for _, m := range members {
			f.Roles = append(f.Roles, p.members[m].Name)
		}
		f.Exporters = exporters
		f.Message = fmt.Sprintf("roles %s must have %d different exporters; %s",
			quoteList(f.Roles), len(f.Roles), onlyAvailable(exporters, stage))
		return f
	}

	// An assignment exists without the constraints but not with them.
	if stage == StageApproved {
		f.Reason = ReasonConstraintUnsatisfiable
	}
	var parts []string
	for _, j := range p.joined {
		var roles []string
		for _, m := range j.members {
			roles = append(roles, p.members[m].Name)
		}
		values := "no exporter has the label"
		if len(j.values) > 0 {
			values = "values " + quoteList(j.values)
		}
		parts = append(parts, fmt.Sprintf("the same %s for roles %s (%s)", quote(j.key), quoteList(roles), values))
	}
	f.Message = fmt.Sprintf("the exporters that are %s cannot satisfy the constraints: %s",
		stageAdjective(stage), strings.Join(parts, "; "))
	return f
}

// omission gives the reason of the first stage that leaves optional member m
// without a candidate, for the values that the result uses.
func (p *problem) omission(m int, chosen []choice) Omission {
	for stage := StageApproved; stage <= StageFree; stage++ {
		if p.hasCandidate(m, stage, chosen) {
			continue
		}
		if stage == StageApproved {
			return Omission{
				Reason:  ReasonConstraintUnsatisfiable,
				Message: "no exporter of the role satisfies the constraints with the other roles",
			}
		}
		return Omission{
			Reason:  stageReason(stage),
			Message: fmt.Sprintf("no exporter of the role is %s", stageAdjective(stage)),
		}
	}
	return Omission{
		Reason:  ReasonNotAvailable,
		Message: "the other roles of the lease use the exporters of the role",
	}
}

func onlyAvailable(exporters []string, stage Stage) string {
	switch len(exporters) {
	case 0:
		return fmt.Sprintf("none is %s", stageAdjective(stage))
	case 1:
		return fmt.Sprintf("only 1 is %s: %s", stageAdjective(stage), quoteList(exporters))
	default:
		return fmt.Sprintf("only %d are %s: %s", len(exporters), stageAdjective(stage), quoteList(exporters))
	}
}

func quote(s string) string {
	return "`" + s + "`"
}

func quoteList(items []string) string {
	quoted := make([]string, len(items))
	for i, item := range items {
		quoted[i] = quote(item)
	}
	return strings.Join(quoted, ", ")
}
