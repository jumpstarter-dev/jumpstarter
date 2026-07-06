---
name: release
description: Guide through the Jumpstarter release process (branch, tag, operator bundle)
argument-hint: "Optional: version (e.g. '0.9.0-rc.1') or phase (e.g. 'operator-bundle')"
---

# Jumpstarter Release

You are guiding the user through the Jumpstarter project release process.

Before proceeding, read `.claude/rules/releasing-operator.md` for operator-specific details.

Release input: $ARGUMENTS

## Conventions

- **Git tags** use a `v` prefix: `v0.8.1`, `v0.9.0-rc.1`
- **Container image tags** do NOT use a `v` prefix: `:0.8.1`, `:0.9.0-rc.1`
- **RC tag format**: `vX.Y.Z-rc.N` (with dot before N). Reject old formats like `rc1` or `-rc1`.
- **RC-first rule**: When a release branch has no final release tags yet, the first tag MUST be an RC. Never tag a direct `vX.Y.0` final on a new branch without at least one RC first.
- Python packages are versioned automatically from git tags via `hatch-vcs` — no manual version files.
- The `bundle/` directory is NOT committed to the repo.
- `GITHUB_USER` env var controls the fork for community-operators (defaults to `mangelajo`).
- Do NOT modify `controller/deploy/operator/api/v1alpha1/jumpstarter_types.go` — the operator resolves `:latest` image defaults to its own version at runtime.

## Ordering constraint

```
Makefile update → commit → tag push → [CI builds images] → GitHub Release → make bundle → make contribute
```

Images must exist before bundle generation. The skill enforces this by splitting the process into phases.

## Steps

### 1. Gather context and determine release type

Fetch the latest remote state first, then inspect:

```bash
git fetch origin --tags
git branch --show-current
git branch -r | grep 'origin/release-'
git tag --sort=-version:refname | head -20
gh release list --limit 5
grep -E '^(VERSION|REPLACES) ' controller/deploy/operator/Makefile
```

Using the git state and `$ARGUMENTS` (if provided), ask the user:

1. **What type of release is this?**
   - **(A) Create a new release branch** — starting a new `X.Y` cycle from `main`
   - **(B) Tag a release** — cut an RC or final from an existing release branch
   - **(C) Operator bundle contribution** — generate and contribute the OLM bundle (after images are built)

2. **What version?** Suggest the next logical version based on existing tags. Examples:
   - Latest tag is `v0.8.1` → suggest `v0.9.0-rc.1` for a new branch, or `v0.8.2-rc.1` for a patch
   - Latest RC is `v0.9.0-rc.2` → suggest `v0.9.0-rc.3` or `v0.9.0` (final)

3. **Validate the version:**
   - Format must be `vX.Y.Z` or `vX.Y.Z-rc.N`
   - If this is a final release (`vX.Y.Z` without `-rc`), verify that at least one `vX.Y.Z-rc.*` tag exists. If not, warn the user and suggest creating an RC first.
   - If creating a new release branch, the first tag must be an RC (e.g., `vX.Y.0-rc.1`)

### 2A. Create a new release branch

Only if the user selected type (A).

**Important:** The `release-*` branch pattern may be protected by repository rulesets. If a direct push is rejected, try creating the branch via the GitHub API:

```bash
# Try direct push first
git fetch origin
git checkout -b release-X.Y origin/main
git push origin release-X.Y

# If rejected by branch protection, use the API:
SHA=$(git rev-parse origin/main)
REPO=$(gh repo view --json owner,name -q '.owner.login + "/" + .name')
gh api "repos/${REPO}/git/refs" -f ref=refs/heads/release-X.Y -f sha="$SHA"
```

If both fail, the user needs to temporarily remove `release-*` from the branch protection ruleset, push the branch, then re-add it.

After pushing, inform the user:
- The branch push triggers CI to build images tagged with the branch name (e.g., `:release-0.9`)
- The next step is to tag the first RC from this branch
- Offer to continue immediately with step 2B to tag `vX.Y.0-rc.1`

### 2B. Tag a release

Only if the user selected type (B), or continuing from step 2A.

#### Phase 1: Pre-tag code changes

Ensure you are on the correct `release-X.Y` branch:

```bash
git fetch origin
git checkout release-X.Y
git pull origin release-X.Y
```

**Update version references:**

1. **`controller/deploy/operator/Makefile`** — update:
   - `VERSION ?= X.Y.Z` (or `X.Y.Z-rc.N` for RCs, no `v` prefix)
   - `REPLACES ?= jumpstarter-operator.vPREVIOUS` — must point to the most recently published version in the OLM channel (including RCs). Check existing tags to determine the correct value. For the first release on a new branch, check what the last published version was across all branches.

2. **Regenerate manifests:**
   ```bash
   cd controller/deploy/operator
   make manifests generate
   ```

3. **Commit and push** the changes to the release branch. Files to include:
   - `controller/deploy/operator/Makefile`
   - Any files changed by `make manifests generate`

#### Phase 2: Tag and GitHub Release

