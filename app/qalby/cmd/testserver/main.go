// testserver — a stand-in for the entry, for the tests only (app/tests/tunnel.sh):
// an AmneziaWG server with the obfuscation the real one uses, in a namespace of
// its own. Every key it makes is fresh and thrown away with the test.
//
//	testserver make-confs <dir> <endpoint host:port>   server.conf and client.conf
//	testserver serve <dir>                            the server, until killed
package main

import (
	"crypto/rand"
	"encoding/base64"
	"encoding/hex"
	"fmt"
	"os"
	"os/exec"
	"os/signal"
	"path/filepath"
	"strings"
	"syscall"

	"github.com/amnezia-vpn/amneziawg-go/v3/conn"
	"github.com/amnezia-vpn/amneziawg-go/v3/device"
	"github.com/amnezia-vpn/amneziawg-go/v3/tun"
	"golang.org/x/crypto/curve25519"
)

const (
	serverAddress = "172.16.17.1/24"
	clientAddress = "172.16.17.15/32"
	listenPort    = 51820
	interfaceName = "awgsrv0"
)

// The obfuscation of the entry (the shapes of tests/pg_users.py and of what the entry runs).
const obfuscation = `Jc = 5
Jmin = 45
Jmax = 101
S1 = 49
S2 = 91
S3 = 40
S4 = 19
H1 = 1020325451
H2 = 3288052141
H3 = 1766607858
H4 = 2528465083
I1 = <r 2><b 0x858000010001000000000669636c6f756403636f6d0000010001c00c000100010000105a00044d583737><rc 36><b 0x0d0a>
`

func main() {
	if len(os.Args) < 3 {
		fmt.Fprintln(os.Stderr, "usage: testserver make-confs <dir> <endpoint> | serve <dir>")
		os.Exit(2)
	}
	var err error
	switch os.Args[1] {
	case "make-confs":
		err = makeConfs(os.Args[2], os.Args[3])
	case "serve":
		err = serve(os.Args[2])
	default:
		err = fmt.Errorf("unknown command")
	}
	if err != nil {
		fmt.Fprintln(os.Stderr, "testserver:", err)
		os.Exit(1)
	}
}

func keyPair() (private, public []byte) {
	private = make([]byte, 32)
	_, _ = rand.Read(private)
	private[0] &= 248
	private[31] = (private[31] & 127) | 64
	public, _ = curve25519.X25519(private, curve25519.Basepoint)
	return private, public
}

func random32() []byte {
	key := make([]byte, 32)
	_, _ = rand.Read(key)
	return key
}

func encode(key []byte) string { return base64.StdEncoding.EncodeToString(key) }

func makeConfs(directory, endpoint string) error {
	serverPrivate, serverPublic := keyPair()
	clientPrivate, clientPublic := keyPair()
	preshared := random32()
	protection := random32()
	shared := obfuscation + "HeaderProtectionKey = " + encode(protection) + "\nContentPaddingAddition = 32-128\n"
	// the client's config is written the way the panel writes it (src/awgscale/users/device.py)
	client := "[Interface]\nPrivateKey = " + encode(clientPrivate) + "\nAddress = " + clientAddress + "\nDNS = 172.16.17.1\nMTU = 1280\n" + shared +
		"\n[Peer]\nPublicKey = " + encode(serverPublic) + "\nPresharedKey = " + encode(preshared) + "\nEndpoint = " + endpoint +
		"\nAllowedIPs = 0.0.0.0/0, ::/0\nPersistentKeepalive = 33\n"
	server := strings.Join([]string{
		"private_key=" + hex.EncodeToString(serverPrivate),
		fmt.Sprintf("listen_port=%d", listenPort),
		uapiOf(shared),
		"header_protection_key=" + hex.EncodeToString(protection),
		"public_key=" + hex.EncodeToString(clientPublic),
		"preshared_key=" + hex.EncodeToString(preshared),
		"allowed_ip=" + clientAddress,
	}, "\n") + "\n"
	if err := os.WriteFile(filepath.Join(directory, "client.conf"), []byte(client), 0o600); err != nil {
		return err
	}
	return os.WriteFile(filepath.Join(directory, "server.uapi"), []byte(server), 0o600)
}

// uapiOf turns the obfuscation lines into UAPI ones (lower case keys; the key itself goes separately).
func uapiOf(shared string) string {
	var lines []string
	for _, line := range strings.Split(strings.TrimSpace(shared), "\n") {
		key, value, _ := strings.Cut(line, "=")
		key = strings.ToLower(strings.TrimSpace(key))
		switch key {
		case "headerprotectionkey":
			continue
		case "contentpaddingaddition":
			key = "content_padding_addition"
		}
		lines = append(lines, key+"="+strings.TrimSpace(value))
	}
	return strings.Join(lines, "\n")
}

func serve(directory string) error {
	config, err := os.ReadFile(filepath.Join(directory, "server.uapi"))
	if err != nil {
		return err
	}
	tunDevice, err := tun.CreateTUN(interfaceName, 1280)
	if err != nil {
		return err
	}
	server := device.NewDevice(tunDevice, conn.NewDefaultBind(), device.NewLogger(device.LogLevelError, "server: "))
	if err := server.IpcSet(string(config)); err != nil {
		return err
	}
	if err := server.Up(); err != nil {
		return err
	}
	for _, arguments := range [][]string{{"address", "add", serverAddress, "dev", interfaceName}, {"link", "set", interfaceName, "up"}} {
		if output, err := exec.Command("ip", arguments...).CombinedOutput(); err != nil {
			return fmt.Errorf("ip: %s", output)
		}
	}
	fmt.Println("serving")
	signals := make(chan os.Signal, 1)
	signal.Notify(signals, syscall.SIGTERM, syscall.SIGINT)
	<-signals
	server.Close()
	return nil
}
