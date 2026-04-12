DRIVER_INTERFACE_DIR = ../../controller/deploy/operator/config/driverinterfaces
EXPORTER_CLASS = example-board-exporterclass.yaml
ROOT = ../..

CODEGEN_CMD = jmp codegen \
	--exporter-class-file $(EXPORTER_CLASS) \
	--driver-interface-dir $(DRIVER_INTERFACE_DIR) \
	--test-fixtures

# ── Code generation ──────────────────────────────────────────────────

.PHONY: generate-all generate-python generate-java generate-typescript generate-rust

generate-all: generate-python generate-java generate-typescript generate-rust

generate-python:
	$(CODEGEN_CMD) --language python --output python/gen/

generate-java:
	$(CODEGEN_CMD) --language java --output java/gen/

generate-typescript:
	$(CODEGEN_CMD) --language typescript --output typescript/gen/

generate-rust:
	$(CODEGEN_CMD) --language rust --output rust/gen/

# ── Build (compile/type-check generated code) ────────────────────────

.PHONY: build-all build-python build-java build-typescript build-rust

build-all: build-python build-java build-typescript build-rust

build-python:
	@echo "Python: checking imports..."
	cd $(ROOT)/python && uv run python -c \
		"import sys; sys.path.insert(0, '../examples/polyglot/python/gen'); \
		from jumpstarter_gen.devices.example_board import ExampleBoardDevice; \
		print('  OK:', ExampleBoardDevice)"

build-java:
	@echo "Java: compiling..."
	cd $(ROOT) && ./gradlew :examples-polyglot-java:build -x test --quiet

build-typescript:
	@echo "TypeScript: building runtime + type-checking example..."
	cd $(ROOT) && npm --workspace=@jumpstarter/client run build
	cd typescript && npx tsc --noEmit

build-rust:
	@echo "Rust: checking..."
	cd $(ROOT) && cargo check -p jumpstarter-example-board --quiet

# ── Test (requires jmp shell) ────────────────────────────────────────
# Run: cd python && uv run jmp shell --exporter-config ../examples/polyglot/exporter.yaml -- make -C ../examples/polyglot test-all

.PHONY: test-all test-python test-java test-typescript test-rust

test-all: test-python test-java test-typescript test-rust

test-python:
	@echo "=== Python tests ==="
	cd $(ROOT)/python && uv run pytest ../examples/polyglot/python/test_example_board.py -v

test-java:
	@echo "=== Java tests ==="
	cd $(ROOT) && ./gradlew :examples-polyglot-java:test

test-typescript:
	@echo "=== TypeScript tests ==="
	cd typescript && npx vitest run --config vitest.config.ts

test-rust:
	@echo "=== Rust tests ==="
	cd $(ROOT) && cargo test -p jumpstarter-example-board-tests -- --ignored

# ── Clean ────────────────────────────────────────────────────────────

.PHONY: clean
clean:
	rm -rf python/gen/ java/gen/ typescript/gen/ rust/gen/
