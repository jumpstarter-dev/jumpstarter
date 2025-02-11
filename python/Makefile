PKG_TARGETS = $(subst packages/,,$(wildcard packages/*))
EXAMPLE_TARGETS = $(subst examples/,example-,$(wildcard examples/*))
DOC_LISTEN ?= --host 127.0.0.1

default: build

docs:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs html

serve-docs:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs serve HOST="$(DOC_LISTEN)"

clean-docs:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs clean

doctest:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs doctest

test-%: packages/%
	uv run --isolated --directory $< pytest

mypy-%: packages/%
	uv run --isolated --directory $< mypy .

test-packages: $(addprefix test-,$(PKG_TARGETS))

mypy-packages: $(addprefix mypy-,$(PKG_TARGETS))

clean-venv:
	-rm -rf ./.venv
	-find . -type d -name __pycache__ -exec rm -r {} \+

clean-build:
	-rm -rf dist

clean-test:
	-rm .coverage
	-rm coverage.xml
	-rm -rf htmlcov

sync:
	uv sync --all-packages --all-extras

test: test-packages doctest

mypy: mypy-packages

generate:
	buf generate

build:
	uv build --all --out-dir dist

clean: clean-docs clean-venv clean-build clean-test

.PHONY: sync docs test test-packages build clean-test clean-docs clean-venv clean-build
