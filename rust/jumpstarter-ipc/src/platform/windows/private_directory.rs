//! Win32 security descriptors are needed here because a general temporary
//! directory inherits its parent's ACL, which is not necessarily private.
//!
//! `CreateDirectoryW` applies a protected DACL at creation, with inheritable
//! full-control entries for the current user and SYSTEM only. The resulting
//! descriptor is verified before returning the directory. Random names come
//! from the system cryptographic RNG; an existing colliding path is not reused.
//!
//! The reserved AF_UNIX path must be absolute, valid UTF-8, NUL-free, and at
//! most 107 UTF-8 bytes. The final path length is checked before creation.
//! Windows short-path aliases may shorten an existing base; if that cannot
//! satisfy the limit, the caller receives an error and can supply a shorter
//! base. There is no network-transport fallback.
//!
//! Cleanup removes only the reserved socket file and the empty directory,
//! never recursively deleting unrelated children. Explicit close retains
//! ownership after a failure so cleanup can be retried; drop is best effort.

use std::ffi::{c_void, OsStr, OsString};
use std::io;
use std::mem::size_of;
use std::os::windows::ffi::{OsStrExt, OsStringExt};
use std::path::{Path, PathBuf};
use std::ptr;
use std::sync::Mutex;

use windows_sys::Win32::Foundation::{CloseHandle, LocalFree, ERROR_ALREADY_EXISTS, HANDLE};
use windows_sys::Win32::Security::Authorization::{
    ConvertSidToStringSidW, ConvertStringSecurityDescriptorToSecurityDescriptorW,
    GetNamedSecurityInfoW, SDDL_REVISION_1, SE_FILE_OBJECT,
};
use windows_sys::Win32::Security::Cryptography::{
    BCryptGenRandom, BCRYPT_USE_SYSTEM_PREFERRED_RNG,
};
use windows_sys::Win32::Security::{
    GetAce, GetSecurityDescriptorControl, GetTokenInformation, IsValidAcl, IsValidSid, TokenUser,
    ACCESS_ALLOWED_ACE, ACL, CONTAINER_INHERIT_ACE, DACL_SECURITY_INFORMATION, OBJECT_INHERIT_ACE,
    SECURITY_ATTRIBUTES, SE_DACL_PROTECTED, TOKEN_QUERY, TOKEN_USER,
};
use windows_sys::Win32::Storage::FileSystem::{
    CreateDirectoryW, GetShortPathNameW, FILE_ALL_ACCESS,
};
use windows_sys::Win32::System::SystemServices::ACCESS_ALLOWED_ACE_TYPE;
use windows_sys::Win32::System::Threading::{GetCurrentProcess, OpenProcessToken};

use super::lock;

const SOCKET_NAME: &str = "s";
const DIRECTORY_PREFIX: &str = "jmp-";
const SYSTEM_SID: &str = "S-1-5-18";

struct LocalAllocation(*mut c_void);

impl Drop for LocalAllocation {
    fn drop(&mut self) {
        // SAFETY: this owns a buffer returned by a LocalAlloc-based Win32 API.
        unsafe {
            LocalFree(self.0);
        }
    }
}

struct Token(HANDLE);

