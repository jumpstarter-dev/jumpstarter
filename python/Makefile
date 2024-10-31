DRIVER_TARGETS = $(subst contrib/drivers/,driver-,$(wildcard contrib/drivers/*))
LIB_TARGETS = $(subst contrib/libs/,lib-,$(wildcard contrib/libs/*))
EXAMPLE_TARGETS = $(subst examples/,example-,$(wildcard examples/*))
DOC_LISTEN ?= --host 127.0.0.1

default: build

docs: sync
	cd docs && uv run --group docs make html

serve-docs: sync
	cd docs && uv run --group docs make serve HOST="$(DOC_LISTEN)"

clean-docs:
	rm -rf ./docs/build

sync-jumpstarter:
	uv sync --all-extras --inexact

test-jumpstarter:
	uv run --isolated --package jumpstarter pytest jumpstarter tests

sync-driver-%: contrib/drivers/%
	uv sync --all-extras --inexact --package jumpstarter_driver_$(<F)

test-driver-%: contrib/drivers/%
	uv run --isolated --package jumpstarter_driver_$(<F) pytest $<

sync-lib-%: contrib/libs/%
	uv sync --all-extras --inexact --package jumpstarter_$(<F)

test-lib-%: contrib/libs/%
	uv run --isolated --package jumpstarter_$(<F) pytest $<

sync-contrib: $(addprefix sync-,$(DRIVER_TARGETS)) $(addprefix sync-,$(LIB_TARGETS))

test-contrib: $(addprefix test-,$(DRIVER_TARGETS)) $(addprefix sync-,$(LIB_TARGETS))

sync-example-%: examples/%
	uv sync --all-extras --inexact --package jumpstarter_example_$(<F)

sync-examples: $(addprefix sync-,$(EXAMPLE_TARGETS))

clean-venv:
	-rm -rf ./.venv
	-find . -type d -name __pycache__ -exec rm -r {} \+

clean-build:
	-rm -rf dist

clean-test:
	-rm .coverage
	-rm coverage.xml
	-rm -rf htmlcov

sync: sync-jumpstarter sync-contrib sync-examples

test: test-jumpstarter test-contrib

build:
	uv build --all --out-dir dist

clean: clean-docs clean-venv clean-build clean-test

.PHONY: sync docs test test-jumpstarter test-contrib build clean-test clean-docs clean-venv clean-build
