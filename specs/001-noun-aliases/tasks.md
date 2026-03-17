# Tasks: CLI Noun Aliases

**Input**: Design documents from `/specs/001-noun-aliases/`
**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/cli-nouns.md

## Phase 1: User Story 1 - Singular/Plural Noun Interchangeability (Priority: P1)

**Goal**: Users can type either singular or plural form of any noun subcommand and get the same result.

**Independent Test**: Invoke CLI commands with both singular and plural noun forms and verify identical behavior.

### Tests for User Story 1

> Write these tests FIRST, ensure they FAIL before implementation

- [x] T001 [P] [US1] Test that `jmp get exporter` resolves to the same command as `jmp get exporters` in `python/packages/jumpstarter-cli/jumpstarter_cli/alias_test.py`
- [x] T002 [P] [US1] Test that `jmp get lease` resolves to the same command as `jmp get leases` in `python/packages/jumpstarter-cli/jumpstarter_cli/alias_test.py`
- [x] T003 [P] [US1] Test that `jmp delete lease` resolves to the same command as `jmp delete leases` in `python/packages/jumpstarter-cli/jumpstarter_cli/alias_test.py`
- [x] T004 [P] [US1] Test that `jmp create leases` resolves to the same command as `jmp create lease` in `python/packages/jumpstarter-cli/jumpstarter_cli/alias_test.py`
- [x] T005 [P] [US1] Test that `jmp update leases` resolves to the same command as `jmp update lease` in `python/packages/jumpstarter-cli/jumpstarter_cli/alias_test.py`

### Implementation for User Story 1

- [x] T006 [US1] Change user CLI subgroups (get, delete, create, update) to use `AliasedGroup` in their respective files under `python/packages/jumpstarter-cli/jumpstarter_cli/`
- [x] T007 [US1] Add reverse plural-to-singular aliases (`exporters -> [exporter]`, `leases -> [lease]`, `clients -> [client]`) to `common_aliases` in `python/packages/jumpstarter-cli-common/jumpstarter_cli_common/alias.py`
- [x] T008 [US1] Add `cluster -> [clusters]` and `clusters -> [cluster]` aliases to `common_aliases` in `python/packages/jumpstarter-cli-common/jumpstarter_cli_common/alias.py`
- [x] T009 [US1] Verify all US1 tests pass and run linting

**Checkpoint**: Singular/plural interchangeability works for all user CLI noun subcommands.

---

## Phase 2: User Story 2 - Batch Delete with Multiple IDs (Priority: P2)

**Goal**: Users can pass multiple resource names to `jmp delete leases` in a single invocation.

**Independent Test**: Create batch delete test that passes multiple names and verifies all are processed.

### Tests for User Story 2

- [x] T010 [P] [US2] Test batch delete with multiple names in `python/packages/jumpstarter-cli/jumpstarter_cli/delete_batch_test.py`
- [x] T011 [P] [US2] Test batch delete with zero names and no flags raises error in `python/packages/jumpstarter-cli/jumpstarter_cli/delete_batch_test.py`
- [x] T012 [P] [US2] Test batch delete with --output name prints each name in `python/packages/jumpstarter-cli/jumpstarter_cli/delete_batch_test.py`

### Implementation for User Story 2

- [x] T013 [US2] Change `click.argument("name", required=False)` to `click.argument("names", nargs=-1)` and update logic in `python/packages/jumpstarter-cli/jumpstarter_cli/delete.py`
- [x] T014 [US2] Verify all US2 tests pass and run linting

**Checkpoint**: Batch delete works with multiple names, single name, and zero names (error).

---

## Phase 3: User Story 3 - Admin CLI Noun Aliases (Priority: P3)

**Goal**: Admin CLI accepts singular/plural interchangeably for noun subcommands (except admin get cluster/clusters).

**Independent Test**: Invoke admin subcommands with alternate noun forms.

### Tests for User Story 3

- [x] T015 [P] [US3] Test that `jmp admin get clients` resolves the same as `jmp admin get client` in `python/packages/jumpstarter-cli-admin/jumpstarter_cli_admin/alias_test.py`
- [x] T016 [P] [US3] Test that `jmp admin get exporters` resolves the same as `jmp admin get exporter` in `python/packages/jumpstarter-cli-admin/jumpstarter_cli_admin/alias_test.py`
- [x] T017 [P] [US3] Test that `jmp admin get cluster` and `jmp admin get clusters` remain distinct commands in `python/packages/jumpstarter-cli-admin/jumpstarter_cli_admin/alias_test.py`
- [x] T018 [P] [US3] Test that `jmp admin create clusters` resolves to `jmp admin create cluster` in `python/packages/jumpstarter-cli-admin/jumpstarter_cli_admin/alias_test.py`
- [x] T019 [P] [US3] Test that `jmp admin delete clusters` resolves to `jmp admin delete cluster` in `python/packages/jumpstarter-cli-admin/jumpstarter_cli_admin/alias_test.py`

### Implementation for User Story 3

- [x] T020 [US3] Verify admin CLI groups already use `AliasedGroup` (no code change needed, just confirmation)
- [x] T021 [US3] Verify all US3 tests pass (aliases added in T007/T008 should cover admin CLI)

**Checkpoint**: Admin CLI noun aliases work except cluster/clusters in admin get remain distinct.

---

## Phase 4: Polish and Cross-Cutting Concerns

- [x] T022 Run full test suite to verify no regressions
- [x] T023 Run linting with `make lint`

---

## Dependencies and Execution Order

### Phase Dependencies

- **Phase 1 (US1)**: No dependencies, start immediately
- **Phase 2 (US2)**: Independent of Phase 1 (different files), can run in parallel
- **Phase 3 (US3)**: Depends on T007/T008 from Phase 1 (alias additions)
- **Phase 4**: Depends on all previous phases

### Within Each Phase

- Tests MUST be written and FAIL before implementation
- Implementation tasks follow test tasks
- Verify tests pass after implementation

### Parallel Opportunities

- T001-T005 can all be written in parallel
- T010-T012 can all be written in parallel
- T015-T019 can all be written in parallel
- Phase 1 and Phase 2 implementation can proceed in parallel (different files)
