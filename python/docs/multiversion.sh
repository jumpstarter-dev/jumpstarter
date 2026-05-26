#!/usr/bin/env bash
set -euox pipefail

declare -a BRANCHES=("main" "release-0.6" "release-0.7" "release-0.8")

# https://stackoverflow.com/a/246128
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
OUTPUT_DIR="${SCRIPT_DIR}/build"

rm    -rf "${OUTPUT_DIR}"
mkdir -p  "${OUTPUT_DIR}"

for BRANCH in "${BRANCHES[@]}"; do
  WORKTREE="${OUTPUT_DIR}/.worktree/tmp-docs-${BRANCH}"

  git worktree add --force    "${WORKTREE}" "${BRANCH}"

  CRD_SCRIPT="${WORKTREE}/python/docs/source/reference/generate-crd-docs.py"
  if [[ -f "${CRD_SCRIPT}" ]]; then
    uv run --project "${WORKTREE}/python" --isolated --all-packages --group docs \
      python3 "${CRD_SCRIPT}"
  fi

  uv run --project "${WORKTREE}/python" --isolated --all-packages --group docs \
    make -C "${WORKTREE}/python/docs" html SPHINXOPTS="-D version=${BRANCH}"

  cp -r "${WORKTREE}/python/docs/build/html" "${OUTPUT_DIR}/${BRANCH}"

  git worktree remove --force "${WORKTREE}"
done

pushd "${OUTPUT_DIR}"
uvx --from git+https://github.com/steinwurf/versjon --with jinja2 versjon
popd
