# The awgscale app

The client a person installs to join their network: one window (Tauri 2) on
Linux and Windows, later phones; and `qalby`, the service that holds the tunnel
on a computer and its command line.

| Path | What it is |
|---|---|
| `core/` | Rust, shared by every app: the join link (`awgscale://join?h=…&c=…` or the panel's `https://<domain>/i/<code>`), the invitation, the app API inside the tunnel (`http://172.16.17.1:8088/app/api`), the saved network, the device's settings, and the tunnel's shape. |
| `shell/` | The Tauri crate: the window and the commands its interface calls. Every request to the network and every secret stays here, in Rust. `packaging/` holds the systemd unit, the `.deb` scripts and the Windows installer's hooks. |
| `ui/` | The interface: React + Vite, the approved screens (start, connection, location, settings, the owner's tabs). |
| `qalby/` | Go, on amneziawg-go: `qalby daemon` (root / SYSTEM) holds the tunnel; the app and `qalby up / down / status` ask it over `/run/awgscale/control.sock` (root and the group `awgscale`) or `\\.\pipe\awgscale` (owned by SYSTEM, open to the people logged on). Linux: an awg-quick-style full tunnel (fwmark, its own table, rules at fixed priorities), nftables, systemd-resolved. Windows: Wintun, routes and DNS by winipcfg, the WFP rules of wireguard-windows (`windows/firewall`, a copy with its notice). |
| `tests/` | `tunnel.sh` — the Linux tunnel end to end in sealed containers, the packaged app included; `tunnel-windows.ps1` — the same on a Windows runner; `screens.sh` — pictures of the screens against `standin.py`, the app driven through its web view's WebDriver by the buttons' texts (`screens.py`), in English and, against an older server's exits, in Russian. |

## Building and checking

```
app/check.sh            the core: format, clippy, tests (cargo on this machine, or a container); the interface's tests (vitest, in the build image)
app/build.sh check      the interface's lint and types, clippy on the app (in a container)
app/build.sh            qalby, then the app and its .deb (in a container): target/release/bundle/deb/
app/qalby/build.sh      qalby and testserver for Linux, vet and tests; Windows must still compile
app/tests/tunnel.sh     the Linux tunnel, sealed off (run app/build.sh first)
app/tests/screens.sh    the screens' pictures: target/screens/
```

Windows is built and checked by `.github/workflows/app-windows.yml`; the
installer (unsigned) is the run's artifact `awgscale-windows-installer`.

## What a device does

1. The person pastes (later scans) the link; the app shows what it is for and
   asks the device's name; the panel makes a user and hands back its config.
   The link may work only once, so the app first makes sure it can keep it.
2. Connect: the service brings the tunnel up and says `up` only while the
   server answers (a handshake younger than 3 minutes).
3. Inside the tunnel the app asks the network who the device is and where it
   can go (`/app/api/me`, `/exits`), and keeps it on an exit if asked.

The kill switch («Без VPN нет интернета», off by default) stops whatever would
leave the device around the tunnel; without it only DNS is kept inside on
Windows, which asks every adapter's DNS at once.
