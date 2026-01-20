PKG_TARGETS = $(subst packages/,,$(wildcard packages/*))

# Help target
.PHONY: help
help:
	@echo "Jumpstarter Makefile Help"
	@echo "=========================="
	@echo ""
	@echo "Build targets:"
	@echo "  build              - Build all packages"
	@echo "  protobuf-gen       - Generate code from protobuf definitions"
	@echo "  sync               - Sync all packages and extras"
	@echo ""
	@echo "Documentation targets:"
	@echo "  docs               - Build HTML documentation with warnings as errors"
	@echo "  docs-singlehtml    - Build single HTML page documentation"
	@echo "  docs-all           - Build multiversion documentation"
	@echo "  docs-serve         - Build and serve documentation locally"
	@echo "  docs-serve-all     - Build and serve multiversion documentation locally"
	@echo "  docs-linkcheck     - Check documentation links"
	@echo ""
	@echo "Testing targets:"
	@echo "  test               - Run all package tests and documentation tests"
	@echo "  pkg-test-all       - Run tests for all packages"
	@echo "  pkg-test-<pkg>     - Run tests for a specific package"
	@echo "  docs-test          - Run documentation tests"
	@echo ""
	@echo "Linting and type checking:"
	@echo "  ty                 - Run ty type checking on all packages"
	@echo "  pkg-ty-all       - Run ty on all packages"
	@echo "  pkg-ty-<pkg>     - Run ty on a specific package"
	@echo "  lint               - Run ruff linter"
	@echo "  lint-fix           - Run ruff linter with auto-fix"
	@echo ""
	@echo "Cleaning targets:"
	@echo "  clean              - Run all clean targets"
	@echo "  clean-venv         - Clean virtual environment"
	@echo "  clean-build        - Clean build artifacts"
	@echo "  clean-test         - Clean test artifacts"
	@echo "  clean-docs         - Clean documentation build"

default: help

docs-singlehtml:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs singlehtml

docs:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs html SPHINXOPTS="-W --keep-going -n"

docs-all:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs multiversion

docs-serve: clean-docs
	uv run --isolated --all-packages --group docs $(MAKE) -C docs serve

docs-serve-all: clean-docs docs-all
	uv run --isolated --all-packages --group docs $(MAKE) -C docs serve-multiversion

docs-test:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs doctest

docs-linkcheck:
	uv run --isolated --all-packages --group docs $(MAKE) -C docs linkcheck

pkg-test-%: packages/%
	uv run --isolated --directory $< pytest || [ $$? -eq 5 ]

pkg-ty-%: packages/%
	uv run --isolated --directory $< ty check .

pkg-test-all: $(addprefix pkg-test-,$(PKG_TARGETS))

pkg-ty-all: $(addprefix pkg-ty-,$(PKG_TARGETS))

build:
	uv build --all --out-dir dist

protobuf-gen:
	podman run --volume "$(shell pwd):/workspace" --workdir /workspace docker.io/bufbuild/buf:latest generate
	# Fix Python imports: convert absolute imports to relative imports
	# In jumpstarter/client/v1: from jumpstarter.v1 import -> from ...v1 import
	find packages/jumpstarter-protocol/jumpstarter_protocol/jumpstarter/client/v1 -name "*_pb2*.py" -type f -exec sed -i.bak \
		-e 's|^from jumpstarter\.v1 import|from ...v1 import|g' \
		-e 's|^from jumpstarter\.client\.v1 import|from . import|g' \
		{} \; -exec rm {}.bak \;
	# In jumpstarter/v1: from jumpstarter.v1 import -> from . import
	find packages/jumpstarter-protocol/jumpstarter_protocol/jumpstarter/v1 -name "*_pb2*.py" -type f -exec sed -i.bak \
		-e 's|^from jumpstarter\.v1 import|from . import|g' \
		{} \; -exec rm {}.bak \;

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

ty: pkg-ty-all

lint:
	uv run ruff check

lint-fix:
	uv run ruff check --fix

.PHONY: default help docs docs-all docs-serve docs-serve-all docs-clean docs-test \
	docs-linkcheck pkg-test-all pkg-ty-all build generate sync \
	clean-venv clean-build clean-test clean-all test-all ty-all docs \
	lint lint-fix \
	pkg-ty-jumpstarter \
	pkg-ty-jumpstarter-cli-admin \
	pkg-ty-jumpstarter-kubernetes \
	pkg-ty-jumpstarter-protocol
