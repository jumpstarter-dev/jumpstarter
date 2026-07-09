//! coordination/v1 Lease leader election, wire-compatible with client-go.
//!
//! This is a port of the client-go leader-election algorithm as configured by
//! controller-runtime and `controller/cmd/main.go`:
//!
//! - the lock is a coordination/v1 `Lease` (controller-runtime defaults to the
//!   `leases` resource lock; the manager passes `LeaderElectionID`
//!   `"a38b78e7.jumpstarter.dev"` and `LeaderElectionNamespace` = the watch
//!   namespace — `controller/cmd/main.go:172-189`),
//! - timings are the controller-runtime defaults `LeaseDuration` 15s /
//!   `RenewDeadline` 10s / `RetryPeriod` 2s
//!   (`sigs.k8s.io/controller-runtime@v0.21.0/pkg/manager/internal.go:52-54`),
//! - acquire retries are jittered by `JitterFactor` 1.2
//!   (`k8s.io/client-go@v0.33.0/tools/leaderelection/leaderelection.go:71-73`),
//! - `LeaderElectionReleaseOnCancel` stays **false** (it is commented out in
//!   `controller/cmd/main.go:184`), so on shutdown the Lease is simply left to
//!   expire after `LeaseDuration`.
//!
//! The Lease record read/written here is byte-for-byte the client-go shape
//! (`holderIdentity`/`leaseDurationSeconds`/`acquireTime`/`renewTime`/
//! `leaseTransitions`), so Rust and Go replicas of the controller can contend
//! for the same Lease during a mixed-version rollout.
//!
//! From the client-go package docs (leaderelection.go): a client only acts on
//! timestamps captured locally to infer the state of the leader election. The
//! client does not consider timestamps in the leader election record to be
//! accurate because these timestamps may not have been produced by a local
//! clock. The implementation does not depend on their accuracy and only uses
//! their change to indicate that another client has renewed the leader lease.
//! Thus the implementation is tolerant to arbitrary clock skew, but is not
//! tolerant to arbitrary clock skew rate. This implementation does not
//! guarantee that only one client is acting as a leader (a.k.a. fencing).
//!
//! The algorithm itself lives in [`ElectionCore`], a pure decision core with
//! no I/O: every transition takes explicit wall-clock/monotonic "now" values
//! so the client-go semantics are table-testable with a fake clock. The
//! surrounding Lease I/O loop is [`spawn_leader_election`].

use std::time::{Duration, Instant, SystemTime, UNIX_EPOCH};

use k8s_openapi::api::coordination::v1::{Lease, LeaseSpec};
use k8s_openapi::apimachinery::pkg::apis::meta::v1::{MicroTime, ObjectMeta};
use k8s_openapi::jiff::Timestamp;
use kube::api::{Api, PostParams};
use tokio::sync::watch;
use tokio::task::JoinHandle;
use tracing::{debug, error, info};

/// LeaseDuration is the duration that non-leader candidates will wait to
/// force acquire leadership. This is measured against time of last observed
/// ack.
///
/// A client needs to wait a full LeaseDuration without observing a change to
/// the record before it can attempt to take over.
///
/// Core clients (and controller-runtime) default this value to 15 seconds.
/// (`controller-runtime/pkg/manager/internal.go:52`)
pub const LEASE_DURATION: Duration = Duration::from_secs(15);

/// RenewDeadline is the duration that the acting master will retry refreshing
/// leadership before giving up.
///
/// Core clients (and controller-runtime) default this value to 10 seconds.
/// (`controller-runtime/pkg/manager/internal.go:53`)
pub const RENEW_DEADLINE: Duration = Duration::from_secs(10);

/// RetryPeriod is the duration the LeaderElector clients should wait between
/// tries of actions.
///
/// Core clients (and controller-runtime) default this value to 2 seconds.
/// (`controller-runtime/pkg/manager/internal.go:54`)
pub const RETRY_PERIOD: Duration = Duration::from_secs(2);

/// Jitter factor applied to `RETRY_PERIOD` while trying to acquire the lease.
/// (`client-go/tools/leaderelection/leaderelection.go:71-73`)
pub const JITTER_FACTOR: f64 = 1.2;

/// Errors surfaced by the leader-election loop.
#[derive(Debug, thiserror::Error)]
pub enum LeaderElectionError {
    /// Leadership was held and then could not be renewed within
    /// `RENEW_DEADLINE`. The Go manager treats this as fatal
    /// (`errors.New("leader election lost")` in controller-runtime's
    /// `pkg/manager/internal.go`) and exits the process; callers of
    /// [`spawn_leader_election`] must do the same when the returned
    /// [`JoinHandle`] resolves with this error.
    #[error("leader election lost: {identity} failed to renew lease {namespace}/{name}")]
    LeadershipLost {
        /// Namespace of the contested Lease.
        namespace: String,
        /// Name of the contested Lease.
        name: String,
        /// Identity this replica contended with.
        identity: String,
    },
}

