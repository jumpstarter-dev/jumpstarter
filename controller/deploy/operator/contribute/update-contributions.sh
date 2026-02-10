#!/bin/bash
set -euo pipefail

# Target repos: "clone-url local-directory"
# Add or remove entries here to sync to more/fewer upstreams.

GITHUB_USER=${GITHUB_USER:-"mangelajo"}

COMMUNITY_OPS=(
  "git@github.com:k8s-operatorhub/community-operators.git community-operators"
  "git@github.com:redhat-openshift-ecosystem/community-operators-prod.git community-operators-prod"
)

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
OPERATOR_DIR="${SCRIPT_DIR}/.."
BUNDLE_DIR="${OPERATOR_DIR}/bundle"
CSV_PATH="${BUNDLE_DIR}/manifests/jumpstarter-operator.clusterserviceversion.yaml"
RELEASE_CONFIG_PATH="${BUNDLE_DIR}/release-config.yaml"

if [ ! -f "$CSV_PATH" ]; then
  echo "Error: CSV not found at $CSV_PATH"
  echo "Run 'make bundle' or 'make contribute' from the operator directory first." >&2
  exit 1
fi

VERSION=$(grep "^  version:" "$CSV_PATH" | awk '{print $2}')
if [ -z "$VERSION" ]; then
  echo "Error: failed to extract VERSION from $CSV_PATH" >&2
  echo "Expected a line matching '^  version:' in $CSV_PATH but got nothing." >&2
  exit 1
fi

# Validate release-config.yaml for FBC auto-release
# See: https://redhat-openshift-ecosystem.github.io/operator-pipelines/users/fbc_autorelease/
if [ ! -f "$RELEASE_CONFIG_PATH" ]; then
  echo "Error: release-config.yaml not found at $RELEASE_CONFIG_PATH" >&2
  echo "" >&2
  echo "The FBC auto-release pipeline requires a release-config.yaml in the bundle directory." >&2
  echo "Set REPLACES in the Makefile and re-run 'make bundle', or create it manually:" >&2
  echo "" >&2
  echo "  cat > ${RELEASE_CONFIG_PATH} <<EOF" >&2
  echo "  ---" >&2
  echo "  catalog_templates:" >&2
  echo "    - template_name: basic.yaml" >&2
  echo "      channels: [alpha]" >&2
  echo "      replaces: jumpstarter-operator.vX.Y.Z" >&2
  echo "      skipRange: '>=X.Y.Z <${VERSION}'" >&2
  echo "  EOF" >&2
  echo "" >&2
  echo "Then re-run 'make contribute'." >&2
  exit 1
fi

REPLACES=$(grep "replaces:" "$RELEASE_CONFIG_PATH" | awk '{print $2}' | head -1)
CHANNELS=$(grep "channels:" "$RELEASE_CONFIG_PATH" | sed 's/.*channels: *\[//;s/\].*//' | head -1)
SKIP_RANGE=$(grep "skipRange:" "$RELEASE_CONFIG_PATH" | sed "s/.*skipRange: *['\"]//;s/['\"]$//" | head -1)

# Extract the version from the replaces field (strip "jumpstarter-operator.v" prefix)
if [ -n "$REPLACES" ]; then
  REPLACES_VERSION="${REPLACES#jumpstarter-operator.v}"
  if [ "$REPLACES_VERSION" = "$VERSION" ]; then
    echo "Error: 'replaces' version ($REPLACES_VERSION) is the same as the bundle version ($VERSION)." >&2
    echo "Update REPLACES in the Makefile to point to the previous release." >&2
    exit 1
  fi
fi

echo ""
echo "============================================"
echo "  Bundle version : ${VERSION}"
echo "  Channels       : ${CHANNELS:-"(not set)"}"
echo "  Replaces       : ${REPLACES:-"(not set)"}"
echo "  Skip range     : ${SKIP_RANGE:-"(not set)"}"
echo "============================================"
echo ""
read -r -p "Proceed with contribution? [y/N] " confirm
if [[ ! "$confirm" =~ ^[Yy]$ ]]; then
  echo ""
  echo "Aborted. To update these values, edit:"
  echo "  - VERSION / REPLACES in: ${OPERATOR_DIR}/Makefile"
  echo "  - Or directly: ${RELEASE_CONFIG_PATH}"
  echo "Then re-run 'make bundle' followed by 'make contribute'."
  exit 1
fi

# Clone any missing repos and ensure "user" remote points to the GITHUB_USER fork
for entry in "${COMMUNITY_OPS[@]}"; do
  read -r url dir <<< "$entry"
  # Derive the repo name (e.g. "community-operators") from the clone URL
  repo_name="$(basename "$url" .git)"
  user_fork="git@github.com:${GITHUB_USER}/${repo_name}.git"

  if [ ! -d "$dir" ]; then
    echo "Cloning $url into $dir ..."
    git clone "$url" "$dir"
  else
    echo "Using existing directory: $dir"
  fi

  # Add or update the "user" remote for the fork
  (
    cd "$dir" || exit 1
    if git remote get-url user &>/dev/null; then
      git remote set-url user "$user_fork"
    else
      git remote add user "$user_fork"
    fi
    echo "  user remote -> $user_fork"
  )
done

# Create/reset release branch from main in each repo
BRANCH="jumpstarter-operator-release-${VERSION}"
for entry in "${COMMUNITY_OPS[@]}"; do
  read -r url dir <<< "$entry"
  echo "--- $dir: switching to branch $BRANCH ---"
  (cd "$dir" || exit 1; git fetch --all && git checkout remotes/origin/main -B "$BRANCH")
done

# Copy bundle into each repo and commit
for entry in "${COMMUNITY_OPS[@]}"; do
  read -r url dir <<< "$entry"
  dest="${dir}/operators/jumpstarter-operator/${VERSION}"
  echo "Updating ${dir} to version ${VERSION}"
  mkdir -p "$dest"
  cp -v -r -f "${BUNDLE_DIR}"/* "$dest"
  (cd "$dir" || exit 1; git add "operators/jumpstarter-operator/${VERSION}" && git commit -s -m "operator jumpstarter-operator (${VERSION})")
done

echo ""
echo "Done. You can review the commits and push to your fork:"
for entry in "${COMMUNITY_OPS[@]}"; do
  read -r url dir <<< "$entry"
  echo "  cd contribute/$dir && git push -f user ${BRANCH}"
done