impl Drop for Token {
    fn drop(&mut self) {
        // SAFETY: OpenProcessToken returned this owned, valid token handle.
        unsafe {
            CloseHandle(self.0);
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

fn sid_string(sid: *mut c_void) -> io::Result<String> {
    // SAFETY: callers provide a SID owned by the token/ACL still alive here.
    if unsafe { IsValidSid(sid) } == 0 {
        return Err(io::Error::new(
            io::ErrorKind::InvalidData,
            "invalid Windows security identifier",
        ));
    }
    let mut text = ptr::null_mut();
    // SAFETY: a validated SID and valid output pointer; output is LocalFree-owned.
    if unsafe { ConvertSidToStringSidW(sid, &mut text) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let _allocation = LocalAllocation(text.cast());
    let mut len = 0;
    // SAFETY: ConvertSidToStringSidW returns a NUL-terminated UTF-16 string.
    unsafe {
        while *text.add(len) != 0 {
            len += 1;
        }
        Ok(String::from_utf16_lossy(std::slice::from_raw_parts(
            text, len,
        )))
    }
}

fn current_user_sid() -> io::Result<String> {
    let mut handle = ptr::null_mut();
    // SAFETY: valid process pseudo-handle and writable output pointer.
    if unsafe { OpenProcessToken(GetCurrentProcess(), TOKEN_QUERY, &mut handle) } == 0 {
        return Err(io::Error::last_os_error());
    }
    let token = Token(handle);
    let mut length = 0;
    // SAFETY: a zero-length query obtains the required token buffer size.
    unsafe {
        GetTokenInformation(token.0, TokenUser, ptr::null_mut(), 0, &mut length);
    }
    if length < size_of::<TOKEN_USER>() as u32 {
        return Err(io::Error::last_os_error());
    }
    // TOKEN_USER contains a pointer, so byte-vector alignment is insufficient.
    let mut buffer = vec![0usize; (length as usize).div_ceil(size_of::<usize>())];
    // SAFETY: the aligned allocation is at least the queried byte length.
    if unsafe {
        GetTokenInformation(
            token.0,
            TokenUser,
            buffer.as_mut_ptr().cast(),
            length,
            &mut length,
        )
    } == 0
    {
        return Err(io::Error::last_os_error());
    }
    // SAFETY: the successful TokenUser query initialized TOKEN_USER and its SID
    // in buffer; neither is used after the buffer is released.
    let user = unsafe { &*buffer.as_ptr().cast::<TOKEN_USER>() };
    sid_string(user.User.Sid)
}

fn security_descriptor(user: &str) -> io::Result<LocalAllocation> {
    // Protected DACL: no parent inheritance. Child files/subdirectories inherit
    // only these current-user and SYSTEM full-control ACEs.
    let sddl = format!("D:P(A;OICI;FA;;;{user})(A;OICI;FA;;;SY)");
    let sddl = wide(OsStr::new(&sddl))?;
    let mut descriptor = ptr::null_mut();
    // SAFETY: NUL-terminated SDDL and valid output pointer; API owns allocation.
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

fn verify_private_directory(path: &Path, user: &str) -> io::Result<()> {
    let path = wide(path.as_os_str())?;
    let mut descriptor = ptr::null_mut();
    let mut dacl: *mut ACL = ptr::null_mut();
    // SAFETY: path is terminated; all optional output pointers are NULL except
    // DACL and the descriptor owning it. The descriptor is LocalFree-owned.
    let result = unsafe {
        GetNamedSecurityInfoW(
            path.as_ptr(),
            SE_FILE_OBJECT,
            DACL_SECURITY_INFORMATION,
            ptr::null_mut(),
            ptr::null_mut(),
            &mut dacl,
            ptr::null_mut(),
            &mut descriptor,
        )
    };
    if result != 0 {
        return Err(io::Error::from_raw_os_error(result as i32));
    }
    let _allocation = LocalAllocation(descriptor);
    let denied = || {
        io::Error::new(io::ErrorKind::PermissionDenied,
        "local socket directory must have a protected DACL granting only the current user and SYSTEM")
    };
    let mut control = 0;
    let mut revision = 0;
    // SAFETY: descriptor returned successfully from the security API.
    if unsafe { GetSecurityDescriptorControl(descriptor, &mut control, &mut revision) } == 0 {
        return Err(io::Error::last_os_error());
    }
    if control & SE_DACL_PROTECTED == 0 || dacl.is_null() {
        return Err(denied());
    }
    // SAFETY: dacl is owned by descriptor. Validate before traversing its ACEs.
    if unsafe { IsValidAcl(dacl) } == 0 {
        return Err(denied());
    }
    // SAFETY: IsValidAcl validated the ACL header and its entries.
    let count = unsafe { (*dacl).AceCount };
    if count != 2 {
        return Err(denied());
    }
    let mut found_user = false;
    let mut found_system = false;
    for index in 0..count {
        let mut raw = ptr::null_mut();
        // SAFETY: index is within the validated ACL; output points inside it.
        if unsafe { GetAce(dacl, index as u32, &mut raw) } == 0 {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: the valid ACL contains an ACE_HEADER at this address. Check
        // type and size before accessing an ACCESS_ALLOWED_ACE body/SID.
        let header = unsafe { &*raw.cast::<windows_sys::Win32::Security::ACE_HEADER>() };
        // Other ACE forms (including object/callback/deny entries) are rejected.
        if header.AceType as u32 != ACCESS_ALLOWED_ACE_TYPE
            || (header.AceSize as usize) < size_of::<ACCESS_ALLOWED_ACE>()
            || header.AceFlags as u32 != OBJECT_INHERIT_ACE | CONTAINER_INHERIT_ACE
        {
            return Err(denied());
        }
        // SAFETY: the header above validated the standard allow-ACE layout.
        let ace = unsafe { &*raw.cast::<ACCESS_ALLOWED_ACE>() };
        if ace.Mask != FILE_ALL_ACCESS {
            return Err(denied());
        }
        let sid = sid_string(ptr::addr_of!(ace.SidStart).cast_mut().cast())?;
        if sid == user && !found_user {
            found_user = true;
        } else if sid == SYSTEM_SID && !found_system {
            found_system = true;
        } else {
            return Err(denied());
        }
    }
    if !found_user || !found_system {
        return Err(denied());
    }
    Ok(())
}

fn short_path(path: &Path) -> io::Result<PathBuf> {
    let path = wide(path.as_os_str())?;
    // SAFETY: terminated input; zero buffer requests required output size.
    let length = unsafe { GetShortPathNameW(path.as_ptr(), ptr::null_mut(), 0) };
    if length == 0 {
        return Err(io::Error::last_os_error());
    }
    let mut output = vec![0u16; length as usize];
    // SAFETY: valid buffer of the exact reported size; API terminates output.
    let written = unsafe { GetShortPathNameW(path.as_ptr(), output.as_mut_ptr(), length) };
    if written == 0 {
        return Err(io::Error::last_os_error());
    }
    if written >= length {
        return Err(io::Error::other(
            "temporary directory changed during short-path lookup",
        ));
    }
    Ok(PathBuf::from(OsString::from_wide(
        &output[..written as usize],
    )))
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
    let probe = base
        .join(format!("{DIRECTORY_PREFIX}{}", "0".repeat(32)))
        .join(SOCKET_NAME);
    if validate_socket_path(&probe).is_ok() {
        return Ok(base);
    }
    if let Ok(short) = short_path(&base) {
        let probe = short
            .join(format!("{DIRECTORY_PREFIX}{}", "0".repeat(32)))
            .join(SOCKET_NAME);
        validate_socket_path(&probe)?;
        return Ok(short);
    }
    validate_socket_path(&probe)?;
    unreachable!("valid paths return before short-path lookup")
}

struct DirectoryState {
    directory: PathBuf,
    socket_path: PathBuf,
}

impl DirectoryState {
    fn create(base: Option<&Path>) -> io::Result<Self> {
        let base = directory_base(base)?;
        let user = current_user_sid()?;
        let descriptor = security_descriptor(&user)?;
        let attributes = SECURITY_ATTRIBUTES {
            nLength: size_of::<SECURITY_ATTRIBUTES>() as u32,
            lpSecurityDescriptor: descriptor.0,
            bInheritHandle: 0,
        };
        for _ in 0..8 {
            let mut random = [0u8; 16];
            // SAFETY: system RNG writes exactly the provided buffer length;
            // a NULL algorithm handle is required with this system-RNG flag.
            let status = unsafe {
                BCryptGenRandom(
                    ptr::null_mut(),
                    random.as_mut_ptr(),
                    random.len() as u32,
                    BCRYPT_USE_SYSTEM_PREFERRED_RNG,
                )
            };
            if status < 0 {
                return Err(io::Error::other(format!(
                    "Windows random generator failed: 0x{status:08x}"
                )));
            }
            let name: String = random.iter().map(|byte| format!("{byte:02x}")).collect();
            let directory = base.join(format!("{DIRECTORY_PREFIX}{name}"));
            let socket_path = directory.join(SOCKET_NAME);
            validate_socket_path(&socket_path)?;
            let path = wide(directory.as_os_str())?;
            // SAFETY: terminated path and a live security descriptor. Creation
            // and the restrictive DACL are atomic; there is no permissive phase.
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
            if let Err(error) = verify_private_directory(&result.directory, &user) {
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
/// Creation atomically sets and verifies a protected current-user/SYSTEM DACL.
/// Close removes only the known socket file and empty directory; unrelated
/// children cause an error. Drop attempts the same cleanup without panicking.
/// Close all sockets first, and call close explicitly to observe cleanup errors.
pub struct PrivateDirectory {
    directory: Mutex<Option<DirectoryState>>,
    socket_path: PathBuf,
}

impl PrivateDirectory {
    /// Create below an existing absolute base, or the process temporary
    /// directory if absent. Validate the 107-byte UTF-8 AF_UNIX limit before
    /// creation, using short path aliases when available for an overlong base.
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
