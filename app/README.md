# The awgscale app

The client a person installs to join their network: one window (Tauri 2) on
Linux, Windows and iOS (built in CI; Android later); and `qalby`, the service that holds the tunnel
on a computer and its command line.

| Path | What it is |
|---|---|
| `core/` | Rust, shared by every app: the join link (`awgscale://join?h=…&c=…` or the panel's `https://<domain>/i/<code>`), a connection given by hand (`manual.rs`: an AmneziaVPN key `vpn://…`, its QR code, an AmneziaWG config), the invitation, the app API inside the tunnel (`http://172.16.17.1:8088/app/api`), the saved network, the device's settings, and the tunnel's shape. |
| `shell/` | The Tauri crate: the window and the commands its interface calls. Every request to the network and every secret stays here, in Rust. `packaging/` holds the systemd unit, the `.deb` scripts and the Windows installer's hooks. |
| `ui/` | The interface: React + Vite, the approved screens (start, connection, location, settings, the owner's tabs). |
| `qalby/` | Go, on amneziawg-go: `qalby daemon` (root / SYSTEM) holds the tunnel; the app and `qalby up / down / status` ask it over `/run/awgscale/control.sock` (root and the group `awgscale`) or `\\.\pipe\awgscale` (owned by SYSTEM, open to the people logged on). Linux: an awg-quick-style full tunnel (fwmark, its own table, rules at fixed priorities), nftables, systemd-resolved. Windows: Wintun, routes and DNS by winipcfg, the WFP rules of wireguard-windows (`windows/firewall`, a copy with its notice). |
| `android/` | The phone's side (a Tauri plugin): Kotlin `TunnelPlugin` (asks Android for the VPN, the `awgscale://` link the app was opened with) and `AwgVpnService` (the tunnel, in its own process `:tunnel`, a foreground service), and the Rust `PhoneTunnel` that is the core's `Tunnel` on Android. The tunnel itself is `qalby/cmd/libawgscale` (`libawgscale.so`): `qalby/mobile` on the desktop's amneziawg-go and `conf.Parse`. |
| `shell/gen/android/` | The Android project `tauri android init` made (Tauri expects it here), with the app's own changes: minSdk 26, the release key from the environment, the `awgscale://join` intent filter, the page kept between the system bars. |
| `tunnel-ios/` | The tunnel on iOS as the app sees it: a Tauri mobile plugin in Swift (`ios/Sources/TunnelPlugin.swift`) that installs the Network Extension as the device's VPN (NETunnelProviderManager), starts and stops it and says how it is (up only while the server answers: a handshake younger than 3 minutes); the core's `Tunnel` over it in Rust. Built into the app only for iOS. |
| `ios/` | The rest of iOS: `tunnel/` — the Network Extension (Packet Tunnel Provider) on WireGuardKit of [amneziawg-apple](https://github.com/amnezia-vpn/amneziawg-apple) (MIT; its wg-quick parser copied with its notice, `LICENSE-amneziawg-apple`); `project.yml` — the Xcode project's template (Tauri's own, MIT, `LICENSE-tauri`), with `tunnel.yml` (the extension) and `signing.yml` (its signing) included on demand; `ci.sh` — the steps of the iOS workflow; `check.sh` — what Linux can check of it. |
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
app/icons.sh            every platform's icons made anew from the logo (icon.svg, icon-foreground.svg, icon.json)
app/tests/android.sh    the app in an Android emulator (KVM): the screens, the join, the tunnel end to end; pictures in target/android-screens/
app/ios/check.sh        iOS, as far as Linux can: clippy of the app and the plugin for a phone and a simulator
                        (no Apple SDK: C is not compiled, nothing linked), the Swift parsed, the scripts linted
```

Windows is built and checked by `.github/workflows/app-windows.yml`; the
installer (unsigned) is the run's artifact `awgscale-windows-installer`.
Android by `.github/workflows/app-android.yml` (the emulator on the runner's
KVM too: a runner without it fails the job, `AWGSCALE_REQUIRE_EMULATOR=1`);
both go to the prerelease `app-latest` only once checked.

## The join screen

It takes an awgscale invitation (`awgscale://join?…`, or the panel's
`https://<domain>/i/<code>`, which the panel's invitation also shows as a QR
code), or a connection given by hand, as the AmneziaVPN app takes one: an Amnezia
key `vpn://…` (what our panel and bot hand out, and what the AmneziaVPN app
shares), the AmneziaVPN QR code of such a key (only a key in one code; the app
splits a long one into several, which are refused), or an AmneziaWG config,
pasted, typed or opened from a file («Open a file»). A connection given by hand
is a tunnel and nothing else: no panel behind it, so no location, no protection,
no owner's tabs; the config is written anew with only the keys every tunnel of
the app takes (wg-quick's own, PostUp, Table…, left out; anything else unknown
refused: the newer keys of the AmneziaVPN app's template — RekeyAfterTime,
RekeyTimeout, RejectAfterTime, KeepaliveTimeout, MaxHandshakeAttempts,
RandomTrailers, DisableCookies — are refused by name until the tunnels take them). On a phone «Scan QR code» reads either with the camera
(tauri-plugin-barcode-scanner: on Android ML Kit of Google Play services, so a
phone without them cannot scan, only paste); a computer has no such button. An
`awgscale://join` link opened on the phone (the system's camera, a message)
opens the app with it in the field: Android by its intent filter, iOS by the
URL scheme (`ios/project.yml`).

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

iOS is built by `.github/workflows/app-ios.yml` on a macOS runner (Xcode 16.4).
There is no Apple developer account yet, so CI builds for the simulator and runs
the app there only. The Xcode project is made there by `tauri ios init` from
`ios/project.yml` and is not kept in git (`shell/gen/apple/`); the app's icon is
copied into it from `shell/icons/ios`. Its jobs: `simulator` — the app (its
tunnel pretends) on an iPhone simulator against `tests/standin.py`, pictures of
the start, home, location and connected screens (`awgscale-ios-screens`);
`simulator-tunnel` — the app with its Network Extension for the simulator,
started, Connect pressed, which an unsigned build is refused
(`awgscale-ios-simulator`); `testflight` — a phone's build, signed, skipped until
its secrets exist (below). On the simulator the app presses buttons itself
(`AWGSCALE_PRESS`, the `pictures` feature, never packaged): there is no WebDriver.
The extension is in the project only when `AWGSCALE_IOS_TUNNEL=YES` while
`tauri ios init` runs (`ci.sh init YES`). The plugin that drives it
(`tunnel-ios/`) is in every iOS build, the pretending one too, so an error in its
Swift fails every job.

The plugin and the extension never pass on an error's text: the extension says why
the tunnel did not start with a fixed code (its log, the app group's
`last-error.txt`), the plugin rejects with a code and at most a config fault's
name or a system error's number, and `tunnel-ios/src/refusal.rs` says it in the
app's words. The kill switch is refused, as on a computer, when the config does not
send all traffic into the tunnel.

## iPhone: what the owner has to do

A VPN on an iPhone is a Network Extension, and Apple gives the entitlement for it
only to a paid developer account; it does not run on the simulator at all. So:

1. **Apple Developer Program** (99 USD a year, developer.apple.com/programs) — as a
   person or an organisation (an organisation needs a D-U-N-S number). Note the
   **Team ID** (Membership details, ten characters).
2. **Identifiers** (Certificates, Identifiers & Profiles):
   * App Group `group.tech.qalby.awgscale`;
   * App ID `tech.qalby.awgscale` (the app) with the capabilities *Network
     Extensions* and *App Groups* (that group);
   * App ID `tech.qalby.awgscale.tunnel` (the extension) with the same two.
   The Network Extension capability (packet tunnel provider) is self-service for
   an account in the program; nothing has to be requested.
3. **Certificate**: an *Apple Distribution* certificate; export it with its key
   from Keychain Access as a `.p12` with a password.
4. **Profiles**: two *App Store* provisioning profiles, one for each App ID, both
   with that certificate.
5. **App Store Connect**: a new app with the bundle id `tech.qalby.awgscale`; under
   Users and Access → Integrations, an **API key** (role App Manager): its Key ID,
   the Issuer ID, and the `.p8` file (downloadable once).
6. **The repository** (Settings → Secrets and variables → Actions). Secrets — the
   names the workflow reads; the values never go into a file of the repository:

   | Secret | What |
   |---|---|
   | `APPLE_DEVELOPMENT_TEAM` | the Team ID |
   | `IOS_CERTIFICATE` | the `.p12`, base64 (`base64 -i cert.p12`) |
   | `IOS_CERTIFICATE_PASSWORD` | its password |
   | `IOS_MOBILE_PROVISION` | the app's profile, base64 |
   | `IOS_TUNNEL_MOBILE_PROVISION` | the extension's profile, base64 |
   | `APP_STORE_CONNECT_KEY_ID` | the API key's Key ID |
   | `APP_STORE_CONNECT_ISSUER_ID` | the Issuer ID |
   | `APP_STORE_CONNECT_KEY` | the `.p8` file's text |

   Until all of them are set, the job `testflight` is skipped. Each is given only
   to the step that needs it.
7. **Export compliance**: the app uses encryption (the VPN), so App Store Connect
   asks about it for every build («Missing Compliance») until it is answered in
   the app's settings there, or `ITSAppUsesNonExemptEncryption` is put into the
   app's Info.plist (`ios/project.yml`) once the answer is known.
8. **TestFlight**: Actions → app-ios → Run workflow. The job `testflight` builds
   the app signed (Tauri signs the app; `ios/signing.yml` names the extension's
   profile), checks the extension is inside and uploads it to App Store Connect.
   After Apple's processing (minutes to an hour) the build is in TestFlight:
   add yourself as an internal tester, install TestFlight on the iPhone, install
   the app; the first Connect asks to add the VPN configuration. External testers
   need Apple's beta review once.

The `testflight` job has never run: it needs the account. Expect to adjust it
the first time (most likely the signing of the extension).

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
