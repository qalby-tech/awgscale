package tech.qalby.awgscale.tunnel

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Context
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.VpnService
import android.os.Build
import android.util.Log
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.util.Locale
import java.util.concurrent.Executors
import org.json.JSONObject
import app.tauri.plugin.JSObject

/**
 * The phone's tunnel. Android binds it as the VPN; while the tunnel is on it is also a foreground
 * service with a notification, so that it outlives the app's window. The config is parsed, routed
 * and run by libawgscale ([Awg]) the way the computer's service does it; what only Android can do
 * is here: the interface (VpnService.Builder: addresses, DNS, routes, MTU), the server's name
 * resolved before the routes change, and the tunnel's own sockets kept out of it (protect).
 *
 * The kill switch is Android's own («Always-on VPN» and «Block connections without VPN» in the
 * system's settings): an app cannot switch it on; the status says when it was asked for and is off.
 */
class AwgVpnService : VpnService() {
    /** What the app asked for: the config (it holds the private key: never in an Intent) and the options. */
    class Request(val conf: String, val options: String, val killSwitch: Boolean)

    /** Why the tunnel did not come up: a code of [Refusal] and what goes with it. */
    class Failure(val code: String, val detail: String)

    private val worker = Executors.newSingleThreadExecutor()

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_UP -> {
                val (request, done) = synchronized(Companion) {
                    val taken = Pair(pending, pendingDone)
                    pending = null
                    pendingDone = null
                    taken
                }
                goForeground()
                worker.execute {
                    val failure = if (request == null) Failure(Refusal.START, "") else turnOn(request)
                    if (failure != null) {
                        quit()
                    }
                    done?.invoke(failure)
                }
            }
            ACTION_DOWN -> worker.execute { quit() }
            // started by Android itself (always-on VPN) or again after it stopped the process: the config
            // lives only in the app, which is not asked here; nothing to run
            else -> worker.execute { if (handle < 0) quit() }
        }
        return START_NOT_STICKY
    }

    /** Brings the tunnel up; null once the server answered, or why not. */
    private fun turnOn(request: Request): Failure? {
        stopTunnel()
        val plan = JSONObject(Awg.plan(request.conf, request.options))
        if (plan.has("error")) {
            return Failure(Refusal.CONFIG, plan.getString("error"))
        }
        val host = plan.getString("endpoint_host")
        val endpoint = resolve(host, plan.getInt("endpoint_port")) ?: return Failure(Refusal.RESOLVE, host)
        val builder = Builder()
            .setSession(SESSION)
            .setMtu(plan.getInt("mtu"))
        launchIntent()?.let { builder.setConfigureIntent(it) }
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) {
            builder.setMetered(false)
        }
        try {
            forEach(plan, "addresses") { address, bits -> builder.addAddress(address, bits) }
            forEach(plan, "routes") { address, bits -> builder.addRoute(address, bits) }
            val servers = plan.optJSONArray("dns")
            for (index in 0 until (servers?.length() ?: 0)) {
                builder.addDnsServer(servers!!.getString(index))
            }
        } catch (refused: IllegalArgumentException) {
            return Failure(Refusal.CONFIG, refused.message ?: "")
        }
        val established = try {
            builder.establish()
        } catch (refused: Exception) {
            Log.w(TAG, "establish: $refused")
            null
        } ?: return Failure(Refusal.ESTABLISH, "")
        val started = Awg.start(established.detachFd(), request.conf, endpoint)
        if (started < 0) {
            return Failure(Refusal.START, Awg.error())
        }
        synchronized(Companion) {
            handle = started
            killSwitch = request.killSwitch
            running = this
        }
        for (family in intArrayOf(4, 6)) {
            val socket = Awg.socket(started, family)
            if (socket >= 0 && !protect(socket)) {
                Log.w(TAG, "the tunnel's socket of IPv$family is not protected")
            }
        }
        val deadline = System.currentTimeMillis() + HANDSHAKE_TIMEOUT_MS
        while (System.currentTimeMillis() < deadline) {
            if (JSONObject(Awg.status(started)).optLong("handshake") != 0L) {
                return null
            }
            Thread.sleep(HANDSHAKE_POLL_MS)
        }
        stopTunnel()
        return Failure(Refusal.HANDSHAKE, "")
    }

    /** The server's address, through the system's DNS, before the interface takes the routes; IPv4 first. */
    private fun resolve(host: String, port: Int): String? {
        val addresses = try {
            InetAddress.getAllByName(host).toList()
        } catch (failed: Exception) {
            return null
        }
        val chosen = addresses.firstOrNull { it is Inet4Address } ?: addresses.firstOrNull() ?: return null
        val text = chosen.hostAddress ?: return null
        return if (chosen is Inet6Address) "[${text.substringBefore('%')}]:$port" else "$text:$port"
    }

    private fun forEach(plan: JSONObject, key: String, add: (String, Int) -> Unit) {
        val prefixes = plan.optJSONArray(key) ?: return
        for (index in 0 until prefixes.length()) {
            val prefix = prefixes.getString(index)
            add(prefix.substringBefore('/'), prefix.substringAfter('/').toInt())
        }
    }

    private fun stopTunnel() {
        val stopped = synchronized(Companion) {
            val was = handle
            handle = -1
            was
        }
        if (stopped >= 0) {
            Awg.stop(stopped)
        }
    }

    private fun quit() {
        stopTunnel()
        synchronized(Companion) {
            if (running === this) running = null
        }
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    /** Another VPN took over, or the person switched this one off in the system's settings. */
    override fun onRevoke() {
        worker.execute { quit() }
    }

    override fun onDestroy() {
        stopTunnel()
        synchronized(Companion) {
            if (running === this) running = null
        }
        worker.shutdown()
        super.onDestroy()
    }

    private fun goForeground() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, said("VPN", "VPN"), NotificationManager.IMPORTANCE_LOW))
        val down = PendingIntent.getService(
            this, 1, Intent(this, AwgVpnService::class.java).setAction(ACTION_DOWN), PendingIntent.FLAG_IMMUTABLE,
        )
        val notification = Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .setContentTitle("awgscale")
            .setContentText(said("VPN включён", "VPN is on"))
            .setOngoing(true)
            .setContentIntent(launchIntent())
            .addAction(Notification.Action.Builder(null, said("Отключить", "Disconnect"), down).build())
            .build()
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION, notification, ServiceInfo.FOREGROUND_SERVICE_TYPE_SYSTEM_EXEMPTED)
        } else {
            startForeground(NOTIFICATION, notification)
        }
    }

    private fun launchIntent(): PendingIntent? {
        val launch = packageManager.getLaunchIntentForPackage(packageName) ?: return null
        return PendingIntent.getActivity(this, 0, launch, PendingIntent.FLAG_IMMUTABLE)
    }

    /** The phone's language: Russian when it is Russian, English otherwise. */
    private fun said(russian: String, english: String): String =
        if (Locale.getDefault().language == "ru") russian else english

    /** Android 10+ tells whether its own kill switch is on; earlier ones do not, so the warning stays. */
    private fun lockdownMissing(): Boolean =
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.Q) !isLockdownEnabled else true

    companion object {
        private const val TAG = "awgscale"
        private const val SESSION = "awgscale"
        private const val CHANNEL = "tunnel"
        private const val NOTIFICATION = 1
        private const val ACTION_UP = "tech.qalby.awgscale.tunnel.UP"
        private const val ACTION_DOWN = "tech.qalby.awgscale.tunnel.DOWN"
        /** As the computer's service waits for the server's first answer (handshakeTimeout in app/qalby/tunnel). */
        private const val HANDSHAKE_TIMEOUT_MS = 20_000L
        private const val HANDSHAKE_POLL_MS = 200L

        private var pending: Request? = null
        private var pendingDone: ((Failure?) -> Unit)? = null
        private var running: AwgVpnService? = null
        private var handle = -1
        private var killSwitch = false

        /** Brings the tunnel up in the service; [done] gets null once the server answered, or why not. */
        fun start(context: Context, request: Request, done: (Failure?) -> Unit) {
            synchronized(Companion) {
                pendingDone?.invoke(Failure(Refusal.START, "replaced"))
                pending = request
                pendingDone = done
            }
            context.startForegroundService(Intent(context, AwgVpnService::class.java).setAction(ACTION_UP))
        }

        fun stop() {
            val service = synchronized(Companion) { running }
            service?.quit() // no service running: no tunnel either
        }

        /** The tunnel's state for the app (Report in app/android/src/lib.rs). */
        fun report(): JSObject {
            val (current, service, asked) = synchronized(Companion) { Triple(handle, running, killSwitch) }
            val answer = JSObject()
            if (current < 0 || service == null) {
                answer.put("state", "down")
                return answer
            }
            answer.put("state", JSONObject(Awg.status(current)).optString("state", "down"))
            answer.put("lockdown_missing", asked && service.lockdownMissing())
            return answer
        }
    }
}
