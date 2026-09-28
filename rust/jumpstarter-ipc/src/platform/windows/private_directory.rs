//! Win32 security descriptors are needed here because a general temporary
//! directory inherits its parent's ACL, which is not necessarily private.
//!
//! `CreateDirectoryW` applies a protected DACL at creation that grants full
//! control only to the owner and SYSTEM, without querying the user's SID:
//! OWNER RIGHTS covers the directory itself, and an inherit-only CREATOR OWNER
//! entry gives each child (the socket, or a key file a client stores there) an
//! entry for its owner's actual SID. Children must not inherit OWNER RIGHTS:
//! tools such as OpenSSH reject key files whose ACL names that group. The
//! resulting DACL is read back and compared before returning the directory,
//! which also rejects filesystems without ACLs.
//! Random names come from the system RNG through `getrandom`; an existing
//! colliding path is not reused.
//!
//! The reserved AF_UNIX path must be absolute, valid UTF-8, NUL-free, and at
//! most 107 UTF-8 bytes. The final path length is checked before creation; an
//! overlong base is an error, and the caller can supply a shorter base. There
//! is no network-transport fallback.
//!
//! Cleanup removes only the reserved socket file and the empty directory,
//! never recursively deleting unrelated children. Explicit close retains
//! ownership after a failure so cleanup can be retried; drop is best effort.

use std::ffi::{c_void, OsStr};
use std::io;
use std::mem::size_of;
use std::os::windows::ffi::OsStrExt;
use std::path::{Path, PathBuf};
use std::ptr;
use std::sync::Mutex;

use windows_sys::Win32::Foundation::{LocalFree, ERROR_ALREADY_EXISTS};
use windows_sys::Win32::Security::Authorization::{
    ConvertSecurityDescriptorToStringSecurityDescriptorW,
    ConvertStringSecurityDescriptorToSecurityDescriptorW, GetNamedSecurityInfoW, SDDL_REVISION_1,
    SE_FILE_OBJECT,
};
use windows_sys::Win32::Security::{DACL_SECURITY_INFORMATION, SECURITY_ATTRIBUTES};
use windows_sys::Win32::Storage::FileSystem::CreateDirectoryW;

use super::lock;

const SOCKET_NAME: &str = "s";
const DIRECTORY_PREFIX: &str = "jmp-";
/// Full control for the directory's owner and SYSTEM; children inherit
/// entries for their owner's SID and SYSTEM.
const PRIVATE_ACES: &str = "(A;;FA;;;OW)(A;OICIIO;FA;;;CO)(A;OICI;FA;;;SY)";

/// A buffer returned by a Win32 API that must be released with `LocalFree`.
struct LocalAllocation(*mut c_void);

impl Drop for LocalAllocation {
    fn drop(&mut self) {
        // SAFETY: this owns a buffer returned by a LocalAlloc-based Win32 API.
        unsafe {
            LocalFree(self.0);
        }
    }
}

fn wide(value: &OsStr) -> io::Result<Vec<u16>> {
    let mut value: Vec<_> = value.encode_wide().collect();
    if value.contains(&0) {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "path contains a NUL character",
        ));
    }
    value.push(0);
    Ok(value)
}

pub(super) fn validate_socket_path(path: &Path) -> io::Result<()> {
    let text = path.to_str().ok_or_else(|| {
        io::Error::new(
            io::ErrorKind::InvalidInput,
            "Windows AF_UNIX requires a valid UTF-8 socket path",
        )
    })?;
    if !path.is_absolute() || text.contains('\0') {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "Windows local socket paths must be absolute and contain no NUL character",
        ));
    }
    if text.len() > 107 {
        return Err(io::Error::new(io::ErrorKind::InvalidInput, format!(
            "Windows local socket path is {} UTF-8 bytes (maximum 107). Set XDG_RUNTIME_DIR or TEMP to a shorter directory; no TCP fallback is used",
            text.len(),
        )));
    }
    Ok(())
}

