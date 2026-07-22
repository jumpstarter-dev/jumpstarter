.PHONY: build test clean lint fmt

build:
	cargo build --workspace

test:
	cargo test --workspace

clean:
	cargo clean

lint:
	cargo clippy --workspace -- -D warnings

fmt:
	cargo fmt --all -- --check
