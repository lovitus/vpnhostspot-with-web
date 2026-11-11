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
    if (mode == "all" || mode == "traffic") {
        val samples = DashboardTraffic()
        val a = DashboardTraffic.Counter("a", "mac", 100, 200)
        val b = DashboardTraffic.Counter("b", "mac", 50, 80)
        val first = samples.sample(listOf(a, b), 0, true)
        check(first.totals.getValue("mac").sent == 0L)
        check(first.rates["mac"] == null)
        val next = samples.sample(listOf(a.copy(sent = 200, received = 400), b), 1000, true)
        check(next.rates.getValue("mac").sent == 100L)
        val failed = samples.sample(emptyList(), 2000, false)
        check(!failed.available && failed.totals == next.totals && failed.rates.isEmpty())
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
    }
}