/// LeaderElectionRecord is the record that is stored in the leader election
/// annotation (for us: the coordination/v1 `LeaseSpec`). This information
/// should be used for observational purposes only.
///
/// Mirrors `client-go/tools/leaderelection/resourcelock/interface.go`
/// (`LeaderElectionRecord`), with Go zero values (`""`/`0`) standing in for
/// unset Lease spec fields exactly as `LeaseSpecToLeaderElectionRecord` does.
#[derive(Clone, Debug, PartialEq)]
pub struct LeaderElectionRecord {
    /// HolderIdentity is the ID that owns the lease. If empty, no one owns
    /// this lease and all callers may acquire.
    pub holder_identity: String,
    /// Duration in seconds that candidates need to wait to force acquire.
    pub lease_duration_seconds: i32,
    /// Time the current lease was acquired (set on acquire/takeover only).
    pub acquire_time: Option<Timestamp>,
    /// Time the current holder last renewed the lease (set on every renew).
    pub renew_time: Option<Timestamp>,
    /// Number of transitions of the lease between holders. Incremented only
    /// when the holder changes.
    pub leader_transitions: i32,
    /// Coordinated-leader-election field; never written by this
    /// implementation, carried only so observation change-detection matches
    /// client-go's raw-record comparison.
    pub preferred_holder: String,
    /// Coordinated-leader-election field; never written by this
    /// implementation (see `preferred_holder`).
    pub strategy: String,
}

impl LeaderElectionRecord {
    /// Port of `LeaseSpecToLeaderElectionRecord`
    /// (`client-go/tools/leaderelection/resourcelock/leaselock.go:141`):
    /// missing spec fields map to Go zero values.
    pub fn from_lease_spec(spec: &LeaseSpec) -> Self {
        Self {
            holder_identity: spec.holder_identity.clone().unwrap_or_default(),
            lease_duration_seconds: spec.lease_duration_seconds.unwrap_or_default(),
            acquire_time: spec.acquire_time.as_ref().map(|t| t.0),
            renew_time: spec.renew_time.as_ref().map(|t| t.0),
            leader_transitions: spec.lease_transitions.unwrap_or_default(),
            preferred_holder: spec.preferred_holder.clone().unwrap_or_default(),
            strategy: spec.strategy.clone().unwrap_or_default(),
        }
    }

    /// Port of `LeaderElectionRecordToLeaseSpec`
    /// (`client-go/tools/leaderelection/resourcelock/leaselock.go:165`):
    /// holder/duration/transitions are always set (client-go writes the
    /// pointers unconditionally, empty string included);
    /// `preferredHolder`/`strategy` only when non-empty. Records produced by
    /// this module always carry both timestamps.
    pub fn to_lease_spec(&self) -> LeaseSpec {
        LeaseSpec {
            holder_identity: Some(self.holder_identity.clone()),
            lease_duration_seconds: Some(self.lease_duration_seconds),
            acquire_time: self.acquire_time.map(MicroTime),
            renew_time: self.renew_time.map(MicroTime),
            lease_transitions: Some(self.leader_transitions),
            preferred_holder: (!self.preferred_holder.is_empty())
                .then(|| self.preferred_holder.clone()),
            strategy: (!self.strategy.is_empty()).then(|| self.strategy.clone()),
        }
    }

    /// Whether two records are the same **observation**, mirroring client-go's
    /// `bytes.Equal(le.observedRawRecord, oldLeaderElectionRawRecord)`
    /// (`leaderelection.go:450`). The raw record there is the JSON of
    /// `LeaderElectionRecord`, whose timestamps are `metav1.Time` and thus
    /// serialize at **whole-second** precision — so sub-second-only timestamp
    /// changes do not count as a new observation, and neither do they here.
    fn same_observation_as(&self, other: &Self) -> bool {
        fn secs(t: Option<Timestamp>) -> Option<i64> {
            t.map(|t| t.as_second())
        }
        self.holder_identity == other.holder_identity
            && self.lease_duration_seconds == other.lease_duration_seconds
            && secs(self.acquire_time) == secs(other.acquire_time)
            && secs(self.renew_time) == secs(other.renew_time)
            && self.leader_transitions == other.leader_transitions
            && self.preferred_holder == other.preferred_holder
            && self.strategy == other.strategy
    }
}

/// Outcome of the slow path of `tryAcquireOrRenew` once the current record
/// has been fetched (`client-go/tools/leaderelection/leaderelection.go:449-478`).
#[derive(Clone, Debug, PartialEq)]
pub enum Decision {
    /// The lock is held by another identity and has not yet expired from our
    /// local point of observation — do not write anything.
    /// (`leaderelection.go:455-458`)
    Standby {
        /// The fresh holder we are deferring to.
        holder: String,
    },
    /// Write this record via Update: either a renewal of our own leadership
    /// or a takeover of an empty/expired record.
    /// (`leaderelection.go:460-477`)
    Update(LeaderElectionRecord),
}

