package tech.qalby.awgscale.tunnel

import android.util.Log
import java.io.ByteArrayOutputStream
import java.net.InetSocketAddress
import java.net.Socket
import java.net.URI
import java.util.concurrent.Executors
import java.util.concurrent.ScheduledFuture
import java.util.concurrent.TimeUnit
import org.json.JSONObject

/**
 * The server the person keeps (the one they chose, or their group's), watched through the tunnel while it is
 * up: the network's app API is asked about once a minute, and [say] is called when that server stops working
 * (the network moves the person to a working one meanwhile) and when it works again. Only the changes are
 * said, and the first look only when the server is down already; nothing when nothing keeps the person on one.
 *
 * The app API is plain HTTP at the network's entry inside the tunnel (`http://172.16.17.1:8088/app/api/`),
 * asked over a socket of its own: Android's cleartext rule (the app's own HTTP is the Rust side's) is for its
 * HTTP clients, and this asks two short questions and nothing else. Runs on a thread of its own, so that the
 * tunnel's worker never waits for the network.
 */
class ExitWatch(private val say: (ServerChange, String) -> Unit) {
    /** What is said about the server the person keeps. */
    enum class ServerChange { DOWN, BACK }

    private val thread = Executors.newSingleThreadScheduledExecutor()
    private var looking: ScheduledFuture<*>? = null
    /** The server looked at last and whether it was down then; null before the first look. */
    private var kept: Int? = null
    private var keptDown: Boolean? = null

    /** Where the network watched now has its app API; null while nothing is watched. */
    private var watched: String? = null

    /**
     * Watching the network whose app API is at [base] (`http://…:8088/app/api/`) from now on; the same network
     * again (the tunnel made anew) goes on as it was, so that what was said is not said twice.
     */
    @Synchronized
    fun start(base: String) {
        if (watched == base) return
        stop()
        watched = base
        looking = thread.scheduleWithFixedDelay({ look(base) }, FIRST_LOOK_S, LOOK_EVERY_S, TimeUnit.SECONDS)
    }

    @Synchronized
    fun stop() {
        looking?.cancel(false)
        looking = null
        watched = null
        thread.execute {
            kept = null
            keptDown = null
        }
    }

    fun close() {
        stop()
        thread.shutdown()
    }

    private fun look(base: String) {
        try {
            val me = JSONObject(get(base + "me"))
            val choice = me.optJSONObject("exit") ?: return forget()
            val index = when {
                !choice.isNull("pinned") -> choice.getInt("pinned")
                !choice.isNull("group") -> choice.getInt("group")
                else -> return forget()
            }
            val exits = JSONObject(get(base + "exits")).optJSONArray("exits") ?: return
            var server: JSONObject? = null
            for (position in 0 until exits.length()) {
                val exit = exits.getJSONObject(position)
                if (exit.optInt("index", -1) == index) server = exit
            }
            if (server == null) return forget()
            val down = !server.optBoolean("up", true)
            val shown = server.optString("address").ifEmpty { server.optString("name") }
            val before = if (kept == index) keptDown else null
            kept = index
            keptDown = down
            when {
                before == null && down -> say(ServerChange.DOWN, shown)
                before == false && down -> say(ServerChange.DOWN, shown)
                before == true && !down -> say(ServerChange.BACK, shown)
            }
        } catch (failed: Exception) {
            // the network did not answer (the tunnel is reconnecting, an older server): the next look asks again;
            // never the exception's text
            Log.i(TAG, "exit watch: no answer")
        }
    }

    private fun forget() {
        kept = null
        keptDown = null
    }

    /** The body of a GET of [address] (http only), or an exception when the answer is not 200. */
    private fun get(address: String): String {
        val uri = URI(address)
        val port = if (uri.port > 0) uri.port else HTTP_PORT
        Socket().use { socket ->
            socket.connect(InetSocketAddress(uri.host, port), TIMEOUT_MS)
            socket.soTimeout = TIMEOUT_MS
            // HTTP/1.0: the answer is the body up to the end of the connection, never in chunks
            val request = "GET ${uri.rawPath} HTTP/1.0\r\nHost: ${uri.host}:$port\r\nAccept: application/json\r\n\r\n"
            socket.getOutputStream().write(request.toByteArray(Charsets.US_ASCII))
            val answer = ByteArrayOutputStream()
            val input = socket.getInputStream()
            val buffer = ByteArray(BUFFER_BYTES)
            while (true) {
                val read = input.read(buffer)
                if (read < 0) break
                answer.write(buffer, 0, read)
                if (answer.size() > LONGEST_ANSWER_BYTES) throw IllegalStateException("too long")
            }
            val text = answer.toString(Charsets.UTF_8.name())
            val head = text.substringBefore("\r\n")
            if (head.split(' ').getOrNull(1) != "200") throw IllegalStateException("not 200")
            return text.substringAfter("\r\n\r\n")
        }
    }

    companion object {
        private const val TAG = "awgscale"
        private const val FIRST_LOOK_S = 5L
        private const val LOOK_EVERY_S = 60L
        private const val TIMEOUT_MS = 5_000
        private const val HTTP_PORT = 80
        private const val BUFFER_BYTES = 8192
        private const val LONGEST_ANSWER_BYTES = 1_000_000
    }
}
