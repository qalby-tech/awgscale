//go:build !linux

package control

import "net"

// Elsewhere the control channel is not a Unix socket (Windows: a named pipe with its own ACL); until
// it is, nobody may control the tunnel through this one.
func mayControl(*net.UnixConn, int) bool { return false }