/// Pure decision core of the client-go `LeaderElector`: the internal
/// bookkeeping (`observedRecord`, `observedRawRecord`, `observedTime`) plus
/// the `tryAcquireOrRenew` decision logic, with all I/O and clock reads
/// hoisted out. Wall-clock time (`Timestamp`) only ever lands in record
/// fields; every freshness comparison uses monotonic [`Instant`]s captured by
/// the caller, mirroring client-go's local-observation rule.
#[derive(Debug)]
pub struct ElectionCore {
    /// Our lock identity (`resourcelock.ResourceLockConfig.Identity`).
    identity: String,
    /// Configured LeaseDuration; written into every record we produce.
    lease_duration: Duration,
    /// `le.observedRecord` — last record observed via fetch **or** written by
    /// us. Backs `is_leader()`.
    observed_record: Option<LeaderElectionRecord>,
    /// `le.observedTime` — when `observed_record` was last replaced. A
    /// foreign holder is considered expired only once a full (observed)
    /// lease duration has elapsed since this instant.
    observed_at: Option<Instant>,
    /// `le.observedRawRecord` — the change-detection baseline. Deliberately
    /// **not** updated when we write a record ourselves: client-go only
    /// refreshes it on fetch (`leaderelection.go:450-454`), and we mirror
    /// that quirk exactly.
    observed_baseline: Option<LeaderElectionRecord>,
}

impl ElectionCore {
    /// Creates a decision core for `identity` using [`LEASE_DURATION`].
    pub fn new(identity: impl Into<String>) -> Self {
        Self::with_lease_duration(identity, LEASE_DURATION)
    }

    /// Creates a decision core with an explicit lease duration (tests).
    pub fn with_lease_duration(identity: impl Into<String>, lease_duration: Duration) -> Self {
        Self {
            identity: identity.into(),
            lease_duration,
            observed_record: None,
            observed_at: None,
            observed_baseline: None,
        }
    }

    /// The identity this core contends with.
    pub fn identity(&self) -> &str {
        &self.identity
    }

    /// IsLeader returns true if the last observed holder was this client.
    /// (`leaderelection.go:246-248`)
    pub fn is_leader(&self) -> bool {
        self.observed_record
            .as_ref()
            .is_some_and(|r| r.holder_identity == self.identity)
    }

    /// GetLeader returns the identity of the last observed leader, or `None`
    /// if no leader has yet been observed. (`leaderelection.go:241-243`)
    pub fn leader(&self) -> Option<&str> {
        self.observed_record
            .as_ref()
            .map(|r| r.holder_identity.as_str())
            .filter(|h| !h.is_empty())
    }

    /// `le.isLeaseValid(now)` (`leaderelection.go:506-508`): the observed
    /// record is fresh iff strictly less than its **own advertised**
    /// `leaseDurationSeconds` has elapsed since we last replaced our
    /// observation of it (`observedTime.Add(d).After(now)`).
    fn is_lease_valid(&self, now: Instant) -> bool {
        let (Some(record), Some(observed_at)) = (&self.observed_record, self.observed_at) else {
            // Go zero-value observedTime: never valid.
            return false;
        };
        let duration =
            Duration::from_secs(u64::try_from(record.lease_duration_seconds).unwrap_or(0));
        observed_at
            .checked_add(duration)
            .is_some_and(|expiry| expiry > now)
    }

    /// The default record `tryAcquireOrRenew` starts from
    /// (`leaderelection.go:409-415`): ourselves as holder, acquireTime and
    /// renewTime both `now`, zero transitions. Also exactly the record
    /// `Create`d when the Lease does not exist yet (`leaderelection.go:439`).
    pub fn fresh_record(&self, now_wall: Timestamp) -> LeaderElectionRecord {
        LeaderElectionRecord {
            holder_identity: self.identity.clone(),
            lease_duration_seconds: self.lease_duration.as_secs() as i32,
            acquire_time: Some(now_wall),
            renew_time: Some(now_wall),
            leader_transitions: 0,
            preferred_holder: String::new(),
            strategy: String::new(),
        }
    }

    /// Fast path for the leader to update optimistically assuming that the
    /// record observed last time is the current version
    /// (`leaderelection.go:417-430`). Returns the record to Update when we
    /// are the fresh leader per our own observation; `None` sends the caller
    /// to the fetch-first slow path. On update failure the caller must also
    /// fall back to the slow path rather than fail the attempt.
    pub fn fast_path_record(
        &self,
        now_wall: Timestamp,
        now: Instant,
    ) -> Option<LeaderElectionRecord> {
        if !(self.is_leader() && self.is_lease_valid(now)) {
            return None;
        }
        let observed = self
            .observed_record
            .as_ref()
            .expect("is_leader implies observed");
        let mut record = self.fresh_record(now_wall);
        // AcquireTime and LeaderTransitions carry over from our observation;
        // RenewTime is the only timestamp that moves on a renewal.
        record.acquire_time = observed.acquire_time;
        record.leader_transitions = observed.leader_transitions;
        Some(record)
    }

