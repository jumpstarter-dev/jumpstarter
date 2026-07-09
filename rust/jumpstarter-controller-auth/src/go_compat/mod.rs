//! Bit-exact ports of the Go primitives the key derivation depends on.
pub mod ecdsa_legacy;
pub mod gorand;

pub use ecdsa_legacy::{derive_key_from_seed, DeriveKeyError};
