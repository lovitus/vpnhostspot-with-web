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
baselines; old-generation samples cannot seed it. VPN and upstream roles remain
flow-driven. Neighbours have no age expiry: `monitorSnapshots` folds deltas and
suppresses identical tables, so an unchanged table is still current. Its snapshot
now explicitly reports source availability at startup/completion/failure. The
dashboard atomically retains the last successful table with availability false on
invalidation, accepts explicit removals from the next valid table, and starts a
replacement session without old-generation data. No heartbeat or longer TTL is
invented.

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

## Schema 3 coverage and sources

| Fields | Existing authority and limits |
| --- | --- |
| Downstream, actual gateway, port, session uptime | Existing RoutingManager lifecycle, interface IPv4 selection, monotonic dashboard creation time; HTTP remains app-owned port 8080 |
| Client MAC, IPv4/IPv6, source interfaces, address states | NetlinkNeighbour.monitorSnapshots, including bridgeMasterByMember; Client and ClientAddressInfo adapt rows using existing grouping semantics. A valid neighbour is not proof of a current Wi-Fi association |
| Name | ClientRecord.Dao.lookupOrDefaultFlow nickname, then existing P2P deviceName; no hostname/MAC lookup requests, database writes, Activity or ViewModel |
| P2P association/online state and count | RepeaterService.updateGroup publishes only interface, MAC and deviceName to the adapter, including null on teardown. Group credentials are not retained or exported. Other Wi-Fi association sources remain unavailable rather than being inferred from neighbours |
| Per-client and aggregate bytes/s and bytes | Existing DaemonController counters converted by TrafficRecorder.counterKey; existing bounded session delta sampler. Totals survive retired sources and failed reads. Unavailable/first rate baselines stay null. Aggregate includes retained session totals, not only currently visible rows |
| Last traffic change | Existing sampled counter delta greater than zero; milliseconds since that observation. It is not an exact last-packet or last-user-activity timestamp |
| DNS sent/received packet counts | Raw existing /dns TrafficCounter sent_packets/received_packets for each MAC, retained on failed samples with dnsCountersAvailable false. These have the daemon source's lifetime/reset semantics, not the dashboard byte-delta lifetime. Local dashboard DNS answers do not increment resolver counters |
| VPN, primary, fallback | Upstreams.vpn/primary/fallback; existing LinkProperties addresses, allInterfaceNames, DNS servers and allRoutes. Existing ConnectivityManager.getNetworkCapabilities reads run in background flow collectors; type/validated are timestamped observations at the last upstream emission, not a new capabilities monitor |
| Selected upstream | The existing Upstreams.primary role selection, explicitly labelled as a role. Neither its presence nor fallback availability proves effective per-flow routing |
| Diagnostics | Last counter reply health/time/age, dashboard-scoped sampler/monitor/name errors (bounded, deduplicated), BuildConfig version, schema/patch identifier, Android API/release, kernel os.version and device elapsedRealtime. Counter responsiveness is not a daemon PID/process-liveness assertion |
| Export | Read-only /api/debug returns the same status contract. The page downloads the last complete successful raw JSON, preserving machine fields, nulls and integer bytes without a JavaScript parse/serialize round trip |

DNS success/failure totals, active TCP/UDP flow counts, effective per-client
egress, fallback use and exact packet activity are `null` with explicit UI labels.
No conntrack reader, proc/iptables parser, DNS accountant, route selector, new
daemon command or protocol fields were added to fill those metrics. The monitor
availability addition is a JVM-side observation lifecycle signal; it changes no
daemon lifecycle, routing, firewall or root cleanup side effects.

Frontend publication is atomic: check HTTP status, parse and validate the payload,
build the entire replacement offscreen, then replace the displayed snapshot.
HTTP errors, invalid JSON, partial rows, aborted fetches and rendering errors leave
the last successful rows and JSON intact with an explicit error banner. Source
availability is independent of HTTP success; unavailable observations remain
clearly labelled. Details expansion survives refreshes. All names and errors use
textContent, and all assets remain local under the existing restrictive CSP.

The hosted production-helper suite covers silence beyond three seconds, explicit
deletion, invalidation, fresh generations, traffic freshness and total retention,
correct Host port acceptance/rejection, loopback HTTP, deadlines and stalled
sampler quarantine. Hosted Chromium covers the actual compiled production HTML
with explicitly identified test fixtures, including malformed and failed responses,
recovery, zero clients, text safety, exact JSON export and mobile layout. These
checks do not claim end-to-end Android daemon/Room/P2P runtime verification or
downstream reachability; those remain separate device checks.
