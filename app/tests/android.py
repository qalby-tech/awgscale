#!/usr/bin/env python3
"""The steps of app/tests/android.sh, run in the emulator's container: the emulator itself (headless), the
APK installed, the app driven through adb and uiautomator. Every button is found by its text in the
screen's tree (the web view hands its page to Android's accessibility), never by a place on the screen. A
step whose text does not appear fails the run.

    android.py <apk> <test-panel apk> <pictures> <client.conf>

The APK is the one people install: everything but the join runs on it. The join runs last, on the build that
trusts the test's stand-in panel (app/build-android.sh test-panel): another app (".testpanel"), installed after
the first is taken away, since both open awgscale:// links.

What the tests ask the server (the app's requests through the tunnel) is checked by android.sh.
"""
import base64
import json
import os
import re
import subprocess
import sys
import time
import zlib
from pathlib import Path

PACKAGE = "tech.qalby.awgscale"
DATA = f"/data/data/{PACKAGE}"
# the build of `app/build-android.sh test-panel`: an app of its own
TEST_PACKAGE = f"{PACKAGE}.testpanel"
# the tunnel's service, by its class (the same in both apps)
SERVICE = "tech.qalby.awgscale.tunnel.AwgVpnService"
AVD = "phone"
WAIT_SECONDS = 30
# the tunnel is up while its last handshake is younger than this (handshakeFresh in app/qalby/mobile)
HANDSHAKE_FRESH_SECONDS = 180
BOOT_SECONDS = 300
# the screen of a phone: 1080 × 2340 at 440 dpi is 393 CSS pixels wide, as the screens were drawn (390)
SCREEN = {"hw.lcd.width": "1080", "hw.lcd.height": "2340", "hw.lcd.density": "440"}
# the network's own address inside the tunnel (the app API's stand-in answers there), and the device's
NETWORK_HOST = "172.16.17.1"
DEVICE_ADDRESS = "172.16.17.15"
# from the emulator, 10.0.2.2 is its container's own 127.0.0.1, where the stand-in for the pictures listens
HOST_LOOPBACK = "10.0.2.2"
# a made-up invitation of the shape the panel hands out (a 22-character secret)
LINK = "awgscale://join?h=vpn.example.org&c=Zx3kQ0aB1cD2eF3gH4iJ5k"
# the server's name in the sealed network (android.sh: the server's alias, which the emulator's DNS answers)
SERVER_NAME = "awg-server.test"
# what the stand-in panel logs (android.sh: /shared/panel.log)
PANEL_LOG = Path("/shared/panel.log")

failures: list[str] = []


class Failed(Exception):
    pass


def adb(*arguments: str, check: bool = True, timeout: int = 60) -> str:
    done = subprocess.run(["adb", *arguments], capture_output=True, text=True, timeout=timeout)
    if check and done.returncode != 0:
        raise Failed(f"adb {' '.join(arguments)}: {done.stderr.strip() or done.stdout.strip()}")
    return done.stdout


def shell(command: str, check: bool = False) -> str:
    return adb("shell", command, check=check)


def check(name: str, passed: bool) -> None:
    print(("ok " if passed else "FAIL ") + name, flush=True)
    if not passed:
        failures.append(name)


def start_emulator() -> None:
    home = Path(os.environ["HOME"]) / ".android" / "avd"
    (home / f"{AVD}.avd").mkdir(parents=True, exist_ok=True)
    (home / f"{AVD}.ini").write_text(f"avd.ini.encoding=UTF-8\npath={home}/{AVD}.avd\ntarget=android-35\n")
    config = {
        "image.sysdir.1": "system-images/android-35/google_apis/x86_64/",
        "tag.id": "google_apis",
        "abi.type": "x86_64",
        "hw.cpu.arch": "x86_64",
        "hw.cpu.ncore": "4",
        "hw.ramSize": "3072",
        "hw.keyboard": "yes",
        "disk.dataPartition.size": "4G",
        "PlayStore.enabled": "false",
        **SCREEN,
    }
    (home / f"{AVD}.avd" / "config.ini").write_text("".join(f"{key}={value}\n" for key, value in config.items()))
    log = open("/tmp/emulator.log", "w")
    subprocess.Popen(
        ["emulator", "-avd", AVD, "-no-window", "-gpu", "swiftshader_indirect", "-no-audio", "-no-boot-anim",
         "-no-snapshot", "-no-metrics", "-wipe-data"],
        stdout=log, stderr=subprocess.STDOUT, cwd="/tmp")
    adb("wait-for-device", timeout=BOOT_SECONDS)
    deadline = time.monotonic() + BOOT_SECONDS
    while shell("getprop sys.boot_completed").strip() != "1":
        if time.monotonic() > deadline:
            raise Failed("the emulator did not boot: " + Path("/tmp/emulator.log").read_text()[-2000:])
        time.sleep(2)
    for setting in ("window_animation_scale", "transition_animation_scale", "animator_duration_scale"):
        shell(f"settings put global {setting} 0")
    adb("root")
    adb("wait-for-device")
    time.sleep(2)


