# Jumpstarter TypeScript SDK Makefile
#
# Currently a single package (api/) lives under this directory. The
# Makefile is structured so additional TypeScript packages can be added by
# extending TS_PKGS — every target iterates the list and dispatches into
# each package via npm scripts.

TS_PKGS := api

NPM ?= npm

# Default goal mirrors the root convention: `make` builds.
.PHONY: all
all: build

# install runs `npm install` once per package. It's a prereq for every
# other target so contributors can run `make build` cold without an
# explicit install step.
.PHONY: install
install:
	@for pkg in $(TS_PKGS); do \
		echo "Installing $$pkg dependencies..."; \
		(cd $$pkg && $(NPM) install --no-audit --no-fund); \
	done

# generate regenerates TypeScript schema modules from the swagger.json
# artifacts produced by `make -C controller protobuf-gen`. It does NOT
# trigger that step itself — wiring this as a hard dependency would force
# every TS contributor to also have `buf` + Go installed. Run protobuf-gen
# explicitly when proto definitions change; the script noisily skips
# missing inputs with a warning otherwise.
.PHONY: generate
generate: install
	@for pkg in $(TS_PKGS); do \
		echo "Generating schemas in $$pkg..."; \
		(cd $$pkg && $(NPM) run generate); \
	done

# build runs `generate` then the package's tsup pipeline so dist/ contains
# CJS+ESM+.d.ts for every subpath export.
.PHONY: build
build: generate
	@for pkg in $(TS_PKGS); do \
		echo "Building $$pkg..."; \
		(cd $$pkg && $(NPM) run build); \
	done

# test is the tsc --strict smoke test. Pure type-check, no HTTP traffic.
# Generation runs first so schemas exist for the smoke test to reference.
.PHONY: test
test: generate
	@for pkg in $(TS_PKGS); do \
		echo "Testing $$pkg..."; \
		(cd $$pkg && $(NPM) test); \
	done

# clean removes everything generate / install / build can produce, leaving
# the source tree pristine.
.PHONY: clean
clean:
	@for pkg in $(TS_PKGS); do \
		echo "Cleaning $$pkg..."; \
		rm -rf $$pkg/node_modules $$pkg/dist $$pkg/src/_generated; \
	done

# lint and fmt are placeholders so the root Makefile's blanket loop over
# subprojects doesn't fail. Add eslint/prettier here when the SDK grows
# enough to need them.
.PHONY: lint
lint:
	@echo "typescript lint: nothing configured"

.PHONY: fmt
fmt:
	@echo "typescript fmt: nothing configured"
