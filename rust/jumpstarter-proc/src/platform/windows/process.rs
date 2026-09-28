//! A parent-owned Windows Job Object for bounded child-process lifetimes.

use std::io;
use std::mem::{size_of, zeroed};
use std::os::windows::io::{AsRawHandle, FromRawHandle, OwnedHandle};
use std::ptr;
use std::sync::Mutex;

use windows_sys::Win32::System::JobObjects::{
    AssignProcessToJobObject, CreateJobObjectW, JobObjectExtendedLimitInformation,
    SetInformationJobObject, JOBOBJECT_EXTENDED_LIMIT_INFORMATION,
    JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
};
use windows_sys::Win32::System::Threading::{
    GetCurrentProcessId, OpenProcess, PROCESS_SET_QUOTA, PROCESS_TERMINATE,
};

/// Owns the lifetime of an assigned child and all descendants it starts later.
///
/// Closing or dropping this guard terminates remaining processes in its job.
/// The private job handle cannot be inherited, so termination of the guard's
/// owning process also closes the last handle and cleans up the child tree.
/// This guard does not wait for processes to finish or implement graceful stop.
pub(crate) struct ChildProcessTree {
    job: Mutex<Option<OwnedHandle>>,
}

impl ChildProcessTree {
    /// Assigns an already-spawned child to a private kill-on-close Job Object.
    ///
    /// The caller must keep the child behind a startup handshake until this
    /// succeeds: processes started before assignment are not captured. The
    /// caller retains ownership of its child-process handle and must terminate
    /// the gated child if assignment fails. Existing incompatible job policies
    /// are reported as errors; this never silently leaves a child unmanaged.
    pub(crate) fn new(pid: u32) -> io::Result<Self> {
        // SAFETY: GetCurrentProcessId takes no arguments or pointers.
        if pid == 0 || pid == unsafe { GetCurrentProcessId() } {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "expected a child process ID, not zero or the current process",
            ));
        }

        // SAFETY: Null attributes create a non-inheritable handle with the
        // creator's default security descriptor; a null name keeps it private.
        let raw_job = unsafe { CreateJobObjectW(ptr::null(), ptr::null()) };
        if raw_job.is_null() {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: The successful call returns a fresh handle owned by us.
        let job = unsafe { OwnedHandle::from_raw_handle(raw_job) };

        // SAFETY: This Windows ABI structure contains only integer fields and
        // other plain data structures; zero initializes every unused limit.
        let mut limits: JOBOBJECT_EXTENDED_LIMIT_INFORMATION = unsafe { zeroed() };
        limits.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE;
        // SAFETY: job is live; the fully initialized structure and its exact
        // byte size stay valid for the synchronous call.
        if unsafe {
            SetInformationJobObject(
                job.as_raw_handle(),
                JobObjectExtendedLimitInformation,
                ptr::from_ref(&limits).cast(),
                size_of::<JOBOBJECT_EXTENDED_LIMIT_INFORMATION>() as u32,
            )
        } == 0
        {
            return Err(io::Error::last_os_error());
        }

        // SAFETY: OpenProcess validates the supplied ID. FALSE keeps the
        // temporary process handle private; request only assignment rights.
        let raw_child = unsafe { OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, 0, pid) };
        if raw_child.is_null() {
            return Err(io::Error::last_os_error());
        }
        // SAFETY: The successful call returns a fresh handle owned by us.
        let child = unsafe { OwnedHandle::from_raw_handle(raw_child) };
        // SAFETY: Both handles are valid and held alive through assignment.
        if unsafe { AssignProcessToJobObject(job.as_raw_handle(), child.as_raw_handle()) } == 0 {
            return Err(io::Error::last_os_error());
        }

        Ok(Self {
            job: Mutex::new(Some(job)),
        })
    }

    /// Terminates remaining members by closing the last job handle.
    ///
    /// Idempotent; callers still own and must reap their child-process handle.
    pub(crate) fn close(&self) -> io::Result<()> {
        let mut job = self
            .job
            .lock()
            .map_err(|_| io::Error::other("child process tree lock poisoned"))?;
        // OwnedHandle closes on drop. No handle is exposed or inherited, so
        // this closes the final handle and triggers KILL_ON_JOB_CLOSE.
        job.take();
        Ok(())
    }
}
