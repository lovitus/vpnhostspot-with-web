# Dashboard sampling ownership

The web dashboard is maintained in `patches/0005-web-admin.patch`, replayed on
p4 commit `ae6f39ed82153b5edf6c797bc1a27e17ab5ee845` by the hosted verification
workflow. Editing the unpatched app tree does not validate the shipped source.

TrafficRecorder remains the sole caller of DaemonController.readTrafficCounters.
DNS and NAT66 remove retired entries on read, so a second web reader could steal
final accounting values. After its existing database insertion pass, the recorder
publishes an immutable raw CounterSnapshot through a read-only StateFlow. Publishing
never suspends for observers. Failures invalidate availability while retaining the
last successful data and timestamps; clean resets the publication.

The original schedule and accounting loop are unchanged: minute-boundary updates
in the background, at most 1015 ms while the original foregroundUpdates has a
subscriber. The web dashboard subscribes only to counterSnapshots, never to the
foreground stream, and never requests an update. It adds no daemon command,
recorder mutex wait, recurring database insert, request timeout or cancellation
to the recorder/daemon transport. Cancelling a web session cannot cancel a
recorder-owned request. HTTP only peeks a volatile immutable publication.

This deliberately trades independent one-second sampling for accounting integrity.
The page refreshes once per second, but its traffic is the latest recorder
observation. Rates are averages over the actual monotonic interval between two
observations, exposed as trafficRateWindowMillis. With only web clients, a new
session may wait about a minute for its baseline and another minute for a rate.
It becomes approximately second-scale only when the existing native foreground
traffic view is already active. Web never accelerates persistence. Rates expire
after 65 seconds (the original one-minute cadence plus the page's five-second
fetch budget); sample age remains visible and uses monotonic time.

Per-session deltas retain completed source totals and discard old-generation
baselines. Empty/failed observations retain totals and provide no rates. Missing,
new or reset sources suppress the affected MAC's rate for that interval; retained
absent MACs do not acquire fabricated zero rates. DNS availability requires an
observed DNS source for that MAC. A partial reply cannot establish complete source
health: diagnostics.daemon stays null, and counterObservation describes only a
recorder publication. Source failures that were never observed cannot be inferred
without expanding the daemon protocol; the page states that limitation.

The publication is conflated, observational state, not a durable replay log. A
slow web observer can skip intermediate publications; sources born and retired
between observations cannot be reconstructed into session totals. The recorder
persists every consumed final counter using its original accounting algorithm
before publishing. Its database remains the authority for historical totals.
No second accounting ledger, routing owner, connection tracker or DNS tracker is
introduced to fill dashboard fields.

VPN and upstream roles remain flow-driven. Neighbours have no age expiry:
monitorObservations folds deltas and reports startup/completion/failure separately.
The dashboard retains the last successful table on invalidation, processes explicit
removals on the next valid table, and isolates replacement generations. The existing
monitorSnapshots is the available-table-only projection, so routing/notification/UI
consumers never receive a fabricated empty table on an observation-health event.

Hosted regressions enforce the single destructive counter reader, unchanged
schedule, publication after database insertion, and passive HTTP wiring. The real
Rust DNS/NAT66 tests assert the final byte values and once-only retirement drain.
Production JVM helper tests hold a recorder-like publisher behind a mutex/barrier
while 64 concurrent publication reads and 32 loopback HTTP exchanges finish; web
cancellation leaves that owner alive, then release publishes recovery. This tests
the publication boundary and source wiring, not Android Room integration or real
daemon transport latency. Existing HTTP/deadline/lifecycle, source-churn/reset,
row-liveness and frontend failure-retention coverage remains.

## Schema 3 coverage and sources

| Fields | Existing authority and limits |
| --- | --- |
| Downstream, actual gateway, port, session uptime | Existing RoutingManager lifecycle, interface IPv4 selection, monotonic dashboard creation time; HTTP remains app-owned port 8080 |
| Client MAC, IPv4/IPv6, source interfaces, address states | NetlinkNeighbour.monitorSnapshots, including bridgeMasterByMember; Client and ClientAddressInfo adapt rows using existing grouping semantics. A valid neighbour is not proof of a current Wi-Fi association |
| Name | ClientRecord.Dao.lookupOrDefaultFlow nickname, then existing P2P deviceName; no hostname/MAC lookup requests, database writes, Activity or ViewModel |
| P2P association/online state and count | RepeaterService.updateGroup publishes only interface, MAC and deviceName to the adapter, including null on teardown. Group credentials are not retained or exported. Other Wi-Fi association sources remain unavailable rather than being inferred from neighbours |
| Per-client and aggregate bytes/s and bytes | Existing recorder-owned raw publications converted by TrafficRecorder.counterKey; original per-session delta adapter with explicit observation window. Totals survive retired sources and failed reads. Unavailable/first rate baselines stay null. Aggregate includes retained session totals, not only currently visible rows |
| Last traffic change | Existing sampled counter delta greater than zero; milliseconds since that observation. It is not an exact last-packet or last-user-activity timestamp |
| DNS sent/received packet counts | Raw existing /dns TrafficCounter sent_packets/received_packets for each MAC, retained on failed samples with dnsCountersAvailable false. These have the daemon source's lifetime/reset semantics, not the dashboard byte-delta lifetime. Local dashboard DNS answers do not increment resolver counters |
| VPN, primary, fallback | Upstreams.vpn/primary/fallback; existing LinkProperties addresses, allInterfaceNames, DNS servers and allRoutes. Existing ConnectivityManager.getNetworkCapabilities reads run in background flow collectors; type/validated are timestamped observations at the last upstream emission, not a new capabilities monitor |
| Selected upstream | The existing Upstreams.primary role selection, explicitly labelled as a role. Neither its presence nor fallback availability proves effective per-flow routing |
| Diagnostics | Last recorder publication/time/monotonic age, scoped recorder-publication/monitor/name errors (bounded, deduplicated), BuildConfig version, schema/patch identifier, Android API/release, kernel os.version and device elapsedRealtime. Counter responsiveness is not a daemon PID/process-liveness assertion |
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
publisher isolation. Hosted Chromium covers the actual compiled production HTML
with explicitly identified test fixtures, including malformed and failed responses,
recovery, zero clients, text safety, exact JSON export and mobile per-client cards
with visible speeds/bytes and compact expanded diagnostics. These
checks do not claim end-to-end Android daemon/Room/P2P runtime verification or
downstream reachability; those remain separate device checks.