    /// Slow path of `tryAcquireOrRenew` after the current record has been
    /// fetched (`leaderelection.go:449-478`):
    ///
    /// 1. if the fetched record differs from our observation baseline,
    ///    re-observe it (`observed_at` := `observed_at_now`);
    /// 2. a non-empty foreign holder whose lease is still locally fresh wins
    ///    — [`Decision::Standby`];
    /// 3. otherwise produce the record to write: a renewal preserves
    ///    `acquireTime`/`leaderTransitions`, a takeover sets
    ///    `acquireTime = now` and increments `leaderTransitions`.
    ///
    /// `now` is the instant captured at the start of the attempt (validity
    /// checks); `observed_at_now` is the instant the fetch returned
    /// (client-go stamps `observedTime` with a fresh `clock.Now()`).
    pub fn decide(
        &mut self,
        fetched: &LeaderElectionRecord,
        now_wall: Timestamp,
        now: Instant,
        observed_at_now: Instant,
    ) -> Decision {
        // 3. Record obtained, check the Identity & Time.
        // (`leaderelection.go:450-454`)
        if !self
            .observed_baseline
            .as_ref()
            .is_some_and(|baseline| baseline.same_observation_as(fetched))
        {
            self.observed_record = Some(fetched.clone());
            self.observed_at = Some(observed_at_now);
            self.observed_baseline = Some(fetched.clone());
        }
        if !fetched.holder_identity.is_empty() && self.is_lease_valid(now) && !self.is_leader() {
            return Decision::Standby {
                holder: fetched.holder_identity.clone(),
            };
        }

        // 4. We're going to try to update. The leaderElectionRecord is set to
        // it's default here. Let's correct it before updating.
        // (`leaderelection.go:460-468`)
        let mut record = self.fresh_record(now_wall);
        if self.is_leader() {
            record.acquire_time = fetched.acquire_time;
            record.leader_transitions = fetched.leader_transitions;
        } else {
            record.leader_transitions = fetched.leader_transitions + 1;
        }
        Decision::Update(record)
    }

    /// `le.setObservedRecord` after a successful Create/Update
    /// (`leaderelection.go:426,444,476` + `510-518`): replaces the observed
    /// record and freshness instant, but — exactly like client-go — leaves
    /// the raw-record comparison baseline untouched.
    pub fn record_written(&mut self, record: LeaderElectionRecord, at: Instant) {
        self.observed_record = Some(record);
        self.observed_at = Some(at);
    }
}

/// Port of `wait.Jitter` (`k8s.io/apimachinery/pkg/util/wait/backoff.go`):
/// returns `duration + r*max_factor*duration`, where `r` is uniform in
/// `[0.0, 1.0)`; a non-positive `max_factor` falls back to `1.0`. With the
/// client-go acquire loop's inputs (2s, 1.2) the sleep lands in `[2s, 4.4s)`.
fn jitter(duration: Duration, max_factor: f64, r: f64) -> Duration {
    let max_factor = if max_factor <= 0.0 { 1.0 } else { max_factor };
    duration + Duration::from_secs_f64(r * max_factor * duration.as_secs_f64())
}

/// A uniform sample in `[0.0, 1.0)` from the OS-seeded std hasher — good
/// enough for retry jitter (client-go uses math/rand, also non-crypto)
/// without pulling a rand dependency into the crate.
fn jitter_sample() -> f64 {
    use std::collections::hash_map::RandomState;
    use std::hash::{BuildHasher, Hasher};

    let mut hasher = RandomState::new().build_hasher();
    hasher.write_u128(
        SystemTime::now()
            .duration_since(UNIX_EPOCH)
            .unwrap_or_default()
            .as_nanos(),
    );
    // 53 high-entropy bits -> [0, 1), the same construction as
    // rand's Standard f64 distribution.
    (hasher.finish() >> 11) as f64 / (1u64 << 53) as f64
}

/// Wall-clock now as a jiff `Timestamp` (k8s-openapi enables jiff without
/// `std`, so `Timestamp::now()` is unavailable; go through `SystemTime`).
fn now_wall() -> Timestamp {
    let since_epoch = SystemTime::now()
        .duration_since(UNIX_EPOCH)
        .unwrap_or_default();
    Timestamp::new(
        since_epoch.as_secs() as i64,
        since_epoch.subsec_nanos() as i32,
    )
    .unwrap_or(Timestamp::UNIX_EPOCH)
}

/// The Lease I/O half of the elector: `resourcelock.LeaseLock` plus the
/// client-go `tryAcquireOrRenew` fetch/create/update sequencing around
/// [`ElectionCore`].
struct LeaseElector {
    api: Api<Lease>,
    namespace: String,
    name: String,
    core: ElectionCore,
    /// `LeaseLock.lease` — the object from the last Get/Create/Update, PUT
    /// back on Update so the carried `resourceVersion` gives us optimistic
    /// concurrency (a concurrent writer turns our Update into a conflict
    /// error, which is just a failed attempt).
    lease: Option<Lease>,
}

impl LeaseElector {
    /// `LeaseLock.Describe()` (`resourcelock/leaselock.go:118-120`).
    fn describe(&self) -> String {
        format!("{}/{}", self.namespace, self.name)
    }

    /// Update the lock: swap the spec into the last-seen Lease object and PUT
    /// it back (`resourcelock/leaselock.go:87-100`).
    async fn update_lock(&mut self, record: &LeaderElectionRecord) -> Result<(), kube::Error> {
        let mut lease = self
            .lease
            .clone()
            .expect("update_lock requires a prior get or create");
        lease.spec = Some(record.to_lease_spec());
        let updated = self
            .api
            .replace(&self.name, &PostParams::default(), &lease)
            .await?;
        self.lease = Some(updated);
        Ok(())
    }

