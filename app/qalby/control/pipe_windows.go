//go:build windows

package control

import (
	"errors"
	"net"
	"time"

	"github.com/amnezia-vpn/amneziawg-go/v3/ipc/namedpipe"
	"golang.org/x/sys/windows"
)

const refusedText = "нет прав управлять VPN"

// pipeSecurity: the pipe is SYSTEM's; SYSTEM and the administrators may do anything with it, and
// the people logged on at this computer (INTERACTIVE) may read and write it. Not services, not
// network logons, not processes of a low integrity level (a sandboxed browser tab).
const pipeSecurity = "O:SYD:P(A;;GA;;;SY)(A;;GA;;;BA)(A;;GRGW;;;IU)S:(ML;;NW;;;ME)"

func listen(path string) (net.Listener, error) {
	descriptor, err := windows.SecurityDescriptorFromString(pipeSecurity)
	if err != nil {
		return nil, err
	}
	// Listen makes the first instance of the pipe and fails when the name is taken: nobody can sit on
	// it before the service and be handed the configs.
	return (&namedpipe.ListenConfig{SecurityDescriptor: descriptor}).Listen(path)
}

// mayControl: the pipe's own rules decided already who could open it.
func mayControl(net.Conn) bool { return true }

func dial(path string) (net.Conn, error) {
	system, err := windows.CreateWellKnownSid(windows.WinLocalSystemSid)
	if err != nil {
		return nil, err
	}
	// only a pipe of SYSTEM is the service: a pipe somebody else made under its name is not spoken to
	connection, err := (&namedpipe.DialConfig{ExpectedOwner: system}).DialTimeout(path, 5*time.Second)
	if errors.Is(err, windows.ERROR_ACCESS_DENIED) {
		return nil, errorText(refusedText)
	}
	if err != nil {
		return nil, errorText("служба awgscale не запущена")
	}
	return connection, nil
}
