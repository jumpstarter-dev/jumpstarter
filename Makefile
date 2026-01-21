# Jumpstarter Monorepo Makefile
#
# This Makefile provides common targets that delegate to subdirectory Makefiles.
#

# Subdirectories containing projects
SUBDIRS := python protocol controller e2e

# Default target
.PHONY: all
all: build

# Help target - shows available commands
.PHONY: help
help:
	@echo "Jumpstarter Monorepo"
	@echo ""
	@echo "Available targets:"
	@echo "  make all        - Build all projects (default)"
	@echo "  make build      - Build all projects"
	@echo "  make test       - Run tests in all projects"
	@echo "  make clean      - Clean build artifacts in all projects"
	@echo "  make lint       - Run linters in all projects"
	@echo "  make fmt        - Format code in all projects"
	@echo ""
	@echo "End-to-end testing:"
	@echo "  make e2e-setup  - Setup e2e test environment (one-time)"
	@echo "  make e2e-run    - Run e2e tests (requires e2e-setup first)"
	@echo "  make e2e        - Same as e2e-run"
	@echo "  make e2e-full   - Full setup + run (for CI or first time)"
	@echo "  make e2e-clean  - Clean up e2e test environment (delete cluster, certs, etc.)"
	@echo ""
	@echo "Per-project targets:"
	@echo "  make build-<project>  - Build specific project"
	@echo "  make test-<project>   - Test specific project"
	@echo "  make clean-<project>  - Clean specific project"
	@echo ""
	@echo "Projects: $(SUBDIRS)"

# Build all projects
.PHONY: build
build:
	@for dir in $(SUBDIRS); do \
		if [ -f $$dir/Makefile ]; then \
			echo "Building $$dir..."; \
			$(MAKE) -C $$dir build || true; \
		fi \
	done

# Test all projects
.PHONY: test
test:
	@for dir in $(SUBDIRS); do \
		if [ -f $$dir/Makefile ]; then \
			echo "Testing $$dir..."; \
			$(MAKE) -C $$dir test ; \
		fi \
	done

# Clean all projects
.PHONY: clean
clean:
	@for dir in $(SUBDIRS); do \
		if [ -f $$dir/Makefile ]; then \
			echo "Cleaning $$dir..."; \
			$(MAKE) -C $$dir clean || true; \
		fi \
	done

# Lint all projects
.PHONY: lint
lint:
	@for dir in $(SUBDIRS); do \
		if [ -f $$dir/Makefile ]; then \
			echo "Linting $$dir..."; \
			$(MAKE) -C $$dir lint; \
		fi \
	done

# Format all projects
.PHONY: fmt
fmt:
	@for dir in $(SUBDIRS); do \
		if [ -f $$dir/Makefile ]; then \
			echo "Formatting $$dir..."; \
			$(MAKE) -C $$dir fmt || true; \
		fi \
	done

# Per-project build targets
.PHONY: build-python build-protocol build-controller build-e2e
build-python:
	@if [ -f python/Makefile ]; then $(MAKE) -C python build; fi

build-protocol:
	@if [ -f protocol/Makefile ]; then $(MAKE) -C protocol build; fi

build-controller:
	@if [ -f controller/Makefile ]; then $(MAKE) -C controller build; fi

build-e2e:
	@if [ -f e2e/Makefile ]; then $(MAKE) -C e2e build; fi

# Per-project test targets
.PHONY: test-python test-protocol test-controller test-e2e
test-python:
	@if [ -f python/Makefile ]; then $(MAKE) -C python test; fi

test-protocol:
	@if [ -f protocol/Makefile ]; then $(MAKE) -C protocol test; fi

test-controller:
	@if [ -f controller/Makefile ]; then $(MAKE) -C controller test; fi

# Setup e2e testing environment (one-time)
.PHONY: e2e-setup
e2e-setup:
	@echo "Setting up e2e test environment..."
	@bash e2e/setup-e2e.sh

# Run e2e tests
.PHONY: e2e-run
e2e-run:
	@echo "Running e2e tests..."
	@bash e2e/run-e2e.sh

# Convenience alias for running e2e tests
.PHONY: e2e
e2e: e2e-run

# Full e2e setup + run
.PHONY: e2e-full
e2e-full:
	@bash e2e/run-e2e.sh --full

# Clean up e2e test environment
.PHONY: e2e-clean
e2e-clean:
	@echo "Cleaning up e2e test environment..."
	@if command -v kind >/dev/null 2>&1; then \
		echo "Deleting jumpstarter kind cluster..."; \
		kind delete cluster --name jumpstarter 2>/dev/null || true; \
	fi
	@echo "Removing certificates and setup files..."
	@rm -f ca.pem ca-key.pem ca.csr server.pem server-key.pem server.csr
	@rm -f .e2e-setup-complete
	@echo "Removing virtual environment..."
	@rm -rf .venv
	@if [ -d /etc/jumpstarter/exporters ] && [ -w /etc/jumpstarter/exporters ]; then \
		echo "Removing exporter configs..."; \
		rm -rf /etc/jumpstarter/exporters/* 2>/dev/null || true; \
	fi
	@echo "✓ E2E test environment cleaned"
	@echo ""
	@echo "Note: You may need to manually remove the dex entry from /etc/hosts:"
	@echo "  sudo sed -i.bak '/dex.dex.svc.cluster.local/d' /etc/hosts"

# Backward compatibility alias
.PHONY: test-e2e
test-e2e: e2e-run

# Per-project clean targets
.PHONY: clean-python clean-protocol clean-controller clean-e2e
clean-python:
	@if [ -f python/Makefile ]; then $(MAKE) -C python clean; fi

clean-protocol:
	@if [ -f protocol/Makefile ]; then $(MAKE) -C protocol clean; fi

clean-controller:
	@if [ -f controller/Makefile ]; then $(MAKE) -C controller clean; fi

clean-e2e:
	@if [ -f e2e/Makefile ]; then $(MAKE) -C e2e clean; fi
