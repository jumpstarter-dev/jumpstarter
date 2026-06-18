//! The lease acquisition/release FSM (spec doc 04; `client/lease.py:120-308`).
//!
//! The condition state machine is decoupled from gRPC behind [`LeaseProvider`] so
//! it can be exhaustively unit-tested without a controller. The real implementation
//! over `ClientService` lives in [`crate::service`]. The poll loop retries transient
//! `GetLease` failures (`UNAVAILABLE`) in [`get_with_retry`], bounded by the overall
//! acquisition budget (mirrors Python `_get_with_retry`).

use std::collections::BTreeMap;
use std::time::Duration;

use jumpstarter_protocol::v1::Condition;
use tokio::time::{sleep, timeout};

use crate::condition;
use crate::error::{ClientError, LeaseError};

/// Parameters for `CreateLease` (`client/grpc.py:448-484`).
#[derive(Debug, Clone, Default)]
pub struct CreateLeaseParams {
    pub selector: Option<String>,
    pub exporter_name: Option<String>,
    pub duration: Duration,
    /// Requested begin time (`--begin-time`); the server computes the effective one.
    pub begin_time: Option<prost_types::Timestamp>,
    /// `lease_id`; reused as the lease name on `NoExporter` re-creation.
    pub lease_id: Option<String>,
    pub tags: BTreeMap<String, String>,
}

/// The subset of a `Lease` resource the FSM reasons about.
#[derive(Debug, Clone, Default)]
pub struct LeaseView {
    pub name: String,
    /// Owning client name (parsed from the `client` identifier).
    pub client: String,
    pub selector: String,
    /// Assigned exporter name (parsed from the `exporter` identifier), once ready.
    pub exporter: String,
    pub conditions: Vec<Condition>,
}

/// A successfully acquired lease.
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct AcquiredLease {
    pub name: String,
    pub exporter: String,
}

/// Poll cadence and overall budget for acquisition (spec doc 04 §timers).
#[derive(Debug, Clone, Copy)]
pub struct LeaseTiming {
    /// Interval between condition polls (Python: 5×1 s ticks).
    pub poll_interval: Duration,
    /// Overall acquisition budget (`acquisition_timeout`, default 7200 s).
    pub acquisition_timeout: Duration,
}

impl Default for LeaseTiming {
    fn default() -> Self {
        Self {
            poll_interval: Duration::from_secs(5),
            acquisition_timeout: Duration::from_secs(7200),
        }
    }
}

/// gRPC-facing lease operations. The `get_lease` implementation is responsible for
/// retrying transient transport errors before returning.
#[allow(async_fn_in_trait)]
pub trait LeaseProvider {
    async fn create_lease(&self, params: &CreateLeaseParams) -> Result<String, ClientError>;
    async fn get_lease(&self, name: &str) -> Result<LeaseView, ClientError>;
    async fn delete_lease(&self, name: &str) -> Result<(), ClientError>;
}

/// Acquire a lease: establish its name (existing or freshly created), then poll
/// conditions until `Ready`, mapping terminal conditions to [`LeaseError`].
///
/// `existing_name` corresponds to a lease supplied via env/flag (`JMP_LEASE`);
/// when present it is verified (ownership + selector) before polling, and a
/// selector mismatch triggers a fresh `CreateLease` (`client/lease.py:177-199`).
pub async fn acquire<P: LeaseProvider>(
    provider: &P,
    params: CreateLeaseParams,
    existing_name: Option<String>,
    client_name: Option<&str>,
    timing: LeaseTiming,
) -> Result<AcquiredLease, ClientError> {
    // ---- request: establish the lease name -------------------------------
    let name = match existing_name {
        Some(existing) => {
            let view = provider.get_lease(&existing).await?;
            if let Some(cn) = client_name {
                if view.client != cn {
                    return Err(LeaseError::WrongOwner {
                        name: existing,
                        owner: view.client,
                        current: cn.to_string(),
                    }
                    .into());
                }
            }
            match &params.selector {
                Some(sel) if &view.selector != sel => {
                    // Mismatched selector: create a brand-new lease.
                    let mut fresh = params.clone();
                    fresh.lease_id = None;
                    provider.create_lease(&fresh).await?
                }
                _ => existing,
            }
        }
        None => provider.create_lease(&params).await?,
    };

    // ---- _acquire: poll conditions under the acquisition budget ----------
    // `CreateLease(lease_id=name)` returns the same name, so `name` is stable
    // across NoExporter re-creation and is safe to report on timeout.
    let poll = poll_until_ready(provider, &name, &params, timing.poll_interval);
    match timeout(timing.acquisition_timeout, poll).await {
        Ok(result) => result,
        Err(_elapsed) => Err(LeaseError::Timeout {
            name,
            timeout_secs: timing.acquisition_timeout.as_secs(),
        }
        .into()),
    }
}

