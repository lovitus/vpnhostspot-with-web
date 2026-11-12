package be.mygod.vpnhotspot

import java.net.InetAddress
import java.net.ServerSocket
import java.net.Socket
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import kotlin.concurrent.thread
import kotlinx.coroutines.*

private fun request(raw: String): DashboardHttp.Request? = ServerSocket(0, 1, InetAddress.getLoopbackAddress()).use { server ->
    Socket(server.inetAddress, server.localPort).use { client ->
        client.getOutputStream().write(raw.toByteArray())
        client.shutdownOutput()
        server.accept().use { DashboardHttp.read(it, System.nanoTime() + 1_000_000_000) }
    }
}

fun main() = runBlocking {
    System.getProperty("dashboard.page.path")?.takeIf { it.isNotEmpty() }?.let { java.io.File(it).writeText(DASHBOARD_PAGE) }
    val mode = System.getProperty("test", "all")
    if (mode == "all" || mode == "http") {
        check(request("GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n")?.path == "/api/status")
        check(request("GET / HTTP/1.1\r\nHost: evil.example\r\n\r\n") == null)
        check(request("GET / HTTP/1.1\r\nHost: vpnhotspot.lan\r\n\r\n") != null)
        check(request("GET / HTTP/1.1\r\nHost: vpnhotspot.lan.evil\r\n\r\n") == null)
        check(request("GET / HTTP/1.1\r\nHost: 127.0.0.2\r\n\r\n") == null)
        check(request("GET / HTTP/1.1\r\nHost: 127.0.0.1\r\nHost: vpnhotspot.lan\r\n\r\n") == null)
        check(request("GET / HTTP/1.1\r\n\r\n") == null)
        check(request("GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n") == null)
        check(request("GET /${"a".repeat(4096)} HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n") == null)
        check(request("GET / HTTP/1.1\r\nHost: 127.0.0.1\r\n" + "X: a\r\n".repeat(65) + "\r\n") == null)
        check(request("GET http://evil.example/ HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n") == null)
        check(request("GET / HTTP/1.1\r\nHost: vpnhotspot.lan:8080\r\n\r\n") != null)
        for (port in listOf("80", "8081", "garbage", "8080:80", "08080", "")) {
            check(request("GET / HTTP/1.1\r\nHost: vpnhotspot.lan:$port\r\n\r\n") == null) { "unexpected Host port accepted: $port" }
        }
        println("PASS bounded HTTP parser and exact Host validation")
    }
    if (mode == "all" || mode == "lifecycle") {
        repeat(100) {
            val old = DashboardSession()
            val listener = ServerSocket(0)
            check(old.publish(listener))
            val client = Socket("127.0.0.1", listener.localPort)
            val accepted = listener.accept()
            check(old.attach(accepted))
            old.stop()
            check(listener.isClosed && accepted.isClosed)
            val replacement = DashboardSession()
            ServerSocket(0).use { fresh ->
                check(replacement.publish(fresh))
                old.stop() // delayed old generation finally
                check(!fresh.isClosed)
            }
            ServerSocket(0).use { late -> check(!old.publish(late)); check(late.isClosed) }
            client.close()
        }
        repeat(100) {
            val session = DashboardSession()
            val listener = ServerSocket(0)
            val gate = CountDownLatch(1)
            val publisher = thread { gate.await(); session.publish(listener) }
            gate.countDown(); session.stop(); publisher.join()
            check(listener.isClosed)
        }
        val session = DashboardSession()
        val sockets = List(17) { Socket() }
        sockets.take(16).forEach { check(session.attach(it)) }
        check(!session.attach(sockets.last()) && sockets.last().isClosed)
        session.stop(); check(sockets.all { it.isClosed })
        println("PASS stop/restart identity, atomic publication, accepted socket cleanup and cap")
    }
    if (mode == "all" || mode == "deadline") {
        ServerSocket(0).use { server ->
            Socket("127.0.0.1", server.localPort).use { client ->
                server.accept().use { socket ->
                    val timer = DashboardDeadline.arm(socket, 150)
                    val started = System.nanoTime()
                    try { DashboardHttp.read(socket, started + 150_000_000) } catch (_: java.io.IOException) {}
                    check(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started) < 1500)
                    timer.cancel(false)
                }
            }
        }
        ServerSocket(0).use { server ->
            Socket("127.0.0.1", server.localPort).use { client ->
                client.receiveBufferSize = 1024
                server.accept().use { socket ->
                    socket.sendBufferSize = 1024
                    val timer = DashboardDeadline.arm(socket, 200)
                    val started = System.nanoTime()
                    var closed = false
                    try { val bytes = ByteArray(65536); while (true) socket.getOutputStream().write(bytes) }
                    catch (_: java.io.IOException) { closed = true }
                    finally { timer.cancel(false) }
                    check(closed && socket.isClosed)
                    check(TimeUnit.NANOSECONDS.toMillis(System.nanoTime() - started) < 1500)
                }
            }
        }
        println("PASS absolute deadline closes stalled reads and writes")
    }
    if (mode == "all" || mode == "cache") {
        var now = 0L
        val cache = DashboardCache<String> { now }
        val loads = AtomicInteger()
        suspend fun read() = cache.read { delay(2); "snapshot-${loads.incrementAndGet()}" }
        check((1..64).map { async(Dispatchers.Default) { read() } }.awaitAll().toSet() == setOf("snapshot-1"))
        now = 999; check(read() == "snapshot-1"); check(loads.get() == 1)
        now = 1000
        check((1..64).map { async(Dispatchers.Default) { read() } }.awaitAll().toSet() == setOf("snapshot-2"))
        check(loads.get() == 2)
        now = 2000
        val cancelled = launch { cache.read { delay(10_000); "never" } }
        delay(20); cancelled.cancelAndJoin()
        check(cache.read { "snapshot-after-cancel" } == "snapshot-after-cancel")
        println("PASS concurrent readers share an immutable snapshot and cancellation releases the cache")
    }
    if (mode == "all" || mode == "sampling") {
        val owner = CoroutineScope(Dispatchers.Default + SupervisorJob())
        val entered = CompletableDeferred<Unit>()
        val cancelling = CompletableDeferred<Unit>()
        val release = CompletableDeferred<Unit>()
        val calls = AtomicInteger()
        var now = 0L
        val cache = DashboardCache<String> { now }
        val sampler = DashboardSampler(owner, 200) {
            when (calls.incrementAndGet()) {
                1 -> "initial"
                2 -> try {
                    entered.complete(Unit)
                    awaitCancellation()
                } finally {
                    cancelling.complete(Unit)
                    // Model DaemonController's potentially stalled NonCancellable cleanup.
                    withContext(NonCancellable) { release.await() }
                }
                else -> "recovered"
            }
        }
        try {
            check(cache.peek() == null)
            check(cache.read { sampler.read()!! } == "initial")
            now = 1000
            val cycle = async { cache.read { sampler.read() ?: "unavailable" } }
            withTimeout(2000) { entered.await() }
            // Both direct status readers and real HTTP exchanges use the same production peek
            // as WebAdmin.status. The loader mutex is held throughout the stalled cycle.
            withTimeout(2000) {
                check((1..64).map { async(Dispatchers.Default) { cache.peek() } }
                    .awaitAll().all { it in setOf("initial", "unavailable") })
                ServerSocket(0, 64, InetAddress.getLoopbackAddress()).use { server ->
                    val serving = async(Dispatchers.IO) {
                        coroutineScope {
                            repeat(32) {
                                val socket = server.accept()
                                launch(Dispatchers.IO) {
                                    socket.use {
                                        check(DashboardHttp.read(it, System.nanoTime() + 1_000_000_000)?.path == "/api/status")
                                        val body = cache.peek()!!
                                        it.getOutputStream().write(("HTTP/1.1 200 OK\r\nContent-Length: ${body.length}\r\n\r\n" + body).toByteArray())
                                    }
                                }
                            }
                        }
                    }
                    val responses = (1..32).map {
                        async(Dispatchers.IO) {
                            Socket(server.inetAddress, server.localPort).use { client ->
                                client.soTimeout = 1500
                                client.getOutputStream().write("GET /api/status HTTP/1.1\r\nHost: 127.0.0.1\r\n\r\n".toByteArray())
                                client.getInputStream().readBytes().toString(Charsets.US_ASCII)
                            }
                        }
                    }.awaitAll()
                    check(responses.all { it.startsWith("HTTP/1.1 200 OK") &&
                        it.substringAfter("\r\n\r\n") in setOf("initial", "unavailable") })
                    serving.await()
                }
            }
            withTimeout(2000) { cancelling.await(); check(cycle.await() == "unavailable") }
            repeat(20) { check(sampler.read() == null) }
            check(calls.get() == 2) // no extra reads while cancellation cleanup is quarantined
            check(!release.isCompleted) // HTTP/status finished before releasing the stalled read
            release.complete(Unit)
            withTimeout(2000) { owner.coroutineContext[Job]!!.children.toList().forEach { it.join() } }
            now = 2000
            check(cache.read { sampler.read()!! } == "recovered")
            check(cache.peek() == "recovered" && calls.get() == 3)
            println("PASS cached status and 32 HTTP exchanges during stalled sampling; bounded timeout, quarantine and recovery")
        } finally {
            release.complete(Unit)
            owner.cancel()
        }
        val fresh = DashboardTraffic.Sample(mapOf("mac" to DashboardTraffic.Totals(123, 456)),
            mapOf("mac" to DashboardTraffic.Totals(10, 20)), true)
        val snapshot = DashboardTrafficSnapshot(fresh, 1000, 9999)
        check(snapshot.current(4000) == fresh)
        val stale = snapshot.current(4001)
        check(!stale.available && stale.rates.isEmpty() && stale.totals == fresh.totals)
        check(snapshot.current(1000) == fresh) // reading does not mutate the published sample
        println("PASS stale traffic suppresses rates and retains immutable totals")
    }
    if (mode == "all" || mode == "neighbours") {
        val observed = DashboardObservation<List<Pair<String?, String>>>()
        check(observed.peek().value == null && !observed.peek().available)
        observed.update(listOf("mac" to "192.168.49.157", "mac" to "fe80::157"), 1000)
        delay(3100) // real silent interval beyond the former 3-second expiry
        val stable = observed.peek()
        check(stable.available && dashboardClients(stable.value!!).size == 1)
        check(stable.changedAt == 1000L)
        observed.update(emptyList(), 5000) // explicit deletion, not a timer
        check(observed.peek().available && observed.peek().value!!.isEmpty())
        observed.update(listOf("mac" to "192.168.49.157"), 6000)
        observed.invalidate()
        check(!observed.peek().available && observed.peek().value!!.isNotEmpty()) // clearly stale last-known state
        val restarted = DashboardObservation<List<Pair<String?, String>>>()
        check(restarted.peek().value == null && !restarted.peek().available)
        restarted.update(emptyList(), 7000)
        check(restarted.peek().available && restarted.peek().value!!.isEmpty())
        check(!observed.peek().available) // old generation cannot populate its replacement
        println("PASS silent neighbours survive >3 seconds; deletion, invalidation and restart")
    }
    if (mode == "all" || mode == "traffic") {
        val samples = DashboardTraffic()
        val a = DashboardTraffic.Counter("a", "mac", 100, 200)
        val b = DashboardTraffic.Counter("b", "mac", 50, 80)
        val first = samples.sample(listOf(a, b), 0, true)
        check(first.totals.getValue("mac").sent == 0L)
        check(first.rates["mac"] == null)
        val next = samples.sample(listOf(a.copy(sent = 200, received = 400), b), 1000, true)
        check(next.rates.getValue("mac").sent == 100L)
        check(next.lastChange["mac"] == 1000L)
        check(dashboardTrafficSummary(next)["sendBytesPerSecond"] == 100L)
        check(dashboardTrafficSummary(first)["sendBytesPerSecond"] == null)
        val failed = samples.sample(emptyList(), 2000, false)
        check(!failed.available && failed.totals == next.totals && failed.rates.isEmpty())
        check(failed.lastChange == next.lastChange)
        check(dashboardTrafficSummary(failed)["sendBytes"] == 100L)
        check(dashboardTrafficSummary(failed)["sendBytesPerSecond"] == null)
        val recovered = samples.sample(listOf(b.copy(sent = 180, received = 300)), 3000, true)
        check(recovered.totals.getValue("mac").sent == 230L) // a source disappearance preserves totals
        check(recovered.rates.getValue("mac").sent == 65L)
        val reset = samples.sample(listOf(a.copy(sent = 10, received = 20), b.copy(sent = 180, received = 300)), 4000, true)
        check(reset.totals.getValue("mac").sent == 230L) // recreated/reset sources are not counted as deltas
        check(reset.rates["mac"] == null) // rate is unavailable across a source generation change
        println("PASS traffic failure preservation, rates, source churn and counter resets")
        val clients = dashboardClients(listOf("mac" to "192.168.1.2", "mac" to "fe80::2",
            "mac" to "192.168.1.2", null to "192.168.1.3", "other" to "192.168.1.4"))
        check(clients.size == 2 && clients.getValue("mac") == listOf("192.168.1.2", "fe80::2"))
        check(dashboardClients(emptyList()).isEmpty())
        println("PASS one observed MAC with distinct IPv4/IPv6 addresses")
        check(dashboardJson(mapOf("name" to "设备\n\"<script>\\", "unknown" to null, "list" to listOf(1, true))) ==
            "{\"name\":\"设备\\u000a\\\"<script>\\\\\",\"unknown\":null,\"list\":[1,true]}")
        check(dashboardTrafficSummary(DashboardTraffic.Sample(emptyMap(), emptyMap(), true)).values.all { it == null })
        println("PASS production JSON escaping, unknown fields and aggregate availability")
    }
}
