# Maintainers Guide

## Releasing

To release a new version of Jumpstarter, you need to follow these steps:

### set the version you want to release
```console
export VERSION_PYTHON=0.7.0
export VERSION_GO=0.7.0
export RELEASE_BRANCH=release-0.7
```

If you wish to create a pre-release you can add the desired suffixes, but please note that go
and python use sighlty different naming conventions.

For example:
```console
export VERSION_PYTHON=0.7.0rc1
export PYTHON_NEXT=0.8.0-dev
export VERSION_GO=0.7.0-rc1
export RELEASE_BRANCH=release-0.7
# the next version of python used as anchor for main branch versions with pip index
export PYTHON_NEXT=0.8.0-dev
```

### checkout and release the jumpstarter-e2e repository

```console
git clone https://github.com/jumpstarter-dev/jumpstarter-e2e.git
cd jumpstarter-e2e
```

For this one we don't tag versions, we just create a new branch.

```console
git fetch --all --tags # in case you already have the repository cloned
git checkout remotes/origin/main -B "${RELEASE_BRANCH}"
git push origin "${RELEASE_BRANCH}"
git checkout remotes/origin/main -B main
git tag -a "v${PYTHON_NEXT}" -m "Development base v${PYTHON_NEXT}"
git push origin "v${PYTHON_NEXT}"
```

### checkout and release the controller repository first

```console
git clone https://github.com/jumpstarter-dev/jumpstarter-controller.git
cd jumpstarter-controller
```

If it's the first time you are creating a tag for a minor version, you need to create a new branch first.
```console
git fetch --all --tags # in case you already have the repository cloned
git checkout remotes/origin/main -B "${RELEASE_BRANCH}"
git push origin "${RELEASE_BRANCH}"
```

Now just tag the branch
```console
git fetch --all --tags # in case you already have the repository cloned
git checkout remotes/origin/${RELEASE_BRANCH} -B ${RELEASE_BRANCH}
git tag -a "v${VERSION_GO}" -m "Release v${VERSION_GO}"
git push origin "v${VERSION_GO}"
```
### checkout and release the jumpstarter python packages repository

```console
git clone https://github.com/jumpstarter-dev/jumpstarter.git
cd jumpstarter
```

If it's the first time you are creating a tag for a minor version, you need to create a new branch first.
```console
git fetch --all --tags
git checkout remotes/origin/main -B "${RELEASE_BRANCH}"
git push origin "${RELEASE_BRANCH}"
```

Now just tag the branch
```console
git fetch --all --tags
git checkout remotes/origin/${RELEASE_BRANCH} -B ${RELEASE_BRANCH}
git tag -a "v${VERSION_PYTHON}" -m "Release v${VERSION_PYTHON}"
git push origin v${VERSION_PYTHON}
```

# Create release notes

* https://github.com/jumpstarter-dev/jumpstarter/releases/new
* https://github.com/jumpstarter-dev/jumpstarter-controller/releases/new

# Triggering a new package index build here

Using the Run Workflow from main:
https://github.com/jumpstarter-dev/packages/actions/workflows/publish-index.yml

# Publish to pypi
```console
export UV_PUBLISH_TOKEN=pypi-.....
git checkout v0.7.0
rm -rf dist
make build
uv publish