# The screen's tree: every node with a text, its words and where it is
NODE = re.compile(r"<node [^>]*>")


def nodes() -> list[dict]:
    for _ in range(5):
        dumped = shell("uiautomator dump /sdcard/screen.xml >/dev/null 2>&1 && cat /sdcard/screen.xml")
        if "<hierarchy" in dumped:
            break
        time.sleep(1)
    found = []
    for match in NODE.finditer(dumped):
        node = match.group(0)
        attribute = lambda name: (re.search(f' {name}="([^"]*)"', node) or [None, ""])[1]  # noqa: E731
        text = (attribute("text") or attribute("content-desc")).replace("&amp;", "&")
        bounds = [int(number) for number in re.findall(r"\d+", attribute("bounds"))]
        kind = attribute("class")
        if (text or kind == "android.widget.EditText") and len(bounds) == 4:
            found.append({"text": text, "clickable": attribute("clickable") == "true", "bounds": bounds, "class": kind})
    return found


def screen_text() -> str:
    return " | ".join(node["text"] for node in nodes())


def wait_for(text: str, clickable: bool = False, seconds: int = WAIT_SECONDS) -> dict:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for node in nodes():
            if (node["text"] == text or text in node["text"].split(" ")) and (node["clickable"] or not clickable):
                return node
        time.sleep(0.5)
    raise Failed(f"«{text}» did not appear; the screen shows: {screen_text()}")


def press(text: str, seconds: int = WAIT_SECONDS) -> None:
    left, top, right, bottom = wait_for(text, clickable=True, seconds=seconds)["bounds"]
    shell(f"input tap {(left + right) // 2} {(top + bottom) // 2}")


def picture(pictures: Path, name: str) -> None:
    (pictures / f"{name}.png").write_bytes(subprocess.run(["adb", "exec-out", "screencap", "-p"], capture_output=True).stdout)


def launch(package: str = PACKAGE) -> None:
    shell(f"monkey -p {package} -c android.intent.category.LAUNCHER 1")


def stop_app() -> None:
    shell(f"am force-stop {PACKAGE}")


def start_as_android(package: str = PACKAGE) -> None:
    """The service started the way Android starts the always-on VPN (at boot), nothing of the app running."""
    shell(f"am start-foreground-service -a android.net.VpnService -n {package}/{SERVICE}", check=True)


def stop_service(package: str = PACKAGE) -> None:
    shell(f"am start-foreground-service -a tech.qalby.awgscale.tunnel.DOWN -n {package}/{SERVICE}")


def notification_text() -> str:
    return " ".join(re.findall(r"android\.text=String \(([^)]*)\)", shell("dumpsys notification --noredact")))


def tunnel_interfaces() -> int:
    return len(re.findall(r"^\d+: tun\d+", shell("ip -o link show"), re.MULTILINE))


def seed(profile: dict) -> None:
    """The network's profile, put where the app keeps it (app_data_dir/network.json), as a joined device has it."""
    stop_app()
    Path("/tmp/network.json").write_text(json.dumps(profile))
    adb("push", "/tmp/network.json", "/data/local/tmp/network.json")
    os.remove("/tmp/network.json")
    owner = shell(f"stat -c %u:%g {DATA}").strip()
    shell(f"cp /data/local/tmp/network.json {DATA}/network.json && rm /data/local/tmp/network.json && "
          f"chown {owner} {DATA}/network.json && chmod 600 {DATA}/network.json && restorecon {DATA}/network.json", check=True)


def by_name(conf: str) -> str:
    """The config with the server given by its name, as the panel writes it (Endpoint = <its name>:<port>)."""
    return re.sub(r"^(Endpoint\s*=\s*)[^:\s]+:", lambda found: found[1] + SERVER_NAME + ":", conf, count=1, flags=re.MULTILINE)


