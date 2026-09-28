//! Selects the private operating-system backend.
//!
//! Only `cfg(windows)` has an implementation today. Linux and macOS expose no
//! process or console types, stubs, or fallback behavior. The public facades own
//! the API contracts; backend modules own OS handles and native operations.
//!
//! Future implementations belong in sibling platform modules selected here.
//! Export their public facades only after the documented lifetime/error contracts
//! and platform-specific tests are satisfied. In particular, replacing process
//! containment with process groups must not silently weaken owner-death cleanup.

#[cfg(windows)]
mod windows;

#[cfg(windows)]
pub(crate) use windows::{console, process};
