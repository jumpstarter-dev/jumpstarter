# Feature Specification: Renovate Dependency Configuration

**Feature Branch**: `732-renovate-dependency-config`
**Created**: 2026-06-04
**Status**: Draft
**Input**: A renovate config that groups/manages cross-module and cross-ecosystem dependencies for the python, golang, grpc, container images and github action dependencies

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Kubernetes Dependency Grouping Across Go Modules (Priority: P1)

As a maintainer, I want all k8s.io/* and sigs.k8s.io/controller-runtime dependencies grouped into a single PR across all go.mod files (controller/, controller/deploy/operator/, e2e/test/) so that Kubernetes API version skew does not break the build.

**Why this priority**: Kubernetes dependencies span multiple Go modules and must stay in sync. Version skew between controller and operator modules causes build failures and runtime incompatibility.

**Independent Test**: Can be tested by validating that the renovate.json packageRules section contains a group matching k8s.io/* and sigs.k8s.io/controller-runtime with matchFileNames covering all three go.mod locations.

**Acceptance Scenarios**:

1. **Given** the renovate config is loaded, **When** Renovate evaluates k8s.io/api across controller/go.mod and controller/deploy/operator/go.mod, **Then** both updates appear in a single grouped PR titled with "kubernetes".
2. **Given** the renovate config is loaded, **When** sigs.k8s.io/controller-runtime has an update, **Then** it is included in the same kubernetes group PR.
3. **Given** the renovate config is loaded, **When** cert-manager has an update alongside controller-runtime, **Then** cert-manager is included in the kubernetes group PR.

---

### User Story 2 - Independent Go Dependency Updates (Priority: P1)

As a maintainer, I want unrelated Go dependencies (grpc, logging, OIDC, uuid, etc.) to get their own individual or small-group PRs so that I can review and merge them independently without waiting for the entire Kubernetes stack.

**Why this priority**: Blocking unrelated dep bumps on a large Kubernetes upgrade slows down the project. Independent PRs allow faster iteration.

**Independent Test**: Can be tested by verifying that the renovate config does NOT group grpc, gin, go-jose, zitadel, or other non-k8s Go deps into the kubernetes group.

**Acceptance Scenarios**:

1. **Given** the renovate config is loaded, **When** google.golang.org/grpc has a patch update, **Then** it gets its own PR separate from the kubernetes group.
2. **Given** the renovate config is loaded, **When** github.com/gin-gonic/gin has an update, **Then** it is not grouped with kubernetes dependencies.

---

### User Story 3 - Python Dependency Management (Priority: P2)

As a maintainer, I want Renovate to detect and update Python dependencies managed by the UV workspace under python/ so that security patches and version bumps are tracked.

**Why this priority**: The python/ directory contains 58+ packages. Automated dependency tracking reduces manual maintenance burden.

**Independent Test**: Can be tested by verifying the renovate config enables the pep621 or pip manager for the python/ directory.

**Acceptance Scenarios**:

1. **Given** the renovate config is loaded, **When** Renovate scans the repository, **Then** it discovers pyproject.toml files under python/.
2. **Given** the renovate config is loaded, **When** a Python dependency has a security update, **Then** Renovate creates a PR for it.

---

### User Story 4 - Docker Base Image Tracking (Priority: P2)

As a maintainer, I want Renovate to track Docker base image updates (UBI9, Fedora 43, uv) so that I am notified when new versions are available.

**Why this priority**: Base image updates often contain security fixes and should not be missed.

**Independent Test**: Can be tested by verifying the renovate config enables the dockerfile manager.

**Acceptance Scenarios**:

1. **Given** the renovate config is loaded, **When** Renovate scans Dockerfiles in controller/ and python/, **Then** it detects the base images (fedora, ubi9, uv).
2. **Given** the renovate config is loaded, **When** a new fedora base image tag is available, **Then** Renovate creates a PR to update it.

---

### User Story 5 - GitHub Actions Grouping by Organization (Priority: P2)

As a maintainer, I want GitHub Actions dependencies grouped by organization (actions/*, docker/*, astral-sh/*) so that related action updates are bundled together to reduce PR noise.

**Why this priority**: The project uses ~20 GHA actions. Grouping by org reduces PR count while keeping related updates together.

**Independent Test**: Can be tested by verifying packageRules contain groups for actions/*, docker/*, and astral-sh/* with matchManagers github-actions.

**Acceptance Scenarios**:

1. **Given** the renovate config is loaded, **When** actions/checkout and actions/cache both have updates, **Then** they appear in a single grouped PR.
2. **Given** the renovate config is loaded, **When** docker/build-push-action and docker/login-action have updates, **Then** they appear in a single grouped PR.

---

### User Story 6 - Auto-merge for Safe Patch Updates (Priority: P3)

As a maintainer, I want patch-level updates for stable dependencies to be auto-merged (after CI passes) so that low-risk updates do not require manual review.

**Why this priority**: Patch updates are typically backward-compatible. Auto-merging them reduces review overhead.

**Independent Test**: Can be tested by verifying packageRules include automerge: true for patch matchUpdateTypes.

**Acceptance Scenarios**:

1. **Given** the renovate config is loaded, **When** a Go dependency has a patch update, **Then** the PR is configured for auto-merge after CI passes.
2. **Given** the renovate config is loaded, **When** a kubernetes dependency has a minor or major update, **Then** it is NOT auto-merged.

---

### User Story 7 - gRPC and Protobuf Python Package Grouping (Priority: P1)

As a maintainer, I want grpcio, grpcio-tools, and protobuf Python packages grouped into a single PR so that gRPC protocol compatibility is maintained when any of these packages update.

**Why this priority**: grpcio, grpcio-tools, and protobuf have tight version coupling. Updating them independently can cause build failures or runtime incompatibility in the protocol layer.

**Independent Test**: Can be tested by verifying that the renovate.json packageRules section contains a group matching grpcio, grpcio-tools, and protobuf with matchManagers pep621.

**Acceptance Scenarios**:

1. **Given** the renovate config is loaded, **When** grpcio and protobuf both have updates, **Then** they appear in a single grouped PR titled with "grpc-protobuf".
2. **Given** the renovate config is loaded, **When** grpcio-tools has an update, **Then** it is included in the same grpc-protobuf group PR.

---

### User Story 8 - Kubernetes Python Package Grouping (Priority: P1)

As a maintainer, I want kubernetes and kubernetes-asyncio Python packages grouped into a single PR so that the synchronous and asynchronous Kubernetes clients stay in sync.

**Why this priority**: kubernetes and kubernetes-asyncio share the same upstream release cycle and API compatibility. Version skew between them causes runtime errors.

**Independent Test**: Can be tested by verifying that the renovate.json packageRules section contains a group matching kubernetes and kubernetes-asyncio with matchManagers pep621.

**Acceptance Scenarios**:

1. **Given** the renovate config is loaded, **When** kubernetes and kubernetes-asyncio both have updates, **Then** they appear in a single grouped PR titled with "kubernetes-python".
2. **Given** the renovate config is loaded, **When** only kubernetes-asyncio has an update, **Then** it is included in the kubernetes-python group PR.

---

### User Story 9 - Go Version Tracking (Priority: P2)

As a maintainer, I want Renovate to track and update the Go version directive in go.mod files so that the project stays current with Go releases.

**Why this priority**: The go directive in go.mod files controls minimum Go version requirements. Renovate's gomod manager handles this natively and groups updates across modules.

**Independent Test**: Can be tested by verifying that the renovate config includes a group for golang version updates across all go.mod files.

**Acceptance Scenarios**:

1. **Given** the renovate config is loaded, **When** a new Go version is released, **Then** Renovate creates a PR to update the go directive across all go.mod files.
2. **Given** the renovate config is loaded, **When** the go directive updates, **Then** all three go.mod files are updated in a single PR.

---

### Edge Cases

- What happens when cert-manager and controller-runtime update simultaneously? They should land in the same kubernetes group PR.
- What happens when a Go dependency appears in multiple go.mod files but is not a k8s dependency? It should get independent PRs per module unless explicitly grouped.
- What happens when the devcontainer Dockerfile updates? It should be tracked by the dockerfile manager.
- What happens when grpcio updates but protobuf does not? They should still be in the same group PR so Renovate evaluates compatibility.
- What happens when kubernetes updates but kubernetes-asyncio does not? They should still be in the same group PR.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Repository MUST contain a valid renovate.json configuration file at the repository root.
- **FR-002**: Configuration MUST group all k8s.io/* dependencies into a single "kubernetes" group across all go.mod files.
- **FR-003**: Configuration MUST include sigs.k8s.io/controller-runtime in the kubernetes group.
- **FR-004**: Configuration MUST include cert-manager/cert-manager in the kubernetes group.
- **FR-005**: Configuration MUST allow independent PRs for non-kubernetes Go dependencies (grpc, gin, go-jose, zitadel, uuid, etc.).
- **FR-006**: Configuration MUST enable Python dependency detection for the python/ directory.
- **FR-007**: Configuration MUST enable Docker base image tracking for Dockerfiles in controller/ and python/.
- **FR-008**: Configuration MUST group GitHub Actions by organization (actions/*, docker/*, astral-sh/*).
- **FR-009**: Configuration MUST enable auto-merge for patch-level updates with automergeType "pr".
- **FR-010**: Configuration MUST NOT auto-merge minor or major updates for the kubernetes group.
- **FR-011**: Configuration MUST use a weekly schedule matching the existing dependabot schedule (UTC timezone).
- **FR-012**: Configuration MUST be valid JSON parseable by standard JSON tools.
- **FR-013**: Configuration MUST group grpcio, grpcio-tools, and protobuf Python packages into a "grpc-protobuf" group.
- **FR-014**: Configuration MUST group kubernetes and kubernetes-asyncio Python packages into a "kubernetes-python" group.
- **FR-015**: Configuration MUST group Go version (go directive) updates across all go.mod files into a "golang-version" group.

### Verification Commands

- **FR-001**: `test -f renovate.json && echo PASS || echo FAIL`
- **FR-002**: `python3 -c "import json; c=json.load(open('renovate.json')); rules=[r for r in c.get('packageRules',[]) if r.get('groupName')=='kubernetes']; assert any('k8s.io/' in str(r.get('matchPackageNames',[])) for r in rules); print('PASS')"`
- **FR-003**: `python3 -c "import json; c=json.load(open('renovate.json')); rules=[r for r in c.get('packageRules',[]) if r.get('groupName')=='kubernetes']; assert any('controller-runtime' in str(r) for r in rules); print('PASS')"`
- **FR-004**: `python3 -c "import json; c=json.load(open('renovate.json')); rules=[r for r in c.get('packageRules',[]) if r.get('groupName')=='kubernetes']; assert any('cert-manager' in str(r) for r in rules); print('PASS')"`
- **FR-005**: `python3 -c "import json; c=json.load(open('renovate.json')); k8s_rules=[r for r in c.get('packageRules',[]) if r.get('groupName')=='kubernetes']; assert not any('grpc' in str(r) for r in k8s_rules); print('PASS')"`
- **FR-006**: `python3 -c "import json; c=json.load(open('renovate.json')); print('PASS')"`
- **FR-007**: `python3 -c "import json; c=json.load(open('renovate.json')); print('PASS')"`
- **FR-012**: `python3 -c "import json; json.load(open('renovate.json')); print('PASS')"`
- **FR-013**: `python3 -c "import json; c=json.load(open('renovate.json')); rules=[r for r in c.get('packageRules',[]) if r.get('groupName')=='grpc-protobuf']; assert len(rules)==1; names=rules[0].get('matchPackageNames',[]); assert 'grpcio' in names and 'grpcio-tools' in names and 'protobuf' in names; print('PASS')"`
- **FR-014**: `python3 -c "import json; c=json.load(open('renovate.json')); rules=[r for r in c.get('packageRules',[]) if r.get('groupName')=='kubernetes-python']; assert len(rules)==1; names=rules[0].get('matchPackageNames',[]); assert 'kubernetes' in names and 'kubernetes-asyncio' in names; print('PASS')"`
- **FR-015**: `python3 -c "import json; c=json.load(open('renovate.json')); rules=[r for r in c.get('packageRules',[]) if r.get('groupName')=='golang-version']; assert len(rules)==1; print('PASS')"`

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: renovate.json passes JSON validation without errors.
- **SC-002**: Kubernetes dependencies across all go.mod files are covered by a single group rule.
- **SC-003**: Non-kubernetes Go deps are not captured by the kubernetes group pattern.
- **SC-004**: GitHub Actions are grouped by organization in the packageRules.
- **SC-005**: Patch-level updates have automerge enabled.
- **SC-006**: The kubernetes group does not have automerge for minor/major updates.
- **SC-007**: grpcio, grpcio-tools, and protobuf are grouped in a single "grpc-protobuf" rule.
- **SC-008**: kubernetes and kubernetes-asyncio Python packages are grouped in a single "kubernetes-python" rule.
- **SC-009**: Go version updates are grouped across all go.mod files.
