//go:build windows

package main

import (
	"fmt"
	"os"
	"path/filepath"
	"time"

	"golang.org/x/sys/windows"
	"golang.org/x/sys/windows/svc"
	"golang.org/x/sys/windows/svc/mgr"

	"github.com/qalby-tech/awgscale/app/qalby/tunnel"
)

// ChannelPath is the service's pipe; the app knows it too (app/core/src/service.rs).
const ChannelPath = `\\.\pipe\awgscale`

// ServiceName is the service's name for the service manager.
const ServiceName = "awgscale"

// runDaemon: under the service manager it reports to it; started by hand it just serves.
func runDaemon(channel string) error {
	isService, err := svc.IsWindowsService()
	if err != nil {
		return err
	}
	if !isService {
		return serve(channel)
	}
	// a service's standard error goes nowhere: its record is a file (no keys, no configs in it)
	_ = tunnel.LogTo(filepath.Join(os.Getenv("ProgramData"), "awgscale", "qalby.log"))
	tunnel.Event("service started")
	return svc.Run(ServiceName, &service{channel: channel})
}

type service struct {
	channel string
}

func (service *service) Execute(_ []string, requests <-chan svc.ChangeRequest, changes chan<- svc.Status) (bool, uint32) {
	changes <- svc.Status{State: svc.StartPending}
	failed := make(chan error, 1)
	go func() { failed <- serve(service.channel) }()
	changes <- svc.Status{State: svc.Running, Accepts: svc.AcceptStop | svc.AcceptShutdown}
	for {
		select {
		case request := <-requests:
			switch request.Cmd {
			case svc.Stop, svc.Shutdown:
				tunnel.Event("service stopping (%v)", request.Cmd)
				changes <- svc.Status{State: svc.StopPending}
				return false, 0 // the process ends; the adapter and the firewall rules end with it
			case svc.Interrogate:
				changes <- request.CurrentStatus
			}
		case err := <-failed:
			tunnel.Event("service failed: %v", err)
			return false, uint32(windows.ERROR_SERVICE_SPECIFIC_ERROR)
		}
	}
}

// manageService registers the service (runs as SYSTEM, starts with Windows, restarts when it
// fails) for this very qalby.exe, or takes it away. The installer calls it.
func manageService(action string) error {
	manager, err := mgr.Connect()
	if err != nil {
		return err
	}
	defer manager.Disconnect()
	switch action {
	case "install":
		executable, err := os.Executable()
		if err != nil {
			return err
		}
		if existing, err := manager.OpenService(ServiceName); err == nil {
			stop(existing)
			_ = existing.Delete()
			existing.Close()
			time.Sleep(time.Second)
		}
		created, err := manager.CreateService(ServiceName, executable, mgr.Config{
			DisplayName:  "awgscale VPN",
			Description:  "Holds this computer's awgscale tunnel; the awgscale app asks it.",
			StartType:    mgr.StartAutomatic,
			ServiceType:  windows.SERVICE_WIN32_OWN_PROCESS,
			ErrorControl: mgr.ErrorNormal,
			// the service's own SID (NT SERVICE\awgscale) in its token: the firewall rules let the
			// tunnel's own packets out by it (permitWireGuardService), as wireguard-windows does
			SidType: windows.SERVICE_SID_TYPE_UNRESTRICTED,
		}, "daemon")
		if err != nil {
			return err
		}
		defer created.Close()
		_ = created.SetRecoveryActions([]mgr.RecoveryAction{{Type: mgr.ServiceRestart, Delay: 2 * time.Second}}, 60)
		return created.Start()
	case "remove":
		existing, err := manager.OpenService(ServiceName)
		if err != nil {
			return nil // nothing to take away
		}
		defer existing.Close()
		stop(existing)
		return existing.Delete()
	default:
		return fmt.Errorf("install or remove")
	}
}

func stop(running *mgr.Service) {
	status, err := running.Control(svc.Stop)
	if err != nil {
		return
	}
	for deadline := time.Now().Add(15 * time.Second); status.State != svc.Stopped && time.Now().Before(deadline); {
		time.Sleep(300 * time.Millisecond)
		if status, err = running.Query(); err != nil {
			return
		}
	}
}
