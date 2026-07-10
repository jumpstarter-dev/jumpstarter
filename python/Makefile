PKG_TARGETS = $(subst packages/,,$(wildcard packages/*))

# Help target
.PHONY: help
help:
	@echo "Jumpstarter Python Makefile Help"
	@echo "================================="
	@echo ""
	@echo "Build targets:"
	@echo "  build              - Build all packages"
	@echo "  protobuf-gen       - Generate code from protobuf definitions"
	@echo "  sync               - Sync all packages and extras"
	@echo ""
	@echo "Testing targets:"
	@echo "  test               - Run all package tests"
	@echo "  pkg-test-all       - Run tests for all packages"
	@echo "  pkg-test-<pkg>     - Run tests for a specific package"
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
	@echo ""
	@echo "Note: Documentation targets are in the root Makefile."

default: help


ifdef LOGS_DIR
pkg-test-%: packages/%
	@mkdir -p $(LOGS_DIR)
	@rm -f $(LOGS_DIR)/$*.failed
	@bash -c 'set -o pipefail; \
	PYTHONUNBUFFERED=1 uv run --isolated --directory $< pytest 2>&1 | tee $(LOGS_DIR)/$*.log; \
	rc=$$?; \
	if [ $$rc -ne 0 ] && [ $$rc -ne 5 ]; then \
		touch $(LOGS_DIR)/$*.failed; \
	fi; \
	true'
else
pkg-test-%: packages/%
	uv run --isolated --directory $< pytest || [ $$? -eq 5 ]
endif

pkg-ty-%: packages/%
	uv run --isolated --directory $< ty check .

pkg-test-all: sync $(addprefix pkg-test-,$(PKG_TARGETS))

ifdef LOGS_DIR
test-report:
	@failed=0; \
	for f in $(LOGS_DIR)/*.failed; do \
		[ -f "$$f" ] || continue; \
		pkg=$$(basename "$$f" .failed); \
		echo ""; \
		echo "========== FAILED: $$pkg =========="; \
		cat "$(LOGS_DIR)/$$pkg.log"; \
		failed=1; \
	done; \
	[ $$failed -eq 0 ] && echo "All package tests passed." || exit 1
endif

pkg-ty-all: $(addprefix pkg-ty-,$(PKG_TARGETS))

build:
	uv build --all --out-dir dist

protobuf-gen:
	podman run --volume "$(shell cd .. && pwd):/workspace" --workdir /workspace/python docker.io/bufbuild/buf:latest generate
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
	-rm -rf .uv-cache

clean: clean-venv clean-build clean-test

ifdef LOGS_DIR
test: pkg-test-all test-report
else
test: pkg-test-all
endif

ty: pkg-ty-all

lint:
	uv run ruff check

lint-fix:
	uv run ruff check --fix

.PHONY: default help pkg-test-all pkg-ty-all build generate sync \
	clean clean-venv clean-build clean-test test test-report ty \
	lint lint-fix \
	pkg-ty-jumpstarter \
	pkg-ty-jumpstarter-cli-admin \
	pkg-ty-jumpstarter-kubernetes \
	pkg-ty-jumpstarter-protocol
