package tech.qalby.awgscale.tunnel

/**
 * The tunnel's library, libawgscale.so (app/qalby/cmd/libawgscale): amneziawg-go and the config's
 * reader of the computer's service, behind a few functions (jni.c there).
 */
object Awg {
    init {
        System.loadLibrary("awgscale")
    }

    /** What to give VpnService.Builder for the config and the options (JSON), or {"error": …}. */
    @JvmStatic external fun plan(conf: String, options: String): String

    /** Runs the tunnel on the interface [descriptor] (now the library's); its number, or -1 ([error]). */
    @JvmStatic external fun start(descriptor: Int, conf: String, endpoint: String): Int

    /** Why the last [start] failed. */
    @JvmStatic external fun error(): String

    /** The tunnel's own UDP socket of IPv4 ([family] 4) or IPv6 (6), for VpnService.protect; or -1. */
    @JvmStatic external fun socket(handle: Int, family: Int): Int

    /** The tunnel's status: {"state": "up"|"connecting"|"down", "handshake": …, "rx": …, "tx": …}. */
    @JvmStatic external fun status(handle: Int): String

    @JvmStatic external fun stop(handle: Int)
}
