// qalby — the awgscale VPN on a computer: the service that holds the tunnel, and
// the command line that asks it.
//
//	qalby daemon            the service (root; systemd starts it)
//	qalby up <file | ->     bring the tunnel of this config up
//	qalby down              take it down
//	qalby status            what the tunnel is doing
package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"

	"github.com/qalby-tech/awgscale/app/qalby/control"
	"github.com/qalby-tech/awgscale/app/qalby/tunnel"
)

// SocketPath is where the service listens; the app knows it too (app/core/src/service.rs).
const SocketPath = "/run/awgscale/control.sock"

func main() {
	if len(os.Args) < 2 {
		usage()
	}
	socket := SocketPath
	if path := os.Getenv("AWGSCALE_SOCKET"); path != "" {
		socket = path
	}
	switch os.Args[1] {
	case "daemon":
		machine := tunnel.New()
		if err := control.Serve(socket, machine); err != nil {
			machine.Down()
			fmt.Fprintln(os.Stderr, "qalby:", err)
			os.Exit(1)
		}
	case "up":
		if len(os.Args) != 3 {
			usage()
		}
		text, err := readConf(os.Args[2])
		if err != nil {
			fail(err)
		}
		ask(socket, control.Request{Command: control.CommandUp, Conf: text})
	case "down":
		ask(socket, control.Request{Command: control.CommandDown})
	case "status":
		ask(socket, control.Request{Command: control.CommandStatus})
	default:
		usage()
	}
}

func readConf(path string) (string, error) {
	if path == "-" {
		bytes, err := io.ReadAll(io.LimitReader(os.Stdin, 64<<10))
		return string(bytes), err
	}
	bytes, err := os.ReadFile(path)
	return string(bytes), err
}

func ask(socket string, request control.Request) {
	answer, err := control.Ask(socket, request)
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
	fmt.Fprintln(os.Stderr, "usage: qalby daemon | up <file|-> | down | status")
	os.Exit(2)
}
