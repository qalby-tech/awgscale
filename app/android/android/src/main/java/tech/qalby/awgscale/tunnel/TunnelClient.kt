package tech.qalby.awgscale.tunnel

import android.content.ComponentName
import android.content.Context
import android.content.Intent
import android.content.ServiceConnection
import android.os.Bundle
import android.os.Handler
import android.os.HandlerThread
import android.os.IBinder
import android.os.Message
import android.os.Messenger
import android.os.RemoteException
import android.util.Log
import java.util.concurrent.ArrayBlockingQueue
import java.util.concurrent.TimeUnit

/**
 * The app's side of [AwgVpnService], which lives in a process of its own: bound once (the binding
 * also starts that process), asked by messages, each answered by a Bundle. Blocking: never called on
 * the main thread.
 */
class TunnelClient(private val context: Context) {
    private val lock = Object()
    private var service: Messenger? = null
    private val replies = HandlerThread("awgscale-tunnel-replies").apply { start() }

    private val connection = object : ServiceConnection {
        override fun onServiceConnected(name: ComponentName?, binder: IBinder?) {
            synchronized(lock) {
                service = binder?.let { Messenger(it) }
                lock.notifyAll()
            }
        }

        override fun onServiceDisconnected(name: ComponentName?) {
            synchronized(lock) { service = null } // the process died; Android binds it again
        }
    }

    init {
        context.bindService(Intent(context, AwgVpnService::class.java), connection, Context.BIND_AUTO_CREATE)
    }

    private fun connected(waitMs: Long): Messenger? {
        val deadline = System.currentTimeMillis() + waitMs
        synchronized(lock) {
            while (service == null) {
                val left = deadline - System.currentTimeMillis()
                if (left <= 0) return null
                lock.wait(left)
            }
            return service
        }
    }

    /**
     * Sends [what] with [data]; the service's answer, or null when it did not answer in [waitMs] or
     * its process is gone (killed between the binding and the message: Android binds it again).
     */
    fun ask(what: Int, data: Bundle = Bundle(), waitMs: Long = ASK_WAIT_MS): Bundle? {
        val target = connected(CONNECT_WAIT_MS) ?: return null
        val answers = ArrayBlockingQueue<Bundle>(1)
        val replyTo = Messenger(Handler(replies.looper) { answer -> answers.offer(Bundle(answer.data)); true })
        try {
            target.send(Message.obtain(null, what).apply {
                this.data = data
                this.replyTo = replyTo
            })
        } catch (gone: RemoteException) { // DeadObjectException among them
            Log.w(TAG, "the tunnel's service is gone")
            synchronized(lock) { if (service === target) service = null }
            return null
        }
        return answers.poll(waitMs, TimeUnit.MILLISECONDS)
    }

    /** Puts the service in the foreground before the tunnel comes up (it must stay when the app goes). */
    fun startForeground() {
        context.startForegroundService(Intent(context, AwgVpnService::class.java).setAction(AwgVpnService.ACTION_UP))
    }

    companion object {
        private const val TAG = "awgscale"
        const val UP = 1
        const val DOWN = 2
        const val STATUS = 3
        const val CONF = "conf"
        const val OPTIONS = "options"
        const val KILL_SWITCH = "kill_switch"
        const val STATE = "state"
        const val LOCKDOWN_MISSING = "lockdown_missing"
        const val FAILURE = "failure"
        const val DETAIL = "detail"
        private const val CONNECT_WAIT_MS = 5_000L
        private const val ASK_WAIT_MS = 5_000L
        /** Down waits for an UP under way to give up (it checks every 0.2 s) before the service stops the tunnel. */
        const val DOWN_WAIT_MS = 10_000L
        /** Bringing the tunnel up waits for the server: 20 s, and the name of the server first. */
        const val UP_WAIT_MS = 60_000L
    }
}
