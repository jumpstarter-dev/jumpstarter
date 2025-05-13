#!/usr/bin/env bash
set -euox pipefail

declare -a BRANCHES=("main" "release-0.5" "release-0.6")

# https://stackoverflow.com/a/246128
SCRIPT_DIR=$( cd -- "$( dirname -- "${BASH_SOURCE[0]}" )" &> /dev/null && pwd )
OUTPUT_DIR="${SCRIPT_DIR}/build"

rm    -rf "${OUTPUT_DIR}"
mkdir -p  "${OUTPUT_DIR}"

for BRANCH in "${BRANCHES[@]}"; do
  WORKTREE="${OUTPUT_DIR}/.worktree/tmp-docs-${BRANCH}"

  git worktree add --force    "${WORKTREE}" "${BRANCH}"

  uv run --project "${WORKTREE}" --isolated --all-packages --group docs \
    make -C "${WORKTREE}/docs" html SPHINXOPTS="-D version=${BRANCH}"

  cp -r "${WORKTREE}/docs/build/html" "${OUTPUT_DIR}/${BRANCH}"

  git worktree remove --force "${WORKTREE}"
done

pushd "${OUTPUT_DIR}"
uvx --from git+https://github.com/steinwurf/versjon --with jinja2 versjon
popd