fn private_security_descriptor() -> io::Result<LocalAllocation> {
    let sddl = wide(OsStr::new(&format!("D:P{PRIVATE_ACES}")))?;
    let mut descriptor = ptr::null_mut();
    // SAFETY: a NUL-terminated SDDL string and a valid output pointer; the
    // descriptor is LocalAlloc-owned and released by LocalAllocation.
    if unsafe {
        ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl.as_ptr(),
            SDDL_REVISION_1,
            &mut descriptor,
            ptr::null_mut(),
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    Ok(LocalAllocation(descriptor))
}

/// Reads a path's DACL as SDDL, for example `D:PAI(A;;FA;;;OW)(...)`.
fn dacl_sddl(path: &Path) -> io::Result<String> {
    let path = wide(path.as_os_str())?;
    let mut descriptor = ptr::null_mut();
    // SAFETY: a NUL-terminated path; only the descriptor output is requested,
    // and it is LocalAlloc-owned and released by LocalAllocation.
    let result = unsafe {
        GetNamedSecurityInfoW(
            path.as_ptr(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            ptr::null_mut(),
            &mut descriptor,
        )
    };
    if result != 0 {
        return Err(io::Error::from_raw_os_error(result as i32));
    }
    let descriptor = LocalAllocation(descriptor);
    let mut text: *mut u16 = ptr::null_mut();
    let mut length = 0;
    // SAFETY: a descriptor returned by GetNamedSecurityInfoW and valid output
    // pointers. On success, `text` holds `length` UTF-16 units, including
    // terminating NULs, in a LocalAlloc buffer that is released below.
    let text = unsafe {
        if ConvertSecurityDescriptorToStringSecurityDescriptorW(
            descriptor.0,
            SDDL_REVISION_1,
            DACL_SECURITY_INFORMATION,
            &mut text,
            &mut length,
        ) == 0
        {
            return Err(io::Error::last_os_error());
        }
        let owned = LocalAllocation(text.cast());
        let units = std::slice::from_raw_parts(text, length as usize);
        let end = units
            .iter()
            .position(|&unit| unit == 0)
            .unwrap_or(units.len());
        let value = String::from_utf16_lossy(&units[..end]);
        drop(owned);
        value
    };
    Ok(text)
}

/// Accepts a protected DACL with exactly the private entries; the automatic
/// inheritance flags (`AI`/`AR`) do not affect access.
fn verify_private_directory(path: &Path) -> io::Result<()> {
    let sddl = dacl_sddl(path)?;
    let private = sddl
        .strip_prefix("D:")
        .and_then(|rest| rest.split_once('('))
        .is_some_and(|(flags, aces)| {
            flags.contains('P')
                && !flags.contains("NO_ACCESS_CONTROL")
                && format!("({aces}") == PRIVATE_ACES
        });
    if !private {
        return Err(io::Error::new(
            io::ErrorKind::PermissionDenied,
            format!("local socket directory must have a protected DACL granting only its owner and SYSTEM, found {sddl}"),
        ));
    }
    Ok(())
}

fn directory_base(base: Option<&Path>) -> io::Result<PathBuf> {
    let base = base
        .map(Path::to_path_buf)
        .unwrap_or_else(std::env::temp_dir);
    if !base.is_absolute() || !base.is_dir() {
        return Err(io::Error::new(
            io::ErrorKind::InvalidInput,
            "the local socket temporary base must be an existing absolute directory",
        ));
    }
    // Validate the maximum final length before creating anything. The name is
    // always 32 random hex characters, plus the short directory/socket prefix.
    validate_socket_path(
        &base
            .join(format!("{DIRECTORY_PREFIX}{}", "0".repeat(32)))
            .join(SOCKET_NAME),
    )?;
    Ok(base)
}

struct DirectoryState {
    directory: PathBuf,
    socket_path: PathBuf,
}

impl DirectoryState {
    fn create(base: Option<&Path>) -> io::Result<Self> {
        let base = directory_base(base)?;
        let descriptor = private_security_descriptor()?;
        let attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor.0,
            bInheritHandle: 0,
        };
        for _ in 0..8 {
            let mut random = [0u8; 16];
            getrandom::fill(&mut random).map_err(|error| io::Error::other(error.to_string()))?;
            let name: String = random.iter().map(|byte| format!("{byte:02x}")).collect();
            let directory = base.join(format!("{DIRECTORY_PREFIX}{name}"));
            let socket_path = directory.join(SOCKET_NAME);
            validate_socket_path(&socket_path)?;
            let path = wide(directory.as_os_str())?;
            // SAFETY: a NUL-terminated path and a live security descriptor.
            // Creation and the restrictive DACL are atomic; there is no
            // permissive phase.
            if unsafe { CreateDirectoryW(path.as_ptr(), &attributes) } == 0 {
                let error = io::Error::last_os_error();
                if error.raw_os_error() == Some(ERROR_ALREADY_EXISTS as i32) {
                    continue;
                }
                return Err(error);
            }
            let result = Self {
                directory,
                socket_path,
            };
            if let Err(error) = verify_private_directory(&result.directory) {
                // Empty directory just created by us; never recurse or remove
                // anything from a parent or a preexisting colliding path.
                let _ = std::fs::remove_dir(&result.directory);
                return Err(error);
            }
            return Ok(result);
        }
        Err(io::Error::new(
            io::ErrorKind::AlreadyExists,
            "could not allocate a unique private socket directory",
        ))
    }

    fn cleanup(&self) -> io::Result<()> {
        match std::fs::remove_file(&self.socket_path) {
            Ok(()) => {}
            Err(error) if error.kind() == io::ErrorKind::NotFound => {}
            Err(error) => return Err(error),
        }
        match std::fs::remove_dir(&self.directory) {
            Ok(()) => Ok(()),
            Err(error) if error.kind() == io::ErrorKind::NotFound => Ok(()),
            Err(error) => Err(error),
        }
    }
}

/// Owns a random private directory and its single reserved socket pathname.
///
/// Creation atomically sets and verifies a protected owner/SYSTEM DACL.
/// Close removes only the known socket file and empty directory; unrelated
/// children cause an error. Drop attempts the same cleanup without panicking.
/// Close all sockets first, and call close explicitly to observe cleanup errors.
pub struct PrivateDirectory {
    directory: Mutex<Option<DirectoryState>>,
    socket_path: PathBuf,
}

impl PrivateDirectory {
    /// Create below an existing absolute base, or the process temporary
    /// directory if absent, after validating the 107-byte UTF-8 AF_UNIX limit.
    pub fn create(base: Option<&Path>) -> io::Result<Self> {
        let directory = DirectoryState::create(base)?;
        let socket_path = directory.socket_path.clone();
        Ok(Self {
            directory: Mutex::new(Some(directory)),
            socket_path,
        })
    }

    /// The absolute UTF-8 path reserved for this directory's socket.
    pub fn socket_path(&self) -> &Path {
        &self.socket_path
    }

    /// Idempotently clean up. On failure ownership is retained so callers can
    /// remove unrelated children or close open handles and retry.
    pub fn close(&self) -> io::Result<()> {
        let mut guard = lock(&self.directory)?;
        if let Some(directory) = guard.as_ref() {
            directory.cleanup()?;
        }
        guard.take();
        Ok(())
    }
}

impl Drop for PrivateDirectory {
    fn drop(&mut self) {
        if let Ok(Some(directory)) = self.directory.get_mut().map(Option::take) {
            let _ = directory.cleanup();
        }
    }
}
