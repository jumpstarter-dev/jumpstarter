# Tasks: Fix Exporter Edit Crash

**Branch**: `018-fix-exporter-edit-crash` | **Date**: 2026-03-17 | **Spec**: [spec.md](spec.md) | **Plan**: [plan.md](plan.md)

## Phase 1: Test-Driven Development

### User Story 1 - Edit exporter config without crash (Priority: P1)

- [ ] [T001] [P1] [US1] Write failing test for edit command path conversion in `/var/home/raballew/code/jumpstarter/python/packages/jumpstarter-cli/jumpstarter_cli/config_exporter_test.py`
  - Implements SC-002: Test that verifies string path is passed to editor function
  - Create test file with test function that mocks `click.edit` to verify it receives a string path
  - Use `unittest.mock.patch` to mock `click.edit` and capture the filename argument
  - Assert that the filename argument is a string (not PosixPath)
  - Test should fail initially because current implementation passes PosixPath object

- [ ] [T002] [P1] [US1] Fix path type conversion in `/var/home/raballew/code/jumpstarter/python/packages/jumpstarter-cli/jumpstarter_cli/config_exporter.py`
  - Implements FR-001: Convert config path to string before passing to editor
  - Change line 73 from `click.edit(filename=config.path)` to `click.edit(filename=str(config.path))`
  - This ensures the Path object is converted to a string before passing to click.edit

- [ ] [T003] [P1] [US1] Verify test passes after fix
  - Verifies FR-002: Command does not crash with TypeError
  - Run `make pkg-test-jumpstarter-cli` to confirm the new test passes
  - Verify no regressions in existing tests

- [ ] [T004] [P1] [US1] Write test for error handling with nonexistent exporter in `/var/home/raballew/code/jumpstarter/python/packages/jumpstarter-cli/jumpstarter_cli/config_exporter_test.py`
  - Verifies acceptance scenario 2 from spec.md
  - Test that editing a nonexistent exporter raises ClickException with appropriate message
  - Use CliRunner to invoke the edit command with a nonexistent alias
  - Assert that exit code is non-zero and error message contains "does not exist"

## Phase 2: Quality Assurance

- [ ] [T005] [P1] Run linting on modified files
  - Execute `make lint-fix` to ensure code style compliance
  - Fix any linting issues that arise

- [ ] [T006] [P1] Run type checking
  - Execute `make pkg-ty-jumpstarter-cli` to verify type correctness
  - Address any type checking errors

- [ ] [T007] [P1] Manual verification
  - Verifies SC-001: `jmp config exporter edit <name>` opens editor without crashing
  - Verifies FR-002: Command does not crash with TypeError
  - Create a test exporter config using `jmp config exporter create`
  - Run `jmp config exporter edit <alias>` to verify editor opens without crash
  - Confirm the fix resolves issue #251

## Checkpoints

- **Checkpoint 1** (After T001): Failing test demonstrates the bug (TypeError with PosixPath)
- **Checkpoint 2** (After T002-T003): Test passes, confirming the fix works
- **Checkpoint 3** (After T004): Error handling is properly tested
- **Checkpoint 4** (After T007): Manual testing confirms real-world fix

## Dependencies & Execution Order

### Must Run Sequentially
1. T001 must complete before T002 (write failing test first - TDD principle)
2. T002 must complete before T003 (fix must be applied before verification)
3. T003 must complete before T005-T007 (verify fix works before quality checks)

### Can Run in Parallel
- T001 and T004 can be written in parallel (independent test scenarios)
- T005, T006, T007 can run in parallel (independent quality checks)

## Implementation Strategy

### TDD Approach
Following the project's TDD requirement:
1. **Red**: Write T001 - a failing test that exposes the bug by mocking `click.edit` and asserting it receives a string
2. **Green**: Implement T002 - minimal fix by wrapping `config.path` with `str()`
3. **Refactor**: Verify with T003 and add edge case test T004

### Testing Strategy
- Use `unittest.mock.patch` to mock `click.edit` and verify the type of the filename argument
- Use `click.testing.CliRunner` for integration testing of the edit command
- Test both happy path (valid exporter) and error path (nonexistent exporter)

### Files Modified
- `/var/home/raballew/code/jumpstarter/python/packages/jumpstarter-cli/jumpstarter_cli/config_exporter.py` (1 line change)
- `/var/home/raballew/code/jumpstarter/python/packages/jumpstarter-cli/jumpstarter_cli/config_exporter_test.py` (new file, ~30-50 lines)

### Success Criteria Mapping
- **SC-001**: Verified by T007 (manual testing)
- **SC-002**: Verified by T001-T004 (unit tests)
- **FR-001**: Implemented in T002
- **FR-002**: Verified by T003 and T007