    /// Port of `tryAcquireOrRenew` (`leaderelection.go:408-478`). Returns
    /// true when we hold the lease after this attempt.
    async fn try_acquire_or_renew(&mut self) -> bool {
        let now = Instant::now();
        let wall = now_wall();

        // 1. fast path for the leader to update optimistically assuming that
        // the record observed last time is the current version.
        if let Some(record) = self.core.fast_path_record(wall, now) {
            if self.lease.is_some() {
                match self.update_lock(&record).await {
                    Ok(()) => {
                        self.core.record_written(record, Instant::now());
                        return true;
                    }
                    Err(err) => {
                        error!(
                            error = %err,
                            lease = %self.describe(),
                            "failed to update lock optimistically, falling back to slow path"
                        );
                    }
                }
            }
        }

        // 2. obtain or create the ElectionRecord.
        let fetched_lease = match self.api.get_opt(&self.name).await {
            Ok(Some(lease)) => lease,
            Ok(None) => {
                let record = self.core.fresh_record(wall);
                let lease = Lease {
                    metadata: ObjectMeta {
                        name: Some(self.name.clone()),
                        namespace: Some(self.namespace.clone()),
                        ..Default::default()
                    },
                    spec: Some(record.to_lease_spec()),
                };
                match self.api.create(&PostParams::default(), &lease).await {
                    Ok(created) => {
                        self.lease = Some(created);
                        self.core.record_written(record, Instant::now());
                        return true;
                    }
                    Err(err) => {
                        error!(
                            error = %err,
                            lease = %self.describe(),
                            "error initially creating leader election record"
                        );
                        return false;
                    }
                }
            }
            Err(err) => {
                error!(
                    error = %err,
                    lease = %self.describe(),
                    "error retrieving resource lock"
                );
                return false;
            }
        };

        // 3.-4. decide against the fetched record.
        let fetched = LeaderElectionRecord::from_lease_spec(
            fetched_lease.spec.as_ref().unwrap_or(&LeaseSpec::default()),
        );
        self.lease = Some(fetched_lease);
        match self.core.decide(&fetched, wall, now, Instant::now()) {
            Decision::Standby { holder } => {
                debug!(
                    holder = %holder,
                    lease = %self.describe(),
                    "lock is held and has not yet expired"
                );
                false
            }
            Decision::Update(record) => match self.update_lock(&record).await {
                Ok(()) => {
                    self.core.record_written(record, Instant::now());
                    true
                }
                Err(err) => {
                    error!(error = %err, lease = %self.describe(), "failed to update lock");
                    false
                }
            },
        }
    }

    /// `LeaderElector.Run` minus release-on-cancel (`leaderelection.go:211-222`):
    /// block acquiring, flip the watch to `true`, renew until the renew
    /// deadline is missed, flip to `false` and return the fatal error.
    async fn run(mut self, is_leader_tx: watch::Sender<bool>) -> LeaderElectionError {
        // acquire: retry every RetryPeriod jittered by JitterFactor until we
        // hold the lease (`leaderelection.go:252-275`).
        info!(
            lease = %self.describe(),
            identity = %self.core.identity(),
            "attempting to acquire leader lease"
        );
        loop {
            if self.try_acquire_or_renew().await {
                break;
            }
            debug!(lease = %self.describe(), "failed to acquire lease");
            tokio::time::sleep(jitter(RETRY_PERIOD, JITTER_FACTOR, jitter_sample())).await;
        }
        info!(
            lease = %self.describe(),
            identity = %self.core.identity(),
            "successfully acquired lease"
        );
        is_leader_tx.send_replace(true);

        // renew: every RetryPeriod run one renewal cycle that itself retries
        // every RetryPeriod for at most RenewDeadline; missing the deadline
        // loses leadership (`leaderelection.go:278-305`, wait.Until around
        // wait.PollUntilContextTimeout).
        loop {
            let renewed = tokio::time::timeout(RENEW_DEADLINE, async {
                loop {
                    if self.try_acquire_or_renew().await {
                        return;
                    }
                    tokio::time::sleep(RETRY_PERIOD).await;
                }
            })
            .await;
            match renewed {
                Ok(()) => tokio::time::sleep(RETRY_PERIOD).await,
                Err(_deadline_elapsed) => break,
            }
        }

        is_leader_tx.send_replace(false);
        error!(
            lease = %self.describe(),
            identity = %self.core.identity(),
            "failed to renew lease within the renew deadline; leadership lost"
        );
        LeaderElectionError::LeadershipLost {
            namespace: self.namespace,
            name: self.name,
            identity: self.core.identity().to_owned(),
        }
    }
}

