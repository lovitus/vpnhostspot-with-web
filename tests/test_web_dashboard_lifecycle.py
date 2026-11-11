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

    def test_manager_starts_web_listener(self):
        self.assertRegex(self.routing, r"WebAdmin\.(?:addInterface|start)\(downstream\)",
                         "RoutingManager never starts the dashboard")

    def test_dashboard_listener_is_bounded_and_read_only(self):
        self.assertIn("ServerSocket(PORT, 16, address)", self.web)
        self.assertIn("MAX_HEADERS = 64", self.web)
        self.assertIn("MAX_CLIENTS = 16", self.web)
        self.assertIn('parts[0] != "GET"', self.web)
        self.assertNotRegex(self.web, r"(?i)\\b(?:POST|PUT|PATCH|DELETE)\\b")

    def test_dashboard_closes_listener_on_stop(self):
        self.assertIn("listeners.remove(iface)?.close()", self.web)
        self.assertIn("WebAdmin.stop(downstream)", self.routing)

    def test_dashboard_csp_allows_same_origin_status_fetch(self):
        self.assertIn("connect-src 'self'", self.web)

    def test_dashboard_uses_bounded_deadline_reader(self):
        self.assertIn("REQUEST_DEADLINE_MS", self.web)
        self.assertIn("readLine(MAX_LINE", self.web)
        self.assertNotIn("reader.readLine()", self.web)
        self.assertIn("System.nanoTime()", self.web)

    def test_dashboard_isolates_connection_io_failures(self):
        self.assertIn("catch (_: IOException)", self.web)
        self.assertIn("catch (_: SocketException)", self.web)

    def test_dashboard_cleanup_is_generation_and_identity_bound(self):
        self.assertIn("generation", self.web)
        self.assertIn("listeners.remove(iface, it)", self.web)
        self.assertIn("jobs.remove(iface, current)", self.web)

    def test_dashboard_status_refresh_is_cached_and_serialized(self):
        self.assertIn("Mutex()", self.web)
        self.assertIn("SNAPSHOT_MIN_INTERVAL_MS", self.web)
        self.assertIn("snapshotMutex.withLock", self.web)
        self.assertIn("TrafficRecorder.update()", self.web)


