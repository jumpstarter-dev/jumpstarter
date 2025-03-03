#!/bin/env bash
set -euox pipefail


# Add excluded releases as we progress, for example
# release candidates once we have provided the final release
# or versions that are not meant to be published
EXCLUDED_RELEASES=("v0\.0\.[0-9]+" \
                   "v0\.5\.0rc[0-9]+")

# find the current branch name
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)

# This script is used to generate all versions of the documentation.
rm -rf docs/build_all 2>/dev/null || true
mkdir -p docs/build_all
RELEASES=$(git tag)

# remove excluded releases
for release in "${EXCLUDED_RELEASES[@]}"; do
    RELEASES=$(echo "${RELEASES}" | sed -E "s/$release//g")
done

RELEASES_WITHOUT_CURRENT="${RELEASES}"

# if current release not in the list, add it at the start
if [[ ! $RELEASES =~ $CURRENT_BRANCH ]]; then
    RELEASES="$CURRENT_BRANCH $RELEASES"
fi

# create a temporary directory to store the necessary files
TMPDIR=$(mktemp -d)

# copy the necessary files from the current branch, older
# branches may have different settings or nothing...
cp docs/source/_static/js/versions.js "${TMPDIR}/"
cp docs/source/_static/css/versions.css "${TMPDIR}/"
cp docs/source/_templates/sidebar/versions.html "${TMPDIR}/"
cp docs/source/conf.py.versions "${TMPDIR}/"


# generate a javascript array of all versions and store in docs/source/_static/js/version_array.js
echo "var version_array = [" > "${TMPDIR}/"/version_array.js
for release in $RELEASES; do
  # if release in semver format, remove the leading 'v'
  if [[ $release =~ ^v[0-9]+\.[0-9]+\.[0-9]+.*$ ]]; then
    release="${release/#v/}"
  fi
  release=$(echo "${release}" | tr '[:upper:]' '[:lower:]')
  echo "  '$release'," >> "${TMPDIR}/"/version_array.js
done

echo "];" >> "${TMPDIR}/"/version_array.js

for release in $RELEASES; do
    git checkout "${release}"
    # copy after checkout to avoid conflict
    mkdir -p docs/source/_static/js # some versions didn't have that directory
    cp "${TMPDIR}/"/version_array.js docs/source/_static/js/version_array.js
    if [ ! -f docs/source/_static/js/versions.js ]; then
        mkdir -p docs/source/_static/js
        mkdir -p docs/source/_static/css
        mkdir -p docs/source/_templates/sidebar
        cp "${TMPDIR}/"/versions.js docs/source/_static/js/versions.js
        cp "${TMPDIR}/"/versions.css docs/source/_static/css/versions.css
        cp "${TMPDIR}/"/versions.html docs/source/_templates/sidebar/versions.html
    fi
    # inject our configuration for version listing into the conf.py
    cat "${TMPDIR}/"/conf.py.versions >> docs/source/conf.py

    make clean
    make sync
    make docs

    git stash # un-do uv.lock
    git checkout HEAD
    # remove the front v from the release tag
    release="${release/#v/}"

    mv docs/build/html docs/build_all/"$(echo "${release}" | tr '[:upper:]' '[:lower:]')"
done

git checkout "${CURRENT_BRANCH}" -f

# use the current branch as the default redirect
REDIRECT="${CURRENT_BRANCH}"

# if redirect has semver format, remove the leading 'v'
if [[ $REDIRECT =~ ^v[0-9]+\.[0-9]+\.[0-9]+.*$ ]]; then
    REDIRECT=$(echo "${REDIRECT/#v/}" | tr '[:upper:]' '[:lower:]')
fi

# render redirect to the right branch
cat <<EOF > docs/build_all/index.html
<!DOCTYPE html>
<html>
    <title>Jumpstarter Documentation</title>
    <head>
        <meta http-equiv="refresh" content="0; url=./${REDIRECT}/" />
    </head>
    <body>
        <p>Welcome to the jumpstarter documentation, you will be redirected to the latest version.</p>
        <p>If you are not redirected automatically, follow this <a href="./${REDIRECT}/">link</a>.</p>
    </body>
</html>
EOF