4. **Create and push the git tag:**
   ```bash
   git tag vX.Y.Z       # or vX.Y.Z-rc.N
   git push origin vX.Y.Z
   ```

5. **Create the GitHub Release:**
   ```bash
   # For a release candidate:
   gh release create vX.Y.Z-rc.N \
     --title "vX.Y.Z-rc.N" \
     --prerelease \
     --generate-notes \
     --notes-start-tag vPREVIOUS_TAG

   # For a final release:
   gh release create vX.Y.Z \
     --title "vX.Y.Z" \
     --generate-notes \
     --notes-start-tag vPREVIOUS_TAG
   ```
   Use the previous tag as `--notes-start-tag` to scope the generated release notes.

#### Phase 3: Wait for CI

The tag push triggers:
- `build-images.yaml` — builds and pushes all container images
- `trigger-packages.yaml` — regenerates the Python package index

The GitHub Release triggers:
- `release-operator-installer.yaml` — uploads `operator-installer.yaml` to the release

Tell the user that CI is now building the container images and you will monitor progress.

Find the CI run triggered by the tag and monitor it:
```bash
# Find the run triggered by the tag push
gh run list --workflow=build-images.yaml --limit 3 --json databaseId,status,conclusion,headBranch,event,createdAt

# Watch the run until it completes (use the databaseId from above)
gh run watch <RUN_ID>
```

Monitor the run periodically using `gh run view <RUN_ID> --json status,conclusion` until it completes. If it fails, offer to re-trigger with `gh run rerun <RUN_ID>`. If it fails again, show the user the failure details with `gh run view <RUN_ID> --log-failed` and stop.

When the build-images workflow succeeds, inform the user and proceed automatically to step 2C.

### 2C. Operator bundle contribution

Only if the user selected type (C), or continuing after step 2B.

#### Verify images exist

Before generating the bundle, confirm the container images for this version are available:

```bash
gh run list --workflow=build-images.yaml --limit 5
```

The user can also check `quay.io/jumpstarter-dev/jumpstarter-controller:X.Y.Z` directly.

#### Generate the OLM bundle

```bash
cd controller/deploy/operator
make bundle
```

#### Verify the bundle output

```bash
# Image references should show :X.Y.Z (no :latest, no :vX.Y.Z)
grep -E "containerImage|image: quay" controller/deploy/operator/bundle/manifests/jumpstarter-operator.clusterserviceversion.yaml

# Release config
cat controller/deploy/operator/bundle/release-config.yaml
```

Show the output to the user and ask them to confirm it looks correct before continuing.

#### Contribute to community-operators

```bash
cd controller/deploy/operator

# Set GITHUB_USER if different from default (mangelajo):
# export GITHUB_USER=yourusername

# AUTO_CONFIRM=1 skips the interactive y/N prompt
AUTO_CONFIRM=1 make contribute
```

After the script completes, push to the fork and create PRs using `gh`:

```bash
BRANCH="jumpstarter-operator-release-X.Y.Z"

cd controller/deploy/operator/contribute/community-operators
git push -f user "$BRANCH"

cd ../community-operators-prod
git push -f user "$BRANCH"
```

Then create PRs on both repos:

```bash
# PR for community-operators
cd controller/deploy/operator/contribute/community-operators
gh pr create --repo k8s-operatorhub/community-operators \
  --title "operator jumpstarter-operator (X.Y.Z)" \
  --body "Release X.Y.Z of the jumpstarter-operator for the alpha channel." \
  --head ${GITHUB_USER:-mangelajo}:$BRANCH --base main

# PR for community-operators-prod
cd ../community-operators-prod
gh pr create --repo redhat-openshift-ecosystem/community-operators-prod \
  --title "operator jumpstarter-operator (X.Y.Z)" \
  --body "Release X.Y.Z of the jumpstarter-operator for the alpha channel." \
  --head ${GITHUB_USER:-mangelajo}:$BRANCH --base main
```

Replace `GITHUB_USER` with the actual GitHub username (default: `mangelajo`).

### 3. Post-release steps

#### Add new release branch to protection ruleset

If a new `release-X.Y` branch was created, remind the user to add it to the repository's branch protection ruleset so it requires merge queue for future changes. This is done in GitHub Settings → Rules → Rulesets → select the main/release ruleset → add `release-X.Y` to the branch targeting pattern.

#### Cherry-pick infrastructure fixes

If the release included infrastructure-only changes to the contribute script or Makefile (not version-specific), cherry-pick them to `main` in a separate PR.

#### Checklist

Present a checklist of what was done (mark completed items) and what remains:

- [ ] Release branch created (if new `X.Y` cycle)
- [ ] Release branch added to protection ruleset (if new branch)
- [ ] Operator Makefile VERSION and REPLACES updated
- [ ] Git tag created and pushed
- [ ] GitHub Release created (with `--prerelease` for RCs)
- [ ] CI image build completed
- [ ] `operator-installer.yaml` asset uploaded (automated by CI)
- [ ] OLM bundle generated and verified (`make bundle`)
- [ ] Community-operators PRs created (`make contribute` + `gh pr create`)
- [ ] Infrastructure fixes cherry-picked to `main` (if applicable)
