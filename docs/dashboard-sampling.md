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

## Local-only client UI verification

`tests/dashboard_dom.cjs PATCHED_SOURCE` (Node + jsdom 26) executes the production
HTML/JavaScript with the same fixtures as the Playwright suite; it covers capsule
switching, All, independent/all expansion through polling, keyed focus, selected
client removal, failure retention and hostile hostname text. It does not measure
layout or scrolling. `tests/dashboard_browser.cjs` remains the real-browser check
for mobile width, capsule scrolling, focus/scroll preservation and exact JSON export.
The JVM helper suite adds hostname precedence, exact source matching, ambiguity,
empty/error/restart and multi-address checks. Python lifecycle tests assert the
session-owned callback wiring and prohibit foreground traffic subscriptions.

## Schema 3 coverage and sources

| Fields | Existing authority and limits |
| --- | --- |
| Downstream, actual gateway, port, session uptime | Existing RoutingManager lifecycle, interface IPv4 selection, monotonic dashboard creation time; HTTP remains app-owned port 8080 |
| Client MAC, IPv4/IPv6, source interfaces, address states | NetlinkNeighbour.monitorSnapshots, including bridgeMasterByMember; Client and ClientAddressInfo adapt rows using existing grouping semantics. A valid neighbour is not proof of a current Wi-Fi association |
| Name | Saved ClientRecord nickname > system addresses[].hostname > P2P deviceName > short MAC. A per-dashboard-session RootManager.use / TetheringCommands.ClientsFlow subscription consumes the same Android >=30 TetheringManagerCompat.ClientsChanged source as ClientViewModel, independent of Activity. No ViewModel, foregroundUpdates subscription, network lookup or database writes |
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

System names are published as immutable value snapshots (persistent lists, copied strings),
not framework callback objects. Each session owns its cold callback subscription;
stop/cancellation unregisters through the existing ClientsFlow cleanup and clears
its snapshot, and restart begins at waiting (unsupported below API 30). No retry
loop or new root command is introduced. Successful empty callbacks replace old
names; waiting, unsupported, unavailable, error and empty are distinct. Source
errors are reported in recentErrors and do not stop the listener. No stale names
are retained after errors or neighbour invalidation. App and Web share the source
contract, not an Activity/ViewModel or a global cache.

Schema 3 is additive: clients now include hostnames, hostnameSource,
hostnameAddresses (IP/hostname/type/downstream), hostnameStatus and hostnameChangedAt.
A name requires an exact MAC + actual current neighbour IP + compatible TetherType
(NCM accepts USB) match on exactly one canonical downstream. Bridge members use
bridgeMasterByMember; ambiguous overlapping downstream tuples are not named.
ClientsChanged has no interface identifier: unknown types, missing addresses,
stale neighbours and ambiguous matches intentionally fall back. Saved nicknames
still win but do not hide the system names in details. Android API33 can deliver
this callback, yet the OS/ROM or P2P may omit hostnames; no vendor-based invention
or guarantee that a callback contains a hostname is made.

Frontend publication checks HTTP status, parses and validates the payload,
builds the next snapshot offscreen, then reconciles keyed existing DOM nodes.
HTTP errors, invalid JSON, partial rows, aborted fetches and rendering errors leave
the last successful rows and JSON intact with an explicit error banner. Source
availability is independent of HTTP success; unavailable observations remain
clearly labelled. The default All capsule shows separate compact client cards,
not a traffic aggregation; other capsules select by MAC. Individual details and
expand-all/collapse-all apply to all cards (including hidden cards). Selection,
expansion, focused nodes and capsule scroll survive one-second polling. Removing
the selected MAC falls back to All; a returning removed MAC starts collapsed.
Existing global traffic totals and diagnostics are not filtered by selection.
All names and errors use
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