def network(on: bool) -> None:
    """The phone's network (the emulator's Wi-Fi) switched off, or on and joined again, as at boot before it comes."""
    if not on:
        shell("svc wifi disable", check=True)
        # the command returns before the network is gone
        deadline = time.monotonic() + 30
        while "Wifi is enabled" in shell("cmd wifi status") or "10.0.2." in shell("ip -o addr show wlan0"):
            if time.monotonic() > deadline:
                raise Failed("the emulator's Wi-Fi did not go")
            time.sleep(1)
        return
    shell("svc wifi enable", check=True)
    deadline = time.monotonic() + 60
    while "Wifi is connected" not in shell("cmd wifi status"):
        if time.monotonic() > deadline:
            raise Failed("the emulator's Wi-Fi did not come back")
        shell("cmd wifi connect-network AndroidWifi open")
        time.sleep(3)


def amnezia_key(conf: str) -> str:
    """The config as an Amnezia key, vpn://…, the way the AmneziaVPN app writes one: its JSON compressed as Qt
    does it (the length, then zlib), URL-safe base64 without padding."""
    inner = json.dumps({"config": conf})
    server = json.dumps({"containers": [{"container": "amnezia-awg", "awg": {"last_config": inner}}],
                         "defaultContainer": "amnezia-awg", "description": "Test server"}).encode()
    packed = len(server).to_bytes(4, "big") + zlib.compress(server)
    return "vpn://" + base64.urlsafe_b64encode(packed).decode().rstrip("=")


def type_text(text: str, piece: int = 40) -> None:
    """Typed a few characters at a time: a long `input text` floods the slower emulators (CI's), and the web
    view drops some of what comes too fast. Only characters a shell takes as they are (a key's base64)."""
    for start in range(0, len(text), piece):
        shell("input text " + text[start:start + piece])
        time.sleep(0.2)


def reaches(address: str) -> bool:
    return " 0% packet loss" in shell(f"ping -c 2 -W 2 {address}")


