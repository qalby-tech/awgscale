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
| `android/` | The phone's side (a Tauri plugin): Kotlin `TunnelPlugin` (asks Android for the VPN, the `awgscale://` link the app was opened with) and `AwgVpnService` (the tunnel, in its own process `:tunnel`, a foreground service), and the Rust `PhoneTunnel` that is the core's `Tunnel` on Android. The tunnel itself is `qalby/cmd/libawgscale` (`libawgscale.so`): `qalby/mobile` on the desktop's amneziawg-go and `conf.Parse`. |
| `shell/gen/android/` | The Android project `tauri android init` made (Tauri expects it here), with the app's own changes: minSdk 26, the release key from the environment, the `awgscale://join` intent filter, the page kept between the system bars. |
| `tests/` | `tunnel.sh` — the Linux tunnel end to end in sealed containers, the packaged app included; `tunnel-windows.ps1` — the same on a Windows runner; `screens.sh` — pictures of the screens against `standin.py`, the app driven through its web view's WebDriver by the buttons' texts (`screens.py`), in English and, against an older server's exits, in Russian. |

## Building and checking

```
app/check.sh            the core: format, clippy, tests (cargo on this machine, or a container); the interface's tests (vitest, in the build image)
app/build.sh check      the interface's lint and types, clippy on the app (in a container)
app/build.sh            qalby, then the app and its .deb (in a container): target/release/bundle/deb/
app/qalby/build.sh      qalby and testserver for Linux, vet and tests; Windows must still compile
app/tests/tunnel.sh     the Linux tunnel, sealed off (run app/build.sh first)
app/tests/screens.sh    the screens' pictures: target/screens/
app/build-android.sh    libawgscale.so (NDK) and the APK for arm64-v8a and x86_64 (in app/docker/android): target/android/awgscale-android.apk
app/build-android.sh test-panel   the APK app/tests/android.sh joins with (it trusts the test's stand-in panel; never shipped): another app, tech.qalby.awgscale.testpanel, signed by a key made for that one build
app/build-android.sh verification the APK, then shell/gen/android/gradle/verification-metadata.xml written anew (after a change of the Android project's dependencies)
app/tests/android.sh    the app in an Android emulator (KVM): the screens, the join, the tunnel end to end; pictures in target/android-screens/
```

Windows is built and checked by `.github/workflows/app-windows.yml`; the
installer (unsigned) is the run's artifact `awgscale-windows-installer`.
Android by `.github/workflows/app-android.yml` (the emulator on the runner's
KVM too: a runner without it fails the job, `AWGSCALE_REQUIRE_EMULATOR=1`);
both go to the prerelease `app-latest` only once checked.

## Android

Every tool of the build is pinned in `docker/android/Dockerfile` (the base
image by digest; JDK, Node, Go, the SDK's command-line tools, platform-tools,
the emulator and its system image by checksum; platforms, build-tools and the
NDK by their exact versions, which sdkmanager checks against Google's list;
Debian's packages by their exact versions from one day of snapshot.debian.org).
The Gradle wrapper is pinned by its checksum, and every plugin and library
Gradle downloads by its sha256 (`shell/gen/android/gradle/verification-metadata.xml`).

The APK is signed. The owner's release key is handed over in the environment
(`AWGSCALE_KEYSTORE_FILE`, `AWGSCALE_KEYSTORE_PASSWORD`, `AWGSCALE_KEY_ALIAS`,
`AWGSCALE_KEY_PASSWORD`; in CI the secrets `ANDROID_KEYSTORE_BASE64`,
`ANDROID_KEYSTORE_PASSWORD`, `ANDROID_KEY_ALIAS`, `ANDROID_KEY_PASSWORD`); without
it a key of the machine is made once in `target/android/keystore/` (in CI: one
per run). A phone takes an update only with the same key, so the release key
must sign the first APK people install. To make one:
`keytool -genkeypair -keystore release.jks -alias awgscale -keyalg RSA -keysize 3072 -validity 10000`,
kept outside the repository.

On a phone the tunnel is the app's VpnService; Android asks the person once
whether the app may be the VPN. The config is read and run by the same Go
(`conf`, amneziawg-go) as on a computer: every obfuscation key the panel writes.
Android routes by destination only, so what goes around the tunnel (the local
networks, the person's list) is taken out of the routes (`conf.Routes`), and
the network's own subnet and DNS stay in. The network's DNS is the VPN's DNS.
The kill switch is refused, as on a computer, when the server does not send all
of IPv4 into the tunnel. What keeps the traffic in is Android's own: Settings →
Network → VPN → awgscale → «Always-on VPN» and «Block connections without VPN»;
the app cannot switch it on and says so while it is asked for and off. One
difference from a computer: Android's switch lets nothing past the VPN, so the
local networks (the printer, the router's page) are blocked too while the tunnel
is down, and likely even while it is up, whatever goes around the tunnel (not yet
tried on a phone); a computer's kill switch lets them through. A config of IPv4 only sends IPv6 by the network, as on a
computer. The service runs in its own process:
the tunnel outlives the window (Tauri ends the app's process with it). Started
by Android as the always-on VPN it comes up with the network and the choices the
app keeps. When the server's name cannot be found yet (at boot, before the
network) it stays, says «Ждём сеть…» and tries again whenever a network comes
(and at growing intervals up to a minute). Settings changed while connected
bring up the new interface first and only then stop the old tunnel.

The app's data (the device's keys among it) is never backed up nor moved to a
new phone (`allowBackup="false"` and `data_extraction_rules.xml`): a new phone
joins with a link of its own. A join link opened from elsewhere only fills the
field; its host is asked nothing until the person presses Continue.

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
