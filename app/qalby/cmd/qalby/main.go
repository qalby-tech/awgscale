// qalby — the awgscale VPN on a computer: the service that holds the tunnel, and
// the command line that asks it.
//
//	qalby daemon            the service (root; systemd or the Windows service manager starts it)
//	qalby up <file | ->     bring the tunnel of this config up
//	qalby down              take it down
//	qalby status            what the tunnel is doing
//	qalby service install | remove   (Windows) register the service, or take it away
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/qalby-tech/awgscale/app/qalby/control"
	"github.com/qalby-tech/awgscale/app/qalby/tunnel"
)

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	channel := ChannelPath
	if path := os.Getenv("AWGSCALE_SOCKET"); path != "" {
		channel = path
	}
	switch os.Args[1] {
	case "daemon":
		if err := runDaemon(channel); err != nil {
			fail(err)
		}
	case "service":
		if len(os.Args) != 3 {
			usage()
		}
		if err := manageService(os.Args[2]); err != nil {
			fail(err)
		}
	case "up":
		if len(os.Args) != 3 {
			usage()
		}
		text, err := readConf(os.Args[2])
		if err != nil {
			fail(err)
		}
		ask(channel, control.Request{Command: control.CommandUp, Conf: text, KillSwitch: os.Getenv("AWGSCALE_KILL_SWITCH") == "1"})
	case "down":
		ask(channel, control.Request{Command: control.CommandDown})
	case "status":
		ask(channel, control.Request{Command: control.CommandStatus})
	default:
		usage()
	}
}

// serve runs the service until the channel fails; the tunnel goes down with it.
func serve(channel string) error {
	machine := tunnel.New()
	err := control.Serve(channel, machine)
	machine.Down()
	return err
}

func readConf(path string) (string, error) {
	if path == "-" {
		bytes, err := io.ReadAll(io.LimitReader(os.Stdin, 64<<10))
		return string(bytes), err
	}
	bytes, err := os.ReadFile(path)
	return string(bytes), err
}

func ask(channel string, request control.Request) {
	answer, err := control.Ask(channel, request)
	if err != nil {
		fail(err)
	}
	if !answer.OK {
		fail(fmt.Errorf("%s", answer.Error))
	}
	shown, _ := json.Marshal(answer.Status)
	fmt.Println(string(shown))
}

func fail(err error) {
	fmt.Fprintln(os.Stderr, "qalby:", err)
	os.Exit(1)
}

func usage() {
	fmt.Fprintln(os.Stderr, "usage: qalby daemon | up <file|-> | down | status | service install|remove")
	os.Exit(2)
}