def steps(apk: str, test_panel_apk: str, pictures: Path, conf: str) -> None:
    start_emulator()
    adb("install", "-r", apk, timeout=180)
    check("the APK installs", PACKAGE in shell("pm list packages"))
    # the notification that says the VPN is on: granted here, Android 13+ asks the person
    shell(f"pm grant {PACKAGE} android.permission.POST_NOTIFICATIONS")

    # the launcher shows the app by its own icon (adaptive: the dots on the logo's orange), not Tauri's
    shell("input keyevent KEYCODE_HOME")
    time.sleep(2)
    shell("input swipe 540 1900 540 400 300")  # the list of all the apps
    wait_for("awgscale")
    time.sleep(1)
    picture(pictures, "0-launcher")
    badging = shell(f"dumpsys package {PACKAGE} | grep -E 'versionName|versionCode'")
    check("the app's version is 0.1.11 (1011)", "versionName=0.1.11" in badging and "versionCode=1011" in badging)

    launch()
    wait_for("Connect to your network")
    wait_for("Scan QR code", clickable=True)
    picture(pictures, "1-start")
    check("the start screen fits the phone's width", all(node["bounds"][2] <= 1080 for node in nodes()))

    # the camera reads a QR code: Android asks for it first; refused, the app says how to allow it
    press("Scan QR code")
    wait_for("While using the app")
    picture(pictures, "1a-camera-asked")
    refuse = next(node for node in nodes() if node["text"].startswith("Don"))["bounds"]
    shell(f"input tap {(refuse[0] + refuse[2]) // 2} {(refuse[1] + refuse[3]) // 2}")
    wait_for("The app may not use the camera — allow it in the phone's settings")
    check("the camera refused: the app says so", True)
    # allowed: the camera's picture under the page, the frame and Cancel over it; Cancel is back on the screen
    shell(f"pm grant {PACKAGE} android.permission.CAMERA")
    press("Scan QR code")
    wait_for("Point the camera at the QR code")
    time.sleep(2)
    picture(pictures, "1b-scanning")
    press("Cancel")
    wait_for("Connect to your network")
    check("a reading cancelled: back on the join screen", "Scan QR code" in screen_text())
    press("Scan QR code")
    wait_for("Point the camera at the QR code")
    shell("input keyevent KEYCODE_BACK")
    wait_for("Connect to your network")
    check("Android's back button stops a reading, the app stays", shell(f"pidof {PACKAGE}").strip() != "")

    # «Open a file» hands the choice to the system's file picker (a config chosen there is read as if pasted)
    press("Open a file")
    deadline = time.monotonic() + WAIT_SECONDS
    while "documentsui" not in shell("dumpsys activity activities | grep -m1 topResumedActivity") and time.monotonic() < deadline:
        time.sleep(1)
    picture(pictures, "1c-file-picker")
    check("«Open a file» opens the system's file picker", "documentsui" in shell("dumpsys activity activities | grep -m1 topResumedActivity"))
    shell("input keyevent KEYCODE_BACK")
    wait_for("Connect to your network")

    # the join link opens the app on it (the intent filter), and fills the field
    shell(f"am start -a android.intent.action.VIEW -d '{LINK}'")
    wait_for(LINK)
    picture(pictures, "2-opened-link")
    check("an awgscale:// link opens the app with it in the field", True)
    check("an opened link is not looked at before a press", "Continue" in screen_text() and "Name this device" not in screen_text())

    # typed into the field as pasted text arrives (the system's paste puts the same text there)
    stop_app()
    launch()
    wait_for("Invitation link")
    field = next(node for node in nodes() if node["class"] == "android.widget.EditText")["bounds"]
    shell(f"input tap {(field[0] + field[2]) // 2} {(field[1] + field[3]) // 2}")
    shell("input text " + LINK.replace("&", "\\&").replace("?", "\\?"))
    wait_for(LINK)
    # through the system's clipboard: all of it cut, then pasted back
    shell("input keycombination KEYCODE_CTRL_LEFT KEYCODE_A")
    shell("input keyevent KEYCODE_CUT")
    time.sleep(1)
    check("the link cut from the field leaves it empty", LINK not in screen_text())
    shell("input keyevent KEYCODE_PASTE")
    wait_for(LINK)
    check("the link pasted into the field", True)
    press("Continue")
    wait_for("No connection to the network's server — check the internet")
    check("a link in the field is looked at (the panel is out of reach here: the app says so)", True)

    # the screens against the stand-in of the app API, outside any tunnel
    seed({"host": "vpn.example.org", "name": "test_phone", "address": "172.16.17.23", "entry": HOST_LOOPBACK,
          "owner": True, "conf": "[Interface]\nPrivateKey = made-up\n"})
    launch()
    wait_for("Disconnected")
    wait_for("owner")  # /app/api/me answered
    picture(pictures, "3-home")
    press("Location")
    wait_for("Auto")
    wait_for("Netherlands")
    picture(pictures, "4-location")
    shell("input keyevent KEYCODE_BACK")

    # a join link opened while the app is in a network: shown on top, nothing asked of its host
    stop_app()
    launch()
    wait_for("Disconnected")
    shell(f"am start -a android.intent.action.VIEW -d '{LINK}'")
    wait_for(LINK)
    picture(pictures, "4b-link-while-joined")
    check("a link opened while in a network is shown", True)
    press("Close")
    time.sleep(1)
    check("the link's note goes when closed", LINK not in screen_text())

    # the tunnel, end to end: the network's address is reached only through it
    seed({"host": "vpn.example.org", "name": "test_phone", "address": DEVICE_ADDRESS, "entry": NETWORK_HOST,
          "owner": True, "conf": conf})
    check("without the tunnel the network's address is out of reach", not reaches(NETWORK_HOST))
    launch()
    press("Connect")
    press("OK")  # Android asks once whether the app may be the VPN
    wait_for("Connected", seconds=60)
    time.sleep(2)  # the network's answers (me, exits) fill the screen; the accessibility tree lags behind it
    picture(pictures, "5-connected")
    check("the tunnel's interface has the device's address", f"inet {DEVICE_ADDRESS}/32" in shell("ip addr show tun0"))
    check("the network's address answers through the tunnel", reaches(NETWORK_HOST))
    check("the notification says the VPN is on", f"pkg={PACKAGE}" in shell("dumpsys notification"))
    routes = shell("dumpsys connectivity | grep -m1 'InterfaceName: tun'")
    check("all of IPv4 but the local networks goes into the tunnel", "11.0.0.0/8" in routes and "10.0.0.0/8" not in routes)
    check("the network's own subnet stays in the tunnel", "172.16.17.0/24" in routes)
    check("the network's DNS is the VPN's", f"DnsAddresses: [ /{NETWORK_HOST} ]" in routes)

    # a setting changed while connected: the tunnel is made anew with it, the new interface before the old one
    # goes, so that there is a tunnel at every moment (nothing goes outside it in between)
    press("Settings")
    local_around = "Local networks"
    wait_for(local_around, clickable=True)
    watcher = subprocess.Popen(
        ["adb", "shell", "for i in $(seq 1 120); do ip -o link show | grep -cE '^[0-9]+: tun[0-9]+:'; sleep 0.1; done"],
        stdout=subprocess.PIPE, text=True)
    time.sleep(1)
    press(local_around)
    counts = watcher.communicate(timeout=120)[0].split()
    check("a setting changed while connected: a tunnel's interface at every moment", len(counts) > 50 and "0" not in counts)
    deadline = time.monotonic() + 40
    routes = ""
    while time.monotonic() < deadline and "0.0.0.0/0" not in routes:
        time.sleep(1)
        routes = shell("dumpsys connectivity | grep -m1 'InterfaceName: tun'")
    check("the new tunnel takes the setting: the local networks go into it too", "0.0.0.0/0" in routes)
    check("the new tunnel carries", reaches(NETWORK_HOST))
    check("one tunnel's interface is left", tunnel_interfaces() == 1)
    press(local_around)
    deadline = time.monotonic() + 40
    while time.monotonic() < deadline and "11.0.0.0/8" not in routes:
        time.sleep(1)
        routes = shell("dumpsys connectivity | grep -m1 'InterfaceName: tun'")
    check("the setting back: the local networks go around the tunnel again", "11.0.0.0/8" in routes and "10.0.0.0/8" not in routes)
    press("Connection")
    wait_for("Connected", seconds=60)

    # the window goes (and with it the app's process): the tunnel stays
    shell("input keyevent KEYCODE_BACK")
    time.sleep(3)
    check("the app's process is gone with its window", shell(f"pidof {PACKAGE}").strip() == "")
    check("the tunnel's process stays", shell(f"pidof {PACKAGE}:tunnel").strip() != "")
    check("the tunnel still carries", reaches(NETWORK_HOST))
    launch()
    wait_for("Connected")
    check("the app opened again finds the tunnel up", True)

    # the server stops answering: up only while the last handshake is younger than 3 minutes
    endpoint = re.search(r"^Endpoint\s*=\s*(?P<host>[^:\s]+):(?P<port>\d+)", conf, re.MULTILINE)
    rule = f"OUTPUT -p udp -d {endpoint['host']} --dport {endpoint['port']} -j DROP"
    shell(f"iptables -I {rule}", check=True)
    wait_for("Connecting…", seconds=HANDSHAKE_FRESH_SECONDS + 30)
    picture(pictures, "6-server-gone")
    check("the server silent for 3 minutes: no longer up", True)
    shell(f"iptables -D {rule}", check=True)
    reaches(NETWORK_HOST)  # traffic into the tunnel makes it shake hands again (no traffic: no handshake)
    wait_for("Connected", seconds=90)
    check("the server back: up again", True)

    press("Disconnect")
    wait_for("Disconnected")
    time.sleep(1)
    check("down: no interface", tunnel_interfaces() == 0)
    check("down: the network's address is out of reach again", not reaches(NETWORK_HOST))
    check("down: no notification", f"pkg={PACKAGE}" not in shell("dumpsys notification"))
    picture(pictures, "7-disconnected")

    # a press while it is still connecting switches it off at once (the server silent here: it would wait 20 s)
    shell(f"iptables -I {rule}", check=True)
    press("Connect")
    wait_for("Connecting…", seconds=10)
    time.sleep(2)
    pressed = time.monotonic()
    press("Disconnect")
    wait_for("Disconnected", seconds=20)
    check("a press while connecting disconnects at once", time.monotonic() - pressed < 8)
    time.sleep(1)
    check("given up while connecting: no interface", tunnel_interfaces() == 0)
    shell(f"iptables -D {rule}", check=True)

    # the always-on VPN: Android starts the service itself (at boot: the person chose the app in the VPN settings),
    # nothing of the app running; it comes up with the network the app keeps. Started here the way Android
    # starts it, since the emulator's network does not come back after a reboot of its guest
    stop_app()
    check("nothing of the app runs", shell(f"pidof {PACKAGE}:tunnel").strip() == "" and shell(f"pidof {PACKAGE}").strip() == "")
    start_as_android()
    deadline = time.monotonic() + 60
    while not reaches(NETWORK_HOST) and time.monotonic() < deadline:
        time.sleep(2)
    check("always-on: the service started by Android brings the kept network up, the app not opened", reaches(NETWORK_HOST))
    check("always-on: the app's window was not opened for it", shell(f"pidof {PACKAGE}").strip() == "")
    stop_service()
    time.sleep(2)

    # always-on at boot, the server by its name, the phone's network not there yet: the service waits for it
    # («Ждём сеть…») and brings the tunnel up once the network comes
    seed({"host": "vpn.example.org", "name": "test_phone", "address": DEVICE_ADDRESS, "entry": NETWORK_HOST,
          "owner": True, "conf": by_name(conf)})
    network(False)
    start_as_android()
    time.sleep(8)
    check("no network: the service stays, waiting", shell(f"pidof {PACKAGE}:tunnel").strip() != "")
    check("no network: its notification says it waits", "Waiting for the network" in notification_text())
    check("no network: no tunnel yet", tunnel_interfaces() == 0)
    picture(pictures, "7b-waiting-for-network")
    network(True)
    deadline = time.monotonic() + 120
    while not reaches(NETWORK_HOST) and time.monotonic() < deadline:
        time.sleep(2)
    check("the network back: the server found by its name, the tunnel up", reaches(NETWORK_HOST))
    check("the network back: the notification says the VPN is on", "VPN is on" in notification_text())
    stop_service()
    time.sleep(2)
    check("always-on stopped: no interface", tunnel_interfaces() == 0)

    # a connection given by hand: the Amnezia key of the test server's config typed into the join screen; it is
    # the device's network at once, a tunnel without a panel, brought up by the same service
    stop_app()
    shell(f"rm -f {DATA}/network.json")
    launch()
    wait_for("Invitation link")
    field = next(node for node in nodes() if node["class"] == "android.widget.EditText")["bounds"]
    shell(f"input tap {(field[0] + field[2]) // 2} {(field[1] + field[3]) // 2}")
    key = amnezia_key(conf)
    type_text(key)
    time.sleep(1)
    typed = next((node["text"] for node in nodes() if node["class"] == "android.widget.EditText"), "")
    check(f"the key typed whole ({len(key)} characters; the field shows {len(typed)})", typed == key)
    press("Continue")
    wait_for("AmneziaWG connection", seconds=30)
    check("a connection given by hand has no location", "Location" not in screen_text())
    picture(pictures, "7c-manual")
    press("Connect")
    wait_for("Connected", seconds=60)
    check("the connection given by hand carries", reaches(NETWORK_HOST))
    picture(pictures, "7d-manual-connected")
    press("Disconnect")
    wait_for("Disconnected")

    # a device joins with the link, the way a person does it, on the build that trusts the stand-in panel; the
    # app people install goes first (both open awgscale:// links)
    adb("uninstall", PACKAGE, check=False)
    adb("install", test_panel_apk, timeout=180)
    installed = shell("pm list packages").split()
    check("the test build is an app of its own", f"package:{TEST_PACKAGE}" in installed and f"package:{PACKAGE}" not in installed)
    shell(f"pm grant {TEST_PACKAGE} android.permission.POST_NOTIFICATIONS")
    shell(f"am start -a android.intent.action.VIEW -d '{LINK}'")
    wait_for(LINK)
    time.sleep(3)
    check("the link opened: its host not asked before a press", "info" not in PANEL_LOG.read_text())
    press("Continue")
    wait_for("Name this device", seconds=60)
    picture(pictures, "8-invitation")
    field = next(node for node in nodes() if node["class"] == "android.widget.EditText")["bounds"]
    shell(f"input tap {(field[0] + field[2]) // 2} {(field[1] + field[3]) // 2}")
    shell("input text test_join")
    press("Connect this device")
    wait_for("Disconnected", seconds=60)
    check("the joined network is kept by the app", "test_join" in shell(f"cat /data/data/{TEST_PACKAGE}/network.json"))
    press("Connect")
    press("OK")  # another app: Android asks whether it may be the VPN
    wait_for("Connected", seconds=60)
    check("the joined device's tunnel is up", reaches(NETWORK_HOST))
    picture(pictures, "9-joined-connected")
    press("Disconnect")
    wait_for("Disconnected")


def main() -> None:
    apk, test_panel_apk, pictures, conf_path = sys.argv[1], sys.argv[2], Path(sys.argv[3]), sys.argv[4]
    try:
        steps(apk, test_panel_apk, pictures, Path(conf_path).read_text())
    except Failed as failure:
        print(f"FAIL {failure}", flush=True)
        failures.append(str(failure))
        picture(pictures, "failed")
    if failures:
        Path(pictures / "logcat.txt").write_text(adb("logcat", "-d", check=False)[-400000:])
    sys.exit(1 if failures else 0)


if __name__ == "__main__":
    main()
