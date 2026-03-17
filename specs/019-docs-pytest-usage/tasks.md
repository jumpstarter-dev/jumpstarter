# Tasks: Document Pytest Class Usage

**Branch**: `019-docs-pytest-usage` | **Generated**: 2026-03-17 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

## Phase 1: Foundational Tasks

### Documentation Structure Setup

- [ ] [T001] [P] Create testing.md guide in /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/testing.md with basic structure (sections: Introduction, Installation, Basic Example, Configuration, Local vs Distributed, Advanced Examples)
- [ ] [T002] Update guides index at /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/index.md to include testing.md in description and toctree

## Phase 2: User Story 1 - Learn how to write tests using Jumpstarter's pytest integration (P1)

### Test Task - Verify example code works

- [ ] [T003] [S1] Write a minimal test in /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/test_example_basic.py that uses JumpstarterTest class and verify it runs successfully

### Implementation Tasks

- [ ] [T004] [S1] Add Installation section to /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/testing.md explaining how to install jumpstarter-testing package
- [ ] [T005] [S1] Add Basic Example section to /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/testing.md with complete working example showing JumpstarterTest class usage, selector, and client fixture
- [ ] [T006] [S1] Add code explanation in /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/testing.md describing each component of the example (JumpstarterTest base class, selector attribute, client fixture, test methods)
- [ ] [T007] [S1] Add Running Tests section to /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/testing.md with pytest command examples

### Verification Test

- [ ] [T008] [S1] Verify documentation build succeeds by running sphinx-build from /var/home/raballew/code/jumpstarter/python/docs directory

## Phase 3: User Story 2 - Understand test configuration options (P2)

### Implementation Tasks

- [ ] [T009] [S2] Add Configuration section to /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/testing.md documenting selector attribute syntax and examples
- [ ] [T010] [S2] Document JUMPSTARTER_HOST environment variable usage in /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/testing.md Configuration section
- [ ] [T011] [S2] Document pytest fixture scope options in /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/testing.md (class vs function scope)
- [ ] [T012] [S2] Add Local vs Distributed Mode section to /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/testing.md explaining when/how to use JUMPSTARTER_HOST for shell mode vs lease acquisition

### Test Task

- [ ] [T013] [S2] Write example test in /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/test_example_config.py demonstrating custom selector and verify it's correctly documented

## Phase 4: Polish and Finalization

### Advanced Examples and Edge Cases

- [ ] [T014] [P] Add Advanced Examples section to /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/testing.md with fixture composition example (console, storage fixtures)
- [ ] [T015] [P] Add troubleshooting subsection to /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/testing.md covering common issues (no exporter found, connection timeout, lease failures)
- [ ] [T016] Document parallel test execution considerations in /var/home/raballew/code/jumpstarter/python/docs/source/getting-started/guides/testing.md (using pytest-xdist with class-scoped fixtures)

### Final Verification

- [ ] [T017] Build documentation locally with make command from /var/home/raballew/code/jumpstarter/python/docs and verify all examples render correctly
- [ ] [T018] Review testing.md against spec.md acceptance criteria: new developer can write test within 15 minutes, both local and distributed modes covered, all examples tested

---

## Checkpoints

1. **After T002**: Documentation structure is ready for content
2. **After T008**: Basic pytest usage is documented with working example
3. **After T013**: Configuration options are fully documented
4. **After T018**: All documentation complete and verified

## Dependencies & Execution Order

**Strict Dependencies**:
- T002 depends on T001 (must create file before updating index)
- T003 must complete before T005 (test example before documenting it)
- T004-T007 can run after T002 in any order
- T008 must run after T004-T007 (verify build after content added)
- T009-T012 can run in parallel after T008
- T013 must run after T009 (test configuration after documenting it)
- T014-T016 can run in parallel after T013
- T017-T018 must run last (final verification)

**Suggested Parallelization**:
- T001 and T003 can run in parallel
- T004, T005, T006, T007 can run in parallel after T002
- T009, T010, T011, T012 can run in parallel after T008
- T014, T015, T016 can run in parallel after T013

## Implementation Strategy

This is a documentation-only feature with no code changes to the jumpstarter-testing package itself. The strategy is:

1. **Structure First**: Create the documentation file and integrate it into the existing documentation structure
2. **TDD for Examples**: Write working pytest examples first to verify they work, then document them
3. **Progressive Detail**: Start with basic usage (S1), then add configuration (S2), then advanced patterns (Phase 4)
4. **Verify Continuously**: Build documentation after each phase to catch issues early

The documentation will reference the existing JumpstarterTest class in `/var/home/raballew/code/jumpstarter/python/packages/jumpstarter-testing/jumpstarter_testing/pytest.py` and use examples from `/var/home/raballew/code/jumpstarter/python/examples/soc-pytest/jumpstarter_example_soc_pytest/test_on_rpi4.py` as reference patterns.

All test examples should be functional and tested before being added to documentation to ensure accuracy (per FR-003 from spec.md).
