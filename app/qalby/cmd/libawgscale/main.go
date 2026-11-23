//go:build android

// libawgscale — the tunnel's library of the Android app (libawgscale.so): the phone's tunnel
// (package mobile) behind a few C functions, which jni.c gives the app's Kotlin class
// tech.qalby.awgscale.tunnel.Awg. Built with the NDK by app/build-android.sh.
package main

/*
#cgo LDFLAGS: -llog
#include <stdlib.h>
extern void awgLog(const char *line);
*/
import "C"

import (
	"encoding/json"
	"unsafe"

	"github.com/qalby-tech/awgscale/app/qalby/mobile"
)

func init() {
	mobile.Logger = func(line string) {
		text := C.CString(line)
		C.awgLog(text)
		C.free(unsafe.Pointer(text))
	}
}

// refusal is what a JSON function hands back instead of its result.
type refusal struct {
	Error string `json:"error"`
}

func encoded(value any) *C.char {
	text, err := json.Marshal(value)
	if err != nil {
		text = []byte(`{"error":"сбой"}`)
	}
	return C.CString(string(text))
}

// awgPlan: the plan of the config and the options (JSON), as {"addresses":…} or {"error":…}.
//
//export awgPlan
func awgPlan(conf *C.char, options *C.char) *C.char {
	var chosen mobile.Options
	if err := json.Unmarshal([]byte(C.GoString(options)), &chosen); err != nil {
		return encoded(refusal{Error: "bad options"})
	}
	plan, err := mobile.MakePlan(C.GoString(conf), chosen)
	if err != nil {
		return encoded(refusal{Error: err.Error()})
	}
	return encoded(plan)
}

// lastError: why the last awgStart failed.
var lastError string

// awgStart runs the tunnel on the interface `descriptor`; the session's number, or -1 (awgError says why).
//
//export awgStart
func awgStart(descriptor C.int, conf *C.char, endpoint *C.char) C.int {
	session, err := mobile.Start(int(descriptor), C.GoString(conf), C.GoString(endpoint))
	if err != nil {
		lastError = err.Error()
		return -1
	}
	return C.int(mobile.Keep(session))
}

//export awgError
func awgError() *C.char {
	return C.CString(lastError)
}

// awgSocket: the session's UDP socket of IPv4 (family 4) or IPv6 (6), or -1.
//
//export awgSocket
func awgSocket(handle C.int, family C.int) C.int {
	session := mobile.Find(int(handle))
	if session == nil {
		return -1
	}
	four, six := session.Sockets()
	if family == 6 {
		return C.int(six)
	}
	return C.int(four)
}

// awgStatus: the session's status as JSON; a session that is not there is down.
//
//export awgStatus
func awgStatus(handle C.int) *C.char {
	session := mobile.Find(int(handle))
	if session == nil {
		return encoded(mobile.Status{State: mobile.Down})
	}
	return encoded(session.Status())
}

//export awgStop
func awgStop(handle C.int) {
	mobile.Forget(int(handle))
}

func main() {}
