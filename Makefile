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

test-e2e:
	@if [ -f e2e/Makefile ]; then $(MAKE) -C e2e test; fi

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