/// Spawns the leader-election loop for `lease_name` in `namespace`.
///
/// `identity` must be unique per replica; controller-runtime builds it as
/// `"<hostname>_<uuid>"` (`pkg/leaderelection/leader_election.go:88-94`) and
/// the manager binary is expected to do the same. The Jumpstarter controller
/// contends for lease name `"a38b78e7.jumpstarter.dev"` in the watch
/// namespace (`controller/cmd/main.go:173,189`).
///
/// Returns:
/// - a [`watch::Receiver`] that starts at `false`, flips to `true` when the
///   lease is acquired, and back to `false` if it is later lost — gate
///   leader-only components (reconcilers, the gRPC service) on it;
/// - the [`JoinHandle`] of the election task. The task **only** completes
///   after leadership was held and then lost ([`LeaderElectionError::LeadershipLost`]);
///   like the Go manager, the caller must treat that as fatal and exit.
///
/// There is no release-on-cancel (`LeaderElectionReleaseOnCancel` is unset in
/// `controller/cmd/main.go`): aborting the task leaves the Lease record in
/// place, and successors take over once it expires a `LEASE_DURATION` after
/// they first observed its final renewTime.
pub fn spawn_leader_election(
    client: kube::Client,
    namespace: &str,
    lease_name: &str,
    identity: &str,
) -> (watch::Receiver<bool>, JoinHandle<LeaderElectionError>) {
    let elector = LeaseElector {
        api: Api::namespaced(client, namespace),
        namespace: namespace.to_owned(),
        name: lease_name.to_owned(),
        core: ElectionCore::new(identity),
        lease: None,
    };
    let (tx, rx) = watch::channel(false);
    let handle = tokio::spawn(elector.run(tx));
    (rx, handle)
}

#[cfg(test)]
mod tests {
    use super::*;

    const ME: &str = "rust-replica_2f0e2e2e-aaaa-bbbb-cccc-000000000001";
    const OTHER: &str = "go-replica_11111111-2222-3333-4444-555555555555";

    /// Wall-clock helper: `secs` (+`nanos`) after the epoch.
    fn ts(secs: i64, nanos: i32) -> Timestamp {
        Timestamp::new(secs, nanos).unwrap()
    }

    fn record(holder: &str, transitions: i32, renew_secs: i64) -> LeaderElectionRecord {
        LeaderElectionRecord {
            holder_identity: holder.to_owned(),
            lease_duration_seconds: 15,
            acquire_time: Some(ts(1_000, 0)),
            renew_time: Some(ts(renew_secs, 0)),
            leader_transitions: transitions,
            preferred_holder: String::new(),
            strategy: String::new(),
        }
    }

    fn core() -> ElectionCore {
        ElectionCore::new(ME)
    }

    /// Fake monotonic clock: a base instant plus explicit offsets.
    fn clock() -> impl Fn(u64) -> Instant {
        let t0 = Instant::now();
        move |secs: u64| t0 + Duration::from_secs(secs)
    }

    #[test]
    fn fresh_record_shape_for_create_on_missing_lease() {
        // go: leaderelection.go:409-415,439-446 — the record Create()d when
        // the Lease does not exist: we hold it, acquire == renew == now,
        // zero transitions.
        let now = ts(2_000, 250_000_000);
        let record = core().fresh_record(now);
        assert_eq!(record.holder_identity, ME);
        assert_eq!(record.lease_duration_seconds, 15);
        assert_eq!(record.acquire_time, Some(now));
        assert_eq!(record.renew_time, Some(now));
        assert_eq!(record.leader_transitions, 0);
        assert!(record.preferred_holder.is_empty());
        assert!(record.strategy.is_empty());
    }

    #[test]
    fn acquires_record_with_empty_holder_and_increments_transitions() {
        // go: leaderelection.go:455 — an empty holderIdentity means nobody
        // owns the lease; step 4 still takes the non-leader branch, so
        // transitions increment even on an uncontested acquire.
        let at = clock();
        let mut core = core();
        let released = LeaderElectionRecord {
            holder_identity: String::new(),
            ..record(OTHER, 3, 1_000)
        };
        let now = ts(2_000, 0);
        let written = match core.decide(&released, now, at(0), at(0)) {
            Decision::Update(rec) => {
                assert_eq!(rec.holder_identity, ME);
                assert_eq!(rec.leader_transitions, 4);
                assert_eq!(rec.acquire_time, Some(now));
                assert_eq!(rec.renew_time, Some(now));
                rec
            }
            other => panic!("expected Update, got {other:?}"),
        };
        // go: leaderelection.go:471-477 — IsLeader() flips only once the
        // Update succeeds and setObservedRecord runs.
        assert!(!core.is_leader());
        core.record_written(written, at(0));
        assert!(core.is_leader());
    }

    #[test]
    fn does_not_steal_fresh_foreign_lease() {
        // go: leaderelection.go:455-458 — a non-empty holder we just observed
        // is fresh for a full LeaseDuration from OUR observation instant,
        // regardless of the wall-clock timestamps in the record.
        let at = clock();
        let mut core = core();
        let theirs = record(OTHER, 7, 1_000);

        // First observation at t=0: fresh by definition.
        assert_eq!(
            core.decide(&theirs, ts(5_000, 0), at(0), at(0)),
            Decision::Standby {
                holder: OTHER.into()
            },
        );
        assert!(!core.is_leader());
        assert_eq!(core.leader(), Some(OTHER));

        // Unchanged record at t=14s: still strictly inside the 15s window.
        assert_eq!(
            core.decide(&theirs, ts(5_014, 0), at(14), at(14)),
            Decision::Standby {
                holder: OTHER.into()
            },
        );
    }

