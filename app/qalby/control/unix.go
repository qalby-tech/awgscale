//go:build linux

package control

import (
	"errors"
	"net"
	"os"
	"os/user"
	"path/filepath"
	"strconv"
	"time"

	"golang.org/x/sys/unix"
)

// Group is the group whose members may run the tunnel (the package adds the desktop's user to it).
const Group = "awgscale"

const refusedText = "нет прав управлять VPN: нужен root или группа " + Group

// the group's number when the socket was made; -1 is no such group (then root only)
var allowedGroupID = -1

func listen(path string) (net.Listener, error) {
	if err := os.MkdirAll(filepath.Dir(path), 0o755); err != nil {
		return nil, err
	}
	_ = os.Remove(path)
	listener, err := net.ListenUnix("unix", &net.UnixAddr{Name: path, Net: "unix"})
	if err != nil {
		return nil, err
	}
	allowedGroupID = lookupGroup()
	if err := protect(path, allowedGroupID); err != nil {
		listener.Close()
		return nil, err
	}
	return listener, nil
}

// protect: the socket is root's and, when the group exists, its group's; nobody else's.
func protect(path string, group int) error {
	if group < 0 {
		return os.Chmod(path, 0o600)
	}
	if err := os.Chown(path, 0, group); err != nil {
		return err
	}
	return os.Chmod(path, 0o660)
}

func lookupGroup() int {
	found, err := user.LookupGroup(Group)
	if err != nil {
		return -1
	}
	number, err := strconv.Atoi(found.Gid)
	if err != nil {
		return -1
	}
	return number
}

// mayControl: the process on the other end is root or in the group, whatever the socket's mode is.
func mayControl(connection net.Conn) bool {
	socket, isUnix := connection.(*net.UnixConn)
	if !isUnix {
		return false
	}
	raw, err := socket.SyscallConn()
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
	if allowedGroupID < 0 {
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
	wanted := strconv.Itoa(allowedGroupID)
	for _, member := range groups {
		if member == wanted {
			return true
		}
	}
	return false
}

func dial(path string) (net.Conn, error) {
	connection, err := net.DialTimeout("unix", path, 5*time.Second)
	if errors.Is(err, os.ErrPermission) {
		return nil, errorText(refusedText)
	}
	if err != nil {
		return nil, errorText("служба awgscale не запущена")
	}
	return connection, nil
}
