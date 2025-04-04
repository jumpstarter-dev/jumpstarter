PKG_TARGETS = $(subst packages/,,$(wildcard packages/*))
DOC_LISTEN ?= --host 127.0.0.1

default: build

docs-build:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs html

docs-build-all:
	./docs/make-all-versions.sh

docs-serve:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs serve HOST="$(DOC_LISTEN)"

docs-serve-all:
	python3 -m http.server 8000 --bind 127.0.0.1 -d ./docs/build_all

docs-test:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs doctest

docs-linkcheck:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs linkcheck

pkg-test-%: packages/%
	uv run --isolated --directory $< pytest

pkg-mypy-%: packages/%
	uv run --isolated --directory $< mypy .

pkg-test-all: $(addprefix pkg-test-,$(PKG_TARGETS))

pkg-mypy-all: $(addprefix pkg-mypy-,$(PKG_TARGETS))

build:
	uv build --all --out-dir dist

generate:
	buf generate

sync:
	uv sync --all-packages --all-extras

clean-venv:
	-rm -rf ./.venv
	-find . -type d -name __pycache__ -exec rm -r {} \+

clean-build:
	-rm -rf dist

clean-test:
	-rm -f .coverage
	-rm -f coverage.xml
	-rm -rf htmlcov

clean-docs:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs clean

clean: clean-docs clean-venv clean-build clean-test

test: pkg-test-all docs-test

mypy: pkg-mypy-all

.PHONY: default docs-build docs-build-all docs-serve docs-serve-all docs-clean docs-test \
	docs-linkcheck pkg-test-all pkg-mypy-all build generate sync \
	clean-venv clean-build clean-test clean-all test-all mypy-all \
	pkg-mypy-jumpstarter \
	pkg-mypy-jumpstarter-cli-admin \
	pkg-mypy-jumpstarter-driver-can \
	pkg-mypy-jumpstarter-driver-dutlink \
	pkg-mypy-jumpstarter-driver-network \
	pkg-mypy-jumpstarter-driver-raspberrypi \
	pkg-mypy-jumpstarter-driver-sdwire \
	pkg-mypy-jumpstarter-driver-tftp \
	pkg-mypy-jumpstarter-driver-yepkit \
	pkg-mypy-jumpstarter-kubernetes \
	pkg-mypy-jumpstarter-protocol