/// `GetLease` with unbounded exponential-backoff retry on transient transport
/// errors (`UNAVAILABLE`), mirroring Python `_get_with_retry` (initial 1 s, doubling,
/// capped at 120 s). The overall `acquire` budget bounds the total time.
async fn get_with_retry<P: LeaseProvider>(
    provider: &P,
    name: &str,
) -> Result<LeaseView, ClientError> {
    let mut attempt = 0u32;
    loop {
        match provider.get_lease(name).await {
            Ok(view) => return Ok(view),
            Err(e) if e.is_transient() => {
                let secs = (1u64 << attempt.min(7)).min(120);
                sleep(Duration::from_secs(secs)).await;
                attempt = attempt.saturating_add(1);
            }
            Err(e) => return Err(e),
        }
    }
}

async fn poll_until_ready<P: LeaseProvider>(
    provider: &P,
    name: &str,
    params: &CreateLeaseParams,
    poll_interval: Duration,
) -> Result<AcquiredLease, ClientError> {
    loop {
        let view = get_with_retry(provider, name).await?;
        let conds = &view.conditions;

        if condition::is_true(conds, "Ready") {
            return Ok(AcquiredLease {
                name: name.to_string(),
                exporter: view.exporter,
            });
        }

        if condition::is_true(conds, "Unsatisfiable") {
            let msg = condition::message(conds, "Unsatisfiable", None)
                .unwrap_or_default()
                .to_string();
            // Old controllers mark offline-but-matching exporters as
            // Unsatisfiable/NoExporter — transient, re-create with the same id.
            if condition::present_and_equal(conds, "Unsatisfiable", "True", Some("NoExporter")) {
                sleep(poll_interval).await;
                let mut recreate = params.clone();
                recreate.lease_id = Some(name.to_string());
                provider.create_lease(&recreate).await?;
                continue;
            }
            return Err(LeaseError::Unsatisfiable(msg).into());
        }

        if condition::is_true(conds, "Invalid") {
            let msg = condition::message(conds, "Invalid", None)
                .unwrap_or_default()
                .to_string();
            return Err(LeaseError::Invalid(msg).into());
        }

        if condition::is_false(conds, "Pending") {
            return Err(LeaseError::NotPending(name.to_string()).into());
        }

        if condition::present_and_equal(conds, "Ready", "False", Some("Released")) {
            return Err(LeaseError::Released(name.to_string()).into());
        }

        sleep(poll_interval).await;
    }
}

#[cfg(test)]
mod tests {
    use super::*;
    use crate::condition::cond;
    use std::collections::VecDeque;
    use std::sync::Mutex;

    struct FakeProvider {
        name: String,
        gets: Mutex<VecDeque<LeaseView>>,
        creates: Mutex<usize>,
        deletes: Mutex<usize>,
    }

    impl FakeProvider {
        fn new(name: &str, views: Vec<LeaseView>) -> Self {
            Self {
                name: name.to_string(),
                gets: Mutex::new(views.into()),
                creates: Mutex::new(0),
                deletes: Mutex::new(0),
            }
        }
        fn view(name: &str, conds: Vec<Condition>) -> LeaseView {
            LeaseView {
                name: name.to_string(),
                client: "me".to_string(),
                selector: "board=x".to_string(),
                exporter: "exp-1".to_string(),
                conditions: conds,
            }
        }
    }

    impl LeaseProvider for FakeProvider {
        async fn create_lease(&self, _params: &CreateLeaseParams) -> Result<String, ClientError> {
            *self.creates.lock().unwrap() += 1;
            Ok(self.name.clone())
        }
        async fn get_lease(&self, _name: &str) -> Result<LeaseView, ClientError> {
            let mut q = self.gets.lock().unwrap();
            // Repeat the last scripted view once the queue drains.
            if q.len() > 1 {
                Ok(q.pop_front().unwrap())
            } else {
                Ok(q.front().cloned().expect("at least one scripted view"))
            }
        }
        async fn delete_lease(&self, _name: &str) -> Result<(), ClientError> {
            *self.deletes.lock().unwrap() += 1;
            Ok(())
        }
    }

    fn fast() -> LeaseTiming {
        LeaseTiming {
            poll_interval: Duration::from_millis(1),
            acquisition_timeout: Duration::from_secs(30),
        }
    }

    fn params() -> CreateLeaseParams {
        CreateLeaseParams {
            selector: Some("board=x".to_string()),
            duration: Duration::from_secs(1800),
            ..Default::default()
        }
    }

    #[tokio::test]
    async fn acquires_when_ready() {
        let p = FakeProvider::new(
            "l1",
            vec![FakeProvider::view(
                "l1",
                vec![cond("Ready", "True", None, None)],
            )],
        );
        let acq = acquire(&p, params(), None, Some("me"), fast())
            .await
            .unwrap();
        assert_eq!(
            acq,
            AcquiredLease {
                name: "l1".into(),
                exporter: "exp-1".into()
            }
        );
        assert_eq!(*p.creates.lock().unwrap(), 1);
    }

