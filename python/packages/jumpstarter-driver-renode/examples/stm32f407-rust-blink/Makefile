ELF = stm32f407-rust-blink.elf

# Help target
.PHONY: help
help:
	@echo "STM32F407 Rust blink firmware Makefile Help"
	@echo "============================================="
	@echo ""
	@echo "Build targets:"
	@echo "  build     - Build the release firmware"
	@echo "  strip     - Build and strip the release firmware"
	@echo "  firmware  - Build, strip, and install $(ELF)"
	@echo ""
	@echo "Cleaning targets:"
	@echo "  clean     - Remove the target/ build directory"

default: help

build:
	cargo build --release

strip:
	cargo strip --release

firmware: strip
	cp target/thumbv7em-none-eabihf/release/stm32f407-rust-blink $(ELF)

clean:
	cargo clean

.PHONY: default help build strip firmware clean
