//! Authentication and authorization for the Rust controller, mirroring
//! `controller/internal/{oidc,authentication,authorization}`.
//!
//! The internal token issuer derives its ES256 key **bit-exactly** like the Go
//! controller (`internal/oidc/op.go` `NewSignerFromSeed`: sha256(CONTROLLER_KEY)
//! -> seeded Go `math/rand` -> `keygen.ECDSALegacy(P256)`), locked by golden
//! vectors generated from the Go code (`controller/hack/goldenvec`). A
//! `CONTROLLER_PRIVATE_KEY_PEM` override takes precedence when set (rotation /
//! HSM escape hatch — an approved deliberate addition).

pub mod authorize;
pub mod discovery;
pub mod go_compat;
pub mod normalize;
pub mod router_token;
pub mod signer;
pub mod validator;
