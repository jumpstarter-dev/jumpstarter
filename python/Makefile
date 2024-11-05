DRIVER_TARGETS = $(subst contrib/drivers/,driver-,$(wildcard contrib/drivers/*))
LIB_TARGETS = $(subst contrib/libs/,lib-,$(wildcard contrib/libs/*))
EXAMPLE_TARGETS = $(subst examples/,example-,$(wildcard examples/*))
DOC_LISTEN ?= --host 127.0.0.1

default: build

docs:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs html

serve-docs:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs serve HOST="$(DOC_LISTEN)"

clean-docs:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs clean

test-jumpstarter:
	uv run --isolated --package jumpstarter pytest jumpstarter tests

test-driver-%: contrib/drivers/%
	uv run --isolated --directory $< pytest

test-lib-%: contrib/libs/%
	uv run --isolated --directory $< pytest

test-contrib: $(addprefix test-,$(DRIVER_TARGETS))

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

test: test-jumpstarter test-contrib

build:
	uv build --all --out-dir dist

clean: clean-docs clean-venv clean-build clean-test

.PHONY: sync docs test test-jumpstarter test-contrib build clean-test clean-docs clean-venv clean-build
