# Tasks: Renovate Dependency Configuration

**Branch**: `732-renovate-dependency-config`
**Spec**: `specs/732-renovate-dependency-config/spec.md`
**Plan**: `specs/732-renovate-dependency-config/plan.md`

## Phase 1: Foundation -- Base Renovate Configuration

### T001: Create base renovate.json with extends and schedule [P]
- **File**: `renovate.json`
- **Action**: Create renovate.json at repo root with base config extending "config:recommended", weekly schedule matching existing dependabot (UTC, weekly), and platform settings.
- **Test**: Validate JSON parses without error. Verify "extends" and "schedule" keys exist.
- **Maps to**: FR-001, FR-011, FR-012

## Phase 2: Kubernetes Dependency Grouping (P1)

### T002: Add kubernetes group packageRule [P]
- **File**: `renovate.json`
- **Depends on**: T001
- **Action**: Add packageRules entry grouping k8s.io/*, sigs.k8s.io/controller-runtime, and cert-manager into a "kubernetes" group. Set matchManagers to gomod. Include matchFileNames for all three go.mod locations.
- **Test**: Verify groupName "kubernetes" exists. Verify k8s.io pattern present. Verify controller-runtime present. Verify cert-manager present.
- **Maps to**: FR-002, FR-003, FR-004

### T003: Configure kubernetes group merge policy
- **File**: `renovate.json`
- **Depends on**: T002
- **Action**: Set automerge to false for the kubernetes group for minor and major updates. Assign reviewers label or require review.
- **Test**: Verify kubernetes group has automerge false or no automerge for non-patch.
- **Maps to**: FR-010

## Phase 3: Independent Go Dependencies (P1)

### T004: Verify non-kubernetes Go deps are independent [P]
- **File**: `renovate.json`
- **Depends on**: T002
- **Action**: Ensure the kubernetes group pattern does NOT capture grpc, gin, go-jose, zitadel, uuid, or other non-k8s deps. The default Renovate behavior creates individual PRs for unmatched packages, so no explicit rule needed -- just verify the patterns are exclusive.
- **Test**: Verify the kubernetes group matchPackagePatterns do not match "google.golang.org/grpc" or "github.com/gin-gonic/gin".
- **Maps to**: FR-005

## Phase 4: Python and Docker (P2)

### T005: Enable Python dependency detection [P]
- **File**: `renovate.json`
- **Depends on**: T001
- **Action**: Ensure pep621 manager is enabled (it is by default with config:recommended). No explicit config needed unless customization is required.
- **Test**: Verify renovate config does not disable pep621 or pip managers.
- **Maps to**: FR-006

### T006: Enable Docker base image tracking [P]
- **File**: `renovate.json`
- **Depends on**: T001
- **Action**: Ensure dockerfile manager is enabled (default with config:recommended). No explicit disabling.
- **Test**: Verify renovate config does not disable dockerfile manager.
- **Maps to**: FR-007

## Phase 5: GitHub Actions Grouping (P2)

### T007: Add GitHub Actions grouping by organization
- **File**: `renovate.json`
- **Depends on**: T001
- **Action**: Add packageRules entries grouping: actions/* as "github-actions-official", docker/* as "github-actions-docker", astral-sh/* as "github-actions-astral". Set matchManagers to github-actions.
- **Test**: Verify three GHA group rules exist with correct patterns.
- **Maps to**: FR-008

## Phase 6: Auto-merge Policy (P3)

### T008: Configure auto-merge for patch updates
- **File**: `renovate.json`
- **Depends on**: T001
- **Action**: Add a packageRule that enables automerge for patch matchUpdateTypes across all managers, with automergeType "pr". Add a separate rule that disables automerge for the kubernetes group on minor/major.
- **Test**: Verify automerge true exists for patch. Verify kubernetes group is excluded from auto-merge on non-patch.
- **Maps to**: FR-009, FR-010

## Phase 7: Final Validation

### T009: Full JSON validation and FR verification
- **File**: `renovate.json`
- **Depends on**: T001-T008
- **Action**: Run all verification commands from spec. Validate complete renovate.json against all functional requirements.
- **Test**: All FR verification commands pass.
- **Maps to**: All FRs

### T010: Commit changes
- **Depends on**: T009
- **Action**: Stage and commit renovate.json with conventional commit message.
