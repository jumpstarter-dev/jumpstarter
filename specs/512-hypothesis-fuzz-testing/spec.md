# Feature Specification: Hypothesis Fuzz Testing

**Feature Branch**: `512-hypothesis-fuzz-testing`
**Created**: 2026-05-27
**Status**: Draft
**Input**: User description: "Add property-based and fuzz testing infrastructure using Hypothesis (Python) and native Go fuzzing"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Run fuzz tests locally (Priority: P1)

A developer runs fuzz tests against the Jumpstarter codebase from the command line to discover edge-case bugs before pushing code.

**Why this priority**: Local fuzz testing is the core capability that all other stories depend on.

**Independent Test**: Run `python3 scripts/fuzz.py --time 5m --python-only` and verify it exercises Hypothesis tests and exits cleanly.

**Acceptance Scenarios**:

1. **Given** a developer has the repository checked out, **When** they run `python3 scripts/fuzz.py --time 5m`, **Then** Python and Go fuzz targets are discovered and exercised within the time budget.
2. **Given** a developer wants to fuzz only Python code, **When** they run `python3 scripts/fuzz.py --python-only --time 5m`, **Then** only Python fuzz tests run.
3. **Given** a developer wants to fuzz a single Go target, **When** they run `python3 scripts/fuzz.py --go-target FuzzName --time 2m`, **Then** only that Go target is fuzzed.

---

### User Story 2 - Robustness tests verify constructors handle arbitrary input (Priority: P1)

Each package with public constructors has robustness tests that verify constructors do not crash on arbitrary input types.

**Why this priority**: Robustness against malformed input is the primary fuzz testing goal for a hardware-interfacing project.

**Independent Test**: Run `pytest python/packages/jumpstarter-driver-power/ -k robustness_test` and verify it passes.

**Acceptance Scenarios**:

1. **Given** a package has public constructors, **When** Hypothesis generates arbitrary input, **Then** constructors either succeed or raise expected exceptions without crashing.
2. **Given** a constructor succeeds with generated input, **When** the object is inspected, **Then** basic type invariants hold on its attributes.

---

### User Story 3 - CI runs fuzz tests on PRs and main branch (Priority: P2)

A GitHub Actions workflow runs fuzz tests automatically on pull requests (short budget) and on pushes to main (longer budget).

**Why this priority**: Continuous fuzzing catches regressions that one-time local runs would miss.

**Independent Test**: Open a PR touching python/ or controller/ and verify the fuzz workflow triggers with a 5m budget.

**Acceptance Scenarios**:

1. **Given** a PR is opened that touches Python or Go code, **When** CI triggers, **Then** fuzz tests run with a 5-minute budget.
2. **Given** code is pushed to main, **When** CI triggers, **Then** fuzz tests run with a 6-hour budget.
3. **Given** a fuzz run discovers a failure, **When** the CI job completes, **Then** hypothesis examples or Go crash reproducers are uploaded as artifacts.

---

### User Story 4 - Property-based tests verify selector logic (Priority: P2)

The label selector parsing and matching code has property-based tests that verify algebraic properties (reflexivity, superset containment, roundtrip parsing).

**Why this priority**: The selector logic is the only production code changed by this branch and needs strong verification.

**Independent Test**: Run `pytest python/packages/jumpstarter/jumpstarter/client/selectors_hypothesis_test.py` and verify all properties hold.

**Acceptance Scenarios**:

1. **Given** a valid label selector string, **When** parsed and re-formatted, **Then** the roundtrip preserves all labels.
2. **Given** a selector, **When** checked against itself, **Then** `selector_contains` returns True (reflexivity).
3. **Given** a superset selector and a subset requirement, **When** checked, **Then** `selector_contains` returns True.

---

### User Story 5 - Regression injection from fuzz discoveries (Priority: P3)

When the fuzzer discovers a failing input, it is automatically injected as a regression test so future runs replay it deterministically.

**Why this priority**: Regression injection prevents known-bad inputs from silently returning.

**Independent Test**: Manually seed a failing Hypothesis example and verify `replay_and_inject_python()` adds an `@example()` decorator.

**Acceptance Scenarios**:

1. **Given** Hypothesis finds a falsifying example, **When** the replay phase runs, **Then** an `@example()` decorator is added to the test source.
2. **Given** a Go fuzzer writes a crash reproducer to testdata/fuzz, **When** the replay phase runs, **Then** an `f.Add()` call is injected into the fuzz test source.

---

### Edge Cases

- What happens when no fuzz test files are found? The runner should exit cleanly with zero targets.
- How does the system handle a time budget of zero seconds? It should validate and reject non-positive durations.
- What happens when the hypothesis database is empty during replay? No regressions are injected and the runner prints an informational message.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST discover Python fuzz test files matching `*hypothesis_test.py` and `*robustness_test.py` patterns under `python/packages/`.
- **FR-002**: System MUST discover Go fuzz targets matching `Fuzz*` function signatures in `*_fuzz_test.go` files under `controller/`.
- **FR-003**: System MUST distribute a time budget across discovered targets.
- **FR-004**: System MUST validate the `--time` duration argument and reject invalid formats.
- **FR-005**: System MUST inject discovered regressions as `@example()` decorators (Python) or `f.Add()` calls (Go) into test source files.
- **FR-006**: Robustness tests MUST verify that constructors do not crash on arbitrary input types beyond expected exception sets.
- **FR-007**: Property-based tests MUST verify algebraic properties of the selector matching logic.
- **FR-008**: CI workflow MUST run fuzz tests on PRs (5m) and main pushes (6h).

### Key Entities

- **Fuzz Runner** (`scripts/fuzz.py`): Orchestrates discovery, execution, and regression injection for both Python and Go fuzz targets.
- **Robustness Tests** (`*robustness_test.py`): Hypothesis-driven tests that verify constructors handle arbitrary input gracefully.
- **Property Tests** (`*hypothesis_test.py`): Hypothesis-driven tests that verify algebraic properties of functions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: All robustness tests pass with `max_examples=100` without crashing.
- **SC-002**: All property-based tests pass with `max_examples=100` without counterexamples.
- **SC-003**: The fuzz runner completes within its time budget (plus 30s grace) for all target languages.
- **SC-004**: CI fuzz workflow triggers on PRs and main pushes as configured.
