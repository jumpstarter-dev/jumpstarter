# Implementation Plan: Renovate Dependency Configuration

**Branch**: `732-renovate-dependency-config` | **Date**: 2026-06-04 | **Spec**: `specs/732-renovate-dependency-config/spec.md`
**Input**: Feature specification from `/specs/732-renovate-dependency-config/spec.md`

## Summary

Add a Renovate configuration (renovate.json) to manage cross-module and cross-ecosystem dependency updates. The config groups Kubernetes dependencies across all Go modules, allows independent updates for unrelated Go deps, handles Python dependencies via UV/pip, tracks Docker base images, groups GitHub Actions by organization, and enables auto-merge for safe patch updates.

## Technical Context

**Language/Version**: JSON configuration (Renovate bot)
**Primary Dependencies**: Renovate bot (GitHub App or self-hosted)
**Storage**: N/A (configuration file only)
**Testing**: JSON schema validation, grep-based verification
**Target Platform**: GitHub repository (Renovate bot reads renovate.json from repo root)
**Project Type**: Configuration file
**Performance Goals**: N/A
**Constraints**: Must be valid Renovate JSON schema; must cover all existing ecosystem managers
**Scale/Scope**: 3 go.mod files, 58+ Python packages, 5 Dockerfiles, 12 GHA workflows

## Constitution Check

- Principle I (Observability): The renovate config exposes dependency state through machine-readable JSON and produces structured PR descriptions. PASS.
- Principle II (Decomposability): Dependencies are decomposed into independent groups that can be updated, reviewed, and merged independently. PASS.
- Principle III (Deterministic Boundaries): The config uses explicit package patterns and group rules, not implicit conventions. PASS.
- Principle IV (Reversibility): Each Renovate PR is a discrete, revertible change. PASS.
- Principle V (Evaluability): The config can be validated via JSON parsing and pattern matching without human judgment. PASS.

## Project Structure

### Documentation (this feature)

```text
specs/732-renovate-dependency-config/
  spec.md
  plan.md
  tasks.md
```

### Source Code (repository root)

```text
renovate.json              # New: Renovate configuration at repo root
.github/dependabot.yml     # Deleted: Replaced by renovate.json
```

**Structure Decision**: Single configuration file at repository root. Renovate reads renovate.json from the default branch. No source code changes needed -- this is purely a configuration addition.

## Existing Dependency Landscape

### Go Modules (3 locations)
- controller/go.mod: k8s.io v0.33.0, controller-runtime v0.21.0, grpc v1.80.0, gin, go-jose, zitadel/oidc
- controller/deploy/operator/go.mod: k8s.io v0.34.1, controller-runtime v0.22.3, cert-manager v1.18.6
- e2e/test/go.mod: ginkgo, gomega (testing only)

### Kubernetes Dependencies (must be grouped)
- k8s.io/api, k8s.io/apimachinery, k8s.io/apiserver, k8s.io/client-go, k8s.io/utils
- sigs.k8s.io/controller-runtime
- github.com/cert-manager/cert-manager

### Docker Base Images
- fedora:43 (python/Dockerfile, python/Dockerfile.utils)
- registry.access.redhat.com/ubi9/go-toolset:1.24.6 (controller/Dockerfile)
- registry.access.redhat.com/ubi9/ubi-micro:9.5 (controller/Dockerfile)
- ghcr.io/astral-sh/uv:latest (python/Dockerfile)

### GitHub Actions Organizations
- actions/* (checkout, cache, setup-go, upload-artifact, etc.)
- docker/* (build-push-action, login-action, metadata-action, etc.)
- astral-sh/* (ruff-action, setup-uv)
- Individual: crate-ci/typos, dorny/paths-filter, korthout/backport-action, peter-evans/repository-dispatch

## New Requirements (Session 2)

### gRPC/Protobuf Python Grouping
- grpcio (jumpstarter-protocol), grpcio-tools (build dep), and protobuf (jumpstarter-protocol) must be grouped
- These packages have tight version coupling for protocol buffer compatibility
- Use matchPackageNames with pep621 manager

### Kubernetes Python Grouping
- kubernetes (jumpstarter-kubernetes) and kubernetes-asyncio (jumpstarter-kubernetes) must be grouped
- Both share upstream release cycle; version skew causes runtime errors
- Use matchPackageNames with pep621 manager

### Go Version Tracking
- All three go.mod files declare `go 1.24.0`
- Renovate's gomod manager natively detects and updates the go directive
- Group go version updates across all go.mod files using matchDepTypes: ["golang-version"]
- matchFileNames covers all three go.mod locations

### Implementation approach
- Add three new packageRules to existing renovate.json
- Add corresponding test classes to tests/test_renovate_config.py
- No structural changes needed; purely additive

## Complexity Tracking

No constitution violations. Single-file configuration change (plus test updates).
