//! A parent-owned Windows Job Object for bounded child-process lifetimes.
//!
//! `win32job` provides the Job Object API over Microsoft's `windows` crate;
//! this module has no FFI.

use std::io;
use std::os::windows::io::AsRawHandle;
use std::sync::{Mutex, MutexGuard};

use win32job::{ExtendedLimitInfo, Job};

/// Owns the lifetime of an assigned child and all descendants it starts later.
///
/// Closing or dropping this guard terminates remaining processes in its job.
/// The private job handle cannot be inherited, so termination of the guard's
/// owning process also closes the last handle and cleans up the child tree.
/// This guard does not wait for processes to finish or implement graceful stop.
pub(crate) struct ChildProcessTree {
    job: Mutex<Option<Job>>,
}

impl ChildProcessTree {
    /// Assigns an already-spawned child to a private kill-on-close Job Object.
    ///
    /// The caller must keep the child behind a startup handshake until this
    /// succeeds: processes started before assignment are not captured. The
    /// caller retains ownership of its child-process handle and must terminate
    /// the gated child if assignment fails. Existing incompatible job policies
    /// are reported as errors; this never silently leaves a child unmanaged.
    pub(crate) fn new(process: &impl AsRawHandle) -> io::Result<Self> {
        let handle = process.as_raw_handle() as isize;
        // Null and the current-process pseudo-handle (-1) never name a child.
        if handle == 0 || handle == -1 {
            return Err(io::Error::new(
                io::ErrorKind::InvalidInput,
                "expected a child process handle, not null or the current process",
            ));
        }
        // The job handle is created without inheritance, so only we hold it.
        let job = Job::create_with_limit_info(ExtendedLimitInfo::new().limit_kill_on_job_close())?;
        job.assign_process(handle)?;
        Ok(Self {
            job: Mutex::new(Some(job)),
        })
    }

    fn job(&self) -> io::Result<MutexGuard<'_, Option<Job>>> {
        self.job
            .lock()
            .map_err(|_| io::Error::other("child process tree lock poisoned"))
    }

    /// Terminates remaining members by closing the last job handle.
    ///
    /// Idempotent; callers still own and must reap their child-process handle.
    pub(crate) fn close(&self) -> io::Result<()> {
        // Dropping the Job closes its only handle and triggers KILL_ON_JOB_CLOSE.
        self.job()?.take();
        Ok(())
    }
}
