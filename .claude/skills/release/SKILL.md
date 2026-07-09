---
name: release
description: Guide through the Jumpstarter release process (branch, tag, FLS bump, operator bundle)
argument-hint: "Optional: version (e.g. '0.9.0-rc.1') or phase (e.g. 'operator-bundle', 'fls')"
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
- **FLS tags** do NOT use a `v` prefix: `0.3.0`, `0.4.0` (repo: `jumpstarter-dev/fls`)
- Python packages are versioned automatically from git tags via `hatch-vcs` — no manual version files.
- The `bundle/` directory is NOT committed to the repo.
- `GITHUB_USER` env var controls the fork for community-operators (defaults to `mangelajo`).
- Do NOT modify `controller/deploy/operator/api/v1alpha1/jumpstarter_types.go` — the operator resolves `:latest` image defaults to its own version at runtime.

## Ordering constraint

```
[optional FLS release] → Makefile/FLS pin update → commit → tag push → GitHub Release / [CI builds images] → make bundle → make contribute
```

If a new FLS version is needed, release FLS first so binaries exist before Jumpstarter CI builds the exporter image. Images must exist before bundle generation. The skill enforces this by splitting the process into phases.

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
# Current FLS pins in Jumpstarter
grep -E 'ARG FLS_VERSION=' python/Containerfile
rg -n 'fls_version: str = "|--fls-version"' \
  python/packages/jumpstarter-driver-flashers/jumpstarter_driver_flashers/client.py
