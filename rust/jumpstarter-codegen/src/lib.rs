//! Proto-first cross-language driver codegen.
//!
//! The `.proto` is the single source of truth. This crate is the hand-rolled engine
//! that turns a compiled [`FileDescriptorSet`](prost_reflect::prost_types::FileDescriptorSet)
//! (produced by `buf build` / `protoc` / `tonic-build`) into a language-neutral
//! [`ir::InterfaceRef`], then drives per-language string-building generators through
//! the [`languages::LanguageGenerator`] seam.
//!
//! - [`ir`] — language-neutral intermediate representation (services, methods, messages,
//!   enums), ported from the jep-14 Python codegen models.
//! - [`engine`] — the descriptor-walking half: `FileDescriptorSet` → `Vec<InterfaceRef>`,
//!   decoded exactly like `jumpstarter-driver-core`'s `driver.rs` `build_native_backend`.
//! - [`languages`] — the `LanguageGenerator` trait plus per-language generators
//!   (`rust`, `java`). The trait is the contract per-language generators code against;
//!   the generators themselves are filled in by the parallel codegen phase.

pub mod ir;
pub mod engine;
pub mod languages;
pub mod resolver;
pub mod device;

/// Build-script helper for a driver crate (feature `build-helper`): one call compiles the interface
/// proto + generates the client + macros + the `jumpstarter_generated.rs` aggregator.
#[cfg(feature = "build-helper")]
pub mod build;
