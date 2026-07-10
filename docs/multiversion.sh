#!/usr/bin/env bash
set -euox pipefail

declare -a BRANCHES=("main" "release-0.6" "release-0.7" "release-0.8" "release-0.9")

# https://stackoverflow.com/a/246128
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
OUTPUT_DIR="${SCRIPT_DIR}/build"

rm    -rf "${OUTPUT_DIR}"
mkdir -p  "${OUTPUT_DIR}"

# Resolve the docs directory for a given worktree.
# Newer branches have docs/ at the repo root; older branches use python/docs/.
resolve_docs_dir() {
  local worktree="$1"
  if [[ -d "${worktree}/docs/source" ]]; then
    echo "${worktree}/docs"
  else
    echo "${worktree}/python/docs"
  fi
}

for BRANCH in "${BRANCHES[@]}"; do
  WORKTREE="${OUTPUT_DIR}/.worktree/tmp-docs-${BRANCH}"

  git worktree add --force    "${WORKTREE}" "${BRANCH}"
  trap 'git worktree remove --force "${WORKTREE}" 2>/dev/null || true' EXIT

  DOCS_DIR="$(resolve_docs_dir "${WORKTREE}")"

  CRD_SCRIPT="${DOCS_DIR}/source/reference/generate-crd-docs.py"
  if [[ -f "${CRD_SCRIPT}" ]]; then
    uv run --project "${WORKTREE}/python" --isolated --all-packages --group docs \
      python3 "${CRD_SCRIPT}"
  fi

  GRPC_SCRIPT="${DOCS_DIR}/source/reference/generate_grpc_docs.py"
  if [[ -f "${GRPC_SCRIPT}" ]]; then
    uv run --project "${WORKTREE}/python" --isolated --all-packages --group docs \
      python3 "${GRPC_SCRIPT}"
  fi

  uv run --project "${WORKTREE}/python" --isolated --all-packages --group docs \
    make -C "${DOCS_DIR}" html SPHINXOPTS="-D version=${BRANCH}"

  cp -r "${DOCS_DIR}/build/html" "${OUTPUT_DIR}/${BRANCH}"

  git worktree remove --force "${WORKTREE}"
  trap - EXIT
done

pushd "${OUTPUT_DIR}"
uvx --from git+https://github.com/steinwurf/versjon --with jinja2 versjon
popd