```

Also inspect the FLS repo for unreleased work (local checkout preferred, e.g. `~/dev/fls`; otherwise use the GitHub API):

```bash
# Prefer local fls checkout (git-only; no gh/network required after fetch)
FLS_DIR="${FLS_DIR:-$HOME/dev/fls}"
# FLS release tags: X.Y.Z or X.Y.Z-rc.N (no v prefix). Ignore non-SemVer tags (e.g. ridesx).
FLS_TAG_RE='^[0-9]+\.[0-9]+\.[0-9]+(-rc\.[0-9]+)?$'
# Parse [package].version from Cargo.toml (ignore other version= keys)
cargo_pkg_version() {
  awk '
    /^\[package\]/ { in_pkg=1; next }
    /^\[/ { in_pkg=0 }
    in_pkg && /^version[[:space:]]*=/ {
      if (match($0, /"[^"]+"/)) { print substr($0, RSTART+1, RLENGTH-2); exit }
    }
  '
}
if git -C "$FLS_DIR" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  git -C "$FLS_DIR" fetch origin --tags
  # version:refname is SemVer-aware (0.10.0 > 0.9.0); filter out non-version tags first
  LATEST_FLS=$(git -C "$FLS_DIR" tag --list --sort=-version:refname | grep -E "$FLS_TAG_RE" | head -1)
  # Always read Cargo.toml from origin/main (not the working tree / current branch)
  CARGO_VER=$(git -C "$FLS_DIR" show origin/main:Cargo.toml | cargo_pkg_version)
  echo "Latest FLS tag (local): $LATEST_FLS"
  echo "Cargo.toml version (origin/main): $CARGO_VER"
  echo "Commits on origin/main since $LATEST_FLS:"
  git -C "$FLS_DIR" log --oneline "${LATEST_FLS}..origin/main"
  echo "Commit count: $(git -C "$FLS_DIR" rev-list --count "${LATEST_FLS}..origin/main")"
else
  # Fallback without local checkout — same SemVer filter/sort as the local path
  # (gh release view returns latest-by-date, which can disagree with highest SemVer tag)
  LATEST_FLS=$(gh api repos/jumpstarter-dev/fls/tags --paginate -q '.[].name' | grep -E "$FLS_TAG_RE" | sort -V -r | head -1)
  CARGO_VER=$(gh api "repos/jumpstarter-dev/fls/contents/Cargo.toml?ref=main" --jq '.content' | base64 -d | cargo_pkg_version)
  echo "Latest FLS tag: $LATEST_FLS"
  echo "Cargo.toml version (main): $CARGO_VER"
  gh api "repos/jumpstarter-dev/fls/compare/${LATEST_FLS}...main" --jq '{ahead_by, commits: [.commits[].commit.message|split("\n")[0]]}'
fi
```

Using the git state and `$ARGUMENTS` (if provided), ask the user:

1. **What type of release is this?**
   - **(A) Create a new release branch** — starting a new `X.Y` cycle from `main`
   - **(B) FLS release / bump** — release a new FLS version and/or bump the FLS pin in Jumpstarter (often done before tagging)
   - **(C) Tag a release** — cut an RC or final from an existing release branch
   - **(D) Operator bundle contribution** — generate and contribute the OLM bundle (after images are built)

2. **What version?** Suggest the next logical version based on existing tags. Examples:
   - Latest tag is `v0.8.1` → suggest `v0.9.0-rc.1` for a new branch, or `v0.8.2-rc.1` for a patch
   - Latest RC is `v0.9.0-rc.2` → suggest `v0.9.0-rc.3` or `v0.9.0` (final)

3. **Validate the version:**
   - Format must be `vX.Y.Z` or `vX.Y.Z-rc.N`
   - If this is a final release (`vX.Y.Z` without `-rc`), verify that at least one `vX.Y.Z-rc.*` tag exists. If not, warn the user and suggest creating an RC first.
   - If creating a new release branch, the first tag must be an RC (e.g., `vX.Y.0-rc.1`)

4. **Decide FLS action** from the gathered evidence. Compute:
   - `PINNED` = `FLS_VERSION` in `python/Containerfile` (must match flashers CLI/`flash()` defaults; warn if they diverge)
   - `LATEST` = latest FLS release tag (from local tags when using a checkout; otherwise GitHub tags with SemVer sort)
   - `CARGO` = `version` in FLS `Cargo.toml` on `main`
   - `AHEAD` = commits on `main` since `LATEST` (count + short log)

   **Version comparisons must use SemVer precedence**, not string/lexicographic order.
   Examples: `0.10.0 > 0.9.0`; prereleases sort below the matching final (`0.4.0-rc.1 < 0.4.0`).
   Prefer `git tag --sort=-version:refname`, `sort -V`, or an equivalent SemVer library — never raw string `>` / `<`.

   Recommend exactly one action:

   | Condition | Recommendation |
   |---|---|
   | `AHEAD > 0` (unreleased commits on FLS `main`) | **Cut new FLS release** (step 2B Phase 0), then bump Jumpstarter pins. Suggest next version: if SemVer(`CARGO`) > SemVer(`LATEST`), use `CARGO`; otherwise propose a patch bump of `LATEST`. |
   | `AHEAD == 0` and `PINNED != LATEST` | **Bump pins only** to `LATEST` (no new FLS release needed). |
   | `AHEAD == 0` and `PINNED == LATEST` | **Leave FLS unchanged**. |
   | SemVer(`CARGO`) != SemVer(`LATEST`) and `AHEAD == 0` | Warn: `Cargo.toml` version does not match the published release tag — ask the user before proceeding. |
   | Pin files disagree with each other | Warn and fix pins to a single version before tagging. |

   Present the recommendation with the supporting numbers (`PINNED`, `LATEST`, `CARGO`, `AHEAD`), then ask the user to confirm or override.

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
- If a new FLS version is needed, do step 2B next; otherwise tag the first RC via step 2C (`vX.Y.0-rc.1`)

### 2B. Release FLS and bump Jumpstarter pins

Only if the user selected type (B), or chose to bump FLS as part of type (C).

Do this **before** tagging Jumpstarter when a new FLS binary is required — the exporter `Containerfile` downloads FLS assets at image build time.

FLS lives in a separate repo (`jumpstarter-dev/fls`). Jumpstarter pins the version in two places that must stay in sync:

| Location | What to update |
|---|---|
| `python/Containerfile` | `ARG FLS_VERSION=X.Y.Z` (pre-installed binary in the exporter image) |
| `python/packages/jumpstarter-driver-flashers/jumpstarter_driver_flashers/client.py` | CLI `--fls-version` click default **and** `flash(..., fls_version=...)` method default |

#### Phase 0: Cut a new FLS release (skip if only bumping to an already-published version)

Work in the local `fls` checkout (commonly `~/dev/fls` or sibling of this repo):

```bash
cd /path/to/fls
git fetch origin
git checkout main
git pull origin main
```

1. **Bump `Cargo.toml` version** to the new FLS version (no `v` prefix), e.g. `version = "0.4.0"`.
2. **Commit and push** to `main` (or the release branch used by that repo).
3. **Create the GitHub Release** — creating the release triggers `.github/workflows/release.yml`, which builds and uploads binaries for each target. FLS tags have NO `v` prefix.

   Final release:

   ```bash
   gh release create 0.4.0 \
     --repo jumpstarter-dev/fls \
     --title "0.4.0" \
     --generate-notes
   ```

   RC release (`X.Y.Z-rc.N`) — mark as prerelease:

   ```bash
   gh release create 0.4.0-rc.1 \
     --repo jumpstarter-dev/fls \
     --title "0.4.0-rc.1" \
     --generate-notes \
     --prerelease
   ```

4. **Wait for FLS CI** to finish uploading assets (`fls-x86_64-linux`, `fls-aarch64-linux-musl`, etc.):

   ```bash
   gh run list --repo jumpstarter-dev/fls --workflow=release.yml --limit 3
   gh run watch --repo jumpstarter-dev/fls <RUN_ID>
   ```

   Confirm assets exist before continuing (use the FLS version from Phase 0):

   ```bash
   gh release view <NEW_FLS_VERSION> --repo jumpstarter-dev/fls
   ```

#### Phase 1: Bump FLS pins in Jumpstarter

On the Jumpstarter branch that will carry the bump (`main` for a PR, or `release-X.Y` when tagging):

1. **`python/Containerfile`** — set `ARG FLS_VERSION=` to the new version.
2. **`python/packages/jumpstarter-driver-flashers/jumpstarter_driver_flashers/client.py`** — update both defaults to the same version:
   - `flash(..., fls_version: str = "X.Y.Z", ...)`
   - `@click.option("--fls-version", ..., default="X.Y.Z", ...)`
3. **Commit** (or include these file changes in the same pre-tag commit as the operator Makefile bump in step 2C).

Do not proceed to Jumpstarter image builds until the FLS release assets are published.

### 2C. Tag a release

Only if the user selected type (C), or continuing from step 2A.

If a new FLS version is part of this release, complete step 2B (FLS release + pin bump) before or as part of Phase 1 below.

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

2. **FLS pins (if bumping)** — update both to the same FLS version (see step 2B):
   - `python/Containerfile` → `ARG FLS_VERSION=`
   - `python/packages/jumpstarter-driver-flashers/jumpstarter_driver_flashers/client.py` → `flash()` default and `--fls-version` click default

3. **Regenerate manifests:**
   ```bash
   cd controller/deploy/operator
   make manifests generate
   ```

4. **Commit and push** the changes to the release branch. Files to include:
   - `controller/deploy/operator/Makefile`
   - Any files changed by `make manifests generate`
   - FLS pin files (if bumped): `python/Containerfile`, `python/packages/jumpstarter-driver-flashers/jumpstarter_driver_flashers/client.py`

#### Phase 2: Tag and GitHub Release

5. **Create and push the git tag:**
   ```bash
   git tag vX.Y.Z       # or vX.Y.Z-rc.N
   git push origin vX.Y.Z
   ```

6. **Create the GitHub Release:**
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

When the build-images workflow succeeds, inform the user and proceed automatically to step 2D.

### 2D. Operator bundle contribution

Only if the user selected type (D), or continuing after step 2C.

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
- [ ] FLS released in `jumpstarter-dev/fls` (if new FLS version) and release assets uploaded
- [ ] FLS pins updated (`python/Containerfile` + flashers CLI/`flash()` defaults)
- [ ] Operator Makefile VERSION and REPLACES updated
- [ ] Git tag created and pushed
- [ ] GitHub Release created (with `--prerelease` for RCs)
- [ ] CI image build completed
- [ ] `operator-installer.yaml` asset uploaded (automated by CI)
- [ ] OLM bundle generated and verified (`make bundle`)
- [ ] Community-operators PRs created (`make contribute` + `gh pr create`)
- [ ] Infrastructure fixes cherry-picked to `main` (if applicable)
