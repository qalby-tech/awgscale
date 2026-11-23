package tech.qalby.awgscale.tunnel

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import android.os.Handler
import android.os.IBinder
import android.os.Looper
import android.os.Message
import android.os.Messenger
import android.util.Log
import java.net.Inet4Address
import java.net.Inet6Address
import java.net.InetAddress
import java.util.Locale
import java.util.concurrent.CountDownLatch
import java.util.concurrent.Executors
import java.util.concurrent.TimeUnit
import org.json.JSONObject

/**
 * The phone's tunnel, in a process of its own (":tunnel"): the app's process goes when its window
 * goes (Tauri ends it), this one stays as long as the tunnel is on, a foreground service with a
 * notification. Android binds it as the VPN; the app binds it too and talks to it by messages
 * ([TunnelClient]): the config goes from process to process by Binder, never in an Intent.
 *
 * The config is parsed, routed and run by libawgscale ([Awg]) the way the computer's service does
 * it; what only Android can do is here: the interface (VpnService.Builder: addresses, DNS, routes,
 * MTU), the server's name resolved before the routes change, and the tunnel's own sockets kept out
 * of it (protect).
 *
 * The kill switch is Android's own («Always-on VPN» and «Block connections without VPN» in the
 * system's settings): an app cannot switch it on; the status says when it was asked for and is off.
 */
class AwgVpnService : VpnService() {
    /** Why the tunnel did not come up: a code of [Refusal] and what goes with it. */
    private class Failure(val code: String, val detail: String)

    private val worker = Executors.newSingleThreadExecutor()
    private var handle = -1
    private var killSwitch = false
    /** Opened once the service is in the foreground (ACTION_UP, from the app's startForegroundService). */
    @Volatile private var foreground = CountDownLatch(1)

    private val messenger = Messenger(Handler(Looper.getMainLooper()) { message -> received(message); true })

    override fun onBind(intent: Intent?): IBinder? =
        if (intent?.action == SERVICE_INTERFACE) super.onBind(intent) else messenger.binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_UP -> {
                goForeground()
                foreground.countDown()
            }
            ACTION_DOWN -> worker.execute { quit() }
            // started by Android itself (always-on VPN): the config lives only in the app, which is not
            // asked here; nothing to run
            else -> worker.execute { if (handle < 0) quit() }
        }
        return START_NOT_STICKY
    }

    /** A message of the app: UP (conf, options, kill switch), DOWN, STATUS; the answer goes to replyTo. */
    private fun received(message: Message) {
        val replyTo = message.replyTo
        val data = Bundle(message.data)
        val what = message.what
        worker.execute {
            val answer = when (what) {
                TunnelClient.UP -> {
                    val failure = turnOn(
                        data.getString(TunnelClient.CONF) ?: "",
                        data.getString(TunnelClient.OPTIONS) ?: "{}",
                        data.getBoolean(TunnelClient.KILL_SWITCH),
                    )
                    if (failure != null) quit()
                    report().apply {
                        failure?.let {
                            putString(TunnelClient.FAILURE, it.code)
                            putString(TunnelClient.DETAIL, it.detail)
                        }
                    }
                }
                TunnelClient.DOWN -> {
                    quit()
                    report()
                }
                else -> report()
            }
            try {
                replyTo?.send(Message.obtain(null, what).apply { this.data = answer })
            } catch (gone: Exception) {
                Log.w(TAG, "the app is gone: $gone")
            }
        }
    }

    /** Brings the tunnel up; null once the server answered, or why not. */
    private fun turnOn(conf: String, options: String, askedKillSwitch: Boolean): Failure? {
        if (!foreground.await(FOREGROUND_WAIT_S, TimeUnit.SECONDS)) {
            return Failure(Refusal.START, "the service did not start")
        }
        stopTunnel()
        val plan = JSONObject(Awg.plan(conf, options))
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
        val started = Awg.start(established.detachFd(), conf, endpoint)
        if (started < 0) {
            return Failure(Refusal.START, Awg.error())
        }
        handle = started
        killSwitch = askedKillSwitch
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

    /** The tunnel's state for the app (Report in app/android/src/lib.rs). Called on the worker. */
    private fun report(): Bundle {
        val answer = Bundle()
        if (handle < 0) {
            answer.putString(TunnelClient.STATE, "down")
            return answer
        }
        answer.putString(TunnelClient.STATE, JSONObject(Awg.status(handle)).optString("state", "down"))
        answer.putBoolean(TunnelClient.LOCKDOWN_MISSING, killSwitch && lockdownMissing())
        return answer
    }

    private fun stopTunnel() {
        if (handle >= 0) {
            Awg.stop(handle)
            handle = -1
        }
    }

    /** No tunnel, no foreground; the service stays only while the app is bound to it. Called on the worker. */
    private fun quit() {
        stopTunnel()
        foreground = CountDownLatch(1)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    /** Another VPN took over, or the person switched this one off in the system's settings. */
    override fun onRevoke() {
        worker.execute { quit() }
    }

    override fun onDestroy() {
        worker.execute { stopTunnel() }
        worker.shutdown()
        super.onDestroy()
    }

    private fun goForeground() {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "VPN", NotificationManager.IMPORTANCE_LOW))
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
        const val ACTION_UP = "tech.qalby.awgscale.tunnel.UP"
        const val ACTION_DOWN = "tech.qalby.awgscale.tunnel.DOWN"
        private const val FOREGROUND_WAIT_S = 10L
        /** As the computer's service waits for the server's first answer (handshakeTimeout in app/qalby/tunnel). */
        private const val HANDSHAKE_TIMEOUT_MS = 20_000L
        private const val HANDSHAKE_POLL_MS = 200L
    }
}
