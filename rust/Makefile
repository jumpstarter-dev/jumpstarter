.PHONY: build test clean check

build:
	cd .. && cargo build -p jumpstarter-client -p jumpstarter-testing -p jumpstarter-testing-macros

test:
	cd .. && cargo test -p jumpstarter-client -p jumpstarter-testing -p jumpstarter-testing-macros

check:
	cd .. && cargo check -p jumpstarter-client -p jumpstarter-testing -p jumpstarter-testing-macros

clean:
	cd .. && cargo clean
