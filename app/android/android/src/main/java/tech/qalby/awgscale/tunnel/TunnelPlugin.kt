package tech.qalby.awgscale.tunnel

import android.Manifest
import android.app.Activity
import android.content.Intent
import android.content.pm.PackageManager
import android.net.VpnService
import android.os.Build
import android.os.Bundle
import androidx.activity.result.ActivityResult
import app.tauri.annotation.ActivityCallback
import app.tauri.annotation.Command
import app.tauri.annotation.TauriPlugin
import app.tauri.plugin.Invoke
import app.tauri.plugin.JSObject
import app.tauri.plugin.Plugin
import java.util.concurrent.Executors
import org.json.JSONArray
import org.json.JSONObject

/**
 * What the app's Rust side (app/android/src/lib.rs) asks of Android: the tunnel (up, down, status),
 * which [AwgVpnService] holds in its own process, and the awgscale:// link the phone opened the app with.
 */
@TauriPlugin
class TunnelPlugin(private val activity: Activity) : Plugin(activity) {
    private val tunnel = TunnelClient(activity.applicationContext)
    /** The questions to the tunnel wait for its answers: never on the main thread. */
    private val worker = Executors.newSingleThreadExecutor()
    private var link: String? = null

    init {
        keepLink(activity.intent)
    }

    override fun onNewIntent(intent: Intent) {
        keepLink(intent)
    }

    /** Up with the config and the options; answers once the server answered (or why not). */
    @Command
    fun up(invoke: Invoke) {
        val consent = VpnService.prepare(activity)
        if (consent != null) {
            // Android asks the person once whether this app may be the VPN
            startActivityForResult(invoke, consent, "consentGiven")
            return
        }
        begin(invoke)
    }

    @ActivityCallback
    fun consentGiven(invoke: Invoke, result: ActivityResult) {
        if (result.resultCode != Activity.RESULT_OK) {
            invoke.reject("", Refusal.CONSENT)
            return
        }
        begin(invoke)
    }

    private fun begin(invoke: Invoke) {
        askForNotifications()
        val arguments = invoke.getArgs()
        val options = JSONObject()
            .put("kill_switch", arguments.optBoolean("kill_switch"))
            .put("local_around", arguments.optBoolean("local_around"))
            .put("around", arguments.optJSONArray("around") ?: JSONArray())
        val request = Bundle().apply {
            putString(TunnelClient.CONF, arguments.getString("conf"))
            putString(TunnelClient.OPTIONS, options.toString())
            putBoolean(TunnelClient.KILL_SWITCH, arguments.optBoolean("kill_switch"))
        }
        tunnel.startForeground()
        worker.execute {
            val answer = tunnel.ask(TunnelClient.UP, request, TunnelClient.UP_WAIT_MS)
            val failure = answer?.getString(TunnelClient.FAILURE)
            when {
                answer == null -> invoke.reject("the tunnel's service did not answer", Refusal.START)
                failure != null -> invoke.reject(answer.getString(TunnelClient.DETAIL) ?: "", failure)
                else -> invoke.resolve(shown(answer))
            }
        }
    }

    @Command
    fun down(invoke: Invoke) {
        worker.execute { invoke.resolve(shown(tunnel.ask(TunnelClient.DOWN))) }
    }

    @Command
    fun status(invoke: Invoke) {
        worker.execute { invoke.resolve(shown(tunnel.ask(TunnelClient.STATUS))) }
    }

    /** The service's answer as the Rust side reads it (Report); no answer is no tunnel. */
    private fun shown(answer: Bundle?): JSObject {
        val shown = JSObject()
        shown.put("state", answer?.getString(TunnelClient.STATE) ?: "down")
        shown.put("lockdown_missing", answer?.getBoolean(TunnelClient.LOCKDOWN_MISSING) ?: false)
        return shown
    }

    @Command
    fun openedLink(invoke: Invoke) {
        val answer = JSObject()
        synchronized(this) {
            answer.put("link", link)
            link = null
        }
        invoke.resolve(answer)
    }

    private fun keepLink(intent: Intent?) {
        val data = intent?.data ?: return
        if (intent.action == Intent.ACTION_VIEW && data.scheme == LINK_SCHEME) {
            synchronized(this) { link = data.toString() }
        }
    }

    /** The notification that says the VPN is on needs the person's yes on Android 13+; asked once, not awaited. */
    private fun askForNotifications() {
        if (Build.VERSION.SDK_INT < Build.VERSION_CODES.TIRAMISU) return
        if (activity.checkSelfPermission(Manifest.permission.POST_NOTIFICATIONS) == PackageManager.PERMISSION_GRANTED) return
        activity.runOnUiThread { activity.requestPermissions(arrayOf(Manifest.permission.POST_NOTIFICATIONS), NOTIFICATIONS_REQUEST) }
    }

    companion object {
        const val LINK_SCHEME = "awgscale"
        private const val NOTIFICATIONS_REQUEST = 4242
    }
}

/** The codes of a refusal (refusal in app/android/src/lib.rs). */
object Refusal {
    const val CONSENT = "consent"
    const val CONFIG = "config"
    const val RESOLVE = "resolve"
    const val ESTABLISH = "establish"
    const val HANDSHAKE = "handshake"
    const val START = "start"
}
