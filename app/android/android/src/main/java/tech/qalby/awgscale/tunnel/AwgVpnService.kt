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
 * While a tunnel runs it is kept carrying ([watchNetworks], [watchdog]): when the phone's networks change
 * (Wi-Fi to mobile data, a lift, the metro) the tunnel is made anew a moment later, and when the server has
 * not answered for longer than a handshake stays fresh (or a new tunnel never got its first answer) it is
 * made anew again, less often each time it does not help. Made anew is made before the old one goes, as for
 * another setting, so that nothing leaves outside the tunnel meanwhile; the service never quits for it. A
 * process Android ended on its own is started again (START_STICKY) and comes up as the always-on one does.
 *
 * The server the person keeps is watched through the tunnel ([ExitWatch]): a notification says when it stops
 * working (the network moves the person to a working one meanwhile) and when it works again.
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

    /** What the running tunnel was started with: made anew from it; null while none runs. Worker only. */
    private var running: Saved? = null
    /** When the running tunnel was made (ms): its first answer is waited for a while before it counts as lost. */
    private var madeAt = 0L
    /** The phone's networks while a tunnel runs, and when the listener came (its first calls are no change). */
    private var watching: ConnectivityManager.NetworkCallback? = null
    private var watchingSince = 0L
    private val underlying = mutableSetOf<Network>()
    private var networkChange: ScheduledFuture<*>? = null
    private var watchdog: ScheduledFuture<*>? = null
    /** Made anew by the watchdog: not before this moment (ms), and the pause after the next try. */
    private var nextRemakeAt = 0L
    private var remakePauseMs = REMAKE_FIRST_MS
    /** The notification says «Переподключаемся…» until the server answers again. */
    private var reconnecting = false
    private val exitWatch = ExitWatch { change, server -> serverChanged(change, server) }

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
        // a process Android ended on its own is started again (no intent: as the always-on one); a tunnel the
        // person switched off stopped the service, which is then not started again
        return START_STICKY
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
            if (failure != null) {
                quit()
            } else {
                began(Saved(data.getString(TunnelClient.CONF) ?: "", data.getString(TunnelClient.OPTIONS) ?: "{}", data.getBoolean(TunnelClient.KILL_SWITCH)))
            }
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
                began(saved)
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

    /**
     * A tunnel runs from [saved]: it is kept carrying from now on, and the server the person keeps is watched.
     * Called on the worker after each tunnel made (the app's, the always-on one's, a remade one).
     */
    private fun began(saved: Saved) {
        running = saved
        madeAt = System.currentTimeMillis()
        watchNetworks()
        if (watchdog == null) {
            watchdog = worker.scheduleWithFixedDelay(
                { guardedKeeping("watchdog") { watchdogTick() } }, WATCHDOG_MS, WATCHDOG_MS, TimeUnit.MILLISECONDS,
            )
        }
        apiBase()?.let { exitWatch.start(it) }
    }

    /** No tunnel to keep any more: no listener, no timers. Called on the worker. */
    private fun stopWatching() {
        running = null
        reconnecting = false
        watchdog?.cancel(false)
        watchdog = null
        networkChange?.cancel(false)
        networkChange = null
        nextRemakeAt = 0L
        remakePauseMs = REMAKE_FIRST_MS
        underlying.clear()
        watching?.let {
            try {
                getSystemService(ConnectivityManager::class.java).unregisterNetworkCallback(it)
            } catch (gone: Exception) {
                Log.w(TAG, "the network listener was not there")
            }
        }
        watching = null
        exitWatch.stop()
        // what was said about the server belongs to the tunnel that went
        getSystemService(NotificationManager::class.java).cancel(SERVER_NOTIFICATION)
    }

    /**
     * The phone's own networks (not a VPN's), listened to while a tunnel runs: one that comes or goes makes
     * the tunnel anew a moment later (a few changes at once make it once). The listener's first calls only
     * say which networks there are. Called on the worker.
     */
    private fun watchNetworks() {
        if (watching != null) return
        val callback = object : ConnectivityManager.NetworkCallback() {
            override fun onAvailable(network: Network) {
                onWorker { guardedKeeping("network") { networksChanged(network, true) } }
            }

            override fun onLost(network: Network) {
                onWorker { guardedKeeping("network") { networksChanged(network, false) } }
            }
        }
        val request = NetworkRequest.Builder()
            .addCapability(NetworkCapabilities.NET_CAPABILITY_INTERNET)
            .addCapability(NetworkCapabilities.NET_CAPABILITY_NOT_VPN)
            .build()
        try {
            getSystemService(ConnectivityManager::class.java).registerNetworkCallback(request, callback)
            watching = callback
            watchingSince = System.currentTimeMillis()
        } catch (refused: Exception) {
            Log.w(TAG, "keep: no network listener; the watchdog alone")
        }
    }

    private fun networksChanged(network: Network, available: Boolean) {
        if (available) underlying.add(network) else underlying.remove(network)
        if (running == null || System.currentTimeMillis() - watchingSince < NETWORKS_SETTLE_MS) return
        networkChange?.cancel(false)
        networkChange = worker.schedule(
            { guardedKeeping("network") { if (running != null && underlying.isNotEmpty()) remake("network") } },
            NETWORK_CHANGE_WAIT_MS, TimeUnit.MILLISECONDS,
        )
    }

    /**
     * Every little while: the server answering again ends «Переподключаемся…»; a server silent for longer than a
     * handshake stays fresh, or a new tunnel with no first answer yet, makes the tunnel anew while there is a
     * network, each time after a longer pause. Called on the worker.
     */
    private fun watchdogTick() {
        val runningHandle = handle
        if (running == null || runningHandle < 0) return
        val status = JSONObject(Awg.status(runningHandle))
        if (status.optString("state") == STATE_UP) {
            remakePauseMs = REMAKE_FIRST_MS
            nextRemakeAt = 0L
            if (reconnecting) {
                reconnecting = false
                showNotification(said("VPN включён", "VPN is on"))
            }
            return
        }
        val now = System.currentTimeMillis()
        if (status.optLong("handshake") == 0L && now - madeAt < FIRST_ANSWER_WAIT_MS) return
        if (watching != null && underlying.isEmpty()) return
        if (now < nextRemakeAt) return
        nextRemakeAt = now + remakePauseMs
        remakePauseMs = minOf(remakePauseMs * 2, REMAKE_LONGEST_MS)
        remake("silent")
    }

    /**
     * The running tunnel made anew from what it was started with: the server's name found again, the new
     * interface before the old tunnel goes. Not found (no network yet): the old one stays, a later change or
     * the watchdog tries again. Called on the worker.
     */
    private fun remake(why: String) {
        val saved = running ?: return
        // the person's «down» may be on its way to the worker already: it goes first
        if (handle < 0 || cancelled) return
        Log.i(TAG, "keep: remade ($why)")
        reconnecting = true
        showNotification(said("Переподключаемся…", "Reconnecting…"))
        val failure = turnOn(saved.conf, saved.options, saved.killSwitch, waitForServer = true)
        if (failure != null) {
            Log.w(TAG, "keep: not remade: ${failure.code}")
            return
        }
        madeAt = System.currentTimeMillis()
        if (handle >= 0 && JSONObject(Awg.status(handle)).optString("state") == STATE_UP) {
            reconnecting = false
            showNotification(said("VPN включён", "VPN is on"))
        }
    }

    /** [work] on the worker; nothing once the service is going (the worker is shut down then). */
    private fun onWorker(work: () -> Unit) {
        try {
            worker.execute(work)
        } catch (going: java.util.concurrent.RejectedExecutionException) {
            Log.i(TAG, "keep: the service is going")
        }
    }

    /** As [guarded], but what fails while keeping a tunnel leaves it as it is: the next try may do better. */
    private fun guardedKeeping(what: String, work: () -> Unit) {
        try {
            work()
        } catch (failed: Exception) {
            Log.w(TAG, "keep: $what: failed")
        }
    }

    /**
     * The network's app API, as the app finds it (Profile.api_base in app/core): an invited device's at its
     * entry, a connection given by hand's where it was found; null when there is none.
     */
    private fun apiBase(): String? = try {
        val profile = JSONObject(java.io.File(applicationInfo.dataDir, "network.json").readText())
        if (profile.optString("kind") == KIND_MANUAL) {
            profile.optJSONObject("panel")?.optString("base")?.ifEmpty { null }
        } else {
            "http://${profile.getString("entry")}:$API_PORT/app/api/"
        }
    } catch (missing: Exception) {
        null
    }

    /** The server the person keeps stopped working, or works again: a notification of its own says so. */
    private fun serverChanged(change: ExitWatch.ServerChange, server: String) {
        val manager = getSystemService(NotificationManager::class.java)
        manager.createNotificationChannel(
            NotificationChannel(SERVER_CHANNEL, said("Сервер", "Server"), NotificationManager.IMPORTANCE_DEFAULT),
        )
        val title = when (change) {
            ExitWatch.ServerChange.DOWN -> said("Сервер недоступен", "Server unavailable")
            ExitWatch.ServerChange.BACK -> said("Сервер снова работает", "Server is back")
        }
        val shown = Notification.Builder(this, SERVER_CHANNEL)
            .setSmallIcon(android.R.drawable.ic_lock_lock)
            .setContentTitle(title)
            .setContentText(server)
            .setContentIntent(launchIntent())
            .setAutoCancel(true)
            .build()
        manager.notify(SERVER_NOTIFICATION, shown)
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
        stopWatching()
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
            stopWatching()
            stopTunnel()
        }
        worker.shutdown()
        exitWatch.close()
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
        /** Keeping a tunnel: how often it is looked at, and how long a new one may wait for its first answer. */
        private const val WATCHDOG_MS = 15_000L
        private const val FIRST_ANSWER_WAIT_MS = 30_000L
        /** The pause before the watchdog makes a silent tunnel anew again, the first and the longest. */
        private const val REMAKE_FIRST_MS = 30_000L
        private const val REMAKE_LONGEST_MS = 300_000L
        /** The networks' listener: its first calls say only what there is; a few changes at once count once. */
        private const val NETWORKS_SETTLE_MS = 3_000L
        private const val NETWORK_CHANGE_WAIT_MS = 2_000L
        /** What libawgscale says of a tunnel whose last handshake is fresh (State Up in app/qalby/mobile). */
        private const val STATE_UP = "up"
        /** A connection given by hand (NetworkKind in app/core/src/profile.rs), and the app API's port. */
        private const val KIND_MANUAL = "manual"
        private const val API_PORT = 8088
        /** The notifications about the server the person keeps. */
        private const val SERVER_CHANNEL = "server"
        private const val SERVER_NOTIFICATION = 2
    }
}
