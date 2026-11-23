package tech.qalby.awgscale.tunnel

import android.app.Notification
import android.app.NotificationChannel
import android.app.NotificationManager
import android.app.PendingIntent
import android.content.Intent
import android.content.pm.ServiceInfo
import android.net.ConnectivityManager
import android.net.Network
import android.net.NetworkCapabilities
import android.net.NetworkRequest
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
import java.util.concurrent.ScheduledFuture
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
 * Android may start it itself (the person chose it as the always-on VPN, at boot): it then comes up
 * with the network and the choices the app keeps, from the app's data, which this process shares.
 * When the server's name cannot be found yet (no network so early, or none at all) it stays, says
 * «Ждём сеть…» in its notification and tries again whenever a network comes, and after a while
 * that grows longer each time ([RETRY_FIRST_MS] to [RETRY_LONGEST_MS]).
 *
 * A tunnel already on is changed (other settings) by making the new interface first: Android moves
 * the traffic to it as it is established, and only then the old tunnel is stopped, so that nothing
 * goes outside the tunnel in between.
 *
 * The kill switch: as on a computer, it is refused when the server does not send all of IPv4 into
 * the tunnel (libawgscale's plan). What keeps traffic in is Android's own «Always-on VPN» and «Block
 * connections without VPN» in the system's settings, which an app cannot switch on; the status says
 * when it was asked for and is off. Unlike a computer's kill switch, Android's lets nothing past the
 * VPN: the local networks (the printer, the router's page) are blocked too while the tunnel is down,
 * and likely even while it is up, whatever the app sends around the tunnel.
 *
 * The log gets fixed codes only, never an exception's text: that may hold a part of the config.
 */
class AwgVpnService : VpnService() {
    /** Why the tunnel did not come up: a code of [Refusal] and what goes with it. */
    private class Failure(val code: String, val detail: String)

    /** Every change of the tunnel runs here, one at a time; the retries of [waitForNetwork] are scheduled here. */
    private val worker = Executors.newSingleThreadScheduledExecutor()
    /** The running tunnel's number in libawgscale, or -1; read by STATUS on the main thread too. */
    @Volatile private var handle = -1
    @Volatile private var killSwitch = false
    /** Opened once the service is in the foreground (ACTION_UP, from the app's startForegroundService). */
    @Volatile private var foreground = CountDownLatch(1)
    /** The person said down while the tunnel was still waiting for the server's first answer. */
    @Volatile private var cancelled = false

    /** Waiting for a network to bring the kept tunnel up (always-on): the next try, and the network's listener. */
    private var retry: ScheduledFuture<*>? = null
    private var retryDelayMs = RETRY_FIRST_MS
    private var networks: ConnectivityManager.NetworkCallback? = null

    private val messenger = Messenger(Handler(Looper.getMainLooper()) { message -> received(message); true })

    override fun onBind(intent: Intent?): IBinder? =
        if (intent?.action == SERVICE_INTERFACE) super.onBind(intent) else messenger.binder

    override fun onStartCommand(intent: Intent?, flags: Int, startId: Int): Int {
        when (intent?.action) {
            ACTION_UP -> {
                goForeground(said("VPN включён", "VPN is on"))
                foreground.countDown()
            }
            ACTION_DOWN -> {
                cancelled = true
                worker.execute { guarded("down") { quit() } }
            }
            // started by Android itself (always-on VPN, at boot): the network the app keeps, with the
            // person's choices
            else -> {
                cancelled = false
                goForeground(said("Ждём сеть…", "Waiting for the network…"))
                foreground.countDown()
                worker.execute { guarded("always-on") { if (handle < 0) upFromSaved() } }
            }
        }
        return START_NOT_STICKY
    }

    /** A message of the app: UP (conf, options, kill switch), DOWN, STATUS; the answer goes to replyTo. */
    private fun received(message: Message) {
        val replyTo = message.replyTo
        val data = Bundle(message.data)
        val what = message.what
        if (what == TunnelClient.DOWN) {
            cancelled = true // an UP still waiting for the server gives up at once
        }
        if (what == TunnelClient.UP) {
            cancelled = false
        }
        if (what == TunnelClient.STATUS) {
            // answered at once, not after an UP that is still waiting for the server
            val answer = try {
                report()
            } catch (failed: Exception) {
                Log.w(TAG, "status: failed")
                Bundle().apply { putString(TunnelClient.STATE, "down") }
            }
            try {
                replyTo?.send(Message.obtain(null, what).apply { this.data = answer })
            } catch (gone: Exception) {
                Log.w(TAG, "the app is gone")
            }
            return
        }
        worker.execute {
            val answer = try {
                answered(what, data)
            } catch (failed: Exception) {
                Log.w(TAG, "message $what: failed")
                quit()
                report().apply { putString(TunnelClient.FAILURE, Refusal.START) }
            }
            try {
                replyTo?.send(Message.obtain(null, what).apply { this.data = answer })
            } catch (gone: Exception) {
                Log.w(TAG, "the app is gone")
            }
        }
    }

    /** The answer to a message of the app. Called on the worker. */
    private fun answered(what: Int, data: Bundle): Bundle = when (what) {
        TunnelClient.UP -> {
            stopWaiting()
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

    /**
     * Up with what the app keeps in its data (app/core: profile.rs network.json, settings.rs
     * settings.json); no network kept, no tunnel. The server's name not found (no network yet):
     * waits for one. Called on the worker.
     */
    private fun upFromSaved() {
        val saved = try {
            val directory = java.io.File(applicationInfo.dataDir)
            val profile = JSONObject(java.io.File(directory, "network.json").readText())
            val settingsFile = java.io.File(directory, "settings.json")
            val settings = if (settingsFile.exists()) JSONObject(settingsFile.readText()) else JSONObject()
            val options = JSONObject()
                .put("kill_switch", settings.optBoolean("kill_switch", false))
                .put("local_around", settings.optBoolean("local_around", true))
                .put("around", settings.optJSONArray("around") ?: org.json.JSONArray())
            Saved(profile.getString("conf"), options.toString(), settings.optBoolean("kill_switch", false))
        } catch (missing: Exception) {
            // never the exception's text: org.json puts the file's content into it
            Log.w(TAG, "always-on: no network kept")
            null
        }
        if (saved == null) {
            quit()
            return
        }
        tryUp(saved)
    }

    /** What the app keeps: the config, the options for the plan, whether the kill switch was asked for. */
    private class Saved(val conf: String, val options: String, val killSwitch: Boolean)

    /** One try of bringing the kept network up; the name not found: the next one waits for a network. */
    private fun tryUp(saved: Saved) {
        retry = null
        // at boot the server may answer later than the network comes: the tunnel then waits for it
        val failure = turnOn(saved.conf, saved.options, saved.killSwitch, waitForServer = true)
        when {
            failure == null -> {
                stopWaiting()
                showNotification(said("VPN включён", "VPN is on"))
            }
            failure.code == Refusal.RESOLVE -> waitForNetwork(saved)
            else -> {
                Log.w(TAG, "always-on: the tunnel did not come up: ${failure.code}")
                quit()
            }
        }
    }

    /**
     * No server yet: the service stays (in the foreground, «Ждём сеть…») and tries again when a
     * network comes, or when the wait is over, each wait longer than the last. Called on the worker.
     */
    private fun waitForNetwork(saved: Saved) {
        Log.i(TAG, "always-on: waiting for the network")
        showNotification(said("Ждём сеть…", "Waiting for the network…"))
        if (networks == null) {
            val callback = object : ConnectivityManager.NetworkCallback() {
                override fun onAvailable(network: Network) {
                    worker.execute {
                        guarded("always-on") {
                            if (networks != null && handle < 0) {
                                retry?.cancel(false)
                                retryDelayMs = RETRY_FIRST_MS
                                tryUp(saved)
                            }
                        }
                    }
                }
            }
            val request = NetworkRequest.Builder()
                .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
                .addCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
                .build()
            try {
                getSystemService(ConnectivityManager::class.java).registerNetworkCallback(request, callback)
                networks = callback
            } catch (refused: Exception) {
                Log.w(TAG, "always-on: no network listener; tries by time only")
            }
        }
        retry?.cancel(false)
        retry = worker.schedule({ guarded("always-on") { if (handle < 0) tryUp(saved) } }, retryDelayMs, TimeUnit.MILLISECONDS)
        retryDelayMs = minOf(retryDelayMs * 2, RETRY_LONGEST_MS)
    }

    /** No more waiting for a network (up, down, or the app took over). Called on the worker. */
    private fun stopWaiting() {
        retry?.cancel(false)
        retry = null
        retryDelayMs = RETRY_FIRST_MS
        networks?.let {
            try {
                getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(it)
            } catch (gone: Exception) {
                Log.w(TAG, "the network listener was not there")
            }
        }
        networks = null
    }

    /**
     * Brings the tunnel up, a new one in place of the one on (if any) without a gap; null once the
     * server answered (or, [waitForServer], once it runs), or why not. Called on the worker.
     */
    private fun turnOn(conf: String, options: String, askedKillSwitch: Boolean, waitForServer: Boolean = false): Failure? {
        if (!foreground.await(FOREGROUND_WAIT_S, TimeUnit.SECONDS)) {
            return Failure(Refusal.START, "")
        }
        val plan = JSONObject(Awg.plan(conf, options))
        if (plan.has("error")) {
            if (plan.optString("code") == PLAN_KILL_SWITCH) {
                return Failure(Refusal.KILL_SWITCH, "")
            }
            // the library's words, which never repeat what the config holds
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
            return Failure(Refusal.CONFIG, "")
        }
        // the new interface takes the traffic from the old one (if any) as it is established
        val established = try {
            builder.establish()
        } catch (refused: Exception) {
            Log.w(TAG, "establish: refused")
            null
        } ?: return Failure(Refusal.ESTABLISH, "")
        val started = Awg.start(established.detachFd(), conf, endpoint)
        if (started < 0) {
            return Failure(Refusal.START, Awg.error())
        }
        for (family in intArrayOf(4, 6)) {
            val socket = Awg.socket(started, family)
            if (socket >= 0 && !protect(socket)) {
                Log.w(TAG, "the tunnel's socket of IPv$family is not protected")
            }
        }
        // the old tunnel goes only now that the new one holds the traffic
        stopTunnel()
        handle = started
        killSwitch = askedKillSwitch
        val deadline = System.currentTimeMillis() + HANDSHAKE_TIMEOUT_MS
        while (System.currentTimeMillis() < deadline && !cancelled) {
            if (JSONObject(Awg.status(started)).optLong("handshake") != 0L) {
                return null
            }
            Thread.sleep(HANDSHAKE_POLL_MS)
        }
        if (waitForServer && !cancelled) {
            return null // connecting: amneziawg-go shakes hands as soon as the server can be reached
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

    /** The tunnel's state for the app (Report in app/android/src/lib.rs). */
    private fun report(): Bundle {
        val answer = Bundle()
        val running = handle
        if (running < 0) {
            answer.putString(TunnelClient.STATE, "down")
            return answer
        }
        answer.putString(TunnelClient.STATE, JSONObject(Awg.status(running)).optString("state", "down"))
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
        stopWaiting()
        stopTunnel()
        foreground = CountDownLatch(1)
        stopForeground(STOP_FOREGROUND_REMOVE)
        stopSelf()
    }

    /** Runs [work] on the worker's turn; whatever it throws ends the tunnel, and the log gets [what] alone. */
    private fun guarded(what: String, work: () -> Unit) {
        try {
            work()
        } catch (failed: Exception) {
            Log.w(TAG, "$what: failed")
            quit()
        }
    }

    /** Another VPN took over, or the person switched this one off in the system's settings. */
    override fun onRevoke() {
        cancelled = true
        worker.execute { guarded("revoke") { quit() } }
    }

    override fun onDestroy() {
        worker.execute {
            stopWaiting()
            stopTunnel()
        }
        worker.shutdown()
        super.onDestroy()
    }

    private fun notification(text: String): Notification {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(NotificationChannel(CHANNEL, "VPN", NotificationManager.IMPORTANCE_LOW))
        val down = PendingIntent.getService(
            this, 1, Intent(this, AwgVpnService::class.java).setAction(ACTION_DOWN), PendingIntent.FLAG_IMMUTABLE,
        )
        return Notification.Builder(this, CHANNEL)
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .setContentTitle("awgscale")
            .setContentText(text)
            .setOngoing(true)
            .setContentIntent(launchIntent())
            .addAction(Notification.Action.Builder(null, said("Отключить", "Disconnect"), down).build())
            .build()
    }

    private fun goForeground(text: String) {
        val shown = notification(text)
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.UPSIDE_DOWN_CAKE) {
            startForeground(NOTIFICATION, shown, ServiceInfo.FOREGROUND_SERVICE_TYPE_SYSTEM_EXEMPTED)
        } else {
            startForeground(NOTIFICATION, shown)
        }
    }

    /** The notification of the service already in the foreground, with another text. */
    private fun showNotification(text: String) {
        getSystemService(NotificationManager::class.java).notify(NOTIFICATION, notification(text))
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
        /** The code of libawgscale's plan when it refuses the kill switch (codeKillSwitch there). */
        private const val PLAN_KILL_SWITCH = "kill_switch"
        private const val FOREGROUND_WAIT_S = 10L
        /** As the computer's service waits for the server's first answer (handshakeTimeout in app/qalby/tunnel). */
        private const val HANDSHAKE_TIMEOUT_MS = 20_000L
        private const val HANDSHAKE_POLL_MS = 200L
        /** Always-on with no network yet: the first wait before trying again, and the longest. */
        private const val RETRY_FIRST_MS = 2_000L
        private const val RETRY_LONGEST_MS = 60_000L
    }
}