    #[test]
    fn takes_over_expired_lease_and_increments_transitions() {
        // go: leaderelection.go:506-508 — validity is observedTime + duration
        // strictly After(now); at exactly the boundary the lease is expired.
        // go: leaderelection.go:466-467 — takeover increments transitions and
        // (via the default record) stamps acquireTime = now.
        let at = clock();
        let mut core = core();
        let theirs = record(OTHER, 7, 1_000);

        assert!(matches!(
            core.decide(&theirs, ts(5_000, 0), at(0), at(0)),
            Decision::Standby { .. }
        ));

        // Same record, exactly LeaseDuration after we first observed it.
        let now = ts(5_015, 0);
        match core.decide(&theirs, now, at(15), at(15)) {
            Decision::Update(rec) => {
                assert_eq!(rec.holder_identity, ME);
                assert_eq!(rec.leader_transitions, 8);
                assert_eq!(rec.acquire_time, Some(now));
                assert_eq!(rec.renew_time, Some(now));
            }
            other => panic!("expected takeover Update, got {other:?}"),
        }
    }

    #[test]
    fn foreign_renewal_restarts_local_expiry_window() {
        // go: leaderelection.go:450-454 — any change to the fetched record
        // (here: renewTime) resets observedTime, so the holder gets a fresh
        // LeaseDuration from the instant WE saw the renewal.
        let at = clock();
        let mut core = core();

        assert!(matches!(
            core.decide(&record(OTHER, 7, 1_000), ts(5_000, 0), at(0), at(0)),
            Decision::Standby { .. }
        ));
        // Holder renews (renewTime moves); we observe it at t=14.
        assert!(matches!(
            core.decide(&record(OTHER, 7, 1_002), ts(5_014, 0), at(14), at(14)),
            Decision::Standby { .. }
        ));
        // t=28: only 14s since the re-observation — still fresh.
        assert!(matches!(
            core.decide(&record(OTHER, 7, 1_002), ts(5_028, 0), at(28), at(28)),
            Decision::Standby { .. }
        ));
        // t=29: 15s elapsed since re-observation — expired, take over.
        assert!(matches!(
            core.decide(&record(OTHER, 7, 1_002), ts(5_029, 0), at(29), at(29)),
            Decision::Update(_)
        ));
    }

    #[test]
    fn sub_second_renew_change_is_not_a_new_observation() {
        // go: leaderelection.go:450 compares the marshalled record, whose
        // metav1.Time fields serialize at whole-second precision — a renewTime
        // that moved only within the same second is byte-identical and must
        // NOT refresh observedTime.
        let at = clock();
        let mut core = core();

        let mut theirs = record(OTHER, 7, 1_000);
        assert!(matches!(
            core.decide(&theirs, ts(5_000, 0), at(0), at(0)),
            Decision::Standby { .. }
        ));
        // renewTime gains 400ms but stays in second 1000.
        theirs.renew_time = Some(ts(1_000, 400_000_000));
        // If this had counted as a new observation, observed_at would move to
        // t=15 and the holder would be fresh again; instead it is expired.
        assert!(matches!(
            core.decide(&theirs, ts(5_015, 0), at(15), at(15)),
            Decision::Update(_)
        ));
    }

    #[test]
    fn fast_path_renews_own_record_preserving_acquire_and_transitions() {
        // go: leaderelection.go:417-430 — the optimistic leader path carries
        // acquireTime/leaderTransitions over from the observation and only
        // moves renewTime.
        let at = clock();
        let mut core = core();
        let mine = LeaderElectionRecord {
            acquire_time: Some(ts(900, 0)),
            ..record(ME, 2, 1_000)
        };
        core.record_written(mine, at(0));

        let now = ts(1_004, 0);
        let renewed = core
            .fast_path_record(now, at(4))
            .expect("leader with valid lease");
        assert_eq!(renewed.holder_identity, ME);
        assert_eq!(renewed.acquire_time, Some(ts(900, 0)));
        assert_eq!(renewed.renew_time, Some(now));
        assert_eq!(renewed.leader_transitions, 2);
    }

    #[test]
    fn fast_path_unavailable_when_stale_or_not_leader() {
        // go: leaderelection.go:419 — fast path requires IsLeader() AND
        // isLeaseValid(now).
        let at = clock();

        // Nothing observed yet.
        assert!(core().fast_path_record(ts(1_000, 0), at(0)).is_none());

        // Own lease, but a full LeaseDuration has elapsed locally.
        let mut mine = core();
        mine.record_written(record(ME, 2, 1_000), at(0));
        assert!(mine.fast_path_record(ts(1_015, 0), at(15)).is_none());

        // Fresh observation, but the holder is someone else.
        let mut standby = core();
        standby.record_written(record(OTHER, 2, 1_000), at(0));
        assert!(standby.fast_path_record(ts(1_001, 0), at(1)).is_none());
    }

