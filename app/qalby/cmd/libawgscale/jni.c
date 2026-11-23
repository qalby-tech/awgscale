//go:build android

// The Kotlin side of libawgscale (tech.qalby.awgscale.tunnel.Awg): strings in and out of Java, and
// the library's record in Android's log (tag awgscale).
#include <jni.h>
#include <stdlib.h>
#include <android/log.h>

extern char *awgPlan(char *conf, char *options);
extern int awgStart(int descriptor, char *conf, char *endpoint);
extern char *awgError(void);
extern int awgSocket(int handle, int family);
extern char *awgStatus(int handle);
extern void awgStop(int handle);

void awgLog(const char *line) {
    __android_log_write(ANDROID_LOG_WARN, "awgscale", line);
}

static jstring handed(JNIEnv *env, char *text) {
    jstring result = (*env)->NewStringUTF(env, text);
    free(text);
    return result;
}

JNIEXPORT jstring JNICALL Java_tech_qalby_awgscale_tunnel_Awg_plan(JNIEnv *env, jclass c, jstring conf, jstring options) {
    const char *confText = (*env)->GetStringUTFChars(env, conf, 0);
    const char *optionsText = (*env)->GetStringUTFChars(env, options, 0);
    char *result = awgPlan((char *)confText, (char *)optionsText);
    (*env)->ReleaseStringUTFChars(env, conf, confText);
    (*env)->ReleaseStringUTFChars(env, options, optionsText);
    return handed(env, result);
}

JNIEXPORT jint JNICALL Java_tech_qalby_awgscale_tunnel_Awg_start(JNIEnv *env, jclass c, jint descriptor, jstring conf, jstring endpoint) {
    const char *confText = (*env)->GetStringUTFChars(env, conf, 0);
    const char *endpointText = (*env)->GetStringUTFChars(env, endpoint, 0);
    int handle = awgStart(descriptor, (char *)confText, (char *)endpointText);
    (*env)->ReleaseStringUTFChars(env, conf, confText);
    (*env)->ReleaseStringUTFChars(env, endpoint, endpointText);
    return handle;
}

JNIEXPORT jstring JNICALL Java_tech_qalby_awgscale_tunnel_Awg_error(JNIEnv *env, jclass c) {
    return handed(env, awgError());
}

JNIEXPORT jint JNICALL Java_tech_qalby_awgscale_tunnel_Awg_socket(JNIEnv *env, jclass c, jint handle, jint family) {
    return awgSocket(handle, family);
}

JNIEXPORT jstring JNICALL Java_tech_qalby_awgscale_tunnel_Awg_status(JNIEnv *env, jclass c, jint handle) {
    return handed(env, awgStatus(handle));
}

JNIEXPORT void JNICALL Java_tech_qalby_awgscale_tunnel_Awg_stop(JNIEnv *env, jclass c, jint handle) {
    awgStop(handle);
}