    #[tokio::test]
    async fn polls_through_pending_then_ready() {
        let p = FakeProvider::new(
            "l1",
            vec![
                FakeProvider::view(
                    "l1",
                    vec![cond("Pending", "True", None, Some("scheduling"))],
                ),
                FakeProvider::view("l1", vec![cond("Pending", "True", None, None)]),
                FakeProvider::view("l1", vec![cond("Ready", "True", None, None)]),
            ],
        );
        let acq = acquire(&p, params(), None, None, fast()).await.unwrap();
        assert_eq!(acq.name, "l1");
    }

    #[tokio::test]
    async fn no_exporter_is_transient_and_recreates() {
        let p = FakeProvider::new(
            "l1",
            vec![
                FakeProvider::view(
                    "l1",
                    vec![cond(
                        "Unsatisfiable",
                        "True",
                        Some("NoExporter"),
                        Some("none"),
                    )],
                ),
                FakeProvider::view("l1", vec![cond("Ready", "True", None, None)]),
            ],
        );
        let acq = acquire(&p, params(), None, None, fast()).await.unwrap();
        assert_eq!(acq.name, "l1");
        // initial create + one re-create on NoExporter.
        assert_eq!(*p.creates.lock().unwrap(), 2);
    }

    #[tokio::test]
    async fn unsatisfiable_non_no_exporter_fails() {
        let p = FakeProvider::new(
            "l1",
            vec![FakeProvider::view(
                "l1",
                vec![cond(
                    "Unsatisfiable",
                    "True",
                    Some("Conflict"),
                    Some("nope"),
                )],
            )],
        );
        let err = acquire(&p, params(), None, None, fast()).await.unwrap_err();
        assert!(matches!(err, ClientError::Lease(LeaseError::Unsatisfiable(m)) if m == "nope"));
    }

    #[tokio::test]
    async fn invalid_fails() {
        let p = FakeProvider::new(
            "l1",
            vec![FakeProvider::view(
                "l1",
                vec![cond("Invalid", "True", None, Some("bad selector"))],
            )],
        );
        let err = acquire(&p, params(), None, None, fast()).await.unwrap_err();
        assert!(matches!(err, ClientError::Lease(LeaseError::Invalid(m)) if m == "bad selector"));
    }

    #[tokio::test]
    async fn pending_false_fails() {
        let p = FakeProvider::new(
            "l1",
            vec![FakeProvider::view(
                "l1",
                vec![cond("Pending", "False", None, None)],
            )],
        );
        let err = acquire(&p, params(), None, None, fast()).await.unwrap_err();
        assert!(matches!(err, ClientError::Lease(LeaseError::NotPending(n)) if n == "l1"));
    }

    #[tokio::test]
    async fn times_out_when_never_ready() {
        let timing = LeaseTiming {
            poll_interval: Duration::from_millis(1),
            acquisition_timeout: Duration::from_millis(40),
        };
        let p = FakeProvider::new(
            "l1",
            vec![FakeProvider::view(
                "l1",
                vec![cond("Pending", "True", None, None)],
            )],
        );
        let err = acquire(&p, params(), None, None, timing).await.unwrap_err();
        assert!(
            matches!(err, ClientError::Lease(LeaseError::Timeout { name, .. }) if name == "l1")
        );
    }

    #[tokio::test]
    async fn existing_lease_wrong_owner_fails() {
        let mut view = FakeProvider::view("l1", vec![cond("Ready", "True", None, None)]);
        view.client = "someone-else".to_string();
        let p = FakeProvider::new("l1", vec![view]);
        let err = acquire(&p, params(), Some("l1".into()), Some("me"), fast())
            .await
            .unwrap_err();
        assert!(matches!(
            err,
            ClientError::Lease(LeaseError::WrongOwner { .. })
        ));
    }

    /// Provider that fails `get_lease` with UNAVAILABLE a few times before serving
    /// a Ready view — exercising the transient-retry in the poll loop.
    struct FlakyProvider {
        transient_left: Mutex<u32>,
        view: LeaseView,
    }

    impl LeaseProvider for FlakyProvider {
        async fn create_lease(&self, _params: &CreateLeaseParams) -> Result<String, ClientError> {
            Ok("l1".to_string())
        }
        async fn get_lease(&self, _name: &str) -> Result<LeaseView, ClientError> {
            let mut left = self.transient_left.lock().unwrap();
            if *left > 0 {
                *left -= 1;
                Err(ClientError::Rpc(Box::new(tonic::Status::unavailable(
                    "flaky",
                ))))
            } else {
                Ok(self.view.clone())
            }
        }
        async fn delete_lease(&self, _name: &str) -> Result<(), ClientError> {
            Ok(())
        }
    }

    #[tokio::test(start_paused = true)]
    async fn retries_transient_get_errors() {
        let p = FlakyProvider {
            transient_left: Mutex::new(3),
            view: FakeProvider::view("l1", vec![cond("Ready", "True", None, None)]),
        };
        let acq = acquire(&p, params(), None, None, fast()).await.unwrap();
        assert_eq!(acq.name, "l1");
        assert_eq!(
            *p.transient_left.lock().unwrap(),
            0,
            "all transient errors retried"
        );
    }
}