    #[test]
    fn slow_path_renewal_keeps_transitions_and_acquire_time() {
        // go: leaderelection.go:462-465 — when the fetched record already
        // names us, the update preserves acquireTime and does NOT increment
        // leaderTransitions.
        let at = clock();
        let mut core = core();
        // Become leader by taking over an expired foreign record.
        assert!(matches!(
            core.decide(&record(OTHER, 7, 1_000), ts(5_000, 0), at(0), at(0)),
            Decision::Standby { .. }
        ));
        let takeover = match core.decide(&record(OTHER, 7, 1_000), ts(5_015, 0), at(15), at(15)) {
            Decision::Update(rec) => rec,
            other => panic!("expected Update, got {other:?}"),
        };
        core.record_written(takeover.clone(), at(15));

        // A later slow-path attempt fetches our own record back.
        match core.decide(&takeover, ts(5_017, 0), at(17), at(17)) {
            Decision::Update(rec) => {
                assert_eq!(rec.holder_identity, ME);
                assert_eq!(rec.leader_transitions, takeover.leader_transitions);
                assert_eq!(rec.acquire_time, takeover.acquire_time);
                assert_eq!(rec.renew_time, Some(ts(5_017, 0)));
            }
            other => panic!("expected renewal Update, got {other:?}"),
        }
    }

    #[test]
    fn jittered_retry_bounds() {
        // go: k8s.io/apimachinery/pkg/util/wait/backoff.go Jitter — the
        // acquire retry sleeps in [period, period*(1+factor)).
        let period = RETRY_PERIOD;

        assert_eq!(jitter(period, JITTER_FACTOR, 0.0), Duration::from_secs(2));
        assert_eq!(
            jitter(period, JITTER_FACTOR, 0.5),
            Duration::from_millis(3_200)
        );
        // r is drawn from [0, 1): the supremum is period * 2.2.
        assert_eq!(
            jitter(period, JITTER_FACTOR, 1.0),
            Duration::from_millis(4_400)
        );
        // Non-positive factors fall back to 1.0 exactly like wait.Jitter.
        assert_eq!(jitter(period, 0.0, 0.5), Duration::from_secs(3));

        for _ in 0..1_000 {
            let r = jitter_sample();
            assert!((0.0..1.0).contains(&r), "jitter sample {r} out of [0,1)");
            let d = jitter(period, JITTER_FACTOR, r);
            assert!(
                (Duration::from_secs(2)..Duration::from_millis(4_400)).contains(&d),
                "jittered sleep {d:?} out of [2s, 4.4s)"
            );
        }
    }

    #[test]
    fn lease_spec_round_trip_matches_client_go_mapping() {
        // go: resourcelock/leaselock.go:141-186 — spec<->record mapping:
        // holder/duration/transitions always written, coordinated fields only
        // when non-empty, missing spec fields read back as zero values.
        let rec = record(ME, 5, 1_000);
        let spec = rec.to_lease_spec();
        assert_eq!(spec.holder_identity.as_deref(), Some(ME));
        assert_eq!(spec.lease_duration_seconds, Some(15));
        assert_eq!(spec.lease_transitions, Some(5));
        assert_eq!(spec.acquire_time, rec.acquire_time.map(MicroTime));
        assert_eq!(spec.renew_time, rec.renew_time.map(MicroTime));
        assert_eq!(spec.preferred_holder, None);
        assert_eq!(spec.strategy, None);
        assert_eq!(LeaderElectionRecord::from_lease_spec(&spec), rec);

        // An empty spec (e.g. a Lease created by something else entirely)
        // reads as the Go zero-value record.
        let empty = LeaderElectionRecord::from_lease_spec(&LeaseSpec::default());
        assert_eq!(empty.holder_identity, "");
        assert_eq!(empty.lease_duration_seconds, 0);
        assert_eq!(empty.acquire_time, None);
        assert_eq!(empty.renew_time, None);
        assert_eq!(empty.leader_transitions, 0);
    }

    #[test]
    fn written_record_does_not_move_observation_baseline() {
        // go: leaderelection.go:512-518 — setObservedRecord after our own
        // write updates observedRecord/observedTime but NOT observedRawRecord,
        // so the next fetch of our own record still counts as "changed" and
        // refreshes observedTime. Mirroring this keeps freshness bookkeeping
        // bit-compatible with client-go.
        let at = clock();
        let mut core = core();

        // Observe a released record, take it over, and record the write.
        let released = LeaderElectionRecord {
            holder_identity: String::new(),
            ..record(OTHER, 0, 1_000)
        };
        let mine = match core.decide(&released, ts(5_000, 0), at(0), at(0)) {
            Decision::Update(rec) => rec,
            other => panic!("expected Update, got {other:?}"),
        };
        core.record_written(mine.clone(), at(0));

        // 14s later a slow-path fetch returns exactly what we wrote. The
        // baseline is still the released record, so this re-observes (Go
        // compares raw bytes from the last *fetch*) — afterwards the fast
        // path is valid at t=28 (observed_at moved to 14), which it would
        // not be had the baseline swallowed the re-observation.
        match core.decide(&mine, ts(5_014, 0), at(14), at(14)) {
            Decision::Update(rec) => core.record_written(rec, at(14)),
            other => panic!("expected renewal Update, got {other:?}"),
        }
        assert!(core.fast_path_record(ts(5_028, 0), at(28)).is_some());
    }
}
