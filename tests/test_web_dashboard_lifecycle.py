"""Regression checks against the patched v3.0.8-p4 source tree.

Set VPNHOTSPOT_SOURCE to a checkout of ae6f39ed82153b5edf6c797bc1a27e17ab5ee845
with patches/0005-web-admin.patch applied, then run:
    python -m unittest discover -s tests -p 'test_web_dashboard_lifecycle.py'

These checks detect missing wiring only; they do not prove the listener, snapshot,
interface binding, concurrency, or HTTP security are correct.
"""
import os
from pathlib import Path
import unittest


class DashboardWiringTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        root = os.environ.get("VPNHOTSPOT_SOURCE")
        if not root:
            raise unittest.SkipTest("set VPNHOTSPOT_SOURCE to the patched release source")
        cls.routing = (Path(root) / "mobile/src/main/java/be/mygod/vpnhotspot/RoutingManager.kt").read_text(
            encoding="utf-8"
        )
        cls.web = (Path(root) / "mobile/src/main/java/be/mygod/vpnhotspot/WebAdmin.kt").read_text(
            encoding="utf-8"
        )
        cls.core = (Path(root) / "mobile/src/main/java/be/mygod/vpnhotspot/DashboardCore.kt").read_text(
            encoding="utf-8"
        )
        cls.source_patch = (Path(__file__).parents[1] / "patches/0005-web-admin.patch").read_text(
            encoding="utf-8"
        )

    def test_manager_starts_web_listener(self):
        self.assertRegex(self.routing, r"WebAdmin\.(?:addInterface|start)\(downstream\)",
                         "RoutingManager never starts the dashboard")

    def test_dashboard_listener_is_bounded_and_read_only(self):
        self.assertIn("ServerSocket(PORT, 16, address)", self.web)
        self.assertIn("MAX_HEADERS = 64", self.core)
        self.assertIn("sockets.size >= 16", self.core)
        self.assertIn('request.method != "GET"', self.web)
        self.assertNotRegex(self.web, r"(?i)\\b(?:POST|PUT|PATCH|DELETE)\\b")

    def test_dashboard_closes_listener_on_stop(self):
        self.assertIn("session.resources.stop()", self.web)
        self.assertIn("WebAdmin.stop(downstream)", self.routing)
        stop = self.routing[self.routing.index("suspend fun stop()") :]
        self.assertLess(stop.index("WebAdmin.stop(downstream)"), stop.index("} ?: return"))
        self.assertIn("WebAdmin.stop(manager.downstream)", self.routing)

    def test_dashboard_csp_allows_same_origin_status_fetch(self):
        self.assertIn("connect-src 'self'", self.web)

    def test_dashboard_uses_bounded_deadline_reader(self):
        self.assertIn("REQUEST_DEADLINE_MS", self.web)
        self.assertIn("DashboardHttp.read(socket", self.web)
        self.assertIn("DashboardDeadline.arm(socket", self.web)
        self.assertIn("System.nanoTime()", self.core)

    def test_dashboard_isolates_connection_io_failures(self):
        self.assertIn("catch (_: IOException)", self.web)
        self.assertIn("Dashboard unavailable on", self.web)

    def test_dashboard_cleanup_is_generation_and_identity_bound(self):
        self.assertIn("sessions[iface] === session", self.web)
        self.assertIn("session.resources.publish(candidate)", self.web)
        self.assertIn("session.resources.attach(socket)", self.web)
        self.assertIn("connectionScope.launch", self.web)

    def test_dashboard_sampler_does_not_poll_or_persist_on_request(self):
        status = self.web[self.web.index("private fun status("):self.web.index("private fun upstreamJson")]
        self.assertNotIn("readTrafficCounters", status)
        self.assertNotIn("trafficRecordDao", self.web)
        self.assertNotIn("DashboardSampler", self.web)
        self.assertIn("session.trafficSnapshots.peek()", status)
        self.assertNotIn(".read {", status)
        self.assertNotIn(".sample(", status)
        self.assertIn("private fun status(", status,
                      "HTTP status must read published state without suspending behind a sampler")
        self.assertNotIn("TrafficRecorder.dashboardSnapshot()", self.web)
        self.assertNotIn("DaemonController", self.web)
        self.assertIn("TrafficRecorder.counterSnapshots.collect", self.web)
        self.assertIn("observationJob.start()", self.web)

    def test_recorder_is_the_only_destructive_counter_consumer(self):
        root = Path(os.environ['VPNHOTSPOT_SOURCE']) / 'mobile/src/main/java'
        consumers = [str(p.relative_to(root)).replace('\\', '/') for p in root.rglob('*.kt')
                     if 'DaemonController.readTrafficCounters()' in p.read_text(encoding='utf-8')]
        self.assertEqual(consumers, ['be/mygod/vpnhotspot/net/monitor/TrafficRecorder.kt'],
                         'DNS/NAT66 retired counters are consumed once: Web must only observe recorder publications')
        self.assertNotIn('TrafficRecorder.foregroundUpdates', self.web,
                         'Web observations must not accelerate persistence to one row per source per second')
        self.assertNotIn('TrafficRecorder.update(', self.web)
        self.assertNotIn('TrafficRecorder.updateLocked(', self.web)
        recorder = (root / consumers[0]).read_text(encoding='utf-8')
        scheduled = recorder[recorder.index('private fun scheduleUpdateLocked()'):recorder.index('suspend fun update()')]
        self.assertIn('val minute = TimeUnit.MINUTES.toMillis(1)', scheduled)
        self.assertIn('foregroundUpdatesState.subscriptionCount.value > 0', scheduled)
        self.assertNotIn('counterSnapshots', scheduled)
        self.assertIn('FOREGROUND_POLL_MS = 1015L', recorder)
        self.assertIn('val counterSnapshots = counterSnapshotsState.asStateFlow()', recorder)
        publication = recorder.index('CounterSnapshot(counters.toList(), observedAt, timestamp, true)')
        self.assertLess(recorder.rindex('trafficRecordDao.insert(record)'), publication,
                        'retired counters must reach the original persistence pass before Web publication')
        self.assertNotIn('emit(', recorder[recorder.index('val counters = DaemonController'):publication],
                         'publication must not suspend accounting behind a Web collector')

    def test_dashboard_retains_event_driven_neighbours(self):
        self.assertIn("Upstreams.vpn.collect", self.web)
        status = self.web[self.web.index("private fun status("):self.web.index("private fun upstreamJson")]
        self.assertNotIn("<= 3000", status, "stable neighbours must not expire without a removal or monitor invalidation")
        self.assertIn("session.neighbours.peek()", status)
        self.assertIn("NetlinkNeighbour.monitorObservations.collect", self.web)
        self.assertIn("snapshot.available", self.web)
        self.assertIn("session.neighbours.invalidate()", self.web)
        self.assertIn("session.neighbours.update(snapshot", self.web)

    def test_client_identity_uses_existing_sources_off_http(self):
        self.assertIn("Client(MacAddress.fromString(mac), iface, type)", self.web)
        self.assertIn("lookupOrDefaultFlow(mac)", self.web)
        self.assertIn("snapshot.bridgeMasterByMember", self.web)
        self.assertIn("WebAdmin.observeP2pGroup(value)", (Path(os.environ['VPNHOTSPOT_SOURCE']) /
            'mobile/src/main/java/be/mygod/vpnhotspot/RepeaterService.kt').read_text())
        status = self.web[self.web.index("private fun status("):self.web.index("private fun upstreamJson")]
        self.assertNotIn("AppDatabase", status)
        self.assertNotIn("Services.connectivity", status)

    def test_monitor_has_explicit_lifecycle_invalidation(self):
        monitor = (Path(os.environ['VPNHOTSPOT_SOURCE']) /
            'mobile/src/main/java/be/mygod/vpnhotspot/net/NetlinkNeighbour.kt').read_text()
        self.assertIn(".onStart { emit(Snapshot(available = false)) }", monitor)
        self.assertIn(".onCompletion", monitor)
        self.assertIn("unavailableReason = e.toString()", monitor)
        self.assertIn("val monitorSnapshots = monitorObservations.filter { it.available }", monitor,
                      "observation invalidation must not remove clients from existing routing subscribers")


