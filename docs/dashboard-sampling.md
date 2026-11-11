# Dashboard sampling ownership

The web dashboard is maintained in `patches/0005-web-admin.patch`, replayed on
p4 commit `ae6f39ed82153b5edf6c797bc1a27e17ab5ee845` by the hosted verification
workflow. Editing the unpatched app tree does not validate the shipped source.

One process-wide dashboard coroutine samples raw daemon counters for all active
downstreams. Its one-second await budget matches the page refresh interval.
It uses only the recorder's pure counter-key conversion; it never acquires the
recorder mutex, calls recorder update, or writes the traffic database. Existing
historical traffic recording retains its separate accounting schedule.

A counter read runs in the existing supervisor scope, independently of the
sampler loop and HTTP/session jobs. Timeout cancels that read without joining
its cleanup. If cancellation cleanup stalls, the single pending deferred stays
quarantined: later cycles report unavailable and cannot queue another read.
Its late result is discarded, and sampling resumes only after it completes.
Stopping a dashboard generation closes its sockets and cancels its listener
without waiting for sampling. No new daemon API, routing state, or root-side
cleanup is introduced.

Only the background owner updates a session's traffic baseline and publishes
its immutable traffic snapshot. HTTP status uses a volatile cache peek with no
loader lock or suspension. Traffic older than three seconds loses its rates
and availability while preserving totals. Failed samples also preserve totals
and the last successful sample timestamp. A replacement session has fresh
baselines; old-generation samples cannot seed it. VPN state remains flow-driven,
and status checks neighbour age on each request instead of caching rendered JSON.

Hosted verification preserves the existing HTTP, deadline, lifecycle, cache,
traffic reset/source-churn, Android JVM, and DNS tests. Added JVM coverage uses
the actual production cache, sampler, snapshot expiration, and HTTP parser with
a stalled read whose cancellation cleanup is deliberately held open. It checks
64 concurrent cache reads and 32 loopback HTTP exchanges, timeout, no replacement
reads during quarantine, and recovery. Wiring assertions verify that the actual
WebAdmin status path uses the tested cache peek and cannot poll or mutate the
traffic baseline.

This is evidence for the production helper boundary and source wiring, not an
Android device end-to-end test of WebAdmin or real daemon cancellation. The
daemon controller still shares its transport lock with other commands; these
tests do not prove routing latency under a blocked transport send/startup or
other unrelated recorder activity. The dashboard adds no recorder lock waits,
and stalled dashboard reads consume at most one outstanding counter request.
