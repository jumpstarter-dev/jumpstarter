#!/bin/bash

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

if [ ! -f "$CSV_PATH" ]; then
  echo "Error: CSV not found at $CSV_PATH"
  echo "Run 'make bundle' or 'make contribute' from the operator directory first." >&2
  exit 1
fi

VERSION=$(grep "^  version:" "$CSV_PATH" | awk '{print $2}')
echo "Bundle version: ${VERSION}"

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
    cd "$dir"
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
  (cd "$dir" && git fetch --all && git checkout remotes/origin/main -B "$BRANCH")
done

# Copy bundle into each repo and commit
for entry in "${COMMUNITY_OPS[@]}"; do
  read -r url dir <<< "$entry"
  dest="${dir}/operators/jumpstarter-operator/${VERSION}"
  echo "Updating ${dir} to version ${VERSION}"
  mkdir -p "$dest"
  cp -v -r -f "${BUNDLE_DIR}"/* "$dest"
  (cd "$dir" && git add -A && git commit -s -m "operator jumpstarter-operator (${VERSION})")
done

echo ""
echo "Done. You can review the commits and push to your fork:"
for entry in "${COMMUNITY_OPS[@]}"; do
  read -r url dir <<< "$entry"
  echo "  cd $dir && git push -f user ${BRANCH}"
done
