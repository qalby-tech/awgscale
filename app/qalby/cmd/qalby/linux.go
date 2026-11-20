//go:build linux

package main

import "errors"

// ChannelPath is where the service listens; the app knows it too (app/core/src/service.rs).
const ChannelPath = "/run/awgscale/control.sock"

func runDaemon(channel string) error { return serve(channel) }

// On Linux the package's systemd unit is the service.
func manageService(string) error {
	return errors.New("на Linux службой управляет systemd: systemctl enable --now awgscale")
}
