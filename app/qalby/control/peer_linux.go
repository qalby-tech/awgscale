//go:build linux

package control

import (
	"net"
	"os/user"
	"strconv"

	"golang.org/x/sys/unix"
)

// mayControl: the process on the other end is root or in the group, whatever the socket's mode is.
func mayControl(connection *net.UnixConn, group int) bool {
	raw, err := connection.SyscallConn()
	if err != nil {
		return false
	}
	var credentials *unix.Ucred
	var failure error
	if err := raw.Control(func(descriptor uintptr) {
		credentials, failure = unix.GetsockoptUcred(int(descriptor), unix.SOL_SOCKET, unix.SO_PEERCRED)
	}); err != nil || failure != nil {
		return false
	}
	if credentials.Uid == 0 {
		return true
	}
	if group < 0 {
		return false
	}
	account, err := user.LookupId(strconv.FormatUint(uint64(credentials.Uid), 10))
	if err != nil {
		return false
	}
	groups, err := account.GroupIds()
	if err != nil {
		return false
	}
	wanted := strconv.Itoa(group)
	for _, member := range groups {
		if member == wanted {
			return true
		}
	}
	return false
}
