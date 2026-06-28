//! `ControllerService.Dial` with the per-connection retry FSM
//! (`client/lease.py:315-377`, spec doc 04 §dial).
//!
//! Dial is retried with `0.3·2^attempt`-capped-at-`2.0 s` backoff, bounded by
//! `dial_timeout` (default 30 s), on `FAILED_PRECONDITION "not ready"` and
//! `UNAVAILABLE` — the race where the lease is held before the exporter reaches
//! `LEASE_READY`. Any other status (e.g. "permission denied" = lease transferred)
//! is terminal.

use std::future::Future;
use std::time::Duration;

use jumpstarter_protocol::v1::DialResponse;
use tokio::time::{sleep, Instant};
use tonic::{Code, Status};
use tracing::debug;

use crate::error::ClientError;

/// Base backoff delay (`client/lease.py:321`).
pub const DIAL_BASE_DELAY: Duration = Duration::from_millis(300);
/// Maximum backoff delay (`client/lease.py:322`).
pub const DIAL_MAX_DELAY: Duration = Duration::from_secs(2);
/// Default dial timeout (`config/client.py:102-107`).
pub const DEFAULT_DIAL_TIMEOUT: Duration = Duration::from_secs(30);

/// `min(0.3·2^attempt, 2.0, remaining)` (`client/lease.py:339`).
pub fn backoff(attempt: u32, remaining: Duration) -> Duration {
    let exp = DIAL_BASE_DELAY.as_secs_f64() * 2f64.powi(attempt.min(20) as i32);
    let secs = exp
        .min(DIAL_MAX_DELAY.as_secs_f64())
        .min(remaining.as_secs_f64())
        .max(0.0);
    Duration::from_secs_f64(secs)
}

/// Whether a Dial failure is a transient "exporter not ready yet" condition.
fn is_retryable(status: &Status) -> bool {
    (status.code() == Code::FailedPrecondition && status.message().contains("not ready"))
        || status.code() == Code::Unavailable
}

/// Drive Dial to success or a terminal error, retrying transient failures under the
/// `dial_timeout` budget. `attempt_fn` performs one Dial RPC.
pub async fn dial_with_retry<F, Fut>(
    dial_timeout: Duration,
    mut attempt_fn: F,
) -> Result<DialResponse, ClientError>
where
    F: FnMut() -> Fut,
    Fut: Future<Output = Result<DialResponse, Status>>,
{
    let deadline = Instant::now() + dial_timeout;
    let mut attempt = 0u32;
    loop {
        let rem = deadline.saturating_duration_since(Instant::now());
        let d = backoff(attempt, rem);
        debug!(attempt, delay = ?d, remaining = ?rem, "dialing");
        match attempt_fn().await {
            Ok(resp) => {
                debug!(attempt, "dial succeeded");
                return Ok(resp);
            }
            Err(status) => {
                if is_retryable(&status) {
                    let remaining = deadline.saturating_duration_since(Instant::now());
                    if remaining.is_zero() {
                        debug!(
                            attempt,
                            code = ?status.code(),
                            "dial timed out: budget exhausted while retrying"
                        );
                        return Err(status.into());
                    }
                    debug!(
                        attempt,
                        code = ?status.code(),
                        delay = ?backoff(attempt, remaining),
                        "dial retry"
                    );
                    sleep(backoff(attempt, remaining)).await;
                    attempt = attempt.saturating_add(1);
                    continue;
                }
                // permission denied (lease transferred) / offline / anything else.
                if status.code() == tonic::Code::PermissionDenied {
                    tracing::warn!(
                        attempt,
                        "dial rejected: lease transferred to another client; session no longer valid"
                    );
                } else {
                    debug!(attempt, code = ?status.code(), "dial failed: terminal/non-retryable status");
                }
                return Err(status.into());
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn resp() -> DialResponse {
        DialResponse {
            router_endpoint: "router:8083".into(),
            router_token: "tok".into(),
        }
    }

    #[test]
    fn backoff_caps_and_floors() {
        // attempt 0 with plenty remaining -> base 0.3s.
        assert_eq!(
            backoff(0, Duration::from_secs(30)),
            Duration::from_millis(300)
        );
        // attempt 3: 0.3*8 = 2.4 -> capped at 2.0s.
        assert_eq!(backoff(3, Duration::from_secs(30)), Duration::from_secs(2));
        // bounded by remaining.
        assert_eq!(
            backoff(5, Duration::from_millis(50)),
            Duration::from_millis(50)
        );
    }

    #[tokio::test(start_paused = true)]
    async fn succeeds_after_transient_not_ready() {
        let mut calls = 0;
        let out = dial_with_retry(Duration::from_secs(30), || {
            calls += 1;
            async move {
                if calls < 3 {
                    Err(Status::failed_precondition("exporter not ready"))
                } else {
                    Ok(resp())
                }
            }
        })
        .await
        .unwrap();
        assert_eq!(out.router_token, "tok");
    }

    #[tokio::test(start_paused = true)]
    async fn retries_unavailable_then_times_out() {
        let err = dial_with_retry(Duration::from_secs(5), || async {
            Err::<DialResponse, _>(Status::unavailable("exporter unavailable"))
        })
        .await
        .unwrap_err();
        assert!(matches!(err, ClientError::Rpc(s) if s.code() == Code::Unavailable));
    }

    #[tokio::test(start_paused = true)]
    async fn permission_denied_is_terminal() {
        let mut calls = 0;
        let err = dial_with_retry(Duration::from_secs(30), || {
            calls += 1;
            async move { Err::<DialResponse, _>(Status::permission_denied("permission denied")) }
        })
        .await
        .unwrap_err();
        assert!(matches!(err, ClientError::Rpc(s) if s.code() == Code::PermissionDenied));
        assert_eq!(calls, 1, "non-retryable status must not retry");
    }
}
